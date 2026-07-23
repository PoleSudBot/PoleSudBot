from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import time
from typing import Any

import aiofiles
from nonebot.adapters.onebot.v11 import Bot

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.services.log import logger
from zhenxun.utils.decorator.retry import Retry
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.utils import get_user_avatar

from .. import base_config
from ..config import summary_config
from .core import ErrorCode, SummaryException
from .scope import (
    SummaryScope,
    build_db_supplement_warning,
    build_db_time_range_limit_warning,
    build_partial_coverage_warning,
    get_scope_timezone,
)


def _truncate_username(username: str) -> str:
    max_len = summary_config.get_username_max_length()
    if len(username) > max_len:
        keep_len = (max_len - 3) // 2
        if keep_len < 1:
            keep_len = 1
        truncated_name = f"{username[:keep_len]}...{username[-keep_len:]}"
        logger.trace(f"用户名 '{username}' 过长，已截断为 '{truncated_name}'")
        return truncated_name
    return username


@dataclass(frozen=True)
class ReplyPreview:
    message_id: str | None
    user_id: str | None
    name: str
    content: str
    timestamp: int | None = None


@dataclass(frozen=True)
class ProcessedMessage:
    user_id: str
    name: str
    timestamp: int
    plain_content: str
    message_id: str | None = None
    reply: ReplyPreview | None = None

    @property
    def content(self) -> str:
        if self.reply:
            return (
                f"【回复 {self.reply.name}：{self.reply.content}】{self.plain_content}"
            )
        return self.plain_content


@dataclass(frozen=True)
class MessageFetchResult:
    messages: list[ProcessedMessage]
    user_info_cache: dict[str, str]
    coverage_complete: bool
    warning_message: str | None
    source: str


@dataclass(frozen=True)
class _DbTimeRangeFetchResult:
    messages: list[dict[str, Any]]
    has_more: bool


_message_cache: dict[str, tuple[MessageFetchResult, float]] = {}
_reply_message_cache: dict[str, tuple[dict[str, Any] | None, float]] = {}

SEGMENT_PLACEHOLDER_MAP = {
    "face": "[emoji]",
    "emoji": "[emoji]",
    "record": "[voice]",
    "audio": "[voice]",
    "video": "[video]",
    "file": "[file]",
    "json": "[card]",
    "xml": "[card]",
    "card": "[card]",
    "forward": "[forward]",
    "reference": "[forward]",
    "share": "[share]",
}


try:
    from zhenxun.services.chat_history import ChatHistoryQuery
except ImportError:
    ChatHistoryQuery = None
    logger.warning("无法导入 ChatHistoryQuery 服务，数据库历史记录功能不可用。")


def _normalize_segments(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, str):
        return [{"type": "text", "data": {"text": message}}]
    if not isinstance(message, list):
        return []

    normalized: list[dict[str, Any]] = []
    for segment in message:
        if isinstance(segment, dict):
            normalized.append(
                {"type": segment.get("type"), "data": segment.get("data", {}) or {}}
            )
            continue
        seg_type = getattr(segment, "type", None)
        seg_data = getattr(segment, "data", None)
        if seg_type:
            normalized.append({"type": seg_type, "data": seg_data or {}})
    return normalized


def _extract_message_user_id(message: dict[str, Any]) -> str | None:
    user_id = message.get("user_id")
    if user_id is None:
        user_id = (message.get("sender") or {}).get("user_id")
    if user_id is None:
        return None
    return str(user_id)


def _extract_message_id(message: dict[str, Any]) -> str | None:
    message_id = message.get("message_id") or message.get("id")
    if message_id is None:
        return None
    return str(message_id)


def _extract_message_timestamp(message: dict[str, Any]) -> int:
    raw_time = message.get("time") or 0
    try:
        return int(raw_time)
    except (TypeError, ValueError):
        return 0


def _sort_raw_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed_messages = list(enumerate(messages))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        index, message = item
        db_id = message.get("_db_id")
        if isinstance(db_id, int):
            return _extract_message_timestamp(message), 0, db_id
        # API列表本身已有平台顺序，同秒时必须保持输入顺序而不是比较message_id。
        return _extract_message_timestamp(message), 1, index

    return [message for _, message in sorted(indexed_messages, key=sort_key)]


def _raw_message_identity_text(message: dict[str, Any]) -> str:
    """提取跨 API/DB 去重时可比较的纯文本内容。"""
    raw_message = message.get("raw_message")
    if raw_message:
        return _compact_whitespace(str(raw_message))

    text_parts: list[str] = []
    for segment in _normalize_segments(message.get("message", [])):
        if segment.get("type") != "text":
            continue
        text_parts.append(str(segment.get("data", {}).get("text", "")))
    return _compact_whitespace(" ".join(text_parts))


def _raw_message_content_key(message: dict[str, Any]) -> tuple:
    """构建跨源重复消息的保守比较键。"""
    text = _raw_message_identity_text(message)
    if text:
        return (
            "content",
            _extract_message_timestamp(message),
            _extract_message_user_id(message) or "",
            text,
        )
    return (
        "fallback",
        _extract_message_timestamp(message),
        _extract_message_user_id(message) or "",
        _extract_message_id(message) or "",
    )


