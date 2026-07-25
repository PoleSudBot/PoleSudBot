from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from datetime import datetime
import json
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
_MESSAGE_ID_QUERY_CHUNK_SIZE = 500
_MAX_DATA_VALUE_LENGTH = 512
_MAX_FORWARD_NODES = 5
_MAX_FORWARD_NODE_SEGMENTS = 10
_MAX_FORWARD_NODE_TEXT_LENGTH = 512
_MEDIA_KEYS = {
    "file",
    "summary",
    "file_size",
    "file_unique",
    "name",
    "id",
}
_SEGMENT_TYPE_ALIASES = {
    "at": "mention",
    "record": "audio",
    "voice": "audio",
    "face": "emoji",
    "forward": "reference",
    "node": "reference",
    "json": "card",
    "xml": "card",
    "share": "card",
    "hyper": "card",
}
_MEDIA_SEGMENTS = {"image", "sticker", "audio", "video", "file"}
_MEDIA_COUNT_SEGMENTS = _MEDIA_SEGMENTS | {"emoji"}
_TEXT_PROJECTION_FIELDS = (
    "id",
    "user_id",
    "create_time",
    "text",
    "message_id",
    "reply_to_message_id",
    "direction",
    "bot_id",
    "platform",
    "message_type",
)
_STRUCTURED_PROJECTION_FIELDS = (
    *_TEXT_PROJECTION_FIELDS,
    "plain_text",
    "segments",
    "segment_types",
)
_PLACEHOLDER_MAP = {
    "mention": "@{target}",
    "emoji": "[emoji:{id}]",
    "image": "[image]",
    "sticker": "[sticker]",
    "audio": "[audio]",
    "video": "[video]",
    "file": "[file]",
    "card": "[card]",
    "reference": "[reference]",
    "reply": "[reply]",
}
_CARD_FIELD_ALIASES = {
    "source": ("source", "app_name", "app", "tag"),
    "title": ("title",),
    "prompt": ("prompt",),
    "description": ("description", "desc", "summary"),
    "id": ("bvid", "aid", "id"),
}
_OUTGOING_RECORD_LOCK = asyncio.Lock()
_OUTGOING_CONTENT_FIELDS = (
    "text",
    "plain_text",
    "segments",
    "segment_types",
)


def _strip_nul(value: str) -> str:
    """移除 PostgreSQL 文本类型无法存储的零字节。"""
    return value.replace("\x00", "")


def _structured_media_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """从结构化段详情派生媒体数量和纯媒体标记，不增加数据库字段。"""
    segments = row.get("segments")
    if not isinstance(segments, list):
        return {**row, "media_count": 0, "is_media_only": False}

    media_count = 0
    has_non_media_content = False
    for segment in segments:
        if not isinstance(segment, dict):
            has_non_media_content = True
            continue
        segment_type = str(segment.get("type") or "")
        data = segment.get("data")
        data = data if isinstance(data, dict) else {}
        if segment_type in _MEDIA_COUNT_SEGMENTS:
            media_count += 1
            continue
        if segment_type == "unknown" and str(data.get("raw_type") or "") in {
            "record",
            "voice",
        }:
            media_count += 1
            continue
        if segment_type == "reply":
            continue
        if segment_type == "text":
            if str(data.get("text") or "").strip():
                has_non_media_content = True
            continue
        has_non_media_content = True

    return {
        **row,
        "media_count": media_count,
        "is_media_only": media_count > 0 and not has_non_media_content,
    }


def _to_str(value: Any) -> str | None:
    """把平台字段统一为字符串，避免不同适配器返回数字造成查询条件不稳定。"""
    if value is None:
        return None
    return _strip_nul(str(value))


def _truncate(value: Any, limit: int = _MAX_DATA_VALUE_LENGTH) -> Any:
    """限制入库字段长度，防止异常payload撑大历史表。"""
    if not isinstance(value, str):
        return value
    value = _strip_nul(value)
    if len(value) <= limit:
        return value
    suffix = "...[truncated]"
    return f"{value[: max(0, limit - len(suffix))]}{suffix}"


def _is_url_or_base64(value: Any) -> bool:
    """识别不应持久化的临时链接与内嵌媒体正文。"""
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return bool(
        "://" in lowered
        or lowered.startswith(("www.", "//", "base64://"))
        or (lowered.startswith("data:") and ";base64," in lowered)
    )


