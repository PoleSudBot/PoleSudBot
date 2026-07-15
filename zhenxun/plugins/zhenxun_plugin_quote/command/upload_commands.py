import base64
import hashlib
import os
import html
from pathlib import Path
import re
import uuid
from typing import Any, cast

import aiofiles
from arclet.alconna import Alconna, Args, Arparma, CommandMeta, MultiVar, Option
from nonebot import get_driver
from nonebot.permission import SUPERUSER
import httpx
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot_plugin_alconna import At, Text, on_alconna
from nonebot_plugin_alconna.uniseg import (
    Image as UniImage,
    Reply,
    UniMessage,
    Segment,
)
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from zhenxun import ui
from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.http_utils import AsyncHttpx

from ..config import ensure_quote_path, get_quote_group_path
from ..command.manage_commands import get_available_themes
from ..model import Quote, QuoteCardData, QuoteSequenceData, QuotedReplyData
from ..services.ocr_service import OCRService
from ..services.quote_service import QuoteService
from ..utils.exceptions import ImageProcessError, NetworkError
from ..utils.image_utils import get_img_hash
from ..utils.tag_utils import (
    collect_tag_parts,
    extract_manual_tags_with_mention_names,
)

from zhenxun.services import avatar_service


def _is_simple_text_message(uni_message: UniMessage) -> bool:
    """
    判断消息是否为“简单纯文本”（可包含@）。
    """
    if not uni_message:
        return False

    allowed_types: tuple[type[Segment], ...] = (Text, At, Reply)
    has_text = False
    for seg in uni_message:
        if not isinstance(seg, allowed_types):
            return False
        if isinstance(seg, Text) and seg.text.strip():
            has_text = True

    return has_text


async def _extract_info_from_reply(event: MessageEvent, bot: Bot):
    """
    仅从回复消息中提取信息，不执行任何网络或渲染操作。
    返回一个包含信息的元组，或一个包含错误信息的元组。
    """
    reply = await reply_fetch(event, bot)
    if not reply or not reply.msg:
        return None, "请回复需要处理的消息，或无法获取回复的详细消息内容。"

    if not (event.reply and event.reply.sender):
        return None, "无法获取回复者信息。"

    uni_message = await UniMessage.generate(message=reply.msg, bot=bot)  # type: ignore

    is_empty = not any(
        isinstance(seg, (Text, UniImage))
        and (not isinstance(seg, Text) or seg.text.strip())
        for seg in uni_message
    )
    if is_empty:
        return None, "回复的消息内容为空。"

    sender = event.reply.sender
    qqid = str(sender.user_id)
    card = sender.card or sender.nickname or qqid
    logger.debug(f"sender: {sender}")
    return (uni_message, card, qqid), None