def _merge_supplemented_messages(
    supplement_messages: list[dict[str, Any]],
    api_messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """合并 DB 补全和 API 消息，只去掉跨源重复记录。"""
    api_keys = {_raw_message_content_key(message) for message in api_messages}
    unique_supplement_messages = [
        message
        for message in supplement_messages
        if _raw_message_content_key(message) not in api_keys
    ]
    return (
        _sort_raw_messages(unique_supplement_messages + api_messages),
        len(unique_supplement_messages),
    )


def _filter_by_scope(
    messages: list[dict[str, Any]],
    scope: SummaryScope,
) -> list[dict[str, Any]]:
    if not scope.is_time_based:
        return messages
    start_ts = int(scope.start_ts or 0)
    end_ts = int(scope.end_ts or 0)
    return [
        msg for msg in messages if start_ts <= _extract_message_timestamp(msg) <= end_ts
    ]


def _filter_by_users(
    messages: list[dict[str, Any]],
    target_user_ids: set[str] | None,
) -> list[dict[str, Any]]:
    if not target_user_ids:
        return messages
    return [
        msg
        for msg in messages
        if (_extract_message_user_id(msg) or "") in target_user_ids
    ]


def _replace_inline_image_markup(text: str) -> str:
    def img_replacer(match: re.Match) -> str:
        summary_match = re.search(r"summary=(.*?)(?:,[a-z_]+=|\])", match.group(0))
        if summary_match and summary_match.group(1):
            return f"[img]{summary_match.group(1)}"
        return "[img]"

    return re.sub(r"\[image:[^\]]+\]", img_replacer, text)


def _path_exists(path: str) -> bool:
    return os.path.exists(path)


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate_text(text: str, limit: int = 80) -> str:
    normalized = _compact_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _resolve_sender_name(
    user_id: str | None,
    message: dict[str, Any],
    user_info_cache: dict[str, str],
) -> str:
    if user_id and user_id in user_info_cache:
        return user_info_cache[user_id]

    sender = message.get("sender") or {}
    fallback_name = (
        sender.get("card")
        or sender.get("nickname")
        or sender.get("name")
        or (f"user_{user_id[-4:]}" if user_id else "unknown")
    )
    if user_id:
        return user_info_cache.get(user_id, _truncate_username(str(fallback_name)))
    return _truncate_username(str(fallback_name))


def _segment_to_text(
    segment: dict[str, Any],
    user_info_cache: dict[str, str],
    *,
    include_forward_nodes: bool = True,
) -> str:
    seg_type = segment.get("type")
    seg_data = segment.get("data", {})

    if seg_type == "text":
        return _compact_whitespace(str(seg_data.get("text", "")))
    if seg_type in {"at", "mention"}:
        target = seg_data.get("qq") if seg_type == "at" else seg_data.get("target")
        if target is not None:
            target_id = str(target)
            default_at_name = _truncate_username(f"user_{target_id[-4:]}")
            return f"@{user_info_cache.get(target_id, default_at_name)}"
    if seg_type == "image":
        summary = seg_data.get("summary")
        return f"[img: {_compact_whitespace(str(summary))}]" if summary else "[img]"
    if seg_type == "sticker":
        summary = seg_data.get("summary")
        return (
            f"[sticker: {_compact_whitespace(str(summary))}]"
            if summary
            else "[sticker]"
        )
    if seg_type == "unknown" and seg_data.get("raw_type") in {"record", "voice"}:
        return "[voice]"
    if seg_type in {"record", "audio"}:
        return "[voice]"
    if seg_type in {"video", "file"}:
        default_names = {"video.mp4"} if seg_type == "video" else {"file.bin"}
        name = str(seg_data.get("name") or "").strip()
        if not name or name in default_names:
            name = str(seg_data.get("file") or seg_data.get("id") or "").strip()
        label = "video" if seg_type == "video" else "file"
        return f"[{label}: {_compact_whitespace(name)}]" if name else f"[{label}]"
    if seg_type in {"json", "xml", "card"}:
        values: list[str] = []
        for key in ("source", "title", "prompt", "description"):
            value = _compact_whitespace(str(seg_data.get(key) or ""))
            if value and value not in values:
                values.append(value)
        if not values:
            fallback_id = _compact_whitespace(str(seg_data.get("id") or ""))
            if fallback_id:
                values.append(fallback_id)
        return f"[card: {' | '.join(values)}]" if values else "[card]"
    if seg_type in {"forward", "reference"}:
        if not include_forward_nodes:
            return "[forward]"
        values: list[str] = []
        if name := _compact_whitespace(str(seg_data.get("name") or "")):
            values.append(name)
        nodes = seg_data.get("nodes")
        if isinstance(nodes, list):
            for node in nodes[:5]:
                if not isinstance(node, dict):
                    continue
                node_user_id = str(node.get("user_id") or "").strip()
                node_name = _compact_whitespace(str(node.get("name") or ""))
                node_label = _user_label(node_name, node_user_id)
                node_text = " ".join(
                    text
                    for node_segment in _normalize_segments(node.get("segments", []))
                    if (
                        text := _segment_to_text(
                            node_segment,
                            user_info_cache,
                            include_forward_nodes=False,
                        )
                    )
                )
                values.append(f"{node_label}: {node_text or '[empty]'}")
        return f"[forward: {' | '.join(values)}]" if values else "[forward]"
    if seg_type in SEGMENT_PLACEHOLDER_MAP:
        return SEGMENT_PLACEHOLDER_MAP[seg_type]
    if seg_type:
        return f"[{seg_type}]"
    return ""


def _get_db_supplement_limit() -> int:
    """读取时间范围数据库补全的独立上限。"""
    raw_supplement_limit = base_config.get("SUMMARY_DB_SUPPLEMENT_MAX_LENGTH", 3000)
    try:
        return max(0, int(raw_supplement_limit))
    except (TypeError, ValueError):
        logger.warning(
            "配置 SUMMARY_DB_SUPPLEMENT_MAX_LENGTH 不是有效整数，使用默认值 3000。",
            command="DB历史",
        )
        return 3000


def _get_db_time_range_limit(max_len: int) -> int:
    """计算纯数据库时间范围查询允许纳入的总消息数。"""
    return max_len + _get_db_supplement_limit()


def _timestamp_to_scope_datetime(timestamp: int) -> datetime:
    """将范围时间戳转换为数据库查询使用的本地时区时间。"""
    return datetime.fromtimestamp(timestamp, get_scope_timezone())


def _db_row_value(message: Any, key: str, default: Any = None) -> Any:
    """兼容服务层字典投影和旧测试使用的模型对象。"""
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _get_db_query_direction() -> str:
    """按配置在查询阶段决定是否纳入 Bot 的出站消息。"""
    return "in" if base_config.get("EXCLUDE_BOT_MESSAGES", False) else "all"


def _format_db_messages(db_messages: list[Any]) -> list[dict[str, Any]]:
    """将 ChatHistory 服务投影转换成后续处理函数可复用的消息字典。"""
    formatted_messages: list[dict[str, Any]] = []
    for msg in db_messages:
        direction = str(_db_row_value(msg, "direction", "in") or "in")
        # 出站记录沿用现有 user_id 存储语义，展示发送者时必须改用真实 bot_id。
        sender_field = "bot_id" if direction == "out" else "user_id"
        sender_id = str(_db_row_value(msg, sender_field, "") or "")
        normalized_sender_id: int | str = (
            int(sender_id) if sender_id.isdigit() else sender_id
        )
        plain_text = str(_db_row_value(msg, "plain_text", "") or "")
        readable_text = plain_text or str(_db_row_value(msg, "text", "") or "")
        create_time = _db_row_value(msg, "create_time")
        segments = _db_row_value(msg, "segments")
        # 新记录优先消费 canonical segments，旧记录缺失时才退回可读文本。
        if not isinstance(segments, list) or not segments:
            segments = [{"type": "text", "data": {"text": readable_text}}]
        else:
            segments = [
                dict(segment) for segment in segments if isinstance(segment, dict)
            ]
        reply_to_message_id = _db_row_value(msg, "reply_to_message_id")
        if reply_to_message_id and not any(
            segment.get("type") == "reply" for segment in segments
        ):
            # 入站UniMessage可能剥离reply段，顶层关系字段是数据库中的可靠兜底。
            segments.insert(
                0,
                {
                    "type": "reply",
                    "data": {"message_id": str(reply_to_message_id)},
                },
            )

        formatted_messages.append(
            {
                "_db_id": _db_row_value(msg, "id"),
                "message_id": _db_row_value(msg, "message_id"),
                "reply_to_message_id": reply_to_message_id,
                "user_id": normalized_sender_id,
                "time": int(create_time.timestamp()) if create_time else 0,
                "message_type": _db_row_value(msg, "message_type", "group")
                or "group",
                "message": segments,
                "raw_message": readable_text,
                "sender": {"user_id": normalized_sender_id},
                "group_id": _db_row_value(msg, "group_id"),
                "bot_id": _db_row_value(msg, "bot_id"),
                "platform": _db_row_value(msg, "platform"),
                "_summary_source": "db",
            }
        )
    return formatted_messages


async def _fetch_raw_messages_from_db(
    group_id: int,
    count: int,
) -> list[dict[str, Any]]:
    group_id_str = str(group_id)
    logger.debug(
        f"尝试从数据库获取群 {group_id} 的最近 {count} 条聊天记录", command="DB历史"
    )
    try:
        db_messages = await ChatHistoryQuery.structured_recent(
            group_id=group_id_str,
            direction=_get_db_query_direction(),
            limit=count,
        )
        if not db_messages:
            logger.warning(
                f"数据库中未找到群 {group_id} 的聊天记录",
                command="DB历史",
                group_id=group_id,
            )
            return []

        formatted_messages = _format_db_messages(list(reversed(db_messages)))
        logger.debug(
            "从数据库成功获取并格式化 "
            f"{len(formatted_messages)} 条结构化消息",
            command="DB历史",
            group_id=group_id,
        )
        return formatted_messages
    except Exception as e:
        logger.error(
            f"从数据库获取群 {group_id} 历史记录失败: {e}",
            command="DB历史",
            group_id=group_id,
            e=e,
        )
        raise SummaryException(
            message=f"数据库历史记录获取失败: {e!s}",
            code=ErrorCode.DB_QUERY_ERROR,
            details={"error": str(e), "group_id": group_id, "count": count},
            cause=e,
        ) from e


async def _fetch_raw_messages_from_db_time_range(
    group_id: int,
    start_ts: int,
    end_ts: int,
    limit: int,
    *,
    include_end: bool = True,
) -> _DbTimeRangeFetchResult:
    """按时间范围从数据库获取聊天记录，并返回是否达到读取上限。"""
    group_id_str = str(group_id)
    safe_limit = max(0, int(limit))
    start_dt = _timestamp_to_scope_datetime(start_ts)
    end_dt = _timestamp_to_scope_datetime(end_ts)
    # 服务层范围查询使用闭区间；排除末秒时将上界退回一整秒保持旧语义。
    query_end = end_dt if include_end else _timestamp_to_scope_datetime(end_ts - 1)

    logger.debug(
        "尝试从数据库按时间范围获取群 "
        f"{group_id} 的聊天记录: {start_dt} ~ {end_dt}, limit={safe_limit}",
        command="DB历史",
    )
    try:
        if safe_limit <= 0:
            return _DbTimeRangeFetchResult(messages=[], has_more=False)

        # 时间范围可能远大于 API 上限，倒序额外多取一条以保留最新记录并判断缺失。
        db_messages = await ChatHistoryQuery.structured_range(
            start=start_dt,
            end=query_end,
            group_id=group_id_str,
            direction=_get_db_query_direction(),
            descending=True,
            limit=safe_limit + 1,
        )
        has_more = len(db_messages) > safe_limit
        if has_more:
            db_messages = db_messages[:safe_limit]

        messages = _format_db_messages(list(reversed(db_messages)))
        logger.debug(
            "从数据库按时间范围成功获取并格式化 "
            f"{len(messages)} 条结构化消息，has_more={has_more}",
            command="DB历史",
            group_id=group_id,
        )
        return _DbTimeRangeFetchResult(
            messages=messages,
            has_more=has_more,
        )
    except Exception as e:
        logger.error(
            f"从数据库按时间范围获取群 {group_id} 历史记录失败: {e}",
            command="DB历史",
            group_id=group_id,
            e=e,
        )
        raise SummaryException(
            message=f"数据库时间范围历史记录获取失败: {e!s}",
            code=ErrorCode.DB_QUERY_ERROR,
            details={
                "error": str(e),
                "group_id": group_id,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "limit": safe_limit,
            },
            cause=e,
        ) from e


async def _fetch_raw_messages_from_api(
    bot: Bot,
    group_id: int,
    count: int,
) -> list[dict[str, Any]]:
    logger.debug(
        f"通过 API 获取群 {group_id} 的最近 {count} 条聊天记录", command="API历史"
    )

    @Retry.simple(
        stop_max_attempt=summary_config.get_max_retries(),
        wait_fixed_seconds=summary_config.get_retry_delay(),
    )
    async def fetch_with_retry() -> list[dict[str, Any]]:
        response = await bot.get_group_msg_history(group_id=group_id, count=count)
        raw_messages = response.get("messages", [])
        logger.debug(
            f"从群 {group_id} API 获取了 {len(raw_messages)} 条原始消息",
            command="API历史",
            group_id=group_id,
        )
        return raw_messages

    try:
        return await fetch_with_retry()
    except Exception as e:
        logger.error(
            f"通过 API 获取群 {group_id} 的原始消息历史失败 (所有重试后): {e}",
            command="API历史",
            group_id=group_id,
            e=e,
        )
        raise SummaryException(
            message=f"API 消息历史获取失败: {e!s}",
            code=ErrorCode.MESSAGE_FETCH_FAILED,
            details={"error": str(e), "group_id": group_id, "count": count},
            cause=e,
        ) from e


async def _supplement_time_scope_with_db(
    group_id: int,
    scope: SummaryScope,
    raw_messages: list[dict[str, Any]],
    fetch_count: int,
) -> tuple[list[dict[str, Any]], bool, str | None, str]:
    """在 API 历史无法覆盖时间范围起点时，用数据库记录补齐较早片段。"""
    coverage_complete = True
    warning_message: str | None = None
    source = "api"

    if not scope.is_time_based or not raw_messages:
        return raw_messages, coverage_complete, warning_message, source

    start_ts = int(scope.start_ts or 0)
    end_ts = int(scope.end_ts or 0)
    earliest_ts = _extract_message_timestamp(raw_messages[0])
    if earliest_ts <= start_ts:
        return raw_messages, coverage_complete, warning_message, source

    coverage_complete = False
    warning_message = build_partial_coverage_warning(scope, fetch_count)

    if not ChatHistoryQuery:
        logger.warning(
            "时间范围超过 API 覆盖范围，但 ChatHistoryQuery 不可用，"
            "无法补全数据库历史。",
            command="DB历史",
            group_id=group_id,
        )
        return raw_messages, coverage_complete, warning_message, source

    supplement_limit = _get_db_supplement_limit()
    if supplement_limit <= 0:
        return raw_messages, coverage_complete, warning_message, source

    # API 只能拿最近固定条数时，用数据库补左侧缺口；边界秒随后用内容去重处理。
    gap_end_ts = min(end_ts, earliest_ts)
    if gap_end_ts < start_ts:
        return raw_messages, coverage_complete, warning_message, source

    try:
        supplement_result = await _fetch_raw_messages_from_db_time_range(
            group_id,
            start_ts,
            gap_end_ts,
            supplement_limit,
        )
    except SummaryException as e:
        logger.warning(
            f"数据库补全时间范围历史失败，将继续使用 API 部分结果: {e}",
            command="DB历史",
            group_id=group_id,
            e=e,
        )
        return raw_messages, coverage_complete, warning_message, source

    if not supplement_result.messages:
        return raw_messages, coverage_complete, warning_message, source

    combined_messages, supplement_count = _merge_supplemented_messages(
        supplement_result.messages,
        raw_messages,
    )
    if supplement_count <= 0:
        return raw_messages, coverage_complete, warning_message, source

    warning_message = build_db_supplement_warning(
        scope,
        supplement_count,
        supplement_result.has_more,
    )
    coverage_complete = not supplement_result.has_more
    return combined_messages, coverage_complete, warning_message, "api+db"


async def get_group_messages(
    bot: Bot,
    group_id: int,
    scope: SummaryScope,
    use_db: bool = False,
    target_user_ids: set[str] | None = None,
) -> MessageFetchResult:
    cache_ttl = int(base_config.get("MESSAGE_CACHE_TTL_SECONDS", 300))
    max_len = int(base_config.get("SUMMARY_MAX_LENGTH", 1000))
    fetch_count = scope.fetch_count(max_len)
    source_key = "db" if use_db and ChatHistoryQuery else "api"
    cache_key = f"{group_id}:{fetch_count}:{source_key}"

    if cache_ttl > 0 and not target_user_ids and not scope.is_time_based:
        current_time = time.time()
        if cache_key in _message_cache:
            cached_data, timestamp = _message_cache[cache_key]
            if current_time - timestamp < cache_ttl:
                logger.debug(
                    f"命中消息缓存 (群: {group_id}, 数量: {fetch_count})，"
                    "剩余有效期: "
                    f"{cache_ttl - (current_time - timestamp):.1f}s"
                )
                return copy.deepcopy(cached_data)

    warning_message: str | None = None
    coverage_complete = True

    if use_db and ChatHistoryQuery:
        if scope.is_time_based:
            # DB 主路径直接按时间查询，避免“最近 N 条”先截断后再过滤导致范围缺失。
            db_result = await _fetch_raw_messages_from_db_time_range(
                group_id,
                int(scope.start_ts or 0),
                int(scope.end_ts or 0),
                _get_db_time_range_limit(max_len),
            )
            raw_messages = db_result.messages
            if db_result.has_more:
                coverage_complete = False
                warning_message = build_db_time_range_limit_warning(
                    scope,
                    len(raw_messages),
                )
        else:
            raw_messages = await _fetch_raw_messages_from_db(group_id, fetch_count)
        source = "db"
    else:
        if use_db and not ChatHistoryQuery:
            logger.warning(
                "配置了使用数据库历史但 ChatHistoryQuery 服务导入失败，"
                "回退到 API 获取。"
            )
        raw_messages = await _fetch_raw_messages_from_api(bot, group_id, fetch_count)
        source = "api"

    raw_messages = _sort_raw_messages(raw_messages)
    if source == "api":
        (
            raw_messages,
            coverage_complete,
            warning_message,
            source,
        ) = await _supplement_time_scope_with_db(
            group_id,
            scope,
            raw_messages,
            fetch_count,
        )

    scoped_messages = _filter_by_scope(raw_messages, scope)
    filtered_messages = _filter_by_users(scoped_messages, target_user_ids)

    if not filtered_messages:
        return MessageFetchResult(
            messages=[],
            user_info_cache={},
            coverage_complete=coverage_complete,
            warning_message=warning_message,
            source=source,
        )

    try:
        processed_data, user_info_cache = await process_message(
            filtered_messages,
            bot,
            group_id,
        )
        result = MessageFetchResult(
            messages=processed_data,
            user_info_cache=user_info_cache,
            coverage_complete=coverage_complete,
            warning_message=warning_message,
            source=source,
        )
        if cache_ttl > 0 and not target_user_ids and not scope.is_time_based:
            _message_cache[cache_key] = (copy.deepcopy(result), time.time())
            logger.debug(f"消息已存入缓存 (群: {group_id}, 数量: {fetch_count})")
        return result
    except Exception as e:
        logger.error(
            f"处理群 {group_id} 消息失败: {e}",
            command="get_group_messages",
            group_id=group_id,
            e=e,
        )
        raise SummaryException(
            message=f"消息处理失败: {e!s}",
            code=ErrorCode.MESSAGE_PROCESS_FAILED,
            details={"error": str(e), "group_id": group_id, "count": fetch_count},
            cause=e,
        ) from e


@Retry.api(
    stop_max_attempt=summary_config.get_user_info_max_retries() + 1,
    wait_exp_multiplier=summary_config.get_user_info_retry_delay(),
    log_name="获取用户信息",
)
async def _fetch_user_info_with_retry(bot: Bot, user_id_str: str, group_id_str: str):
    user_info_timeout = summary_config.get_user_info_timeout()
    result = await asyncio.wait_for(
        PlatformUtils.get_user(bot, user_id_str, group_id_str),
        timeout=user_info_timeout,
    )
    return result


async def _build_user_info_cache(
    bot: Bot,
    group_id: int,
    user_ids_to_fetch: set[str],
) -> dict[str, str]:
    user_info_cache: dict[str, str] = {}
    if not user_ids_to_fetch:
        return user_info_cache

    logger.debug(
        f"需要获取 {len(user_ids_to_fetch)} 个用户的信息: {sorted(user_ids_to_fetch)}",
        group_id=group_id,
    )
    semaphore = asyncio.Semaphore(summary_config.get_concurrent_user_fetch_limit())
    group_id_str = str(group_id)

    async def get_user_with_sem(user_id: str):
        async with semaphore:
            try:
                return user_id, await _fetch_user_info_with_retry(
                    bot, user_id, group_id_str
                )
            except Exception as e:
                logger.warning(
                    f"获取用户 {user_id} 信息最终失败: {e}", group_id=group_id
                )
                return user_id, None

    tasks = [get_user_with_sem(uid) for uid in user_ids_to_fetch]
    timeout = summary_config.get_message_process_timeout()
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            f"批量获取用户信息整体超时 ({timeout}s)，将使用默认用户名",
            group_id=group_id,
        )
        results = []

    for res in results:
        if not res:
            continue
        user_id_str, user_data = res
        fallback_name = f"user_{user_id_str[-4:]}"
        if user_data:
            sender_name = user_data.card or user_data.name or fallback_name
            user_info_cache[user_id_str] = _truncate_username(str(sender_name))
        else:
            user_info_cache[user_id_str] = _truncate_username(fallback_name)

    logger.debug(
        f"用户信息并发获取完成，缓存了 {len(user_info_cache)} 个用户信息",
        group_id=group_id,
    )
    return user_info_cache