def _clean_summary(value: Any) -> str | None:
    """规范化媒体描述，避免嵌入占位符时产生双重方括号。"""
    summary = str(value or "").strip()
    if len(summary) >= 2 and summary.startswith("[") and summary.endswith("]"):
        summary = summary[1:-1].strip()
    return _truncate(summary) if summary else None


def _safe_card_text(value: Any) -> str | None:
    """过滤卡片中的临时链接与大型正文，只保留短标量语义。"""
    if not isinstance(value, str | int | float | bool):
        return None
    text = str(value).strip()
    if not text or _is_url_or_base64(text):
        return None
    return _truncate(text)


def _find_nested_card_text(payload: Any, aliases: tuple[str, ...]) -> str | None:
    """按层级搜索卡片白名单字段，避免保存整份平台JSON。"""
    queue = [payload]
    lowered_aliases = set(aliases)
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).lower() in lowered_aliases:
                    if safe_value := _safe_card_text(value):
                        return safe_value
            queue.extend(
                value for value in current.values() if isinstance(value, dict | list)
            )
        elif isinstance(current, list):
            queue.extend(value for value in current if isinstance(value, dict | list))
    return None


def _safe_card_data(data: dict[str, Any], raw_type: str) -> dict[str, Any]:
    """从卡片payload提取跨平台短文本摘要。"""
    result = {"format": _to_str(data.get("format") or raw_type)}
    payload = data.get("content")
    if payload is None:
        payload = data.get("raw") or data.get("data")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict | list):
        return result
    for field, aliases in _CARD_FIELD_ALIASES.items():
        if value := _find_nested_card_text(payload, aliases):
            result[field] = value
    return result


def _node_value(node: Any, key: str, default: Any = None) -> Any:
    """兼容平台节点字典与UniSeg CustomNode对象。"""
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


