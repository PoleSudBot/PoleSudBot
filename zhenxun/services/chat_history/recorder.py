from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Literal

from nonebot.adapters import Bot, Message
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import SerializeFailed
from tortoise import timezone
from tortoise.functions import Count

from zhenxun.models.chat_history import ChatHistory
from zhenxun.services.log import logger
from zhenxun.utils.platform import PlatformUtils

Direction = Literal["in", "out"]
QueryDirection = Direction | Literal["all"]

DEFAULT_QUERY_LIMIT = 100
MAX_QUERY_LIMIT = 10000
SEGMENT_SCAN_PAGE_SIZE = 500
SEGMENT_SCAN_MAX_ROWS = 50000
_MAX_DATA_VALUE_LENGTH = 512
_MEDIA_KEYS = {
    "file",
    "url",
    "summary",
    "file_size",
    "file_unique",
    "name",
    "id",
}
_SEGMENT_TYPE_ALIASES = {
    "at": "mention",
    "record": "audio",
    "face": "emoji",
    "forward": "reference",
    "node": "reference",
    "json": "card",
    "xml": "card",
    "share": "card",
    "hyper": "card",
}
_MEDIA_SEGMENTS = {"image", "audio", "video", "file"}
_PLACEHOLDER_MAP = {
    "mention": "@{target}",
    "emoji": "[表情:{id}]",
    "image": "[图片]",
    "audio": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "card": "[卡片]",
    "reference": "[合并转发]",
    "reply": "[引用消息]",
}


def _to_str(value: Any) -> str | None:
    """把平台字段统一为字符串，避免不同适配器返回数字造成查询条件不稳定。"""
    if value is None:
        return None
    return str(value)


def _truncate(value: Any, limit: int = _MAX_DATA_VALUE_LENGTH) -> Any:
    """限制入库字段长度，防止媒体URL或异常payload撑大历史表。"""
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    suffix = "...[truncated]"
    return f"{value[: max(0, limit - len(suffix))]}{suffix}"


def _segment_type(segment: Any) -> str | None:
    """从字符串、dict或MessageSegment中提取段类型。"""
    if isinstance(segment, str):
        return "text"
    if isinstance(segment, dict):
        return _to_str(segment.get("type"))
    return _to_str(getattr(segment, "type", None))


def _canonical_segment_type(seg_type: str) -> str:
    """将适配器或Alconna段类型映射为ChatHistory稳定协议。"""
    return _SEGMENT_TYPE_ALIASES.get(seg_type, seg_type)


def _segment_data(segment: Any) -> dict[str, Any]:
    """从不同消息段结构中提取data字典。"""
    if isinstance(segment, str):
        return {"text": segment}
    if isinstance(segment, dict):
        data = segment.get("data") or {}
    else:
        data = getattr(segment, "data", None) or {}
    return dict(data) if isinstance(data, dict) else {}


def _safe_segment_data(
    seg_type: str,
    data: dict[str, Any],
    *,
    raw_type: str,
) -> dict[str, Any]:
    """按段类型保留可查询元数据，媒体正文和未知大字段不入库。"""
    if seg_type == "text":
        return {"text": _truncate(data.get("text", ""), 4096)}
    if seg_type == "mention":
        return {
            "target": _to_str(data.get("target") or data.get("qq")),
            "kind": _to_str(data.get("flag") or "user"),
        }
    if seg_type == "reply":
        return {"message_id": _to_str(data.get("message_id") or data.get("id"))}
    if seg_type == "reference":
        return {
            key: value
            for key, value in {
                "id": _to_str(data.get("id")),
                "name": _truncate(data.get("name")),
            }.items()
            if value is not None
        }
    if seg_type == "emoji":
        return {
            key: _truncate(data.get(key))
            for key in ("id", "name", "url")
            if data.get(key) is not None
        }
    if seg_type in _MEDIA_SEGMENTS:
        media_data: dict[str, Any] = {}
        for key, value in data.items():
            if key not in _MEDIA_KEYS or value is None:
                continue
            if isinstance(value, str) and value.startswith("base64://"):
                continue
            media_data[key] = _truncate(value)
        return media_data
    if seg_type == "card":
        return {"format": _to_str(data.get("format") or raw_type)}
    # 未知段只保留类型与简单小字段，避免平台扩展段导致整条消息丢失。
    return {
        "raw_type": raw_type,
        **{
            key: _truncate(value)
            for key, value in data.items()
            if (isinstance(value, str | int | float | bool) or value is None)
            and not (isinstance(value, str) and value.startswith("base64://"))
        },
    }