async def _process_nested_reply(
    message_array: list[dict], bot: Bot
) -> QuotedReplyData | None:
    """
    检查消息段中是否存在对另一条消息的回复（即"引用中的引用"），
    如果存在，则处理并返回一个 QuotedReplyData 对象。
    """
    try:
        if not isinstance(message_array, list):
            return None

        for seg_dict in message_array:
            if seg_dict.get("type") == "reply":
                grandparent_id = int(seg_dict.get("data", {}).get("id", 0))
                if not grandparent_id:
                    continue

                grandparent_msg_info = await bot.get_msg(message_id=grandparent_id)
                gp_group_id = grandparent_msg_info.get("group_id")

                gp_sender = grandparent_msg_info["sender"]
                gp_user_id = str(gp_sender["user_id"])
                gp_author = (
                    gp_sender.get("card") or gp_sender.get("nickname") or gp_user_id
                )

                raw_gp_message_array = grandparent_msg_info["message"]
                gp_message_obj = Message(
                    MessageSegment(d["type"], d["data"]) for d in raw_gp_message_array
                )
                gp_uni_msg = await UniMessage.generate(message=gp_message_obj, bot=bot)

                gp_content_list: list[dict] = []
                current_text_parts: list[str] = []

                async def flush_text_gp() -> None:
                    nonlocal current_text_parts
                    if current_text_parts:
                        gp_content_list.append(
                            {"type": "text", "value": "".join(current_text_parts)}
                        )
                        current_text_parts = []

                for seg in gp_uni_msg:
                    if isinstance(seg, Text) and seg.text:
                        current_text_parts.append(html.escape(seg.text))
                    elif isinstance(seg, At):
                        at_qq = seg.target
                        at_name = seg.display or at_qq
                        if gp_group_id:
                            try:
                                member_info_at = await bot.get_group_member_info(
                                    group_id=int(gp_group_id), user_id=int(at_qq)
                                )
                                at_name = (
                                    member_info_at.get("card")
                                    or member_info_at.get("nickname")
                                    or at_name
                                )
                            except Exception:
                                pass
                        current_text_parts.append(
                            f'<span class="message-at">@{html.escape(at_name)}</span>'
                        )
                    elif isinstance(seg, UniImage):
                        await flush_text_gp()
                        try:
                            if seg.path:
                                async with aiofiles.open(seg.path, "rb") as img_f:
                                    img_bytes = await img_f.read()
                            elif seg.url:
                                async with httpx.AsyncClient() as client:
                                    resp = await client.get(seg.url)
                                    resp.raise_for_status()
                                    img_bytes = resp.content
                            else:
                                continue

                            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                            gp_content_list.append(
                                {
                                    "type": "image",
                                    "value": f"data:image/png;base64,{img_base64}",
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                f"处理嵌套引用内图片失败: {e}", "群聊语录", e=e
                            )
                await flush_text_gp()

                gp_avatar_path = await avatar_service.get_avatar_path(
                    platform="qq", identifier=gp_user_id
                )
                if gp_avatar_path:
                    async with aiofiles.open(gp_avatar_path, "rb") as f:
                        gp_avatar_bytes = await f.read()
                    gp_avatar_base64 = base64.b64encode(gp_avatar_bytes).decode("utf-8")
                    return QuotedReplyData(
                        avatar_data_url=f"data:image/png;base64,{gp_avatar_base64}",
                        author=gp_author,
                        text=gp_content_list,
                    )
    except Exception as e:
        logger.debug(f"处理'引用中引用'失败(或消息不含嵌套引用): {e}", "群聊语录")
    return None


async def _get_member_details(
    bot: Bot, group_id: str, user_id: str, fallback_sender: Any
) -> tuple[str, str | None, str | None, str | None]:
    """
    获取群成员详细信息 (名片/昵称, 角色, 头衔, 等级)。
    """
    card, author_role, author_title, author_level_info = None, None, None, None
    try:
        # 尝试调用 API 获取最新信息
        member_info = await bot.call_api(
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=True,
        )
        if member_info and isinstance(member_info, dict):
            card = member_info.get("card") or member_info.get("nickname") or user_id
            author_role = member_info.get("role")
            author_title = member_info.get("title")
            author_level_info = (
                f"LV{member_info.get('level')}" if member_info.get("level") else None
            )
        else:
            raise ValueError("API返回数据异常")
    except Exception:
        # 回退到 sender 携带的信息
        if isinstance(fallback_sender, dict):
            card = (
                fallback_sender.get("card")
                or fallback_sender.get("nickname")
                or user_id
            )
            author_role = fallback_sender.get("role")
            author_title = fallback_sender.get("title")
            author_level_info = (
                f"LV{fallback_sender.get('level', '')}"
                if fallback_sender.get("level")
                else None
            )
        else:
            # 处理对象类型的 fallback (如 event.sender)
            card = (
                getattr(fallback_sender, "card", None)
                or getattr(fallback_sender, "nickname", None)
                or user_id
            )
            author_role = getattr(fallback_sender, "role", None)
            author_title = getattr(fallback_sender, "title", None)
            author_level_info = (
                f"LV{getattr(fallback_sender, 'level', '')}"
                if getattr(fallback_sender, "level", None)
                else None
            )

    return card, author_role, author_title, author_level_info


async def _convert_msg_to_card(
    bot: Bot,
    group_id: str,
    user_id: str,
    uni_message: UniMessage,
    sender_info: Any,
    variant: str | None,
    quoted_reply_data: QuotedReplyData | None = None,
) -> tuple[QuoteCardData, str]:
    """
    将单条消息转换为语录卡片数据模型，并返回用于记录的纯文本。
    """
    # 1. 获取用户信息
    card, role, title, level = await _get_member_details(
        bot, group_id, user_id, sender_info
    )

    # 2. 获取头像
    avatar_path = await avatar_service.get_avatar_path(
        platform="qq", identifier=user_id
    )
    if not avatar_path:
        raise NetworkError(f"获取用户 {user_id} 的头像失败")

    async with aiofiles.open(avatar_path, "rb") as f:
        avatar_bytes = await f.read()
    avatar_base64 = base64.b64encode(avatar_bytes).decode("utf-8")

    # 3. 处理消息内容
    content_list = []
    current_text_parts = []
    text_for_record = ""

    async def flush_text():
        nonlocal current_text_parts, text_for_record
        if current_text_parts:
            full_text = "".join(current_text_parts)
            content_list.append({"type": "text", "value": full_text})
            text_for_record += re.sub(
                r"<[^>]+>", "", full_text
            )  # 简单去除HTML标签用于记录
            current_text_parts = []

    for seg in uni_message:
        if isinstance(seg, Text) and seg.text:
            current_text_parts.append(html.escape(seg.text))
        elif isinstance(seg, At):
            # 处理 At (尝试获取群名片)
            at_name = seg.display or seg.target
            try:
                at_info = await bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(seg.target)
                )
                at_name = at_info.get("card") or at_info.get("nickname") or at_name
            except Exception:
                pass
            current_text_parts.append(
                f'<span class="message-at">@{html.escape(at_name)}</span>'
            )
        elif isinstance(seg, UniImage):
            await flush_text()
            text_for_record += "[图片]"
            try:
                if seg.path:
                    async with aiofiles.open(seg.path, "rb") as img_f:
                        img_bytes = await img_f.read()
                elif seg.url:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(seg.url)
                        resp.raise_for_status()
                        img_bytes = resp.content
                else:
                    continue
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                content_list.append(
                    {"type": "image", "value": f"data:image/png;base64,{img_b64}"}
                )
            except Exception as e:
                logger.warning(f"处理语录内图片失败: {e}", "群聊语录")

    await flush_text()

    card_data = QuoteCardData(
        avatar_data_url=f"data:image/png;base64,{avatar_base64}",
        text=content_list,
        author=card,
        author_role=role,
        author_title=title,
        author_level=level,
        variant=variant or "default",
        quoted_reply=quoted_reply_data,
    )
    return card_data, text_for_record