def _truncate_forward_node_segments(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """同时限制节点段数和累计文本，防止单个转发节点放大历史表。"""
    bounded: list[dict[str, Any]] = []
    text_length = 0
    for segment in segments[:_MAX_FORWARD_NODE_SEGMENTS]:
        if segment.get("type") != "text":
            bounded.append(segment)
            continue
        remaining = _MAX_FORWARD_NODE_TEXT_LENGTH - text_length
        if remaining <= 0:
            continue
        data = segment.get("data") or {}
        text = str(data.get("text") or "")
        if len(text) <= remaining:
            clipped = text
        else:
            suffix = "...[truncated]"
            clipped = (
                text[:remaining]
                if remaining <= len(suffix)
                else f"{text[: remaining - len(suffix)]}{suffix}"
            )
        text_length += len(clipped)
        bounded.append({"type": "text", "data": {"text": clipped}})
    return bounded


def _safe_forward_nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """保存前五个安全节点；嵌套转发禁止递归展开。"""
    raw_nodes = data.get("nodes") or data.get("content")
    if not isinstance(raw_nodes, list):
        return []
    nodes: list[dict[str, Any]] = []
    for raw_node in raw_nodes[:_MAX_FORWARD_NODES]:
        sender = _node_value(raw_node, "sender", {})
        sender = sender if isinstance(sender, dict) else {}
        content = _node_value(raw_node, "message")
        if content is None:
            content = _node_value(raw_node, "content", "")
        node_segments, _, _ = _normalize_message_segments(
            content,
            include_reference_nodes=False,
        )
        node: dict[str, Any] = {
            "segments": _truncate_forward_node_segments(node_segments)
        }
        user_id = (
            _node_value(raw_node, "user_id")
            or _node_value(raw_node, "uid")
            or sender.get("user_id")
        )
        name = (
            _node_value(raw_node, "nickname")
            or _node_value(raw_node, "name")
            or sender.get("card")
            or sender.get("nickname")
        )
        node_time = _node_value(raw_node, "time")
        if user_id is not None:
            node["user_id"] = _truncate(str(user_id))
        if name is not None:
            node["name"] = _truncate(str(name))
        if isinstance(node_time, int | float):
            node["time"] = int(node_time)
        nodes.append(node)
    return nodes


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
    include_reference_nodes: bool = True,
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
        reference_data = {
            key: value
            for key, value in {
                "id": _to_str(data.get("id")),
                "name": _truncate(data.get("name")),
            }.items()
            if value is not None
        }
        if include_reference_nodes:
            if nodes := _safe_forward_nodes(data):
                reference_data["nodes"] = nodes
        return reference_data
    if seg_type == "emoji":
        return {
            key: _truncate(data.get(key))
            for key in ("id", "name")
            if data.get(key) is not None
        }
    if seg_type in _MEDIA_SEGMENTS:
        media_data: dict[str, Any] = {}
        allowed_keys = _MEDIA_KEYS
        if seg_type == "sticker":
            allowed_keys = _MEDIA_KEYS | {"emoji_id", "emoji_package_id"}
        for key, value in data.items():
            if key not in allowed_keys or value is None:
                continue
            if _is_url_or_base64(value):
                continue
            if key == "summary":
                if summary := _clean_summary(value):
                    media_data[key] = summary
                continue
            media_data[key] = _truncate(value)
        return media_data
    if seg_type == "card":
        return _safe_card_data(data, raw_type)
    # 未知段只保留类型与简单小字段，避免平台扩展段导致整条消息丢失。
    return {
        "raw_type": raw_type,
        **{
            key: _truncate(value)
            for key, value in data.items()
            if key != "url"
            and (isinstance(value, str | int | float | bool) or value is None)
            and not _is_url_or_base64(value)
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


def _forward_nodes_from_response(response: Any) -> list[Any]:
    """兼容NapCat及不同OneBot实现的合并转发响应外壳。"""
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return []
    if isinstance(response.get("messages"), list):
        return response["messages"]
    data = response.get("data")
    if not isinstance(data, dict):
        return []
    for key in ("messages", "message"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


async def enrich_forward_segments(bot: Bot, message: Any) -> list[dict[str, Any]]:
    """通过OneBot合并转发ID补取节点，API不可用时保留原始ID段。"""
    enriched: list[dict[str, Any]] = []
    for segment in _iter_message_segments(message):
        raw_type = _segment_type(segment)
        if not raw_type:
            continue
        data = _segment_data(segment)
        forward_id = data.get("id")
        if raw_type == "forward" and forward_id and not (
            data.get("nodes") or data.get("content")
        ):
            try:
                response = await bot.call_api("get_forward_msg", id=str(forward_id))
            except Exception as e:
                # 合并转发预览是增强信息，平台不支持时不能影响整条消息入库。
                logger.warning(
                    f"获取合并转发节点失败，保留ID占位 id={forward_id}",
                    "chat_history",
                    e=e,
                )
            else:
                if nodes := _forward_nodes_from_response(response):
                    data["nodes"] = nodes
        enriched.append({"type": raw_type, "data": data})
    return enriched


def _is_sticker_segment(seg_type: str, data: dict[str, Any]) -> bool:
    """识别UniSeg sticker标记和OneBot自定义表情元数据。"""
    if seg_type == "sticker":
        return True
    subtype = data.get("sub_type") if "sub_type" in data else data.get("subType")
    return bool(
        data.get("sticker")
        or str(subtype or "") == "1"
        or data.get("emoji_id")
        or data.get("emoji_package_id")
    )


def _normalize_message_segments(
    message: Any,
    *,
    include_reference_nodes: bool = True,
) -> tuple[list[dict[str, Any]], list[str], str]:
    """执行单一消息源的canonical规范化。"""
    normalized: list[dict[str, Any]] = []
    readable_parts: list[str] = []
    for segment in _iter_message_segments(message):
        raw_type = _segment_type(segment)
        if not raw_type:
            continue
        seg_type = _canonical_segment_type(raw_type)
        raw_data = _segment_data(segment)
        if seg_type == "image" and _is_sticker_segment(seg_type, raw_data):
            seg_type = "sticker"
        if seg_type not in _PLACEHOLDER_MAP and seg_type != "text":
            seg_type = "unknown"
        data = _safe_segment_data(
            seg_type,
            raw_data,
            raw_type=raw_type,
            include_reference_nodes=include_reference_nodes,
        )
        normalized.append({"type": seg_type, "data": data})
        readable_parts.append(_segment_readable_text(seg_type, data))
    segment_types = list(dict.fromkeys(seg["type"] for seg in normalized))
    return normalized, segment_types, "".join(readable_parts)


def _segments_compatible(base_type: str, raw_type: str) -> bool:
    """允许原始自定义表情补全UniMessage中的普通image。"""
    return base_type == raw_type or {base_type, raw_type} == {"image", "sticker"}


def _merge_raw_segments(
    base_segments: list[dict[str, Any]],
    raw_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按原始消息顺序补回语义，同时复用UniMessage已经归一化的字段。"""
    if not base_segments:
        return raw_segments
    remaining = list(base_segments)
    merged: list[dict[str, Any]] = []
    for raw_segment in raw_segments:
        raw_type = str(raw_segment.get("type") or "")
        match_index = next(
            (
                index
                for index, base_segment in enumerate(remaining)
                if _segments_compatible(
                    str(base_segment.get("type") or ""),
                    raw_type,
                )
            ),
            None,
        )
        if match_index is None:
            # 原始事件独有的reply等段必须补回；未知扩展段交给UniMessage主体决定。
            if raw_type != "unknown":
                merged.append(raw_segment)
            continue
        base_segment = remaining.pop(match_index)
        merged_type = (
            "sticker"
            if "sticker" in {str(base_segment.get("type")), raw_type}
            else raw_type
        )
        merged.append(
            {
                "type": merged_type,
                "data": {
                    **(base_segment.get("data") or {}),
                    **(raw_segment.get("data") or {}),
                },
            }
        )
    merged.extend(remaining)
    return merged


def normalize_message_segments(
    message: Any,
    *,
    raw_message: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str], str]:
    """规范化消息段，并用原始事件安全补全适配器丢失的语义。"""
    base_segments, _, _ = _normalize_message_segments(message)
    normalized = base_segments
    if raw_message is not None:
        raw_segments, _, _ = _normalize_message_segments(raw_message)
        normalized = _merge_raw_segments(base_segments, raw_segments)
    segment_types = list(dict.fromkeys(seg["type"] for seg in normalized))
    readable_text = "".join(
        _segment_readable_text(
            str(segment.get("type") or "unknown"),
            segment.get("data") or {},
        )
        for segment in normalized
    )
    return normalized, segment_types, readable_text


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
        return _strip_nul(str(message.extract_plain_text() or ""))
    return _strip_nul(_segments_to_plain_text(segments))


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
    *,
    raw_message: Any | None = None,
) -> ChatHistory:
    """构造入站聊天记录，数据库写入仍交给原队列批量处理。"""
    from zhenxun.utils.utils import get_entity_ids

    entity = get_entity_ids(session)
    raw_message = raw_message or getattr(event, "original_message", None)
    segments, segment_types, readable_text = normalize_message_segments(
        message,
        raw_message=raw_message,
    )
    plain_text = _extract_plain_text(message, segments)
    reply_to_message_id = _extract_reply_to_message_id(segments, event)
    return ChatHistory(
        user_id=_to_str(entity.user_id) or "",
        group_id=_to_str(entity.group_id),
        text=readable_text,
        plain_text=plain_text,
        bot_id=_to_str(session.self_id),
        platform=_to_str(PlatformUtils.get_platform(session)),
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
    create_time: datetime | None = None,
    raw_message: Any | None = None,
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
        normalized_message,
        raw_message=raw_message if raw_message is not None else message,
    )
    plain_text = _extract_plain_text(message, segments)
    reply_to_message_id = _extract_reply_to_message_id(segments, None)
    bot_id = _to_str(bot.self_id)
    return ChatHistory(
        user_id=_to_str(user_id) or bot_id or "",
        group_id=_to_str(group_id),
        text=readable_text,
        plain_text=plain_text,
        bot_id=bot_id,
        platform=_to_str(PlatformUtils.get_platform(bot)),
        direction="out",
        message_id=_extract_message_id_from_result(result),
        message_type=_to_str(message_type),
        create_time=create_time or timezone.now(),
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
    create_time: datetime | None = None,
    platform_event: bool = False,
    raw_message: Any | None = None,
) -> ChatHistory:
    """幂等写入Bot出站记录，并允许平台事件覆盖本机时间回退。"""
    record = build_outgoing_record(
        bot,
        user_id=user_id,
        group_id=group_id,
        message_type=message_type,
        message=message,
        result=result,
        create_time=create_time,
        raw_message=raw_message,
    )
    if not record.message_id:
        await record.save()
        return record

    # API hook与message_sent可能并发到达，锁内查写可避免同一平台消息产生两行。
    async with _OUTGOING_RECORD_LOCK:
        existing = await (
            ChatHistory.filter(
                platform=record.platform,
                bot_id=record.bot_id,
                message_id=record.message_id,
                direction="out",
            )
            .order_by("-id")
            .first()
        )
        if existing is None:
            await record.save()
            return record
        if not platform_event:
            # 事件先到时只能知道Bot自身，后到的API上下文负责补齐真实目标用户。
            if record.user_id and existing.user_id != record.user_id:
                existing.user_id = record.user_id
                await existing.save(update_fields=["user_id"])
            return existing

        # 平台时间始终更可信；其余字段只用有效值补全，避免清空API已解析的目标。
        existing.create_time = record.create_time
        update_fields = ["create_time"]
        if record.group_id is not None:
            existing.group_id = record.group_id
            update_fields.append("group_id")
        if record.message_type is not None:
            existing.message_type = record.message_type
            update_fields.append("message_type")
        if record.segments:
            for field in _OUTGOING_CONTENT_FIELDS:
                setattr(existing, field, getattr(record, field))
            update_fields.extend(_OUTGOING_CONTENT_FIELDS)
        if record.reply_to_message_id is not None:
            existing.reply_to_message_id = record.reply_to_message_id
            update_fields.append("reply_to_message_id")
        await existing.save(update_fields=update_fields)
        return existing


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
    async def text_recent(
        cls,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """倒序读取面向总结和导出的最近轻量文本字段。"""
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
            .limit(cls._normalize_limit(limit, default=1000))
            .values(*_TEXT_PROJECTION_FIELDS)
        )

    @classmethod
    async def structured_recent(
        cls,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
        platform: str | None = None,
        message_type: str | None = None,
        direction: QueryDirection = "in",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """倒序读取包含消息段详情的最近结构化字段。"""
        rows = await (
            cls._base_query(
                group_id=group_id,
                user_id=user_id,
                bot_id=bot_id,
                platform=platform,
                message_type=message_type,
                direction=direction,
            )
            .order_by("-create_time", "-id")
            .limit(cls._normalize_limit(limit, default=1000))
            .values(*_STRUCTURED_PROJECTION_FIELDS)
        )
        return [_structured_media_metadata(row) for row in rows]

    @classmethod
    async def structured_by_message_ids(
        cls,
        message_ids: Iterable[str],
        *,
        platform: str | None = None,
        bot_id: str | None = None,
        group_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """批量读取被引用消息，每个平台消息ID只返回最新结构化记录。"""
        unique_ids = list(
            dict.fromkeys(
                str(message_id)
                for message_id in message_ids
                if message_id is not None
            )
        )[:MAX_QUERY_LIMIT]
        found: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(unique_ids), _MESSAGE_ID_QUERY_CHUNK_SIZE):
            chunk = unique_ids[offset : offset + _MESSAGE_ID_QUERY_CHUNK_SIZE]
            rows = await (
                cls._base_query(
                    group_id=group_id,
                    bot_id=bot_id,
                    platform=platform,
                    direction="all",
                )
                .filter(message_id__in=chunk)
                .order_by("-create_time", "-id")
                .limit(MAX_QUERY_LIMIT)
                .values(*_STRUCTURED_PROJECTION_FIELDS)
            )
            rows = [_structured_media_metadata(row) for row in rows]
            # 倒序查询确保首次出现的是最新记录，避免旧重复行覆盖新数据。
            for row in rows:
                message_id = str(row.get("message_id") or "")
                if message_id and message_id not in found:
                    found[message_id] = row
        return [found[message_id] for message_id in unique_ids if message_id in found]

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
            .values(*_TEXT_PROJECTION_FIELDS)
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
        descending: bool = False,
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
        order_fields = (
            ("-create_time", "-id") if descending else ("create_time", "id")
        )
        rows = await (
            query.order_by(*order_fields)
            .limit(cls._normalize_limit(limit, default=1000))
            .values(*_STRUCTURED_PROJECTION_FIELDS)
        )
        return [_structured_media_metadata(row) for row in rows]

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
