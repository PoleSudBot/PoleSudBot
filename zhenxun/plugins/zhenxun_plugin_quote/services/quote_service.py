import json
import os
from pathlib import Path
import random
import base64
from collections.abc import Iterable
from typing import Any, ClassVar

from cachetools import TTLCache
from nonebot.adapters.onebot.v11 import Bot
from nonebot_plugin_alconna import At, Text
from tortoise.expressions import F, Q
from tortoise.functions import Count

from zhenxun import ui
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services import avatar_service, logger
from zhenxun.utils.echart_utils import ChartUtils
from zhenxun.utils.echart_utils.models import Barh
from zhenxun.utils.platform import PlatformUtils
import aiofiles

from ..config import DATA_PATH, resolve_quote_image_path
from ..model import HotQuoteItemData, HotQuotesPageData, Quote, QuoteCardData

try:
    import spacy_pkuseg as pkuseg

    seg = pkuseg.pkuseg(model_name="web")
except ImportError:
    logger.warning(
        "未安装 'spacy_pkuseg'，分词功能将受限。请运行 `pip install zhenxun[pkuseg]`",
        "群聊语录",
    )

    class DummySeg:
        def cut(self, text):
            return [text] if text else []

    seg = DummySeg()


class QuoteService:
    """语录服务类"""

    _recent_quotes: ClassVar[TTLCache] = TTLCache(maxsize=1000, ttl=600)
    _max_history_per_key: ClassVar[int] = 30
    _user_tag_prefix: ClassVar[str] = "user:"

    @classmethod
    def serialize_user_tag(cls, user_id: str) -> str:
        return f"{cls._user_tag_prefix}{str(user_id).strip()}"

    @classmethod
    def parse_tag_text(cls, text: str | None) -> list[str]:
        if not text:
            return []
        return [part.strip() for part in text.split() if part.strip()]

    @classmethod
    def normalize_tags(cls, tags: Iterable[str | None]) -> list[str]:
        normalized_tags: list[str] = []
        seen: set[str] = set()

        for tag in tags:
            if tag is None:
                continue
            tag_value = str(tag).strip()
            if not tag_value or tag_value in seen:
                continue
            seen.add(tag_value)
            normalized_tags.append(tag_value)

        return normalized_tags

    @classmethod
    def parse_tag_segments(cls, parts: Iterable[Any]) -> list[str]:
        raw_tags: list[str] = []

        for part in parts:
            if (
                isinstance(part, At) or part.__class__.__name__ == "At"
            ) and getattr(part, "target", None):
                raw_tags.append(cls.serialize_user_tag(str(part.target)))
            elif isinstance(part, Text) or part.__class__.__name__ == "Text":
                raw_tags.extend(cls.parse_tag_text(part.text))
            elif isinstance(part, str):
                raw_tags.extend(cls.parse_tag_text(part))

        return cls.normalize_tags(raw_tags)

    @classmethod
    def _deserialize_storage_tags(cls, raw_tags: Any) -> list[str]:
        if isinstance(raw_tags, list):
            return cls.normalize_tags(str(tag) for tag in raw_tags)

        if isinstance(raw_tags, tuple):
            return cls.normalize_tags(str(tag) for tag in raw_tags)

        if isinstance(raw_tags, str):
            stripped = raw_tags.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return cls.normalize_tags(cls.parse_tag_text(stripped))
            return cls._deserialize_storage_tags(parsed)

        return []

    @classmethod
    def get_auto_tags(cls, quote: Quote) -> list[str]:
        return cls._deserialize_storage_tags(getattr(quote, "tags", []))

    @classmethod
    def get_manual_tags(cls, quote: Quote) -> list[str]:
        return cls._deserialize_storage_tags(getattr(quote, "manual_tags", []))

    @classmethod
    def get_all_tags(cls, quote: Quote) -> list[str]:
        return cls.normalize_tags(cls.get_manual_tags(quote) + cls.get_auto_tags(quote))

    @classmethod
    def parse_user_tag(cls, tag: str) -> str | None:
        if not tag.startswith(cls._user_tag_prefix):
            return None
        user_id = tag.removeprefix(cls._user_tag_prefix).strip()
        return user_id or None

    @classmethod
    def format_tags_for_message(cls, tags: Iterable[str]) -> list[Any]:
        normalized_tags = cls.normalize_tags(tags)
        if not normalized_tags:
            return ["无"]

        message_parts: list[Any] = []
        for index, tag in enumerate(normalized_tags):
            if index:
                message_parts.append(" ")

            if user_id := cls.parse_user_tag(tag):
                message_parts.append(At(target=user_id, flag="user"))
            else:
                message_parts.append(tag)

        return message_parts

    @classmethod
    def _match_user_filter(cls, quote: Quote, user_id_filter: str | None) -> bool:
        if not user_id_filter:
            return True

        user_id = str(user_id_filter)
        if str(getattr(quote, "quoted_user_id", "") or "") == user_id:
            return True

        user_tag = cls.serialize_user_tag(user_id)
        return user_tag in cls.get_all_tags(quote)

    @staticmethod
    async def add_quote(
        group_id: str,
        image_path: str,
        ocr_content: str | None,
        recorded_text: str | None,
        quoted_user_id: str | None = None,
        image_hash: str | None = None,
        uploader_user_id: str | None = None,
        manual_tags: list[str] | None = None,
    ) -> tuple[Quote | None, bool]:
        """
        向数据库添加语录，并在内部处理所有重复性检查。
        """
        try:
            logger.info(
                f"开始添加语录 - 群组: {group_id}, 图片路径: {image_path}, 被记录用户: {quoted_user_id}, 上传者: {uploader_user_id}",
                "群聊语录",
            )
            if image_hash:
                existing_quote = await Quote.filter(
                    group_id=group_id, image_hash=image_hash
                ).first()
                if existing_quote:
                    logger.warning(
                        f"发现重复语录 (基于图片哈希值) - 群组: {group_id}, 已存在ID: {existing_quote.id}",
                        "群聊语录",
                    )
                    return existing_quote, False

            tags_source = ocr_content if ocr_content else recorded_text
            tags = QuoteService.cut_sentence(tags_source) if tags_source else []
            normalized_manual_tags = QuoteService.normalize_tags(manual_tags or [])

            relative_image_path = os.path.relpath(image_path, DATA_PATH)
            relative_image_path = Path(relative_image_path).as_posix()

            quote = await Quote.create(
                group_id=group_id,
                image_path=relative_image_path,
                image_hash=image_hash,
                ocr_text=ocr_content,
                recorded_text=recorded_text,
                tags=tags,
                manual_tags=normalized_manual_tags,
                quoted_user_id=quoted_user_id,
                uploader_user_id=uploader_user_id,
                view_count=0,
            )

            logger.info(f"语录添加成功 - ID: {quote.id}, 群组: {group_id}", "群聊语录")
            return quote, True

        except Exception as e:
            logger.error(f"添加语录失败 - 群组: {group_id}, 错误: {e}", "群聊语录", e=e)
            return None, False

    @staticmethod
    async def delete_quote(group_id: str, image_basename: str) -> bool:
        """从数据库删除语录并删除对应的图片文件"""
        try:
            logger.info(
                f"尝试删除语录 - 群组: {group_id}, 图片: {image_basename}", "群聊语录"
            )
            quote = await QuoteService.find_quote_by_basename(group_id, image_basename)
            if quote:
                return await QuoteService.delete_quote_instance(quote)
            else:
                logger.warning(
                    f"要删除的语录不存在 - 群组: {group_id}, 图片: {image_basename}",
                    "群聊语录",
                )
                return False
        except Exception as e:
            logger.error(f"删除语录失败 - 群组: {group_id}, 错误: {e}", "群聊语录", e=e)
            return False

    @staticmethod
    async def delete_quote_instance(quote: Quote) -> bool:
        """按语录实体删除数据，避免定位成功后再次走文件名查找。"""
        try:
            absolute_image_path = resolve_quote_image_path(quote.image_path)
            if os.path.exists(absolute_image_path):
                try:
                    os.remove(absolute_image_path)
                    logger.info(f"图片文件删除成功: {absolute_image_path}", "群聊语录")
                except Exception as file_error:
                    logger.warning(
                        f"删除图片文件失败: {absolute_image_path}, 错误: {file_error}",
                        "群聊语录",
                        e=file_error,
                    )
            else:
                logger.warning(f"图片文件不存在: {absolute_image_path}", "群聊语录")

            await Quote.filter(id=quote.id).delete()
            logger.info(
                f"语录删除成功 - ID: {quote.id}, 群组: {quote.group_id}",
                "群聊语录",
            )
            return True
        except Exception as e:
            logger.error(
                f"按语录实体删除失败 - ID: {quote.id}, 群组: {quote.group_id}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return False

    @classmethod
    async def get_random_quote(
        cls, group_id: str, user_id_filter: str | None = None
    ) -> Quote | None:
        """随机获取一条语录，可根据用户筛选，并避免短时间内重复展示"""
        try:
            logger.info(
                f"尝试随机获取语录 - 群组: {group_id}, 用户筛选: {user_id_filter}",
                "群聊语录",
            )
            quotes = list(await Quote.filter(group_id=group_id))
            if user_id_filter:
                quotes = [
                    quote
                    for quote in quotes
                    if cls._match_user_filter(quote, user_id_filter)
                ]

            count = len(quotes)
            if count == 0:
                logger.info(
                    f"群组 {group_id} 中 (用户: {user_id_filter or '任意'}) 没有语录",
                    "群聊语录",
                )
                return None

            memory_key = f"{group_id}_{user_id_filter or 'all'}"

            recent_ids = cls._recent_quotes.get(memory_key) or []
            if recent_ids and count > cls._max_history_per_key:
                unseen_quotes = [q for q in quotes if q.id not in recent_ids]
                if unseen_quotes:
                    quotes = unseen_quotes
                else:
                    logger.warning(
                        f"所有语录 ({memory_key}) 都已展示过，将重置记忆",
                        "群聊语录",
                    )
                    cls._recent_quotes[memory_key] = []

            quote = cls._select_and_record_quote(memory_key, quotes)

            if quote:
                logger.info(
                    f"随机获取到语录 ID: {quote.id} (路径: {quote.image_path}) 来自群组 {group_id} (用户: {user_id_filter or '任意'})",
                    "群聊语录",
                )
                return quote
            else:
                logger.warning(
                    f"随机获取语录失败，即使 count > 0 (count={count}) - {memory_key}",
                    "群聊语录",
                )
                return None
        except Exception as e:
            logger.error(
                f"随机获取语录时发生错误 - 群组: {group_id}, 用户筛选: {user_id_filter}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @classmethod
    async def _search_quotes_by_text_and_filter_by_tags(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> list[Quote]:
        """
        分阶段搜索语录：
        1. [精确匹配] 首先尝试匹配完整的关键词。
        2. [模糊匹配] 如果没有精确匹配结果，则回退到分词模糊搜索。
        """
        base_filters = {"group_id": group_id}
        candidate_quotes = list(await Quote.filter(**base_filters))
        if user_id_filter:
            candidate_quotes = [
                quote
                for quote in candidate_quotes
                if cls._match_user_filter(quote, user_id_filter)
            ]
        logger.debug(f"搜索候选语录共 {len(candidate_quotes)} 条", "群聊语录")
        if not candidate_quotes:
            return []

        logger.info(
            f"第一阶段：尝试对 '{keyword}' 进行精确匹配搜索...", "群聊语录-搜索"
        )
        # 精确阶段也必须把手动/自动 tag 视作同一检索池，否则一旦文本先命中，
        # 手动 tag 命中的语录会被提前短路掉，表现上就像 manual_tags 没参与查询。
        exact_matches = [
            quote
            for quote in candidate_quotes
            if cls._check_exact_keyword_in_quote(keyword, quote)
        ]

        if exact_matches:
            logger.info(
                f"精确匹配成功，找到 {len(exact_matches)} 条语录。", "群聊语录-搜索"
            )
            return exact_matches

        logger.info("精确匹配未找到结果，回退到分词与 tag 综合搜索...", "群聊语录-搜索")
        keywords = [k.strip() for k in keyword.split() if k.strip()]
        if not keywords:
            return []

        final_matches = []
        for quote in candidate_quotes:
            if all(cls._check_single_keyword_in_quote(kw, quote) for kw in keywords):
                final_matches.append(quote)

        logger.debug(f"经过最终过滤后，匹配到 {len(final_matches)} 条语录", "群聊语录")
        return final_matches

    @classmethod
    async def search_quotes(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> list[Quote]:
        """根据关键词搜索语录并返回完整命中集。"""
        logger.info(
            f"开始搜索语录 - 群组: {group_id}, 关键词: {keyword}, 用户筛选: {user_id_filter}",
            "群聊语录",
        )

        try:
            all_matches = await cls._search_quotes_by_text_and_filter_by_tags(
                group_id, keyword, user_id_filter
            )
            if all_matches:
                logger.info(f"总共找到匹配的语录 {len(all_matches)} 条", "群聊语录")
            else:
                logger.info(
                    f"群组 {group_id} (用户: {user_id_filter or '任意'}) 中未找到与 '{keyword}' 相关的语录。",
                    "群聊语录",
                )
            return all_matches
        except Exception as e:
            logger.error(
                f"搜索语录时发生错误 - 群组: {group_id}, 关键词: {keyword}, "
                f"用户筛选: {user_id_filter}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return []

    @classmethod
    def _check_exact_keyword_in_quote(cls, keyword: str, quote: Quote) -> bool:
        """
        检查完整关键词是否直接命中语录文本或任一 tag。

        精确阶段如果只看 ocr/recorded_text，会导致文本命中先返回，
        让手动 tag 命中的语录完全失去参与抽取的机会。
        """
        kw_lower = keyword.lower()

        if quote.ocr_text and kw_lower in quote.ocr_text.lower():
            return True
        if quote.recorded_text and kw_lower in quote.recorded_text.lower():
            return True

        return any(kw_lower in str(tag).lower() for tag in cls.get_all_tags(quote))

    @classmethod
    def _check_single_keyword_in_quote(cls, keyword: str, quote: Quote) -> bool:
        """
        检查单个关键词（及其分词）是否存在于语录的文本或标签中。
        这是一个在Python层面执行的辅助函数。
        """
        kw_lower = keyword.lower()
        tokens = cls.cut_sentence(keyword)

        if quote.ocr_text and kw_lower in quote.ocr_text.lower():
            return True
        if quote.recorded_text and kw_lower in quote.recorded_text.lower():
            return True
        for token in tokens:
            if quote.ocr_text and token.lower() in quote.ocr_text.lower():
                return True
            if quote.recorded_text and token.lower() in quote.recorded_text.lower():
                return True

        tags_to_check = cls.get_all_tags(quote)
        for tag in tags_to_check:
            tag_lower = str(tag).lower()
            if kw_lower in tag_lower:
                return True
            for token in tokens:
                if token.lower() in tag_lower:
                    return True

        return False

    @classmethod
    async def search_quote(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> Quote | None:
        """根据关键词搜索单条语录，可根据用户筛选。"""
        memory_key = f"{group_id}_{user_id_filter or 'all'}_{keyword}"

        try:
            all_matches = await cls.search_quotes(group_id, keyword, user_id_filter)

            if all_matches:
                random_quote = cls._select_and_record_quote(memory_key, all_matches)
                logger.info(
                    f"搜索到语录 ID: {random_quote.id} (路径: {random_quote.image_path})",
                    "群聊语录",
                )
                return random_quote

            return None
        except Exception as e:
            logger.error(
                f"搜索语录时发生错误 - 群组: {group_id}, 关键词: {keyword}, "
                f"用户筛选: {user_id_filter}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    async def get_random_quote_ids(group_id: str) -> list[int]:
        """仅拉取语录 ID，避免随机多张时把整群 ORM 实体化到内存。"""
        try:
            return list(
                await Quote.filter(group_id=group_id).values_list("id", flat=True)
            )
        except Exception as e:
            logger.error(
                f"获取随机语录 ID 列表失败 - 群组: {group_id}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return []

    @staticmethod
    async def get_quotes_by_ids_in_order(ids: list[int]) -> list[Quote]:
        """按输入 ID 顺序返回语录，避免 id__in 查询结果顺序不稳定。"""
        if not ids:
            return []

        try:
            quotes = await Quote.filter(id__in=ids)
            quote_by_id = {quote.id: quote for quote in quotes}
            # SQL 的 IN 查询不保证结果顺序，这里按输入顺序重排，
            # 否则多张发送时会把前面挑好的去重/随机顺序打乱。
            return [quote_by_id[quote_id] for quote_id in ids if quote_id in quote_by_id]
        except Exception as e:
            logger.error(
                f"根据 ID 列表获取语录失败 - IDs: {ids}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return []

    @staticmethod
    async def find_quote_by_basename(
        group_id: str, image_basename: str
    ) -> Quote | None:
        """根据图片文件名查找语录"""
        try:
            logger.info(
                f"根据文件名查找语录 - 群组: {group_id}, 文件名: {image_basename}",
                "群聊语录",
            )

            quotes = await Quote.filter(
                group_id=group_id, image_path__iendswith=image_basename
            )
            quote = quotes[0] if quotes else None

            if quote:
                logger.info(
                    f"找到语录 ID: {quote.id} (路径: {quote.image_path})",
                    "群聊语录",
                )
                return quote

            logger.info(f"未找到包含文件名 {image_basename} 的语录", "群聊语录")
            return None
        except Exception as e:
            logger.error(
                f"根据文件名查找语录时发生错误 - 群组: {group_id}, 文件名: {image_basename}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    def _is_quote_image_stem_match(quote: Quote, image_md5: str) -> bool:
        """只比较最终文件名主干，避免把路径中的其他十六进制串误判为语录标识。"""
        return Path(str(quote.image_path)).stem.lower() == image_md5.lower()

    @classmethod
    def _pick_quote_by_image_md5(
        cls, quotes: Iterable[Quote], image_md5: str
    ) -> Quote | None:
        for quote in quotes:
            if cls._is_quote_image_stem_match(quote, image_md5):
                logger.debug(
                    f"回复图片 md5 最终校验通过 - quote_id={quote.id}, image_path={quote.image_path}",
                    "群聊语录",
                )
                return quote

            logger.debug(
                f"回复图片 md5 最终校验未通过 - quote_id={quote.id}, image_path={quote.image_path}, image_md5={image_md5}",
                "群聊语录",
            )
        return None

    @classmethod
    async def find_quote_by_reply_image(
        cls,
        group_id: str,
        reply_image_md5: str | None = None,
        reply_image_basename: str | None = None,
    ) -> Quote | None:
        """根据回复中的图片标识查找语录，优先走 md5 链路，失败后回退 basename。"""
        try:
            if reply_image_md5:
                logger.info(
                    f"根据回复图片 md5 查找语录 - 群组: {group_id}, md5: {reply_image_md5}",
                    "群聊语录",
                )

                exact_candidates = await Quote.filter(
                    group_id=group_id,
                    image_path__iendswith=f"{reply_image_md5}.png",
                ).limit(10)
                if exact_candidates:
                    logger.debug(
                        f"回复图片 md5 首选粗筛命中 {len(exact_candidates)} 条候选",
                        "群聊语录",
                    )
                    if quote := cls._pick_quote_by_image_md5(
                        exact_candidates, reply_image_md5
                    ):
                        return quote
                else:
                    logger.debug("回复图片 md5 首选粗筛未命中", "群聊语录")

                fuzzy_candidates = await Quote.filter(
                    group_id=group_id,
                    image_path__icontains=reply_image_md5,
                ).limit(10)
                if fuzzy_candidates:
                    logger.debug(
                        f"回复图片 md5 兼容粗筛命中 {len(fuzzy_candidates)} 条候选",
                        "群聊语录",
                    )
                    if quote := cls._pick_quote_by_image_md5(
                        fuzzy_candidates, reply_image_md5
                    ):
                        return quote
                else:
                    logger.debug("回复图片 md5 兼容粗筛未命中", "群聊语录")

            if reply_image_basename:
                logger.debug(
                    f"回复图片查找回退到 basename 链路 - 群组: {group_id}, basename: {reply_image_basename}",
                    "群聊语录",
                )
                return await cls.find_quote_by_basename(group_id, reply_image_basename)

            logger.info(
                f"回复图片未提供可用标识，无法在群组 {group_id} 中定位语录。",
                "群聊语录",
            )
            return None
        except Exception as e:
            logger.error(
                f"根据回复图片查找语录时发生错误 - 群组: {group_id}, md5: {reply_image_md5}, basename: {reply_image_basename}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    async def get_last_quote(group_id: str) -> Quote | None:
        """获取群组内最后保存的一条语录"""
        try:
            return await Quote.filter(group_id=group_id).order_by("-id").first()
        except Exception:
            return None

    @staticmethod
    async def get_all_quotes() -> list[Quote]:
        """获取所有语录"""
        try:
            logger.info("开始获取所有语录", "群聊语录")
            quotes = await Quote.all()
            logger.info(f"获取所有语录成功 - 总数: {len(quotes)}", "群聊语录")
            return quotes
        except Exception as e:
            logger.error(f"获取所有语录失败 - 错误: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def add_tags(quote: Quote, tags: list[str]) -> bool:
        """为语录添加标签"""
        before_tags = QuoteService.get_manual_tags(quote)
        updated_tags = await QuoteService.add_manual_tags(quote, tags)
        return updated_tags != before_tags or not QuoteService.normalize_tags(tags)

    @classmethod
    async def add_manual_tags(cls, quote: Quote, tags: list[str]) -> list[str]:
        """为语录添加手动标签"""
        try:
            normalized_tags = cls.normalize_tags(tags)
            logger.info(
                f"为语录 ID: {quote.id} 添加手动标签: {normalized_tags}", "群聊语录"
            )

            current_tags = cls.get_manual_tags(quote)
            updated_tags = cls.normalize_tags(current_tags + normalized_tags)
            quote.manual_tags = updated_tags
            await quote.save(update_fields=["manual_tags"])

            logger.info(
                f"语录 ID: {quote.id} 手动标签更新成功，现有标签: {updated_tags}",
                "群聊语录",
            )
            return updated_tags
        except Exception as e:
            logger.error(
                f"为语录添加手动标签失败 - 语录 ID: {quote.id}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return cls.get_manual_tags(quote)

    @staticmethod
    async def delete_tags(quote: Quote, tags: list[str]) -> bool:
        """删除语录的标签"""
        before_tags = QuoteService.get_manual_tags(quote)
        updated_tags = await QuoteService.delete_manual_tags(quote, tags)
        return updated_tags != before_tags or not QuoteService.normalize_tags(tags)

    @classmethod
    async def delete_manual_tags(cls, quote: Quote, tags: list[str]) -> list[str]:
        """删除语录的手动标签"""
        try:
            remove_tags = set(cls.normalize_tags(tags))
            logger.info(
                f"从语录 ID: {quote.id} 删除手动标签: {list(remove_tags)}", "群聊语录"
            )
            current_tags = cls.get_manual_tags(quote)
            updated_tags = [tag for tag in current_tags if tag not in remove_tags]
            quote.manual_tags = updated_tags
            await quote.save(update_fields=["manual_tags"])

            logger.info(
                f"语录 ID: {quote.id} 手动标签删除成功，现有标签: {updated_tags}",
                "群聊语录",
            )
            return updated_tags
        except Exception as e:
            logger.error(
                f"删除语录手动标签失败 - 语录 ID: {quote.id}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return cls.get_manual_tags(quote)

    @classmethod
    def _select_ids_without_record(
        cls, memory_key: str, candidate_ids: list[int], limit: int
    ) -> list[int]:
        """按 unseen-first 规则挑选 ID，但把写入历史延后到真正发送成功之后。"""
        if not candidate_ids or limit <= 0:
            return []

        recent_ids = cls._recent_quotes.get(memory_key) or []
        unseen_ids = [quote_id for quote_id in candidate_ids if quote_id not in recent_ids]
        selected_ids = list(unseen_ids) if unseen_ids else list(candidate_ids)
        random.shuffle(selected_ids)
        selected_ids = selected_ids[:limit]

        if len(selected_ids) < limit:
            # 当未看过的候选不足时再回填历史记录内的语录，
            # 这样一次多张请求也尽量保持“先展示没发过的内容”。
            fallback_ids = [
                quote_id for quote_id in candidate_ids if quote_id not in selected_ids
            ]
            random.shuffle(fallback_ids)
            selected_ids.extend(fallback_ids[: limit - len(selected_ids)])

        return selected_ids

    @classmethod
    def select_quote_ids_without_record(
        cls, memory_key: str, candidate_ids: list[int], limit: int
    ) -> list[int]:
        """为随机多张路径挑选 ID，避免把整群 ORM 实体一次性加载到内存。"""
        return cls._select_ids_without_record(memory_key, candidate_ids, limit)

    @classmethod
    def select_quotes_without_record(
        cls, memory_key: str, quotes: list[Quote], limit: int
    ) -> list[Quote]:
        """在完成文件校验前先挑选候选，避免坏文件污染最近记录。"""
        if not quotes or limit <= 0:
            return []

        quote_by_id = {quote.id: quote for quote in quotes}
        candidate_ids = [quote.id for quote in quotes]
        selected_ids = cls._select_ids_without_record(memory_key, candidate_ids, limit)
        return [quote_by_id[quote_id] for quote_id in selected_ids if quote_id in quote_by_id]

    @classmethod
    def record_recent_quote_ids(cls, memory_key: str, quote_ids: list[int]) -> None:
        """只有真正发送成功的语录才写入去重历史，避免无效记录污染最近窗口。"""
        if not quote_ids:
            return

        history = list(cls._recent_quotes.get(memory_key) or [])
        history.extend(quote_ids)
        # 多张请求会一次写入多个 ID，这里统一只保留最近窗口，
        # 防止一次 10 连立刻把同 key 的去重历史撑爆。
        cls._recent_quotes[memory_key] = history[-cls._max_history_per_key :]

    @classmethod
    def _select_and_record_quote(cls, memory_key: str, quotes: list[Quote]) -> Quote:
        """选择并记录单条语录。"""
        if not quotes:
            raise ValueError("语录列表为空")

        selected_quotes = cls.select_quotes_without_record(memory_key, quotes, 1)
        if not selected_quotes:
            raise ValueError("语录列表为空")
        selected_quote = selected_quotes[0]
        cls.record_recent_quote_ids(memory_key, [selected_quote.id])
        return selected_quote

    @classmethod
    async def increment_view_counts(cls, quote_ids: list[int]) -> None:
        """批量增加查看次数，避免多图发送时逐条 UPDATE 放大写压力。"""
        if not quote_ids:
            return

        try:
            unique_ids = list(dict.fromkeys(quote_ids))
            await Quote.filter(id__in=unique_ids).update(view_count=F("view_count") + 1)
            logger.debug(
                f"批量增加语录查看次数成功 - IDs: {unique_ids}",
                "群聊语录",
            )
        except Exception as e:
            logger.error(
                f"批量增加语录查看次数失败 - IDs: {quote_ids}, 错误: {e}",
                "群聊语录",
                e=e,
            )

    @classmethod
    async def search_quotes_for_deletion(
        cls, group_id: str, keywords: list[str] | None = None, **filters: Any
    ) -> list[Quote]:
        """根据关键词（OR逻辑）或其他条件搜索语录，用于批量删除。"""
        logger.info(
            f"开始搜索语录用于删除 - 群组: {group_id}, 关键词: {keywords}, 过滤器: {filters}",
            "群聊语录",
        )

        try:
            query = Q(group_id=group_id)

            if keywords:
                keyword_query = Q()
                for kw in keywords:
                    keyword_query |= Q(ocr_text__icontains=kw)
                    keyword_query |= Q(recorded_text__icontains=kw)
                query &= keyword_query

            if filters:
                for key, value in filters.items():
                    if value is not None:
                        query &= Q(**{key: value})

            final_matched_quotes = await Quote.filter(query)

            logger.info(
                f"找到 {len(final_matched_quotes)} 条与条件匹配的语录",
                "群聊语录",
            )
            return final_matched_quotes

        except Exception as e:
            logger.error(
                f"搜索语录用于删除时发生错误 - 群组: {group_id}, 关键词: {keywords}, "
                f"过滤器: {filters}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return []

    @staticmethod
    async def find_quotes_from_left_users(group_id: str, bot: Bot) -> list[Quote]:
        """查找指定群组中由已退群用户产生或记录的语录"""
        logger.info(f"开始查找群组 {group_id} 中已退群用户的语录", "群聊语录")
        try:
            uploaders = await Quote.filter(
                group_id=group_id, uploader_user_id__not_isnull=True
            ).values_list("uploader_user_id", flat=True)
            quoted = await Quote.filter(
                group_id=group_id, quoted_user_id__not_isnull=True
            ).values_list("quoted_user_id", flat=True)
            all_quote_users = set(uploaders) | set(quoted)

            current_members_info = await PlatformUtils.get_group_member_list(
                bot, group_id
            )
            current_member_ids = {
                str(member.user_id) for member in current_members_info
            }

            left_user_ids = all_quote_users - current_member_ids

            if not left_user_ids:
                logger.info(f"群组 {group_id} 中没有发现已退群用户的语录", "群聊语录")
                return []

            logger.info(
                f"在群组 {group_id} 中找到 {len(left_user_ids)} 个已退群用户留下的语录记录。"
            )

            left_user_quotes = await Quote.filter(
                Q(group_id=group_id)
                & (
                    Q(uploader_user_id__in=list(left_user_ids))
                    | Q(quoted_user_id__in=list(left_user_ids))
                )
            ).all()

            return left_user_quotes
        except Exception as e:
            logger.error(f"查找已退群用户语录时发生错误: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def generate_temp_quote(
        avatar_bytes: bytes,
        text: Any,
        author: str,
        variant: str | None = None,
        author_role: str | None = None,
        author_title: str | None = None,
        author_level: str | None = None,
        quoted_reply: Any = None,
    ) -> bytes:
        """生成临时语录图片"""
        try:
            logger.info(
                f"开始生成临时语录图片 - 作者: {author}, 皮肤(variant): {variant}",
                "群聊语录",
            )

            avatar_base64 = base64.b64encode(avatar_bytes).decode("utf-8")

            quote_card = QuoteCardData(
                avatar_data_url=f"data:image/png;base64,{avatar_base64}",
                text=text,
                author=author,
                author_role=author_role,
                author_title=author_title,
                author_level=author_level,
                quoted_reply=quoted_reply,
                variant=variant,
            )

            img_data = await ui.render(quote_card)

            return img_data
        except Exception as e:
            logger.error(f"生成临时语录图片失败: {e}", "群聊语录", e=e)
            raise e

    @staticmethod
    async def increment_view_count(quote_id: int) -> None:
        """增加语录的查看次数"""
        await QuoteService.increment_view_counts([quote_id])

    @staticmethod
    async def get_hottest_quotes(group_id: str, limit: int = 10) -> list[Quote]:
        """获取最热门的语录 (按查看次数)"""
        logger.debug(f"开始获取群组 {group_id} 最热门语录，数量: {limit}", "群聊语录")
        try:
            hottest_quotes = (
                await Quote.filter(group_id=group_id)
                .order_by("-view_count")
                .limit(limit)
                .all()
            )

            logger.debug(
                f"成功获取群组 {group_id} 热门语录，共 {len(hottest_quotes)} 条",
                "群聊语录",
            )

            for i, quote in enumerate(hottest_quotes):
                quote_type = (
                    "图片语录"
                    if (
                        quote.image_path and not (quote.ocr_text or quote.recorded_text)
                    )
                    else "文本语录"
                )
                logger.debug(
                    f"热门语录 #{i + 1}: ID={quote.id}, 类型={quote_type}, 查看次数={quote.view_count}",
                    "群聊语录",
                )

            return hottest_quotes
        except Exception as e:
            logger.error(f"获取群组 {group_id} 热门语录失败: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def generate_hottest_quotes_image(
        group_id: str, hottest_quotes: list[Quote], bot_self_id: str
    ) -> bytes | str:
        """使用HTML模板为热门语录列表生成一张汇总图片"""
        if not hottest_quotes:
            return f"群组 {group_id} 暂时没有热门语录。"

        logger.info(f"开始使用HTML模板生成群组 {group_id} 的热门语录图片", "群聊语录")

        quote_cards_data = []

        for i, quote in enumerate(hottest_quotes):
            avatar_base64 = ""
            if quote.quoted_user_id:
                try:
                    avatar_path = await avatar_service.get_avatar_path(
                        platform="qq", identifier=quote.quoted_user_id
                    )
                    if avatar_path:
                        async with aiofiles.open(avatar_path, "rb") as f:
                            avatar_data = await f.read()
                        avatar_base64 = base64.b64encode(avatar_data).decode("utf-8")
                except Exception as e:
                    logger.warning(
                        f"获取用户 {quote.quoted_user_id} 头像失败: {e}", "群聊语录"
                    )

            user_name = ""
            if quote.quoted_user_id:
                user_info = await GroupInfoUser.get_or_none(
                    user_id=quote.quoted_user_id, group_id=quote.group_id
                )
                if user_info:
                    user_name = (
                        user_info.user_name
                        or user_info.nickname
                        or quote.quoted_user_id
                    )
                else:
                    user_name = quote.quoted_user_id

            is_image_quote = quote.image_path and not (
                quote.ocr_text or quote.recorded_text
            )
            preview_text = quote.ocr_text or quote.recorded_text
            if is_image_quote:
                preview_text = "[图片语录]"
            elif not preview_text:
                preview_text = "[未知内容]"

            image_path = ""
            if is_image_quote:
                absolute_path = resolve_quote_image_path(quote.image_path)
                if os.path.exists(absolute_path):
                    image_path = f"file://{absolute_path}"

            card_data = HotQuoteItemData(
                rank=i + 1,
                user_name=user_name,
                avatar_data_url=f"data:image/png;base64,{avatar_base64}"
                if avatar_base64
                else "",
                preview_text=preview_text,
                is_image_quote=is_image_quote,
                image_path=image_path,
                view_count=quote.view_count,
                quote_id=quote.id,
            )
            quote_cards_data.append(card_data)

        page_data = HotQuotesPageData(
            group_id=group_id,
            quotes=quote_cards_data,
        )

        try:
            pic_bytes = await ui.render(page_data)
            logger.info(f"群组 {group_id} 热门语录图片生成成功", "群聊语录")
            return pic_bytes
        except Exception as e:
            logger.error(f"使用HTML模板生成热门语录图片失败: {e}", "群聊语录", e=e)
            return "生成热门语录图片失败，请检查模板文件或日志。"

    @staticmethod
    async def get_most_prolific_uploaders(group_id: str, limit: int = 10) -> list[dict]:
        """获取最高产的语录上传用户"""
        logger.debug(f"获取群组 {group_id} 最高产上传用户，数量: {limit}", "群聊语录")
        prolific_users = (
            await Quote.filter(group_id=group_id, uploader_user_id__not_isnull=True)
            .annotate(upload_count=Count("uploader_user_id"))
            .group_by("uploader_user_id")
            .order_by("-upload_count")
            .limit(limit)
            .values("uploader_user_id", "upload_count")
        )
        return prolific_users

    @staticmethod
    async def get_most_quoted_users(group_id: str, limit: int = 10) -> list[dict]:
        """获取被记录语录最多的用户"""
        logger.debug(f"获取群组 {group_id} 被记录最多用户，数量: {limit}", "群聊语录")
        quoted_users = (
            await Quote.filter(group_id=group_id, quoted_user_id__not_isnull=True)
            .annotate(quote_count=Count("quoted_user_id"))
            .group_by("quoted_user_id")
            .order_by("-quote_count")
            .limit(limit)
            .values("quoted_user_id", "quote_count")
        )
        return quoted_users

    @classmethod
    async def generate_bar_chart_for_prolific_users(
        cls, group_id: str, data: list[dict], title_prefix: str
    ) -> bytes | str:
        """为高产用户生成柱状图"""
        if not data:
            return f"{title_prefix}数据为空"

        user_ids = [
            item.get("uploader_user_id") or item.get("quoted_user_id") for item in data
        ]
        counts = [item.get("upload_count") or item.get("quote_count") for item in data]

        user_names = []
        for uid in user_ids:
            if not uid:
                user_names.append("未知用户")
                continue
            user_info = await GroupInfoUser.get_or_none(
                group_id=group_id, user_id=str(uid)
            )
            user_names.append(
                user_info.user_name if user_info and user_info.user_name else str(uid)
            )

        user_names.reverse()
        counts.reverse()

        barh_data = Barh(
            category_data=user_names,
            data=counts,  # type: ignore
            title=f"群组 {group_id} {title_prefix}排行",
        )
        try:
            chart_image = await ChartUtils.barh(barh_data)
            return chart_image  # type: ignore
        except Exception as e:
            logger.error(f"生成 {title_prefix} 图表失败: {e}", "群聊语录", e=e)
            return f"生成 {title_prefix} 图表失败: {e}"

    @staticmethod
    def cut_sentence(text: str | None) -> list[str]:
        """使用 pkuseg 对文本进行分词，并去除标点符号等无用词"""
        if not text:
            logger.debug("分词文本为空", "群聊语录")
            return []

        cut_words = seg.cut(text)
        cut_words_list: list[str] = list(set(str(word) for word in cut_words))

        punctuation = ".,!?:;。，！？：；%$\n []()（）《》<>「」'''-_+=*&^#@~`"
        stopwords = [
            "的",
            "了",
            "是",
            "在",
            "我",
            "有",
            "和",
            "就",
            "不",
            "人",
            "都",
            "一",
            "一个",
            "上",
            "也",
            "很",
            "到",
            "说",
            "要",
            "去",
            "你",
            "会",
            "着",
            "没有",
            "看",
            "好",
            "自己",
            "这",
        ]
        remove_set = set(punctuation) | set(stopwords)

        new_words: list[str] = [
            word
            for word in cut_words_list
            if word not in remove_set and len(word.strip()) > 0
        ]

        if len(text) <= 10:
            if text.strip() and text.strip() not in remove_set:
                new_words.append(text.strip())

        return new_words