async def _generate_quote_from_reply(
    event: MessageEvent, bot: Bot, uni_message: UniMessage, variant: str | None = None
):
    """
    从回复消息中提取信息并生成语录图片。
    这是一个辅助函数，用于合并 make_record 和 render_quote 的公共逻辑。
    """
    sender = event.reply.sender
    qqid = str(sender.user_id)
    replied_msg_id = cast(int, event.reply.message_id)

    full_replied_msg_info = await bot.get_msg(message_id=replied_msg_id)
    message_array = full_replied_msg_info.get("message", [])
    quoted_reply_data = await _process_nested_reply(message_array, bot)

    group_id = str(event.group_id)

    try:
        card_data, _ = await _convert_msg_to_card(
            bot, group_id, qqid, uni_message, sender, variant, quoted_reply_data
        )

        # 直接渲染卡片
        img_data = await ui.render(card_data)

        return (img_data, card_data.author, qqid, quoted_reply_data), None
    except (NetworkError, ImageProcessError, FileNotFoundError) as e:
        return None, str(e)
    except Exception as e:
        logger.error(f"生成语录图片时发生未知错误: {e}", "群聊语录", e=e)
        return None, f"生成语录图片时发生未知错误: {e}"


MAX_RECORD_COUNT = 10


async def _generate_sequence_from_history(
    bot: Bot,
    group_id: str,
    messages: list[dict],
    variant: str | None,
) -> tuple[bytes, str, str]:
    """从多条历史消息记录生成语录序列图片"""
    card_data_list = []
    recorded_text_parts = []
    last_quoted_user_id = ""

    messages.sort(key=lambda m: m.get("time", 0))

    for msg_info in messages:
        sender = msg_info["sender"]
        qqid = str(sender["user_id"])
        last_quoted_user_id = qqid

        raw_message_array = msg_info.get("message", [])
        if isinstance(raw_message_array, str):
            raw_message_array = [{"type": "text", "data": {"text": raw_message_array}}]

        # 处理嵌套引用
        reply_prefix = ""
        quoted_reply_data = await _process_nested_reply(raw_message_array, bot)

        if quoted_reply_data:
            quoted_text_parts = []
            for seg in quoted_reply_data.text:
                if seg.get("type") == "text":
                    quoted_text_parts.append(seg.get("value", ""))
            quoted_text_plain = "".join(quoted_text_parts)
            if quoted_text_plain:
                reply_prefix = (
                    f"「回复 {quoted_reply_data.author}: {quoted_text_plain}」\n"
                )

        message_obj = Message(
            MessageSegment(d["type"], d["data"]) for d in raw_message_array
        )
        uni_message = await UniMessage.generate(message=message_obj, bot=bot)

        # 调用统一转换器
        card_data, text_content = await _convert_msg_to_card(
            bot, group_id, qqid, uni_message, sender, variant, quoted_reply_data
        )

        recorded_text_parts.append(f"{reply_prefix}{card_data.author}: {text_content}")
        card_data_list.append(card_data)

    sequence_data = QuoteSequenceData(messages=card_data_list)
    img_data = await ui.render(sequence_data)
    return img_data, "\n".join(recorded_text_parts), last_quoted_user_id


def _extract_target_image_from_parts(parts: list[Any]) -> UniImage | None:
    for part in parts:
        if isinstance(part, UniImage):
            return part
    return None


def _message_has_image_segment(message: Any) -> bool:
    if not message:
        return False

    try:
        return any(getattr(seg, "type", None) == "image" for seg in message)
    except TypeError:
        return False


def _strip_command_start(text: str) -> str:
    """
    去掉消息前导命令前缀，避免 `/上传语录` 这类写法误落到旧命令路由。
    """
    normalized = text.lstrip()
    command_starts = sorted(
        (str(start) for start in get_driver().config.command_start if start),
        key=len,
        reverse=True,
    )
    for start in command_starts:
        if normalized.startswith(start):
            return normalized[len(start) :].lstrip()
    return normalized


def _starts_with_command_name(text: str, command_name: str) -> bool:
    return _strip_command_start(text).startswith(command_name)


def _is_new_upload_command_text(text: str) -> bool:
    return _starts_with_command_name(text, "上传语录")


def _is_new_record_command_text(text: str) -> bool:
    return _starts_with_command_name(text, "记录语录")


def _build_basic_usage_hint_text() -> str:
    return (
        "基础用法：\n"
        "上传语录 [图片] [tag/@用户 ...]\n"
        "记录语录 [tag/@用户 ...]（记录 为别名，需回复消息）\n"
        "入典 [tag/@用户 ...]（classic 预设，需回复消息）\n"
        "语录 [关键词/@用户]"
    )