def _segment_readable_text(seg_type: str, data: dict[str, Any]) -> str:
    """把消息段转换为稳定可读文本，供总结和LLM上下文消费。"""
    if seg_type == "text":
        return str(data.get("text") or "")
    if seg_type == "mention":
        target = data.get("target")
        return f"@{target}" if target is not None else "@"
    template = _PLACEHOLDER_MAP.get(seg_type)
    if not template:
        return f"[{seg_type}]"
    try:
        return template.format(**data)
    except KeyError:
        return template.split(":", 1)[0].rstrip("{") + "]"


def _iter_message_segments(message: Any) -> Iterable[Any]:
    """兼容字符串、Message、UniMsg和普通list形式的消息。"""
    if isinstance(message, str):
        return [message]
    if isinstance(message, Sequence):
        return message
    try:
        return list(message)
    except TypeError:
        return []


def normalize_message_segments(
    message: Any,
) -> tuple[list[dict[str, Any]], list[str], str]:
    """规范化消息段，并返回轻量segments、段类型和可读文本。"""
    normalized: list[dict[str, Any]] = []
    readable_parts: list[str] = []
    for segment in _iter_message_segments(message):
        raw_type = _segment_type(segment)
        if not raw_type:
            continue
        seg_type = _canonical_segment_type(raw_type)
        if seg_type not in _PLACEHOLDER_MAP and seg_type != "text":
            seg_type = "unknown"
        data = _safe_segment_data(
            seg_type,
            _segment_data(segment),
            raw_type=raw_type,
        )
        normalized.append({"type": seg_type, "data": data})
        readable_parts.append(_segment_readable_text(seg_type, data))
    segment_types = list(dict.fromkeys(seg["type"] for seg in normalized))
    return normalized, segment_types, "".join(readable_parts)


def _segments_to_plain_text(segments: list[dict[str, Any]]) -> str:
    """只拼接文本段，避免媒体占位符污染词云和关键词统计。"""
    return "".join(
        str((segment.get("data") or {}).get("text") or "")
        for segment in segments
        if segment.get("type") == "text"
    )


def _extract_plain_text(message: Any, segments: list[dict[str, Any]]) -> str:
    """优先使用适配器纯文本提取；无该能力时只从文本段兜底。"""
    if hasattr(message, "extract_plain_text"):
        return str(message.extract_plain_text() or "")
    return _segments_to_plain_text(segments)


def segments_to_readable_text(segments: list[dict[str, Any]] | None) -> str:
    """将结构化消息段转换为面向总结和上下文的稳定可读文本。"""
    if not segments:
        return ""
    parts: list[str] = []
    for segment in segments:
        raw_type = _to_str(segment.get("type"))
        if not raw_type:
            continue
        seg_type = _canonical_segment_type(raw_type)
        data = segment.get("data") or {}
        segment_data = data if isinstance(data, dict) else {}
        if seg_type == "mention" and "target" not in segment_data:
            segment_data = {**segment_data, "target": segment_data.get("qq")}
        if seg_type == "reply" and "message_id" not in segment_data:
            segment_data = {**segment_data, "message_id": segment_data.get("id")}
        parts.append(_segment_readable_text(seg_type, segment_data))
    return "".join(parts)


def _extract_message_id_from_result(result: Any) -> str | None:
    """读取发送API返回的message_id。"""
    if isinstance(result, dict):
        return _to_str(result.get("message_id"))
    return _to_str(getattr(result, "message_id", None))


def _extract_incoming_message_id(event: Any) -> str | None:
    """读取入站事件的message_id。"""
    message_id = getattr(event, "message_id", None)
    if message_id is None and hasattr(event, "get_message_id"):
        message_id = event.get_message_id()
    return _to_str(message_id)


