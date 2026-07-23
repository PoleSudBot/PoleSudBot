from collections.abc import Awaitable, Callable

from .recorder import (
    ChatHistoryQuery,
    build_incoming_record,
    build_outgoing_record,
    create_outgoing_record,
    enrich_forward_segments,
    normalize_message_segments,
    segments_to_readable_text,
)

PendingHistoryFlusher = Callable[[], Awaitable[int]]
_pending_history_flusher: PendingHistoryFlusher | None = None


def register_pending_history_flusher(flusher: PendingHistoryFlusher) -> None:
    """注册运行时待写队列刷新器，避免服务层反向依赖具体插件。"""
    global _pending_history_flusher
    _pending_history_flusher = flusher


async def flush_pending_history() -> int:
    """尽力刷新当前进程的待写聊天记录；未加载记录插件时安全跳过。"""
    if _pending_history_flusher is None:
        return 0
    return await _pending_history_flusher()

__all__ = [
    "ChatHistoryQuery",
    "build_incoming_record",
    "build_outgoing_record",
    "create_outgoing_record",
    "enrich_forward_segments",
    "flush_pending_history",
    "normalize_message_segments",
    "register_pending_history_flusher",
    "segments_to_readable_text",
]