def _build_upload_migration_hint_text() -> str:
    return (
        "“上传”不再直接解析图片。\n"
        "上传图片并解析请使用：上传语录 [图片] [tag/@用户 ...]\n"
        "回复文本生成语录请使用：记录语录 [tag/@用户 ...]\n"
        "classic 风格预设可使用：入典 [tag/@用户 ...]\n"
        "兼容别名：记录（需回复消息）"
    )


def _build_upload_success_text() -> str:
    return f"保存成功\n{_build_basic_usage_hint_text()}"


def _build_upload_success_message(img_data: bytes) -> list[bytes | str]:
    return [img_data, "\n保存成功\n", _build_basic_usage_hint_text()]


def _build_record_success_message(img_data: bytes) -> list[bytes | str]:
    return [img_data, "\n保存成功\n", _build_basic_usage_hint_text()]


def _has_style_override_parts(parts: list[Any]) -> bool:
    """
    `入典` 是固定 classic 预设；这里显式拦截样式参数，避免 strict=False
    把 `-s/--style` 误吞成普通 tag，导致预设命令语义漂移。
    """
    for part in parts:
        text = part.text if isinstance(part, Text) else part if isinstance(part, str) else None
        if text is None:
            continue
        normalized = text.strip()
        if normalized in {"-s", "--style"}:
            return True
        if normalized.startswith("-s=") or normalized.startswith("--style="):
            return True
    return False


async def _match_upload_with_image(event: MessageEvent) -> bool:
    if _message_has_image_segment(event.message):
        return True

    # 这里故意只看事件里已经携带的 reply.message，避免为了“静默让路”
    # 额外触发 reply_fetch/get_msg 这类重解析或网络调用。
    reply = getattr(event, "reply", None)
    return reply is not None and _message_has_image_segment(getattr(reply, "message", None))


def upload_has_image_rule(*, exclude_new_command: bool = False) -> Rule:
    async def _rule(event: MessageEvent) -> bool:
        # 旧“上传”必须显式让出“上传语录”，否则 compact 前缀匹配会把新命令吞掉。
        if exclude_new_command and _is_new_upload_command_text(event.get_plaintext()):
            return False
        return await _match_upload_with_image(event)

    return Rule(_rule)


async def _read_local_upload_image(
    image_path: str | os.PathLike[str], max_bytes: int | None
) -> bytes:
    """读取本地上传图片，并在加载进内存前执行字节大小限制。"""
    path = os.fspath(image_path)
    file_size = os.path.getsize(path)
    if max_bytes is not None and file_size > max_bytes:
        raise ImageProcessError(f"图片大小超过限制（最大 {max_bytes} 字节）")
    async with aiofiles.open(path, "rb") as file:
        return await file.read()


def _validate_raw_upload_size(
    img_data: bytes,
    *,
    max_bytes: int | None,
    max_size_mb: int,
) -> None:
    """在处理 raw 图片前校验其字节大小。"""
    if max_bytes is not None and len(img_data) > max_bytes:
        raise ImageProcessError(f"图片大小超过限制（最大 {max_size_mb} MB）")


async def _write_quote_image_if_absent(image_path: Path, img_data: bytes) -> bool:
    """排他创建语录图片，并返回当前操作是否拥有该文件。"""
    try:
        async with aiofiles.open(image_path, "xb") as file:
            await file.write(img_data)
        return True
    except FileExistsError:
        return False


async def _save_quote_image_and_record(
    image_path: Path,
    img_data: bytes,
    **quote_kwargs: Any,
) -> tuple[Quote | None, bool]:
    """在同一路径锁内完成文件创建、数据库写入和失败清理。"""
    async with QuoteService.quote_save_lock(
        str(quote_kwargs.get("group_id", "")),
        cast(str | None, quote_kwargs.get("image_hash")),
        image_path,
    ):
        image_created = await _write_quote_image_if_absent(image_path, img_data)
        try:
            quote, is_new = await QuoteService.add_quote(
                image_path=str(image_path),
                **quote_kwargs,
            )
        except Exception:
            # 仅清理本操作排他创建的文件，已有文件始终留给原引用方。
            if image_created:
                await QuoteService.delete_image_if_unreferenced(image_path)
            raise

        # 返回已有记录时，当前 MD5 路径可能与感知哈希命中的记录不同。
        if image_created and (quote is None or not is_new):
            await QuoteService.delete_image_if_unreferenced(image_path)
        return quote, is_new


async def _match_record_reply(event: MessageEvent) -> bool:
    return getattr(event, "reply", None) is not None


def record_reply_rule(*, exclude_new_command: bool = False) -> Rule:
    async def _rule(event: MessageEvent) -> bool:
        # “记录语录”是新的正式入口；旧“记录”作为别名时必须先避开它，
        # 否则 “记录语录 xxx” 会被旧命令误拆成 tag。
        if exclude_new_command and _is_new_record_command_text(event.get_plaintext()):
            return False
        return await _match_record_reply(event)

    return Rule(_rule)


