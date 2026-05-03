from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import random
import re
import time
from typing import Any

import nonebot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
import websockets

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.services.cache.runtime_cache import (
    BotMemoryCache,
    GroupMemoryCache,
    PluginInfoMemoryCache,
    _parse_block_modules,
)
from zhenxun.services.external_onebot_gateway_config import (
    ContentFilterSettings,
    ExternalOneBotAppSettings,
    IdFilterSettings,
)
from zhenxun.services.log import logger
from zhenxun.utils.enum import BlockType

LOG_COMMAND = "ExternalOneBotGateway"

_REPLY_CQ_PATTERN = re.compile(r"\[CQ:reply,(?:[^\]]*?,)?id=([^,\]]+)")
_TEXT_WHITESPACE_PATTERN = re.compile(r"^(\s*)(.*)$", re.S)
_ECHO_SOURCE_MAX_SIZE = 10000
_SUPERUSER_NOTICE_TEXT_LIMIT = 120


class _OneBotSelfIdUnavailable(RuntimeError):
    """Local OneBot adapter has not connected yet, so lifecycle self_id is unknown."""

    pass


@dataclass(frozen=True)
class ExternalOneBotAppSpec:
    name: str
    display_name: str
    plugin_module: str
    settings_factory: Callable[[], ExternalOneBotAppSettings]


@dataclass(frozen=True)
class QueuedOneBotEvent:
    bot_self_id: str
    event: MessageEvent


@dataclass(frozen=True)
class BuiltOneBotEvent:
    """OneBot event payload plus local-only metadata used for attribution."""

    payload: dict[str, Any]
    auto_slash_applied: bool
    source_plain_text: str
    echo_suspect: bool
    echo_source_gap_seconds: float | None


@dataclass(frozen=True)
class AttributionRecord:
    message_id: str
    user_id: str
    group_id: str | None
    source_bot_self_id: str
    plugin_module: str
    app_name: str
    auto_slash_applied: bool
    source_plain_text: str
    echo_suspect: bool
    echo_source_gap_seconds: float | None
    expires_at: float


@dataclass
class _AutoSlashFuseState:
    timestamps: deque[float] = field(default_factory=deque)
    seen_reply_ids: dict[str, float] = field(default_factory=dict)
    suspend_until: float = 0.0


@dataclass(frozen=True)
class AutoSlashFuseTrigger:
    suspend_until: float