def _normalize_message_type(event: Any, session: Any) -> str | None:
    """将适配器事件或Uninfo场景收敛为稳定的会话类型。"""
    raw_type = _to_str(getattr(event, "message_type", None))
    if raw_type:
        lowered = raw_type.lower()
        if "private" in lowered:
            return "private"
        if "group" in lowered:
            return "group"
        if "channel" in lowered or "guild" in lowered:
            return "channel"
    scene_type = getattr(getattr(session, "scene", None), "type", None)
    scene_name = _to_str(getattr(scene_type, "name", None) or scene_type)
    if not scene_name:
        return "group" if getattr(session, "group", None) is not None else "private"
    lowered = scene_name.lower()
    if "channel" in lowered or "guild" in lowered:
        return "channel"
    if "group" in lowered:
        return "group"
    if "private" in lowered or "friend" in lowered:
        return "private"
    return None


def _extract_create_time(event: Any) -> datetime:
    """优先读取平台事件时间；缺失或异常时使用当前时间兜底。"""
    raw_time = getattr(event, "time", None)
    if raw_time is None:
        return timezone.now()
    try:
        return datetime.fromtimestamp(int(raw_time), timezone.get_default_timezone())
    except (TypeError, ValueError, OSError):
        return timezone.now()


def _extract_reply_to_message_id(
    segments: list[dict[str, Any]],
    event: Any | None,
) -> str | None:
    """按消息段和事件兜底顺序提取被回复消息ID。"""
    for segment in segments:
        if segment.get("type") == "reply":
            data = segment.get("data") or {}
            reply_id = data.get("message_id") or data.get("id")
            if reply_id is not None:
                return _to_str(reply_id)
    for source in (
        getattr(event, "reply", None),
        getattr(event, "source", None),
    ):
        if source is None:
            continue
        for attr in ("message_id", "id"):
            reply_id = getattr(source, attr, None)
            if reply_id is not None:
                return _to_str(reply_id)
        if isinstance(source, dict):
            reply_id = source.get("message_id") or source.get("id")
            if reply_id is not None:
                return _to_str(reply_id)
    return None


def build_incoming_record(
    message: Any,
    session: Any,
    event: Any | None = None,
) -> ChatHistory:
    """构造入站聊天记录，数据库写入仍交给原队列批量处理。"""
    from zhenxun.utils.utils import get_entity_ids

    entity = get_entity_ids(session)
    segments, segment_types, readable_text = normalize_message_segments(message)
    plain_text = _extract_plain_text(message, segments)
    reply_to_message_id = _extract_reply_to_message_id(segments, event)
    return ChatHistory(
        user_id=entity.user_id,
        group_id=entity.group_id,
        text=readable_text,
        plain_text=plain_text,
        bot_id=session.self_id,
        platform=PlatformUtils.get_platform(session),
        direction="in",
        message_id=_extract_incoming_message_id(event),
        message_type=_normalize_message_type(event, session),
        create_time=_extract_create_time(event),
        segments=segments,
        segment_types=segment_types,
        reply_to_message_id=reply_to_message_id,
    )


def build_outgoing_record(
    bot: Bot,
    *,
    user_id: str | None,
    group_id: str | None,
    message_type: str | None,
    message: Message | str,
    result: Any,
) -> ChatHistory:
    """构造Bot出站聊天记录，沿用发送hook解析出的目标信息。"""
    normalized_message: Any = message
    if isinstance(message, Message):
        try:
            normalized_message = UniMessage.of(message, bot=bot)
        except (SerializeFailed, TypeError, ValueError, NotImplementedError):
            # 适配器转换失败时仍记录原消息，不能让历史功能影响发送链路。
            normalized_message = message
    segments, segment_types, readable_text = normalize_message_segments(
        normalized_message
    )
    plain_text = _extract_plain_text(message, segments)
    reply_to_message_id = _extract_reply_to_message_id(segments, None)
    return ChatHistory(
        user_id=user_id or str(bot.self_id),
        group_id=group_id,
        text=readable_text,
        plain_text=plain_text,
        bot_id=str(bot.self_id),
        platform=PlatformUtils.get_platform(bot),
        direction="out",
        message_id=_extract_message_id_from_result(result),
        message_type=message_type,
        create_time=timezone.now(),
        segments=segments,
        segment_types=segment_types,
        reply_to_message_id=reply_to_message_id,
    )