upload_quote_alc = Alconna(
    "上传语录",
    Args["parts?", MultiVar(At | Text | UniImage)],
    meta=CommandMeta(strict=False, compact=True),
)
save_img_cmd = on_alconna(
    upload_quote_alc,
    auto_send_output=False,
    block=True,
    rule=upload_has_image_rule(),
)
legacy_upload_alc = Alconna(
    "上传",
    Args["parts?", MultiVar(At | Text | UniImage)],
    meta=CommandMeta(strict=False, compact=True),
)
upload_hint_cmd = on_alconna(
    legacy_upload_alc,
    auto_send_output=False,
    block=True,
    rule=upload_has_image_rule(exclude_new_command=True),
)
make_record_alc = Alconna(
    "记录语录",
    Option("-s|--style", Args["style_name", str], help_text="指定主题样式"),
    Option("-n|--num", Args["count", int, 1], help_text="记录连续消息的数量"),
    Option("-o|--only|--仅作者", help_text="仅记录/生成被回复用户的连续消息"),
    Args["parts?", MultiVar(At | Text)],
    meta=CommandMeta(strict=False, compact=True),
)
make_record_cmd = on_alconna(make_record_alc, block=True, rule=record_reply_rule())
legacy_record_alc = Alconna(
    "记录",
    Option("-s|--style", Args["style_name", str], help_text="指定主题样式"),
    Option("-n|--num", Args["count", int, 1], help_text="记录连续消息的数量"),
    Option("-o|--only|--仅作者", help_text="仅记录/生成被回复用户的连续消息"),
    Args["parts?", MultiVar(At | Text)],
    meta=CommandMeta(strict=False, compact=True),
)
legacy_record_cmd = on_alconna(
    legacy_record_alc,
    block=True,
    rule=record_reply_rule(exclude_new_command=True),
)
idiom_record_alc = Alconna(
    "入典",
    Option("-n|--num", Args["count", int, 1], help_text="记录连续消息的数量"),
    Option("-o|--only|--仅作者", help_text="仅记录/生成被回复用户的连续消息"),
    Args["parts?", MultiVar(At | Text)],
    meta=CommandMeta(strict=False, compact=True),
)
idiom_record_cmd = on_alconna(
    idiom_record_alc,
    block=True,
    rule=record_reply_rule(),
)

generate_quote_alc = Alconna(
    "生成",
    Args(),
    Option("-s|--style", Args["style_name", str], help_text="指定主题样式"),
    Option("-n|--num", Args["count", int, 1], help_text="生成连续消息的数量"),
    Option("-o|--only|--仅作者", help_text="仅生成被回复用户的连续消息"),
)
generate_quote_cmd = on_alconna(generate_quote_alc, block=True)


async def _set_pending_emoji_like(bot: Bot, event: MessageEvent) -> None:
    """
    为上传中的消息添加轻量反馈。

    这是 NapCat 的扩展能力，其他协议端可能不支持，所以这里只做 best-effort，
    失败时仅记录调试日志，不能影响后续 OCR/AI 与保存流程。
    """
    emoji_id = str(
        Config.get_config("quote", "QUOTE_UPLOAD_PENDING_EMOJI_ID", "10024") or ""
    ).strip()
    if not emoji_id:
        return

    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=int(event.message_id),
            emoji_id=emoji_id,
        )
    except Exception as e:
        logger.debug(f"设置上传处理中表情失败，可能协议端不支持: {e}", "群聊语录")


@upload_hint_cmd.handle()
async def upload_hint_handle(bot: Bot, event: MessageEvent):
    """旧上传命令只负责提示迁移，不再触发 OCR 或保存。"""
    session_id = event.get_session_id()
    if "group" not in session_id:
        await upload_hint_cmd.finish("上传功能目前仅支持群聊。")
        return

    group_id = session_id.split("_")[1]
    await bot.call_api(
        "send_group_msg",
        **{
            "group_id": int(group_id),
            "message": MessageSegment.reply(event.message_id)
            + _build_upload_migration_hint_text(),
        },
    )