class AutoSlashFuse:
    """Track users whose same-source auto-slash replies look like a feedback loop."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], _AutoSlashFuseState] = {}

    def is_suspended(
        self,
        *,
        app_name: str,
        user_id: str | int,
        enabled: bool,
        now: float | None = None,
    ) -> bool:
        # 熔断只影响 auto-slash 开关，不参与用户/群硬拦截判断。
        if not enabled:
            return False
        current = time.monotonic() if now is None else now
        state = self._states.get((app_name, str(user_id)))
        return bool(state and state.suspend_until > current)

    def record_reply(
        self,
        *,
        attribution: AttributionRecord,
        reply_id: str,
        enabled: bool,
        window_seconds: float,
        max_replies: int,
        suspend_seconds: float,
        now: float | None = None,
    ) -> AutoSlashFuseTrigger | None:
        # 只把 same-source echo 捕获器标记过的 auto-slash 回复计入窗口。
        if (
            not enabled
            or not attribution.auto_slash_applied
            or not attribution.echo_suspect
        ):
            return None
        current = time.monotonic() if now is None else now
        window = max(1.0, float(window_seconds))
        max_count = max(1, int(max_replies))
        suspend = max(1.0, float(suspend_seconds))
        key = (attribution.app_name, attribution.user_id)
        state = self._states.setdefault(key, _AutoSlashFuseState())
        self._prune_state(state, current, window, suspend)

        # 同一个 reply_id 往往代表一次 Haruki 指令的多段回复；只记一次，
        # 否则单次长回复就可能把兜底熔断误打满。
        if reply_id in state.seen_reply_ids:
            return None
        state.seen_reply_ids[reply_id] = current
        if state.suspend_until > current:
            return None

        state.timestamps.append(current)
        if len(state.timestamps) < max_count:
            return None
        state.suspend_until = current + suspend
        state.timestamps.clear()
        return AutoSlashFuseTrigger(suspend_until=state.suspend_until)

    def sweep(
        self,
        *,
        window_seconds: float,
        suspend_seconds: float,
        now: float | None = None,
    ) -> int:
        current = time.monotonic() if now is None else now
        window = max(1.0, float(window_seconds))
        suspend = max(1.0, float(suspend_seconds))
        removed = 0
        for key, state in list(self._states.items()):
            self._prune_state(state, current, window, suspend)
            if (
                not state.timestamps
                and not state.seen_reply_ids
                and state.suspend_until <= current
            ):
                self._states.pop(key, None)
                removed += 1
        return removed

    def clear(self) -> None:
        self._states.clear()

    def __len__(self) -> int:
        return len(self._states)

    @staticmethod
    def _prune_state(
        state: _AutoSlashFuseState,
        current: float,
        window_seconds: float,
        suspend_seconds: float,
    ) -> None:
        while state.timestamps and current - state.timestamps[0] > window_seconds:
            state.timestamps.popleft()
        reply_ttl = max(window_seconds, suspend_seconds)
        for reply_id, seen_at in list(state.seen_reply_ids.items()):
            if current - seen_at > reply_ttl:
                state.seen_reply_ids.pop(reply_id, None)


@dataclass(frozen=True)
class EchoSourceRecord:
    sent_at: float
    source_message_id: str
    source_auto_slash_applied: bool


class EchoSourceTracker:
    def __init__(self, max_size: int = _ECHO_SOURCE_MAX_SIZE) -> None:
        self.max_size = max(1, int(max_size))
        self._items: dict[tuple[str, str], EchoSourceRecord] = {}
        self._order: deque[tuple[tuple[str, str], float]] = deque()

    def put(
        self,
        *,
        group_id: str | int | None,
        user_id: str | int | None,
        source_message_id: str,
        source_auto_slash_applied: bool,
        now: float | None = None,
    ) -> None:
        if group_id is None or user_id is None:
            return
        current = time.monotonic() if now is None else now
        key = (str(group_id), str(user_id))
        record = EchoSourceRecord(
            sent_at=current,
            source_message_id=str(source_message_id),
            source_auto_slash_applied=source_auto_slash_applied,
        )
        self._items[key] = record
        self._order.append((key, current))
        self._evict_overflow()

    def get(
        self,
        *,
        group_id: str | int | None,
        user_id: str | int | None,
        ttl_seconds: float,
        now: float | None = None,
    ) -> EchoSourceRecord | None:
        if group_id is None or user_id is None:
            return None
        current = time.monotonic() if now is None else now
        key = (str(group_id), str(user_id))
        record = self._items.get(key)
        if record is None:
            return None
        if current - record.sent_at > max(1.0, float(ttl_seconds)):
            self._items.pop(key, None)
            return None
        return record

    def sweep(self, *, ttl_seconds: float, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        ttl = max(1.0, float(ttl_seconds))
        removed = 0
        while self._order:
            key, sent_at = self._order[0]
            if current - sent_at <= ttl:
                break
            self._order.popleft()
            record = self._items.get(key)
            if record is not None and record.sent_at == sent_at:
                self._items.pop(key, None)
                removed += 1
        return removed

    def clear(self) -> None:
        self._items.clear()
        self._order.clear()

    def __len__(self) -> int:
        return len(self._items)

    def _evict_overflow(self) -> None:
        while len(self._items) > self.max_size and self._order:
            key, sent_at = self._order.popleft()
            record = self._items.get(key)
            if record is not None and record.sent_at == sent_at:
                self._items.pop(key, None)


class AttributionCache:
    def __init__(self, ttl_seconds: float, max_size: int) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_size = max(1, int(max_size))
        self._items: dict[tuple[str, str], AttributionRecord] = {}
        self._order: deque[tuple[tuple[str, str], float]] = deque()
        self._last_overflow_warning = 0.0

    def configure(self, ttl_seconds: float, max_size: int) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_size = max(1, int(max_size))

    def put(
        self,
        message_id: str,
        *,
        user_id: str,
        group_id: str | None,
        source_bot_self_id: str,
        plugin_module: str,
        app_name: str,
        auto_slash_applied: bool = False,
        source_plain_text: str = "",
        echo_suspect: bool = False,
        echo_source_gap_seconds: float | None = None,
        now: float | None = None,
    ) -> None:
        if not message_id:
            return
        current = time.monotonic() if now is None else now
        expires_at = current + self.ttl_seconds
        key = (str(source_bot_self_id), str(message_id))
        record = AttributionRecord(
            message_id=str(message_id),
            user_id=user_id,
            group_id=group_id,
            source_bot_self_id=source_bot_self_id,
            plugin_module=plugin_module,
            app_name=app_name,
            auto_slash_applied=auto_slash_applied,
            source_plain_text=source_plain_text,
            echo_suspect=echo_suspect,
            echo_source_gap_seconds=echo_source_gap_seconds,
            expires_at=expires_at,
        )
        self._items[key] = record
        self._order.append((key, expires_at))
        self._evict_overflow(current)

    def get(
        self,
        message_id: str,
        *,
        group_id: str | int | None = None,
        source_bot_self_id: str | int | None = None,
        now: float | None = None,
    ) -> AttributionRecord | None:
        current = time.monotonic() if now is None else now
        normalized_message_id = str(message_id)
        normalized_group_id = str(group_id) if group_id is not None else None
        if source_bot_self_id is not None:
            key = (str(source_bot_self_id), normalized_message_id)
            record = self._items.get(key)
            if record is None:
                return None
            if record.expires_at <= current:
                self._items.pop(key, None)
                return None
            if normalized_group_id and record.group_id != normalized_group_id:
                return None
            return record

        candidates: list[AttributionRecord] = []
        for (bot_self_id, cached_message_id), record in list(self._items.items()):
            if cached_message_id != normalized_message_id:
                continue
            if record.expires_at <= current:
                self._items.pop((bot_self_id, cached_message_id), None)
                continue
            if normalized_group_id and record.group_id != normalized_group_id:
                continue
            candidates.append(record)

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # 多 bot 或协议端 message_id 碰撞时不强行猜测归属，避免把统计记到错误用户。
        return None

    def sweep(self, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        removed = 0
        while self._order:
            key, expires_at = self._order[0]
            if expires_at > current:
                break
            self._order.popleft()
            record = self._items.get(key)
            if record is not None and record.expires_at == expires_at:
                self._items.pop(key, None)
                removed += 1
        return removed

    def clear(self) -> None:
        self._items.clear()
        self._order.clear()

    def __len__(self) -> int:
        return len(self._items)

    def _evict_overflow(self, now: float) -> None:
        removed = 0
        while len(self._items) > self.max_size and self._order:
            key, expires_at = self._order.popleft()
            record = self._items.get(key)
            if record is not None and record.expires_at == expires_at:
                self._items.pop(key, None)
                removed += 1

        if removed and now - self._last_overflow_warning >= 30:
            self._last_overflow_warning = now
            logger.warning(
                f"归因缓存超过上限，已按 FIFO 驱逐 {removed} 条记录。",
                LOG_COMMAND,
            )


def _coerce_onebot_id(value: str | int | None) -> int | str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


def _copy_message_segments(message: Any) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if isinstance(message, list):
        source = message
    else:
        source = list(message or [])
    for segment in source:
        if isinstance(segment, dict):
            segment_type = segment.get("type")
            data = segment.get("data") or {}
        else:
            segment_type = getattr(segment, "type", None)
            data = getattr(segment, "data", {}) or {}
        if not segment_type:
            continue
        segments.append({"type": str(segment_type), "data": dict(data)})
    return segments


def _message_plain_text(segments: list[dict[str, Any]]) -> str:
    return "".join(
        str(segment.get("data", {}).get("text", ""))
        for segment in segments
        if segment.get("type") == "text"
    )


def _find_first_text_segment(
    segments: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    for segment in segments:
        if segment.get("type") != "text":
            continue
        data = segment.setdefault("data", {})
        text = str(data.get("text") or "")
        if text.strip():
            return segment, text
    return None


def apply_extra_prefix_policy(
    segments: list[dict[str, Any]],
    *,
    extra_prefixes: tuple[str, ...],
    prefix_replace: str,
) -> bool:
    found = _find_first_text_segment(segments)
    if not found:
        return False
    segment, original_text = found
    match = _TEXT_WHITESPACE_PATTERN.match(original_text)
    if match is None:
        return False
    leading, stripped_text = match.groups()
    for prefix in extra_prefixes:
        if not stripped_text.startswith(prefix):
            continue
        segment["data"]["text"] = (
            f"{leading}{prefix_replace}{stripped_text[len(prefix):]}"
        )
        return True
    return False


def apply_auto_slash(
    segments: list[dict[str, Any]], *, enabled: bool
) -> bool:
    if not enabled:
        return False
    found = _find_first_text_segment(segments)
    if not found:
        return False
    segment, original_text = found
    match = _TEXT_WHITESPACE_PATTERN.match(original_text)
    if match is None:
        return False
    leading, stripped_text = match.groups()
    if not stripped_text or stripped_text.startswith("/"):
        return False
    segment["data"]["text"] = f"{leading}/{stripped_text}"
    return True


def apply_token_rewrites(
    segments: list[dict[str, Any]], token_rewrites: dict[str, str]
) -> bool:
    if not token_rewrites:
        return False
    found = _find_first_text_segment(segments)
    if not found:
        return False
    segment, original_text = found
    match = _TEXT_WHITESPACE_PATTERN.match(original_text)
    if match is None:
        return False
    leading, stripped_text = match.groups()
    token, separator, rest = stripped_text.partition(" ")
    target = token_rewrites.get(token)
    if target is None:
        return False
    segment["data"]["text"] = f"{leading}{target}{separator}{rest}"
    return True


def id_filter_allows(
    filter_settings: IdFilterSettings,
    value: str | int | None,
) -> bool:
    if value is None:
        return filter_settings.mode != "whitelist"
    normalized = str(value)
    matched = normalized in filter_settings.ids
    return matched if filter_settings.mode == "whitelist" else not matched


def content_filter_allows(
    filter_settings: ContentFilterSettings,
    plain_text: str,
    *,
    bypass: bool = False,
) -> bool:
    if bypass:
        return True
    if filter_settings.mode == "on":
        return True
    if filter_settings.mode == "off":
        return False
    matched = any(
        re.search(pattern, plain_text) for pattern in filter_settings.patterns
    )
    if filter_settings.mode == "whitelist":
        return matched
    return not matched


def should_apply_auto_slash(
    settings: ExternalOneBotAppSettings,
    group_id: str | int | None,
) -> bool:
    if not settings.enable_auto_slash:
        return False
    if group_id is None:
        return True
    return str(group_id) not in settings.auto_slash_disabled_group_ids


def extract_reply_id(message: Any) -> str | None:
    if isinstance(message, str):
        match = _REPLY_CQ_PATTERN.search(message)
        return match.group(1) if match else None
    if not isinstance(message, list):
        return None
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "reply":
            continue
        data = segment.get("data") or {}
        reply_id = data.get("id")
        if reply_id is not None:
            return str(reply_id)
    return None


def normalize_action_message(message: Any) -> Any:
    if isinstance(message, Message | str) or message is None:
        return message
    if not isinstance(message, list):
        return message

    segments: list[MessageSegment] = []
    for segment in message:
        if isinstance(segment, MessageSegment):
            segments.append(segment)
            continue
        if not isinstance(segment, dict):
            return message
        segment_type = segment.get("type")
        if not segment_type:
            return message
        data = segment.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        segments.append(MessageSegment(str(segment_type), dict(data)))
    return Message(segments)


def build_action_response(
    *,
    echo: Any = None,
    ok: bool,
    data: Any = None,
    message: str = "",
    retcode: int | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": "ok" if ok else "failed",
        "retcode": 0 if ok else (retcode or 100),
        "data": data,
    }
    if not ok:
        response["msg"] = message or "failed"
        response["wording"] = message or "failed"
    if echo is not None:
        response["echo"] = echo
    return response


class ExternalOneBotSession:
    def __init__(self, spec: ExternalOneBotAppSpec) -> None:
        self.spec = spec
        settings = spec.settings_factory()
        self._attribution = AttributionCache(
            settings.attribution_ttl_seconds,
            settings.attribution_max_size,
        )
        self._auto_slash_fuse = AutoSlashFuse()
        self._echo_source_tracker = EchoSourceTracker()
        self._event_queue: asyncio.Queue[QueuedOneBotEvent] = asyncio.Queue(
            maxsize=settings.event_queue_max_size
        )
        self._connection_task: asyncio.Task[None] | None = None
        self._sweep_task: asyncio.Task[None] | None = None
        self._sender_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._ws: Any | None = None
        self._runtime_self_id: int | str | None = None
        self._connected = asyncio.Event()
        self._closing = False
        self._last_log_times: dict[str, float] = {}

    def update_spec(self, spec: ExternalOneBotAppSpec) -> None:
        self.spec = spec

    def submit_event(self, bot: OneBotV11Bot, event: MessageEvent) -> bool:
        if not self._connected.is_set() or self._ws is None:
            self._log_throttled(
                "drop_disconnected",
                f"{self.spec.display_name} 未连接，已丢弃本次转发。",
                level="warning",
            )
            return False
        try:
            self._event_queue.put_nowait(
                QueuedOneBotEvent(bot_self_id=str(bot.self_id), event=event)
            )
        except asyncio.QueueFull:
            self._log_throttled(
                "drop_queue_full",
                f"{self.spec.display_name} 转发队列已满，已丢弃新消息。",
                level="warning",
            )
            return False
        return True

    async def start(self) -> None:
        self._closing = False
        if self._connection_task is None or self._connection_task.done():
            self._connection_task = asyncio.create_task(
                self._connection_loop(),
                name=f"external-onebot-{self.spec.name}-connection",
            )
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(
                self._sweep_loop(),
                name=f"external-onebot-{self.spec.name}-attribution-sweep",
            )

    async def close(self) -> None:
        self._closing = True
        tasks = [
            task
            for task in (
                self._connection_task,
                self._sweep_task,
                self._sender_task,
                self._heartbeat_task,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._connection_task = None
        self._sweep_task = None
        self._sender_task = None
        self._heartbeat_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        self._connected.clear()
        self._runtime_self_id = None
        self._attribution.clear()
        self._auto_slash_fuse.clear()
        self._echo_source_tracker.clear()
        self._drain_event_queue()

    async def _connection_loop(self) -> None:
        backoff = 1.0
        while not self._closing:
            settings = self.spec.settings_factory()
            try:
                await self._run_connection(settings)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except _OneBotSelfIdUnavailable:
                self._log_throttled(
                    "wait_runtime_self_id",
                    (
                        f"{self.spec.display_name} 等待 OneBot V11 bot 连接后"
                        "再建立外部连接。"
                    ),
                )
                backoff = 1.0
            except Exception as exc:
                self._log_throttled(
                    "connect_failed",
                    f"{self.spec.display_name} 连接异常，将重试。",
                    level="warning",
                    e=exc,
                )

            self._connected.clear()
            self._attribution.clear()
            self._drain_event_queue()
            if self._closing:
                break
            jitter = random.uniform(0, min(backoff, 3.0))
            await asyncio.sleep(backoff + jitter)
            backoff = min(backoff * 2, 60.0)

    async def _run_connection(self, settings: ExternalOneBotAppSettings) -> None:
        headers = (
            {"Authorization": f"Bearer {settings.access_token}"}
            if settings.access_token
            else None
        )
        runtime_self_id = self._resolve_runtime_self_id(settings)
        if runtime_self_id is None:
            raise _OneBotSelfIdUnavailable(
                "no OneBot self_id available for lifecycle event"
            )
        async with websockets.connect(
            settings.ws_url,
            additional_headers=headers,
            proxy=None,
        ) as ws:
            self._ws = ws
            self._runtime_self_id = runtime_self_id
            self._connected.set()
            logger.info(f"{self.spec.display_name} 已连接。", LOG_COMMAND)
            try:
                await self._send_lifecycle_event("connect", settings)
                self._sender_task = asyncio.create_task(
                    self._sender_loop(),
                    name=f"external-onebot-{self.spec.name}-sender",
                )
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(),
                    name=f"external-onebot-{self.spec.name}-heartbeat",
                )
                await self._receive_loop()
            finally:
                # receive_loop 正常结束也代表这条 WS 生命周期结束；同步取消
                # 发送与心跳协程，避免重连后旧协程继续向已关闭连接写入。
                tasks = [
                    task
                    for task in (self._sender_task, self._heartbeat_task)
                    if task is not None
                ]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self._sender_task = None
                self._heartbeat_task = None
                self._ws = None
                self._runtime_self_id = None

    async def _receive_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        async for raw_payload in ws:
            echo = None
            try:
                payload = json.loads(raw_payload)
                if not isinstance(payload, dict):
                    raise ValueError("OneBot action payload must be a JSON object")
                echo = payload.get("echo")
                await self._handle_action(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._send_action_response(
                    build_action_response(
                        echo=echo,
                        ok=False,
                        message=str(exc),
                    )
                )
                self._log_throttled(
                    "action_failed",
                    f"{self.spec.display_name} action 处理失败。",
                    level="warning",
                    e=exc,
                )

    async def _sender_loop(self) -> None:
        while True:
            item = await self._event_queue.get()
            try:
                await self._send_queued_event(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_throttled(
                    "send_event_failed",
                    f"{self.spec.display_name} 事件转发失败。",
                    level="warning",
                    e=exc,
                )
            finally:
                self._event_queue.task_done()

    async def _heartbeat_loop(self) -> None:
        while True:
            settings = self.spec.settings_factory()
            await asyncio.sleep(settings.heartbeat_interval_seconds)
            await self._send_heartbeat_event(settings)

    async def _sweep_loop(self) -> None:
        while True:
            settings = self.spec.settings_factory()
            self._attribution.configure(
                settings.attribution_ttl_seconds,
                settings.attribution_max_size,
            )
            await asyncio.sleep(settings.attribution_sweep_interval_seconds)
            self._attribution.sweep()
            self._auto_slash_fuse.sweep(
                window_seconds=settings.auto_slash_fuse_window_seconds,
                suspend_seconds=settings.auto_slash_fuse_suspend_seconds,
            )
            self._echo_source_tracker.sweep(
                ttl_seconds=settings.auto_slash_fuse_echo_source_seconds
            )

    async def _send_queued_event(self, item: QueuedOneBotEvent) -> None:
        settings = self.spec.settings_factory()
        self._attribution.configure(
            settings.attribution_ttl_seconds,
            settings.attribution_max_size,
        )
        built_event = self._build_event_payload(item, settings)
        if built_event is None:
            return
        payload = built_event.payload
        await self._send_ws(payload)
        message_id = payload.get("message_id")
        if message_id is None:
            return
        self._attribution.put(
            str(message_id),
            user_id=str(payload.get("user_id") or ""),
            group_id=str(payload.get("group_id")) if payload.get("group_id") else None,
            source_bot_self_id=item.bot_self_id,
            plugin_module=self.spec.plugin_module,
            app_name=self.spec.name,
            auto_slash_applied=built_event.auto_slash_applied,
            source_plain_text=built_event.source_plain_text,
            echo_suspect=built_event.echo_suspect,
            echo_source_gap_seconds=built_event.echo_source_gap_seconds,
        )

    def _build_event_payload(
        self,
        item: QueuedOneBotEvent,
        settings: ExternalOneBotAppSettings,
        *,
        now: float | None = None,
    ) -> BuiltOneBotEvent | None:
        current = time.monotonic() if now is None else now
        event = item.event
        if not isinstance(event, GroupMessageEvent | PrivateMessageEvent):
            return None
        if not id_filter_allows(settings.user_filter, event.user_id):
            return None
        group_id = getattr(event, "group_id", None)
        if group_id is not None and not id_filter_allows(
            settings.group_filter,
            group_id,
        ):
            return None

        payload = event.model_dump()
        payload.pop("to_me", None)
        payload.pop("reply", None)
        payload.pop("original_message", None)
        if virtual_self_id := _coerce_onebot_id(
            settings.virtual_self_id or item.bot_self_id
        ):
            payload["self_id"] = virtual_self_id

        segments = _copy_message_segments(payload.get("message"))
        bypass_content_filter = apply_extra_prefix_policy(
            segments,
            extra_prefixes=settings.extra_prefixes,
            prefix_replace=settings.prefix_replace,
        )
        plain_text = _message_plain_text(segments).strip()
        if not plain_text:
            return None
        if not content_filter_allows(
            settings.content_filter,
            plain_text,
            bypass=bypass_content_filter,
        ):
            return None

        auto_slash_enabled = should_apply_auto_slash(settings, group_id)
        if auto_slash_enabled and self._auto_slash_fuse.is_suspended(
            app_name=self.spec.name,
            user_id=event.user_id,
            enabled=settings.auto_slash_fuse_enabled,
            now=current,
        ):
            auto_slash_enabled = False

        # auto-slash 是内容改写策略；分群禁用或用户熔断只跳过补斜杠，
        # 不改变显式 / 指令转发与硬拦截语义。
        auto_slash_applied = apply_auto_slash(
            segments,
            enabled=auto_slash_enabled,
        )
        echo_suspect = False
        echo_source_gap_seconds = None
        if auto_slash_applied:
            echo_source = self._echo_source_tracker.get(
                group_id=group_id,
                user_id=event.user_id,
                ttl_seconds=settings.auto_slash_fuse_echo_source_seconds,
                now=current,
            )
            if echo_source is not None and echo_source.source_auto_slash_applied:
                echo_suspect = True
                echo_source_gap_seconds = max(0.0, current - echo_source.sent_at)
        apply_token_rewrites(segments, settings.token_rewrites)
        payload["message"] = segments
        payload["raw_message"] = str(normalize_action_message(segments))
        return BuiltOneBotEvent(
            payload=payload,
            auto_slash_applied=auto_slash_applied,
            source_plain_text=plain_text,
            echo_suspect=echo_suspect,
            echo_source_gap_seconds=echo_source_gap_seconds,
        )

    async def _send_lifecycle_event(
        self,
        lifecycle_type: str,
        settings: ExternalOneBotAppSettings,
    ) -> None:
        await self._send_ws(
            {
                "time": int(time.time()),
                "self_id": self._runtime_self_id
                or self._resolve_runtime_self_id(settings),
                "post_type": "meta_event",
                "meta_event_type": "lifecycle",
                "sub_type": lifecycle_type,
            }
        )

    async def _send_heartbeat_event(self, settings: ExternalOneBotAppSettings) -> None:
        await self._send_ws(
            {
                "time": int(time.time()),
                "self_id": self._runtime_self_id
                or self._resolve_runtime_self_id(settings),
                "post_type": "meta_event",
                "meta_event_type": "heartbeat",
                "status": {"online": True, "good": True},
                "interval": int(settings.heartbeat_interval_seconds * 1000),
            }
        )

    async def _handle_action(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "").strip()
        echo = payload.get("echo")
        if not action:
            raise ValueError("OneBot action is missing")

        settings = self.spec.settings_factory()
        if action not in settings.action_allowlist:
            await self._send_action_response(
                build_action_response(
                    echo=echo,
                    ok=False,
                    message=f"action {action} is not allowed",
                )
            )
            return
        if action in {"get_status", "get_version_info"}:
            await self._send_action_response(
                build_action_response(
                    echo=echo,
                    ok=True,
                    data=self._build_mock_action_data(action),
                )
            )
            return

        params = payload.get("params")
        if not isinstance(params, dict):
            params = {
                key: value
                for key, value in payload.items()
                if key not in {"action", "echo"}
            }

        reply_id = extract_reply_id(params.get("message"))
        if "message" in params:
            params["message"] = normalize_action_message(params["message"])
        target_group_id = params.get("group_id")
        attribution = (
            self._attribution.get(reply_id, group_id=target_group_id)
            if reply_id
            else None
        )
        bot = self._resolve_action_bot(settings, attribution)
        if bot is None:
            raise RuntimeError("no available OneBot V11 bot for action routing")
        if action.startswith("send") and not await self._can_send_action(
            bot,
            action,
            params,
        ):
            await self._send_action_response(
                build_action_response(
                    echo=echo,
                    ok=False,
                    message=f"{self.spec.display_name} is disabled for this target",
                )
            )
            return

        result = await bot.call_api(action, **params)
        fuse_trigger = None
        if action.startswith("send") and attribution is not None:
            try:
                await self._record_statistics(attribution, bot.self_id)
            except Exception as exc:
                self._log_throttled(
                    "statistics_failed",
                    f"{self.spec.display_name} 调用统计写入失败。",
                    level="warning",
                    e=exc,
                )
            self._record_echo_source(action, params, attribution)
            fuse_trigger = self._auto_slash_fuse.record_reply(
                attribution=attribution,
                reply_id=reply_id or attribution.message_id,
                enabled=settings.auto_slash_fuse_enabled,
                window_seconds=settings.auto_slash_fuse_window_seconds,
                max_replies=settings.auto_slash_fuse_max_replies,
                suspend_seconds=settings.auto_slash_fuse_suspend_seconds,
            )
        await self._send_action_response(
            build_action_response(echo=echo, ok=True, data=result)
        )
        if fuse_trigger is not None and attribution is not None:
            await self._notify_auto_slash_fuse(
                bot=bot,
                attribution=attribution,
                trigger=fuse_trigger,
                settings=settings,
            )

    def _resolve_action_bot(
        self,
        settings: ExternalOneBotAppSettings,
        attribution: AttributionRecord | None,
    ) -> OneBotV11Bot | None:
        candidates = [
            attribution.source_bot_self_id if attribution else "",
            settings.route_bot_self_id,
            settings.virtual_self_id,
        ]
        bots = {
            bot_id: bot
            for bot_id, bot in nonebot.get_bots().items()
            if isinstance(bot, OneBotV11Bot)
        }
        for bot_id in candidates:
            if bot_id and (bot := bots.get(str(bot_id))):
                return bot
        if len(bots) == 1:
            return next(iter(bots.values()))
        return None

    async def _can_send_action(
        self,
        bot: OneBotV11Bot,
        action: str,
        params: dict[str, Any],
    ) -> bool:
        plugin = await self._resolve_plugin_info()
        if plugin is None:
            return False

        if not await self._is_bot_enabled(bot.self_id, plugin.module):
            return False

        group_id = self._resolve_action_group_id(action, params)
        if group_id:
            return await self._is_group_enabled(str(group_id), plugin)
        return self._is_private_enabled(plugin)

    async def _resolve_plugin_info(self) -> PluginInfo | None:
        plugin = await PluginInfoMemoryCache.get_by_module(self.spec.plugin_module)
        if plugin is not None:
            return plugin
        plugin = await PluginInfo.get_plugin(module=self.spec.plugin_module)
        if plugin is not None:
            PluginInfoMemoryCache.set_plugin(plugin)
        return plugin

    async def _is_bot_enabled(self, bot_id: str, plugin_module: str) -> bool:
        bot_snapshot = await BotMemoryCache.get(str(bot_id))
        if bot_snapshot is None:
            return False
        if not bot_snapshot.status:
            return False
        return plugin_module not in _parse_block_modules(bot_snapshot.block_plugins)

    async def _is_group_enabled(self, group_id: str, plugin: PluginInfo) -> bool:
        group_snapshot = await GroupMemoryCache.get(group_id)
        if group_snapshot is None:
            return False
        if group_snapshot.level < 0 or not group_snapshot.status:
            return False
        if plugin.level > group_snapshot.level:
            return False
        if plugin.block_type == BlockType.GROUP:
            return False
        if not plugin.status and plugin.block_type == BlockType.ALL:
            return False
        block_plugins = group_snapshot.block_plugin_set or _parse_block_modules(
            group_snapshot.block_plugin
        )
        super_block_plugins = (
            group_snapshot.superuser_block_plugin_set
            or _parse_block_modules(group_snapshot.superuser_block_plugin)
        )
        return plugin.module not in block_plugins | super_block_plugins

    @staticmethod
    def _is_private_enabled(plugin: PluginInfo) -> bool:
        if plugin.block_type == BlockType.PRIVATE:
            return False
        return plugin.status or plugin.block_type != BlockType.ALL

    @staticmethod
    def _resolve_action_group_id(action: str, params: dict[str, Any]) -> Any:
        if action == "send_group_msg":
            return params.get("group_id")
        if action == "send_msg":
            message_type = str(params.get("message_type") or "").lower()
            if message_type == "group" or params.get("group_id"):
                return params.get("group_id")
        return None

    def _resolve_runtime_self_id(
        self,
        settings: ExternalOneBotAppSettings,
    ) -> int | str | None:
        for bot_id in (settings.virtual_self_id, settings.route_bot_self_id):
            if resolved := _coerce_onebot_id(bot_id):
                return resolved
        onebot_bots = [
            bot
            for bot in nonebot.get_bots().values()
            if isinstance(bot, OneBotV11Bot)
        ]
        if not onebot_bots:
            return None
        return _coerce_onebot_id(onebot_bots[0].self_id)

    async def _record_statistics(
        self,
        attribution: AttributionRecord,
        routed_bot_self_id: str,
    ) -> None:
        await Statistics.create(
            user_id=attribution.user_id,
            group_id=attribution.group_id,
            plugin_name=attribution.plugin_module,
            create_time=datetime.now(),
            bot_id=str(routed_bot_self_id),
        )

    def _record_echo_source(
        self,
        action: str,
        params: dict[str, Any],
        attribution: AttributionRecord,
    ) -> None:
        if not attribution.auto_slash_applied:
            return
        group_id = self._resolve_action_group_id(action, params)
        if not group_id:
            return
        # 每个被回复账号独立维护短时捕获器，避免大群多人协作互相覆盖。
        self._echo_source_tracker.put(
            group_id=group_id,
            user_id=attribution.user_id,
            source_message_id=attribution.message_id,
            source_auto_slash_applied=attribution.auto_slash_applied,
        )

    async def _notify_auto_slash_fuse(
        self,
        *,
        bot: OneBotV11Bot,
        attribution: AttributionRecord,
        trigger: AutoSlashFuseTrigger,
        settings: ExternalOneBotAppSettings,
    ) -> None:
        # 通知是事故定位辅助；任何发送失败都只记录日志，不影响原回复。
        suspend_seconds = max(
            1,
            int(round(trigger.suspend_until - time.monotonic())),
        )
        if settings.auto_slash_fuse_group_notice_enabled and attribution.group_id:
            try:
                await bot.call_api(
                    "send_group_msg",
                    group_id=attribution.group_id,
                    message=(
                        f"{self.spec.display_name} 检测到该账号疑似在响应 "
                        f"{self.spec.display_name} 回复并反复触发自动补 /，"
                        "已暂停该账号自动补 / "
                        f"{suspend_seconds} 秒；"
                        "期间仍可使用 /xxx 指令。"
                    ),
                )
            except Exception as exc:
                self._log_throttled(
                    "auto_slash_fuse_group_notice_failed",
                    f"{self.spec.display_name} auto-slash 熔断群提示发送失败。",
                    level="warning",
                    e=exc,
                )

        if not settings.auto_slash_fuse_superuser_notice_enabled:
            return
        superusers = getattr(driver.config, "superusers", set())
        for superuser_id in sorted(str(uid) for uid in superusers):
            if not superuser_id:
                continue
            try:
                await bot.call_api(
                    "send_private_msg",
                    user_id=superuser_id,
                    message=self._build_auto_slash_fuse_superuser_notice(
                        attribution=attribution,
                        routed_bot_self_id=str(bot.self_id),
                        suspend_seconds=suspend_seconds,
                    ),
                )
            except Exception as exc:
                self._log_throttled(
                    f"auto_slash_fuse_superuser_notice_failed:{superuser_id}",
                    (
                        f"{self.spec.display_name} auto-slash 熔断超级用户"
                        "提示发送失败。"
                    ),
                    level="warning",
                    e=exc,
                )

    def _build_auto_slash_fuse_superuser_notice(
        self,
        *,
        attribution: AttributionRecord,
        routed_bot_self_id: str,
        suspend_seconds: int,
    ) -> str:
        source_text = attribution.source_plain_text.replace("\n", "\\n")
        if len(source_text) > _SUPERUSER_NOTICE_TEXT_LIMIT:
            source_text = f"{source_text[:_SUPERUSER_NOTICE_TEXT_LIMIT]}..."
        return (
            f"{self.spec.display_name} auto-slash 熔断触发\n"
            f"app: {attribution.app_name}\n"
            f"user_id: {attribution.user_id}\n"
            f"group_id: {attribution.group_id or '-'}\n"
            f"bot_id: {routed_bot_self_id}\n"
            f"message_id: {attribution.message_id}\n"
            f"echo_suspect: {attribution.echo_suspect}\n"
            f"echo_gap: {attribution.echo_source_gap_seconds or '-'}\n"
            f"cooldown: {suspend_seconds}s\n"
            f"source: {source_text}"
        )

    def _build_mock_action_data(self, action: str) -> dict[str, Any]:
        if action == "get_version_info":
            return {
                "app_name": "NapCat.OneBot",
                "app_version": "external-onebot-gateway",
                "protocol_version": "v11",
            }
        return {"online": True, "good": True}

    async def _send_action_response(self, payload: dict[str, Any]) -> None:
        await self._send_ws(payload)

    async def _send_ws(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("websocket is not connected")
        await ws.send(json.dumps(payload, ensure_ascii=False))

    def _drain_event_queue(self) -> None:
        while True:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                self._event_queue.task_done()

    def _log_throttled(
        self,
        key: str,
        message: str,
        *,
        level: str = "info",
        interval: float = 30.0,
        e: Exception | None = None,
    ) -> None:
        now = time.monotonic()
        if now - self._last_log_times.get(key, 0.0) < interval:
            return
        self._last_log_times[key] = now
        log_func = logger.warning if level == "warning" else logger.info
        if e is not None:
            log_func(message, LOG_COMMAND, e=e)
        else:
            log_func(message, LOG_COMMAND)


class ExternalOneBotGateway:
    def __init__(self) -> None:
        self._sessions: dict[str, ExternalOneBotSession] = {}

    def register_app(self, spec: ExternalOneBotAppSpec) -> None:
        session = self._sessions.get(spec.name)
        if session is None:
            self._sessions[spec.name] = ExternalOneBotSession(spec)
        else:
            session.update_spec(spec)

    def submit_event(
        self,
        app_name: str,
        bot: OneBotV11Bot,
        event: MessageEvent,
    ) -> bool:
        session = self._sessions.get(app_name)
        if session is None:
            logger.warning(f"未注册外部 OneBot app: {app_name}", LOG_COMMAND)
            return False
        return session.submit_event(bot, event)

    async def start_background_tasks(self) -> None:
        for session in self._sessions.values():
            await session.start()

    async def close(self) -> None:
        await asyncio.gather(
            *(session.close() for session in self._sessions.values()),
            return_exceptions=True,
        )


external_onebot_gateway = ExternalOneBotGateway()

driver = nonebot.get_driver()


@driver.on_startup
async def _start_external_onebot_gateway() -> None:
    try:
        await external_onebot_gateway.start_background_tasks()
    except Exception as exc:
        logger.warning(
            "外部 OneBot 网关后台任务初始化失败，已跳过，不影响主进程启动。",
            LOG_COMMAND,
            e=exc,
        )


driver.on_shutdown(external_onebot_gateway.close)