async def create_outgoing_record(
    bot: Bot,
    *,
    user_id: str | None,
    group_id: str | None,
    message_type: str | None,
    message: Message | str,
    result: Any,
) -> ChatHistory:
    """立即写入Bot出站上下文账本；发送审计仍由BotMessageStore负责。"""
    record = build_outgoing_record(
        bot,
        user_id=user_id,
        group_id=group_id,
        message_type=message_type,
        message=message,
        result=result,
    )
    await record.save()
    return record


class ChatHistoryQuery:
    """ChatHistory查询服务，给后续插件迁移提供稳定入口。"""

    @staticmethod
    def _normalize_limit(limit: int | None, default: int = DEFAULT_QUERY_LIMIT) -> int:
        """限制单次查询返回量，插件可在调用前实现更细的业务限流。"""
        if limit is None:
            return default
        return max(1, min(int(limit), MAX_QUERY_LIMIT))

    @classmethod
    def _base_query(
        cls,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
    ):
        query = ChatHistory.all()
        if group_id is not None:
            query = query.filter(group_id=group_id)
        if user_id is not None:
            query = query.filter(user_id=user_id)
        if bot_id is not None:
            query = query.filter(bot_id=bot_id)
        if platform is not None:
            query = query.filter(platform=platform)
        if message_type is not None:
            query = query.filter(message_type=message_type)
        if direction != "all":
            query = query.filter(direction=direction)
        return query

    @classmethod
    def _apply_time_range(
        cls,
        query,
        *,
        start: datetime,
        end: datetime,
    ):
        """统一按消息发生时间过滤。"""
        return query.filter(create_time__range=(start, end))

    @classmethod
    async def recent(
        cls,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        limit: int = 100,
    ) -> list[ChatHistory]:
        """按消息发生时间倒序读取最近消息。"""
        limit = cls._normalize_limit(limit)
        return await (
            cls._base_query(
                group_id=group_id,
                user_id=user_id,
                bot_id=bot_id,
                platform=platform,
                message_type=message_type,
                direction=direction,
            )
            .order_by("-create_time", "-id")
            .limit(limit)
        )

    @classmethod
    async def time_range(
        cls,
        *,
        start: datetime,
        end: datetime,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        limit: int | None = None,
    ) -> list[ChatHistory]:
        """按消息发生时间范围顺序读取消息。"""
        query = cls._apply_time_range(
            cls._base_query(
                group_id=group_id,
                user_id=user_id,
                bot_id=bot_id,
                platform=platform,
                message_type=message_type,
                direction=direction,
            ),
            start=start,
            end=end,
        )
        query = query.order_by("create_time", "id")
        query = query.limit(
            cls._normalize_limit(limit, default=MAX_QUERY_LIMIT)
        )
        return await query

    @classmethod
    async def text_range(
        cls,
        *,
        start: datetime,
        end: datetime,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """读取面向总结和导出的轻量文本字段，不加载结构化消息JSON。"""
        query = cls._apply_time_range(
            cls._base_query(
                group_id=group_id,
                user_id=user_id,
                bot_id=bot_id,
                platform=platform,
                message_type=message_type,
                direction=direction,
            ),
            start=start,
            end=end,
        )
        return await (
            query.order_by("create_time", "id")
            .limit(cls._normalize_limit(limit, default=1000))
            .values(
                "id",
                "user_id",
                "create_time",
                "text",
                "message_id",
                "reply_to_message_id",
            )
        )

    @classmethod
    async def structured_range(
        cls,
        *,
        start: datetime,
        end: datetime,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """读取需要媒体、艾特和引用详情的结构化消息字段。"""
        query = cls._apply_time_range(
            cls._base_query(
                group_id=group_id,
                user_id=user_id,
                bot_id=bot_id,
                platform=platform,
                message_type=message_type,
                direction=direction,
            ),
            start=start,
            end=end,
        )
        return await (
            query.order_by("create_time", "id")
            .limit(cls._normalize_limit(limit, default=1000))
            .values(
                "id",
                "user_id",
                "create_time",
                "text",
                "plain_text",
                "message_id",
                "reply_to_message_id",
                "segments",
                "segment_types",
            )
        )

    @classmethod
    async def count(
        cls,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        """统计消息数量，默认只统计入站用户消息。"""
        query = cls._base_query(
            group_id=group_id,
            user_id=user_id,
            bot_id=bot_id,
            platform=platform,
            message_type=message_type,
            direction=direction,
        )
        if start is not None and end is not None:
            query = cls._apply_time_range(
                query,
                start=start,
                end=end,
            )
        return await query.count()

    @classmethod
    async def first(
        cls,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
    ) -> ChatHistory | None:
        """读取最早一条消息，供入群首言等场景复用。"""
        return await (
            cls._base_query(
                group_id=group_id,
                user_id=user_id,
                bot_id=bot_id,
                platform=platform,
                message_type=message_type,
                direction=direction,
            )
            .order_by("create_time", "id")
            .first()
        )

    @classmethod
    async def rank(
        cls,
        *,
        group_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        direction: QueryDirection = "in",
        descending: bool = True,
        limit: int = 100,
    ) -> list[tuple[str, int]]:
        """按用户聚合消息排行，保持与旧排行方法一致的返回形状。"""
        query = cls._base_query(group_id=group_id, direction=direction)
        if start is not None and end is not None:
            query = cls._apply_time_range(
                query,
                start=start,
                end=end,
            )
        order_prefix = "-" if descending else ""
        return list(
            await query.annotate(count=Count("user_id"))
            .order_by(f"{order_prefix}count")
            .group_by("user_id")
            .limit(cls._normalize_limit(limit))
            .values_list("user_id", "count")
        )

    @classmethod
    async def by_message_id(
        cls,
        message_id: str,
        *,
        platform: str | None = None,
        bot_id: str | None = None,
    ) -> ChatHistory | None:
        """用平台消息id查找记录，平台和bot条件可避免跨账号碰撞。"""
        query = ChatHistory.filter(message_id=message_id)
        if platform is not None:
            query = query.filter(platform=platform)
        if bot_id is not None:
            query = query.filter(bot_id=bot_id)
        return await query.order_by("-create_time", "-id").first()

    @classmethod
    async def by_segment_type(
        cls,
        segment_type: str,
        *,
        group_id: str | None = None,
        direction: QueryDirection = "in",
        limit: int = 100,
    ) -> list[ChatHistory]:
        """分页扫描包含指定消息段类型的记录，避免稀疏媒体消息被近似查询漏掉。"""
        limit = cls._normalize_limit(limit)
        found: list[ChatHistory] = []
        scanned = 0
        last_create_time: datetime | None = None
        last_id: int | None = None
        while len(found) < limit and scanned < SEGMENT_SCAN_MAX_ROWS:
            query = cls._base_query(group_id=group_id, direction=direction)
            if last_create_time is not None and last_id is not None:
                query = query.filter(create_time__lte=last_create_time).exclude(
                    create_time=last_create_time,
                    id__gte=last_id,
                )
            page = await (
                query.order_by("-create_time", "-id")
                .limit(min(SEGMENT_SCAN_PAGE_SIZE, SEGMENT_SCAN_MAX_ROWS - scanned))
            )
            if not page:
                break
            scanned += len(page)
            for row in page:
                row_segment_types = row.segment_types or [
                    seg.get("type")
                    for seg in (row.segments or [])
                    if isinstance(seg, dict)
                ]
                if segment_type in row_segment_types:
                    found.append(row)
                    if len(found) >= limit:
                        break
            last = page[-1]
            last_create_time = last.create_time
            last_id = last.id
        logger.debug(
            (
                "chat_history segment scan "
                f"type={segment_type} scanned={scanned} returned={len(found)}"
            ),
            "chat_history",
        )
        return found