@save_img_cmd.handle()
async def save_img_handle(bot: Bot, event: MessageEvent, arp: Arparma, state: T_State):
    """上传语录处理函数"""
    session_id = event.get_session_id()
    message_id = event.message_id
    user_id = str(event.get_user_id())
    upload_parts = collect_tag_parts(arp)
    group_id = session_id.split("_")[1] if "group" in session_id else None
    manual_tags = await extract_manual_tags_with_mention_names(bot, group_id, arp)
    target_image: UniImage | None = _extract_target_image_from_parts(upload_parts)

    # 1. 尝试从回复中获取图片
    if not target_image:
        if reply := await reply_fetch(event, bot):
            if reply.msg:
                reply_uni = await UniMessage.generate(message=reply.msg, bot=bot)
                for seg in reply_uni:
                    if isinstance(seg, UniImage):
                        target_image = seg
                        break

    # 2. 尝试从当前消息中获取 (处理 Alconna 可能未捕获的情况)
    if not target_image:
        current_uni = await UniMessage.generate(event=event, bot=bot)
        for seg in current_uni:
            if isinstance(seg, UniImage):
                target_image = seg
                break

    if not target_image:
        await save_img_cmd.finish(
            "请直接发送「上传语录+图片」或回复图片消息来上传语录"
        )

    # 3. 统一提取图片二进制数据，并在进入内存前限制外部资源大小。
    max_size_mb = int(Config.get_config("quote", "QUOTE_MAX_IMAGE_SIZE_MB", 15) or 0)
    max_bytes = max_size_mb * 1024 * 1024 if max_size_mb > 0 else None
    quote_path = ensure_quote_path()
    temp_image_path = quote_path / f"temp_{uuid.uuid4().hex}.png"
    img_data = b""
    try:
        if target_image.raw:
            _validate_raw_upload_size(
                target_image.raw,
                max_bytes=max_bytes,
                max_size_mb=max_size_mb,
            )
            img_data = target_image.raw
        elif target_image.path and os.path.exists(target_image.path):
            img_data = await _read_local_upload_image(target_image.path, max_bytes)
        elif target_image.url:
            downloaded = await AsyncHttpx.download_file(
                target_image.url,
                temp_image_path,
                stream=True,
                max_bytes=max_bytes,
            )
            if downloaded:
                img_data = await _read_local_upload_image(temp_image_path, max_bytes)
        elif target_image.id and hasattr(bot, "get_image"):
            # 针对 OneBot V11 本地路径/URL 的回退处理
            resp = await bot.get_image(file=target_image.id)
            if file_path := resp.get("file"):
                if os.path.exists(file_path):
                    img_data = await _read_local_upload_image(file_path, max_bytes)
            if not img_data and (url := resp.get("url")):
                downloaded = await AsyncHttpx.download_file(
                    url,
                    temp_image_path,
                    stream=True,
                    max_bytes=max_bytes,
                )
                if downloaded:
                    img_data = await _read_local_upload_image(
                        temp_image_path,
                        max_bytes,
                    )
    except ImageProcessError as e:
        temp_image_path.unlink(missing_ok=True)
        await save_img_cmd.finish(str(e))
    except Exception as e:
        logger.warning(f"获取图片数据失败: {e}", "群聊语录")

    if not img_data:
        temp_image_path.unlink(missing_ok=True)
        limit_hint = f"且不超过 {max_size_mb} MB" if max_bytes is not None else ""
        await save_img_cmd.finish(
            f"未能成功获取图片数据，请检查图片是否有效{limit_hint}。"
        )

    # 4. 创建临时文件以供后续 OCR 和哈希计算使用
    try:
        async with aiofiles.open(temp_image_path, "wb") as f:
            await f.write(img_data)
    except Exception as e:
        await save_img_cmd.finish(f"写入临时文件失败: {e}")

    if "group" in session_id:
        assert group_id is not None
        image_hash = await get_img_hash(temp_image_path)

        if (
            image_hash
            and await Quote.filter(group_id=group_id, image_hash=image_hash).exists()
        ):
            if temp_image_path.name.startswith("temp_") and temp_image_path.exists():
                try:
                    os.remove(temp_image_path)
                    logger.info(
                        f"删除临时文件 (发现重复): {temp_image_path}", "群聊语录"
                    )
                except Exception as e:
                    logger.error(f"删除临时文件失败: {e}", "群聊语录", e=e)
            await bot.call_api(
                "send_group_msg",
                **{
                    "group_id": int(group_id),
                    "message": MessageSegment.reply(message_id) + "不要重复记录",
                },
            )
            return

        await _set_pending_emoji_like(bot, event)
        ocr_content = await OCRService.recognize_text(str(temp_image_path))

        image_name = hashlib.md5(img_data).hexdigest() + ".png"
        final_image_path = get_quote_group_path(group_id) / image_name

        if temp_image_path != final_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
                logger.info(f"删除临时文件: {temp_image_path}", "群聊语录")
            except Exception as e:
                logger.error(f"删除临时文件失败: {e}", "群聊语录", e=e)

        quote, is_new = await _save_quote_image_and_record(
            final_image_path,
            img_data,
            group_id=group_id,
            ocr_content=ocr_content,
            recorded_text=None,
            uploader_user_id=user_id,
            image_hash=image_hash,
            manual_tags=manual_tags,
        )

        if quote:
            if is_new:
                await MessageUtils.build_message(
                    _build_upload_success_message(img_data)
                ).send(
                    target=event,
                    bot=bot,
                )
            else:
                await bot.call_api(
                    "send_group_msg",
                    **{
                        "group_id": int(group_id),
                        "message": MessageSegment.reply(message_id) + "不要重复记录",
                    },
                )
        else:
            await bot.call_api(
                "send_group_msg",
                **{
                    "group_id": int(group_id),
                    "message": (
                        MessageSegment.reply(message_id) + "保存失败，可能是数据库错误"
                    ),
                },
            )
    else:
        logger.info(
            f"上传指令在非群聊环境 ({session_id}) 中被调用，未处理。", "群聊语录"
        )
        await save_img_cmd.send("上传功能目前仅支持群聊。")