async def _fetch_reply_message(
    bot: Bot,
    message_id: str,
) -> dict[str, Any] | None:
    cache_ttl = int(base_config.get("MESSAGE_CACHE_TTL_SECONDS", 300))
    now = time.time()
    cached = _reply_message_cache.get(message_id)
    if cached and now - cached[1] < cache_ttl:
        return copy.deepcopy(cached[0])

    @Retry.simple(
        stop_max_attempt=summary_config.get_max_retries(),
        wait_fixed_seconds=summary_config.get_retry_delay(),
    )
    async def fetch_with_retry() -> dict[str, Any] | None:
        payload: dict[str, Any]
        if message_id.isdigit():
            payload = await bot.get_msg(message_id=int(message_id))
        else:
            payload = await bot.get_msg(message_id=message_id)
        return payload

    try:
        message = await fetch_with_retry()
        _reply_message_cache[message_id] = (copy.deepcopy(message), now)
        return message
    except Exception as e:
        logger.warning(f"获取引用消息 {message_id} 失败: {e}", command="消息处理", e=e)
        _reply_message_cache[message_id] = (None, now)
        return None


async def _prefetch_reply_messages(
    bot: Bot,
    messages: list[dict[str, Any]],
    group_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    reply_ids: set[str] = set()
    for msg in messages:
        for segment in _normalize_segments(msg.get("message", [])):
            if segment.get("type") == "reply":
                reply_id = _extract_reply_segment_id(segment)
                if reply_id:
                    reply_ids.add(reply_id)

    if not reply_ids:
        return {}

    reply_messages: dict[str, dict[str, Any]] = {}
    platform = next(
        (str(message["platform"]) for message in messages if message.get("platform")),
        None,
    )
    bot_id = next(
        (str(message["bot_id"]) for message in messages if message.get("bot_id")),
        str(bot.self_id),
    )
    resolved_group_id = str(group_id) if group_id is not None else next(
        (str(message["group_id"]) for message in messages if message.get("group_id")),
        None,
    )
    try:
        db_rows = await ChatHistoryQuery.structured_by_message_ids(
            reply_ids,
            platform=platform,
            bot_id=bot_id,
            group_id=resolved_group_id,
        )
        reply_messages.update(
            {
                str(message["message_id"]): message
                for message in _format_db_messages(db_rows)
                if message.get("message_id")
            }
        )
    except Exception as e:
        # 引用正文补全属于增强路径，数据库异常时继续沿用平台API，不能中断总结。
        logger.warning(
            "从ChatHistory预取引用消息失败，回退平台API",
            command="消息处理",
            e=e,
        )

    missing_reply_ids = reply_ids - reply_messages.keys()
    if not missing_reply_ids:
        return reply_messages

    semaphore = asyncio.Semaphore(summary_config.get_concurrent_user_fetch_limit())

    async def fetch_one(reply_id: str):
        async with semaphore:
            return reply_id, await _fetch_reply_message(bot, reply_id)

    fetched = await asyncio.gather(
        *(fetch_one(reply_id) for reply_id in missing_reply_ids)
    )
    reply_messages.update(
        {reply_id: message for reply_id, message in fetched if message}
    )
    return reply_messages


def _collect_user_ids_from_segments(
    segments: list[dict[str, Any]],
    user_ids_to_fetch: set[str],
) -> None:
    """收集 API at 与核心 mention 两种消息段中的用户 ID。"""
    for segment in segments:
        seg_type = segment.get("type")
        seg_data = segment.get("data", {})
        target = None
        if seg_type == "at":
            target = seg_data.get("qq")
        elif seg_type == "mention":
            target = seg_data.get("target")
        if target is not None:
            user_ids_to_fetch.add(str(target))


def _collect_user_ids(
    messages: list[dict[str, Any]],
    reply_messages: dict[str, dict[str, Any]],
) -> set[str]:
    user_ids_to_fetch: set[str] = set()
    for msg in messages:
        sender_id = _extract_message_user_id(msg)
        if sender_id:
            user_ids_to_fetch.add(sender_id)
        _collect_user_ids_from_segments(
            _normalize_segments(msg.get("message", [])), user_ids_to_fetch
        )

    for reply_message in reply_messages.values():
        sender_id = _extract_message_user_id(reply_message)
        if sender_id:
            user_ids_to_fetch.add(sender_id)
        _collect_user_ids_from_segments(
            _normalize_segments(reply_message.get("message", [])),
            user_ids_to_fetch,
        )

    return user_ids_to_fetch


def _build_reply_preview(
    reply_id: str,
    reply_messages: dict[str, dict[str, Any]],
    user_info_cache: dict[str, str],
    depth: int,
) -> ReplyPreview | None:
    if depth >= 1:
        return None

    reply_message = reply_messages.get(reply_id)
    if not reply_message:
        return ReplyPreview(
            message_id=reply_id,
            user_id=None,
            name="unknown",
            content="reply unavailable",
        )

    reply_user_id = _extract_message_user_id(reply_message)
    reply_name = _resolve_sender_name(reply_user_id, reply_message, user_info_cache)
    quoted_text, _ = _render_message_segments(
        reply_message,
        user_info_cache,
        reply_messages,
        depth=depth + 1,
    )
    quoted_text = (
        quoted_text or str(reply_message.get("raw_message") or "[empty]").strip()
    )

    return ReplyPreview(
        message_id=reply_id,
        user_id=reply_user_id,
        name=reply_name,
        content=_truncate_text(quoted_text, limit=80),
        timestamp=_extract_message_timestamp(reply_message),
    )


def _extract_reply_segment_id(segment: dict[str, Any]) -> str:
    """兼容 API reply.id 与核心 canonical reply.message_id。"""
    segment_data = segment.get("data", {})
    return str(segment_data.get("id") or segment_data.get("message_id") or "").strip()


def _render_message_segments(
    message: dict[str, Any],
    user_info_cache: dict[str, str],
    reply_messages: dict[str, dict[str, Any]],
    depth: int = 0,
) -> tuple[str, ReplyPreview | None]:
    text_segments: list[str] = []
    reply_preview: ReplyPreview | None = None

    for segment in _normalize_segments(message.get("message", [])):
        seg_type = segment.get("type")
        if seg_type == "reply":
            reply_id = _extract_reply_segment_id(segment)
            if not reply_id:
                text_segments.append("[回复消息]")
                continue
            reply_preview = reply_preview or _build_reply_preview(
                reply_id,
                reply_messages,
                user_info_cache,
                depth=depth,
            )
            if not reply_preview:
                text_segments.append("[回复消息]")
            continue

        seg_text = _segment_to_text(segment, user_info_cache)
        if seg_text:
            text_segments.append(seg_text)

    message_content = _compact_whitespace(
        _replace_inline_image_markup(" ".join(text_segments))
    )
    return message_content, reply_preview


async def process_message(
    messages: list[dict[str, Any]],
    bot: Bot,
    group_id: int,
) -> tuple[list[ProcessedMessage], dict[str, str]]:
    logger.debug(
        f"开始处理群 {group_id} 的 {len(messages)} 条原始消息",
        command="消息处理",
        group_id=group_id,
    )
    try:
        if not messages:
            return [], {}

        exclude_bot = base_config.get("EXCLUDE_BOT_MESSAGES", False)
        bot_self_id = str(bot.self_id)
        reply_messages = await _prefetch_reply_messages(bot, messages, group_id)
        user_info_cache = await _build_user_info_cache(
            bot,
            group_id,
            _collect_user_ids(messages, reply_messages),
        )

        processed_log: list[ProcessedMessage] = []
        for msg in _sort_raw_messages(messages):
            user_id = _extract_message_user_id(msg)
            if not user_id:
                continue
            if exclude_bot and user_id == bot_self_id:
                continue

            sender_name = _resolve_sender_name(user_id, msg, user_info_cache)
            plain_content, reply_preview = _render_message_segments(
                msg,
                user_info_cache,
                reply_messages,
            )
            if not plain_content and not reply_preview:
                continue

            processed_log.append(
                ProcessedMessage(
                    user_id=user_id,
                    name=sender_name,
                    timestamp=_extract_message_timestamp(msg),
                    plain_content=plain_content,
                    message_id=_extract_message_id(msg),
                    reply=reply_preview,
                )
            )

        logger.debug(
            f"消息处理完成，生成 {len(processed_log)} 条处理记录 "
            f"(已应用Bot排除设置: {exclude_bot})",
            group_id=group_id,
        )
        return processed_log, user_info_cache

    except Exception as e:
        logger.error(
            f"处理群 {group_id} 消息时出错: {e}",
            command="消息处理",
            e=e,
            group_id=group_id,
        )
        raise SummaryException(
            message=f"消息处理失败: {e!s}",
            code=ErrorCode.MESSAGE_PROCESS_FAILED,
            details={
                "error": str(e),
                "group_id": group_id,
                "message_count": len(messages) if messages else 0,
            },
            cause=e,
        ) from e


def build_export_text(
    messages: list[ProcessedMessage],
    group_id: int,
    scope: SummaryScope,
    source: str,
    warning_message: str | None = None,
) -> str:
    lines = [
        f"scope: {_compact_whitespace(scope.label)}",
        f"source: {source}",
    ]
    if warning_message:
        lines.append(f"warn: {_compact_whitespace(warning_message)}")

    tz = get_scope_timezone()
    for message in messages:
        lines.append(_serialize_message_for_export(message, tz))

    return "\n".join(line for line in lines if line) + "\n"


def _user_label(name: str | None, user_id: str | None) -> str:
    clean_name = _compact_whitespace(name or "")
    clean_user_id = _compact_whitespace(user_id or "")
    if clean_name and clean_user_id and clean_name != clean_user_id:
        return f"{clean_name}({clean_user_id})"
    return clean_name or clean_user_id or "unknown"


def _message_body_for_ai(message: ProcessedMessage) -> str:
    body = _compact_whitespace(message.plain_content)
    if message.reply:
        reply_user = _user_label(message.reply.name, message.reply.user_id)
        reply_text = _truncate_text(message.reply.content, limit=80)
        reply_part = f"> {reply_user}: {reply_text}"
        if body:
            return f"{reply_part} | {body}"
        return reply_part
    return body or "[empty]"


def _serialize_message_for_export(message: ProcessedMessage, tz) -> str:
    message_time = datetime.fromtimestamp(message.timestamp, tz).strftime(
        "%m-%d %H:%M:%S"
    )
    return (
        f"[{message_time}] "
        f"{_user_label(message.name, message.user_id)}: "
        f"{_message_body_for_ai(message)}"
    )


def serialize_messages_for_summary(messages: list[ProcessedMessage]) -> str:
    return "\n".join(
        f"{_user_label(message.name, message.user_id)}: {_message_body_for_ai(message)}"
        for message in messages
    )


async def check_message_count(
    messages: list[Any],
    min_count: int | None = None,
) -> bool:
    try:
        if not messages:
            return False

        if min_count is None:
            min_len = base_config.get("SUMMARY_MIN_LENGTH")
            max_len = base_config.get("SUMMARY_MAX_LENGTH")
            if min_len is None or max_len is None:
                logger.warning(
                    "无法从配置获取 SUMMARY_MIN/MAX_LENGTH，使用默认检查值 (50)"
                )
                min_count = 50
            else:
                try:
                    min_count = min(int(min_len), int(max_len))
                except (ValueError, TypeError):
                    logger.warning(
                        "配置 SUMMARY_MIN/MAX_LENGTH 值无效，使用默认检查值 (50)"
                    )
                    min_count = 50

        return len(messages) >= min_count
    except Exception as e:
        logger.error(f"检查消息数量时出错: {e}", command="check_message_count", e=e)
        return False


class AvatarEnhancer:
    """头像增强器，为总结内容中的用户名添加头像"""

    def __init__(self):
        self.avatar_cache: dict[str, str | None] = {}
        self.avatar_dir = TEMP_PATH / "summary_group" / "avatar"
        self.avatar_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"头像缓存目录: {self.avatar_dir}")

        try:
            self.clear_avatar_files()
        except Exception as e:
            logger.warning(f"启动时清理过期头像文件失败: {e}")

    async def enhance_summary_with_avatars(
        self, summary_text: str, user_info_cache: dict[str, str]
    ) -> None:
        """
        在总结文本中为用户名添加头像或高亮。
        此方法现在只负责准备数据（如下载头像），不再返回增强后的文本。
        """
        try:
            name_to_id = {name: uid for uid, name in user_info_cache.items()}
            mentioned_users = self._find_mentioned_users(summary_text, name_to_id)

            if not mentioned_users:
                logger.debug("总结中未发现提及的用户，跳过头像下载。")
                return

            use_avatars = base_config.get("ENABLE_AVATAR_ENHANCEMENT", False)
            if use_avatars:
                max_avatars = summary_config.get_avatar_max_count()
                if len(mentioned_users) > max_avatars:
                    logger.info(
                        f"提及用户数量 ({len(mentioned_users)}) "
                        f"超过建议值 ({max_avatars})，继续处理所有用户"
                    )

                avatar_io_tasks = await self._fetch_avatars_to_files(mentioned_users)
                if avatar_io_tasks:
                    logger.debug(f"等待 {len(avatar_io_tasks)} 个头像I/O任务完成...")
                    await asyncio.gather(*avatar_io_tasks, return_exceptions=True)
                    logger.debug("所有头像I/O任务已完成。")

        except Exception as e:
            logger.warning(f"准备头像数据时失败: {e}")

    def _get_all_valid_mentions(
        self, text: str, name_to_id: dict[str, str]
    ) -> list[tuple[re.Match, str, str]]:
        """
        [新增] 统一的核心用户识别函数。
        使用强大的正则表达式和启发式规则，返回所有有效提及的详细信息。
        返回: 列表，每个元素为 (匹配对象, 用户ID, 用户名)
        """
        valid_mentions: list[tuple[re.Match, str, str]] = []
        all_user_names = sorted(name_to_id.keys(), key=len, reverse=True)
        if not all_user_names:
            return []

        pattern = re.compile(
            r"(?<!\w)(@?)(" + "|".join(map(re.escape, all_user_names)) + r")(?!\w)"
        )

        for match in pattern.finditer(text):
            user_name = match.group(2)
            user_id = name_to_id.get(user_name)

            if user_id and self._is_likely_a_user_mention(match, text, user_name):
                valid_mentions.append((match, user_id, user_name))

        return valid_mentions

    def _find_mentioned_users(
        self, text: str, name_to_id: dict[str, str]
    ) -> dict[str, str]:
        """
        [重构] 查找文本中提及的用户。现在调用统一的核心识别函数。
        """
        mentioned: dict[str, str] = {}
        valid_mentions = self._get_all_valid_mentions(text, name_to_id)

        for _, user_id, user_name in valid_mentions:
            if user_id not in mentioned:
                mentioned[user_id] = user_name
                logger.debug(f"发现提及用户: {user_name} (ID: {user_id})")

        return mentioned

    def _should_skip_username(self, user_name: str) -> bool:
        """判断是否应该跳过处理该用户名"""
        if len(user_name) == 1:
            special_chars = set(".,;:!?@#$%^&*()[]{}|\\/<>-_=+`~\"' ")
            if user_name in special_chars:
                return True

        return False

    def _is_avatar_expired(self, avatar_path: Path) -> bool:
        """检查头像文件是否已过期"""
        try:
            import time

            from ..config import summary_config

            expire_days = summary_config.get_avatar_cache_expire_days()
            current_time = time.time()
            cutoff_time = current_time - (expire_days * 24 * 60 * 60)

            return avatar_path.stat().st_mtime < cutoff_time
        except Exception as e:
            logger.warning(f"检查头像文件过期状态时出错: {e}")
            return False

    @Retry.download(
        stop_max_attempt=3,
        log_name="获取用户头像",
        return_on_failure=None,
    )
    async def _fetch_avatar_with_retry(self, user_id: str) -> str | None:
        """带重试机制的头像获取并保存到本地文件 (增加强制同步)"""
        avatar_file = self.avatar_dir / f"{user_id}.jpg"

        if avatar_file.exists():
            if self._is_avatar_expired(avatar_file):
                logger.debug(
                    f"用户 {user_id} 头像文件已过期，将重新获取: {avatar_file}"
                )
                try:
                    avatar_file.unlink()
                except Exception as e:
                    logger.warning(f"删除过期头像文件失败: {e}")
            else:
                logger.debug(f"用户 {user_id} 头像文件已存在且未过期: {avatar_file}")
                return str(avatar_file)

        avatar_bytes = await get_user_avatar(user_id)
        if avatar_bytes:
            async with aiofiles.open(avatar_file, "wb") as f:
                await f.write(avatar_bytes)
                await f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as e:
                    logger.warning(
                        f"os.fsync for {avatar_file} failed: {e}. "
                        f"The flush() should be sufficient in most cases."
                    )
            logger.debug(f"成功保存并同步了用户 {user_id} 的头像到 {avatar_file}")
            return str(avatar_file)

        logger.debug(f"用户 {user_id} 头像获取失败")
        return None

    async def _fetch_avatars_to_files(
        self, mentioned_users: dict[str, str]
    ) -> list[asyncio.Task]:
        """
        并发批量获取用户头像并保存到本地文件。
        【修改】: 此方法现在返回一个包含所有文件写入任务的列表。
        """
        users_to_fetch = {}

        for user_id, user_name in mentioned_users.items():
            need_fetch = False

            if user_id not in self.avatar_cache:
                need_fetch = True
                logger.debug(f"用户 {user_id} 不在缓存中，需要获取")
            elif self.avatar_cache[user_id] is None:
                need_fetch = True
                logger.debug(f"用户 {user_id} 缓存为 None，需要重新获取")
            else:
                avatar_path_str = self.avatar_cache[user_id]
                avatar_path = Path(avatar_path_str)
                if not _path_exists(avatar_path_str):
                    need_fetch = True
                    logger.debug(
                        f"用户 {user_id} 缓存的文件不存在，需要重新获取: {avatar_path}"
                    )
                else:
                    if self._is_avatar_expired(avatar_path):
                        need_fetch = True
                        logger.debug(
                            f"用户 {user_id} 头像文件已过期，"
                            f"需要重新获取: {avatar_path}"
                        )
                    else:
                        logger.debug(
                            f"用户 {user_id} 头像文件已存在且未过期: {avatar_path}"
                        )

            if need_fetch:
                users_to_fetch[user_id] = user_name

        if not users_to_fetch:
            logger.debug("所有用户头像都已缓存且文件存在，跳过获取")
            return []

        logger.debug(
            f"开始创建 {len(users_to_fetch)} 个用户的头像获取任务: "
            f"{list(users_to_fetch.keys())}"
        )

        max_concurrent = min(5, len(users_to_fetch))
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_and_cache_avatar(user_id: str):
            async with semaphore:
                try:
                    avatar_path = await self._fetch_avatar_with_retry(user_id)
                    self.avatar_cache[user_id] = avatar_path
                    if avatar_path:
                        logger.debug(f"任务成功: 用户 {user_id} -> {avatar_path}")
                    else:
                        logger.debug(f"任务失败: 用户 {user_id} -> None")
                except Exception as e:
                    logger.warning(f"获取用户 {user_id} 头像的任务中发生异常: {e}")
                    self.avatar_cache[user_id] = None

        tasks = [
            asyncio.create_task(fetch_and_cache_avatar(user_id))
            for user_id in users_to_fetch.keys()
        ]

        return tasks

    def _create_user_with_avatar_html(
        self, user_name: str, avatar_file_path: str
    ) -> str:
        """创建带头像的用户名HTML"""
        escaped_name = (
            user_name.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        )
        abs_path = Path(avatar_file_path).resolve()
        file_url = abs_path.as_uri()
        return (
            f'<span class="user-mention with-avatar">'
            f'<img src="{file_url}" alt="{escaped_name}" class="user-avatar" />'
            f'<span class="user-name">{escaped_name}</span>'
            f"</span>"
        )

    def _create_user_mention_html(self, user_name: str) -> str:
        """创建仅高亮的用户名HTML"""
        escaped_name = (
            user_name.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return f'<span class="user-mention">{escaped_name}</span>'

    def _create_user_without_avatar_html(self, user_name: str) -> str:
        """创建无头像的用户名HTML"""
        escaped_name = (
            user_name.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return f'<span class="user-mention no-avatar">{escaped_name}</span>'

    def clear_cache(self):
        """清空头像缓存"""
        self.avatar_cache.clear()
        logger.debug("头像缓存已清空")

    def clear_avatar_files(self, keep_recent_days: int | None = None):
        """清理头像文件"""
        try:
            import time

            from ..config import summary_config

            if keep_recent_days is None:
                keep_recent_days = summary_config.get_avatar_cache_expire_days()

            current_time = time.time()
            cutoff_time = current_time - (keep_recent_days * 24 * 60 * 60)

            deleted_count = 0
            for avatar_file in self.avatar_dir.glob("*.jpg"):
                if avatar_file.stat().st_mtime < cutoff_time:
                    avatar_file.unlink()
                    deleted_count += 1
                    user_id = avatar_file.stem
                    if user_id in self.avatar_cache:
                        del self.avatar_cache[user_id]

            logger.debug(
                f"清理了 {deleted_count} 个过期头像文件 (保留 {keep_recent_days} 天)"
            )

        except Exception as e:
            logger.warning(f"清理头像文件时出错: {e}")

    def enhance_html_with_markup(
        self,
        html_content: str,
        user_info_cache: dict[str, str],
        mode: str,
    ) -> str:
        """[重构] 使用统一的核心识别函数来增强HTML"""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_content, "lxml")
            name_to_id = {name: uid for uid, name in user_info_cache.items()}

            text_nodes = soup.find_all(string=True)

            for node in text_nodes:
                if node.parent.name in ["script", "style", "a"]:
                    continue

                original_text = str(node)

                valid_mentions = self._get_all_valid_mentions(original_text, name_to_id)
                if not valid_mentions:
                    continue

                new_parts = []
                last_index = 0
                for match, user_id, user_name in valid_mentions:
                    new_parts.append(original_text[last_index : match.start()])

                    replacement_html = ""
                    if mode == "avatar":
                        avatar_path = self.avatar_cache.get(user_id)
                        if avatar_path and Path(avatar_path).exists():
                            replacement_html = self._create_user_with_avatar_html(
                                user_name, avatar_path
                            )
                        else:
                            replacement_html = self._create_user_without_avatar_html(
                                user_name
                            )
                    else:
                        replacement_html = self._create_user_mention_html(user_name)

                    new_parts.append(replacement_html)
                    last_index = match.end()

                new_parts.append(original_text[last_index:])

                new_html_str = "".join(new_parts)
                if new_html_str != original_text:
                    new_soup = BeautifulSoup(new_html_str, "html.parser")
                    node.replace_with(*new_soup.contents)

            return str(soup)

        except Exception as e:
            logger.warning(f"在HTML中增强用户名时失败，将返回原始HTML: {e}", e=e)
            return html_content

    def _is_likely_a_user_mention(
        self, match: re.Match, full_text: str, user_name: str
    ) -> bool:
        """
        [重构] 更精细的启发式规则，用于判断一个匹配到的字符串是否真的是一个用户名。
        """
        if match.group(1) or (
            match.end() < len(full_text) and full_text[match.end()] in (":", "：")
        ):
            return True

        exclusion_patterns = [f"吃{user_name}", f"是{user_name}", f"的{user_name}"]
        context_window = full_text[
            max(0, match.start() - 2) : min(len(full_text), match.end() + 2)
        ]
        if any(p in context_window for p in exclusion_patterns):
            logger.debug(f"跳过对 '{user_name}' 的替换，因为它出现在排除模式中。")
            return False

        if len(user_name) >= 4:
            return True
        if len(user_name) == 1:
            logger.debug(f"跳过对单字符名称 '{user_name}' 的替换，风险太高。")
            return False

        return True


avatar_enhancer = AvatarEnhancer()
