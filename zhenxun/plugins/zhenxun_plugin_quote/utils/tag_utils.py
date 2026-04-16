import re
from typing import Any

from arclet.alconna import Arparma
from nonebot_plugin_alconna import At, Text

from zhenxun.services.log import logger

from ..services.quote_service import QuoteService

_WHITESPACE_RE = re.compile(r"\s+")


def _extend_parts(collected_parts: list[Any], raw_parts: Any) -> None:
    if raw_parts is None:
        return
    if isinstance(raw_parts, (list, tuple)):
        collected_parts.extend(raw_parts)
        return
    collected_parts.append(raw_parts)


def collect_tag_parts(arp: Arparma, arg_name: str = "parts") -> list[Any]:
    collected_parts: list[Any] = []

    _extend_parts(collected_parts, arp.all_matched_args.get(arg_name))
    extra_args = arp.main_args.get("$extra", [])
    _extend_parts(collected_parts, extra_args)

    return collected_parts


def extract_manual_tags(arp: Arparma, arg_name: str = "parts") -> list[str]:
    return QuoteService.parse_tag_segments(collect_tag_parts(arp, arg_name))


def _normalize_mention_name(raw_name: str | None) -> str | None:
    if raw_name is None:
        return None

    # 群名片里的空白要压成单个下划线，保证昵称 tag 始终是一个完整 token，
    # 否则后续按空白切分时会把一个昵称错误拆成多个 tag。
    normalized = _WHITESPACE_RE.sub("_", str(raw_name).strip())
    return normalized or None


async def _resolve_mention_name(bot: Any, group_id: str | None, part: Any) -> str | None:
    display_name = _normalize_mention_name(getattr(part, "display", None))
    target = getattr(part, "target", None)
    if not group_id or not target:
        return display_name

    try:
        member_info = await bot.get_group_member_info(
            group_id=int(group_id), user_id=int(str(target))
        )
    except Exception as e:
        logger.debug(f"获取 @目标群名片失败，将回退 display: {e}", "群聊语录")
        return display_name

    if not isinstance(member_info, dict):
        return display_name

    return _normalize_mention_name(
        member_info.get("card") or member_info.get("nickname") or display_name
    )


async def parse_tag_segments_with_mention_names(
    bot: Any, group_id: str | None, parts: list[Any]
) -> list[str]:
    raw_tags: list[str] = []

    for part in parts:
        if (
            isinstance(part, At) or part.__class__.__name__ == "At"
        ) and getattr(part, "target", None):
            raw_tags.append(QuoteService.serialize_user_tag(str(part.target)))
            if mention_name := await _resolve_mention_name(bot, group_id, part):
                raw_tags.append(mention_name)
        elif isinstance(part, Text) or part.__class__.__name__ == "Text":
            raw_tags.extend(QuoteService.parse_tag_text(part.text))
        elif isinstance(part, str):
            raw_tags.extend(QuoteService.parse_tag_text(part))

    return QuoteService.normalize_tags(raw_tags)


async def extract_manual_tags_with_mention_names(
    bot: Any, group_id: str | None, arp: Arparma, arg_name: str = "parts"
) -> list[str]:
    return await parse_tag_segments_with_mention_names(
        bot, group_id, collect_tag_parts(arp, arg_name)
    )