async def _handle_quote_generation(
    bot: Bot,
    event: MessageEvent,
    arp: Arparma,
    session: Uninfo,
    issuer_user_id: str | None = None,
    forced_variant: str | None = None,
) -> tuple[bytes | None, str | None, str | None, str | None]:
    """
    统一处理'记录'和'生成'命令的核心逻辑。

    返回:
        元组 (img_data, recorded_text, quoted_user_id, error_msg)
    """
    # `入典` 这类预设命令必须绑定具体主题名，而不是继续依赖 `-s 1`
    # 之类会随主题排序变化的外部语义。
    user_variant: str | None = forced_variant or arp.query("style.style_name")
    count: int = arp.query("num.count", 1) if not user_variant == "classic" else 1
    is_only_author = arp.find("only")

    if count > MAX_RECORD_COUNT:
        return None, None, None, f"一次最多只能处理 {MAX_RECORD_COUNT} 条消息哦。"

    if count == 1:
        info, error = await _extract_info_from_reply(event, bot)
        if error:
            return None, None, None, error

        assert info is not None
        uni_message, card, qqid = info

        allow_bot_record = Config.get_config("quote", "QUOTE_ALLOW_BOT_RECORD", False)
        if not allow_bot_record and str(qqid) == str(event.self_id):
            return None, None, None, "不允许记录Bot的消息。"

        is_superuser = await SUPERUSER(bot, event)
        allow_self_record = Config.get_config("quote", "QUOTE_ALLOW_SELF_RECORD", False)
        if (
            not is_superuser
            and issuer_user_id
            and not allow_self_record
            and str(qqid) == issuer_user_id
        ):
            return None, None, None, "不允许记录自己的消息。"

        replied_msg_id = cast(int, event.reply.message_id)
        full_replied_msg_info = await bot.get_msg(message_id=replied_msg_id)
        message_array = full_replied_msg_info.get("message", [])
        quoted_reply_data = await _process_nested_reply(message_array, bot)
        has_nested_reply = quoted_reply_data is not None

        if user_variant and user_variant.isdigit():
            try:
                theme_index = int(user_variant)
                available_themes = get_available_themes()
                if 1 <= theme_index <= len(available_themes):
                    user_variant = available_themes[theme_index - 1]
                else:
                    return (
                        None,
                        None,
                        None,
                        f"无效的主题序号 '{theme_index}'。请从 1 到 {len(available_themes)} 中选择。",
                    )
            except (ValueError, IndexError):
                pass

        if user_variant:
            final_variant = user_variant
        else:
            final_variant = None
            is_simple_text = _is_simple_text_message(uni_message)

            if is_simple_text and not has_nested_reply:
                text_only_theme = Config.get_config(
                    "quote", "QUOTE_TEXT_ONLY_THEME", ""
                )
                if text_only_theme:
                    final_variant = text_only_theme

            if not final_variant:
                final_variant = Config.get_config("quote", "THEME", "qq-native")

        if final_variant == "classic":
            is_pure_image = not any(
                isinstance(seg, Text) and seg.text.strip() for seg in uni_message
            ) and any(isinstance(seg, UniImage) for seg in uni_message)
            if is_pure_image:
                return None, None, None, "不支持使用 classic 主题记录纯图片消息。"

        recorded_text_content = uni_message.extract_plain_text()
        recorded_text = f"{card} {recorded_text_content}"

        result, error_render = await _generate_quote_from_reply(
            event, bot, uni_message, final_variant
        )
        if error_render:
            return None, None, None, error_render

        assert result is not None
        img_data, _, _, _ = result

        if quoted_reply_data:
            quoted_text_parts = []
            for seg in quoted_reply_data.text:
                if seg.get("type") == "text":
                    quoted_text_parts.append(seg.get("value", ""))
            quoted_text_plain = "".join(quoted_text_parts)
            if quoted_text_plain:
                reply_prefix = (
                    f"「回复 {quoted_reply_data.author}: {quoted_text_plain}」\n"
                )
                recorded_text = f"{reply_prefix}{recorded_text}"

        return img_data, recorded_text, str(qqid), None

    else:
        if not session.group:
            return None, None, None, "连续消息处理功能仅限群聊使用。"

        reply = await reply_fetch(event, bot)
        if not reply or not reply.msg:
            return None, None, None, "请回复需要作为结尾的那条消息。"

        if user_variant == "classic":
            return None, None, None, "不支持使用 classic 主题处理连续消息。"

        group_id = session.group.id
        start_msg_id = int(reply.id)
        target_author_id = None
        message_history = []
        try:
            replied_msg_info = await bot.get_msg(message_id=start_msg_id)
            anchor_seq = replied_msg_info.get("message_seq")
            if not anchor_seq:
                return None, None, None, "获取被回复消息的序列号失败，无法处理。"

            fetch_count = count
            if is_only_author:
                target_author_id = str(replied_msg_info["sender"]["user_id"])
                fetch_count = min(max(count * 8, 20), 100)

            history_result = await bot.call_api(
                "get_group_msg_history",
                **{
                    "group_id": int(group_id),
                    "message_seq": anchor_seq,
                    "count": fetch_count,
                    "reverseOrder": True,
                },
            )
            raw_messages = history_result.get("messages", [])
            logger.debug(f"raw_messages: {raw_messages}")

            allow_bot_record = Config.get_config(
                "quote", "QUOTE_ALLOW_BOT_RECORD", False
            )
            valid_messages = [
                msg
                for msg in raw_messages
                if (
                    allow_bot_record
                    or str(msg["sender"]["user_id"]) != str(event.self_id)
                )
                and _is_message_renderable(msg)
            ]

            if is_only_author and target_author_id:
                valid_messages = [
                    msg
                    for msg in valid_messages
                    if str(msg["sender"]["user_id"]) == target_author_id
                ][-count:]

            valid_messages.sort(key=lambda m: m.get("time", 0))
            message_history = valid_messages
        except Exception as e:
            return None, None, None, f"获取历史消息时出错: {e}"

        if not message_history:
            return None, None, None, "未能获取到任何有效的历史消息。"

        last_message_user_id = str(message_history[-1]["sender"]["user_id"])
        is_superuser = await SUPERUSER(bot, event)
        allow_self_record = Config.get_config("quote", "QUOTE_ALLOW_SELF_RECORD", False)
        if (
            not is_superuser
            and issuer_user_id
            and not allow_self_record
            and last_message_user_id == issuer_user_id
        ):
            return None, None, None, "不允许记录自己的消息。"

        try:
            (
                img_data,
                recorded_text,
                quoted_user_id,
            ) = await _generate_sequence_from_history(
                bot, group_id, message_history, user_variant
            )
            return img_data, recorded_text, quoted_user_id, None
        except (NetworkError, ImageProcessError, FileNotFoundError) as e:
            return None, None, None, f"生成语录序列失败: {e}"


def _is_message_renderable(message_dict: dict) -> bool:
    """
    检查一条消息是否包含可渲染的内容，并排除不支持的类型。
    - 显式排除合并转发消息。
    - 只允许包含文本或图片的消息通过。
    """
    message_segments = message_dict.get("message", [])
    if not isinstance(message_segments, list):
        return isinstance(message_segments, str) and bool(message_segments.strip())

    if any(seg.get("type") == "forward" for seg in message_segments):
        return False

    return any(seg.get("type") in {"text", "image"} for seg in message_segments)


async def _handle_record_command(
    bot: Bot,
    event: MessageEvent,
    arp: Arparma,
    session: Uninfo,
    forced_variant: str | None = None,
):
    """记录语录处理函数 (重构后)"""
    user_id = str(event.get_user_id())
    manual_tags = await extract_manual_tags_with_mention_names(
        bot, session.group.id if session.group else None, arp
    )

    generation_kwargs = {"issuer_user_id": user_id}
    if forced_variant is not None:
        generation_kwargs["forced_variant"] = forced_variant

    img_data, recorded_text, quoted_user_id, error = await _handle_quote_generation(
        bot,
        event,
        arp,
        session,
        **generation_kwargs,
    )

    if error:
        await MessageUtils.build_message(error).send(target=event, bot=bot)
        return

    assert img_data is not None
    assert recorded_text is not None

    image_hash = hashlib.md5(img_data).hexdigest()
    group_id = session.group.id if session.group else ""

    if await Quote.filter(group_id=group_id, image_hash=image_hash).exists():
        await MessageUtils.build_message("不要重复记录").send(target=event, bot=bot)
        return

    image_name = image_hash + ".png"
    image_path = get_quote_group_path(group_id) / image_name

    try:
        quote, is_new = await _save_quote_image_and_record(
            image_path,
            img_data,
            group_id=group_id,
            ocr_content=None,
            recorded_text=recorded_text,
            uploader_user_id=user_id,
            quoted_user_id=quoted_user_id,
            image_hash=image_hash,
            manual_tags=manual_tags,
        )

        if quote and is_new:
            await MessageUtils.build_message(
                _build_record_success_message(img_data)
            ).send(target=event, bot=bot)
        else:
            msg = "不要重复记录" if not is_new else "保存语录时发生意外，请稍后再试"
            await MessageUtils.build_message(msg).send(target=event, bot=bot)
    except Exception as e:
        logger.error(f"记录语录过程中发生IO或数据库错误: {e}", "群聊语录", e=e)
        await MessageUtils.build_message("保存语录时发生意外，请稍后再试").send(
            target=event, bot=bot
        )


@make_record_cmd.handle()
async def make_record_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    await _handle_record_command(bot, event, arp, session)


@legacy_record_cmd.handle()
async def legacy_record_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    await _handle_record_command(bot, event, arp, session)


@idiom_record_cmd.handle()
async def idiom_record_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    if _has_style_override_parts(collect_tag_parts(arp)):
        await MessageUtils.build_message(
            "入典 为固定 classic 预设，不支持 -s/--style。"
        ).send(target=event, bot=bot)
        return

    await _handle_record_command(
        bot, event, arp, session, forced_variant="classic"
    )


@generate_quote_cmd.handle()
async def generate_quote_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    """生成语录处理函数 (重构后支持多条消息)"""
    img_data, _, _, error = await _handle_quote_generation(bot, event, arp, session)

    if error:
        await generate_quote_cmd.finish(error)
        return

    if img_data:
        await MessageUtils.build_message(img_data).send(target=event, bot=bot)
    else:
        await generate_quote_cmd.finish("生成语录图片失败。")
