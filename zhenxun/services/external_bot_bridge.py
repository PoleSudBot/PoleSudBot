from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import importlib
import importlib.machinery
import json
from pathlib import Path
import random
import sys
import time
from types import ModuleType
from typing import Any

import httpx
import nonebot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11 import MessageEvent as OneBotV11MessageEvent
from nonebot.compat import type_validate_python

from zhenxun.services.log import logger

from .external_bot_bridge_config import get_external_bot_bridge_settings


class ExternalBotBridgeError(Exception):
    """桥接服务基础异常。"""


class BridgeDependencyUnavailable(ExternalBotBridgeError):
    """桥接依赖不可用。"""


class BridgeConnectionTimeout(ExternalBotBridgeError):
    """侧车连接超时。"""


class BridgeResponseTimeout(ExternalBotBridgeError):
    """等待侧车响应超时。"""


class BridgeServiceWarmingUp(ExternalBotBridgeError):
    """侧车已连接但应用层尚未 ready。"""


class BridgeUnsupportedEvent(ExternalBotBridgeError):
    """当前事件不支持桥接。"""


class BridgeUnsupportedResponse(BridgeUnsupportedEvent):
    """侧车返回了当前协议不支持的响应内容。"""


@dataclass(frozen=True)
class _BridgeLibrary:
    gs_client_cls: type[Any]
    message_cls: type[Any]
    message_receive_cls: type[Any]
    message_send_cls: type[Any]
    protocol_cls: type[Any]


@dataclass(frozen=True)
class _SourceRoute:
    wrapper_module: str
    display_name: str


_LIBRARY_CACHE: _BridgeLibrary | None = None
_BRIDGE_VENDOR_PACKAGE = "_zhenxun_external_bot_bridge_genshinuid"

REQUEST_READY_GRACE_SECONDS = 5.0
READY_PROBE_TIMEOUT_SECONDS = 2.0
READY_PROBE_INTERVAL_SECONDS = 0.5


def _get_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _get_vendor_src_paths() -> list[Path]:
    repo_root = _get_repo_root()
    return [
        repo_root
        / "sidecar"
        / ".runtime"
        / "vendors"
        / "nonebot-plugin-genshinuid"
        / "src",
        repo_root
        / ".bridge_runtime"
        / "vendors"
        / "nonebot-plugin-genshinuid"
        / "src",
    ]


def _resolve_bridge_vendor_src() -> Path:
    for vendor_src in _get_vendor_src_paths():
        if not vendor_src.exists():
            continue
        return vendor_src

    raise BridgeDependencyUnavailable(
        "未找到桥接依赖目录，请先执行 `uv run --no-project nbm.py init --install` "
        "或 `uv run --no-project nbm.py prod-setup` 准备 sidecar 运行时目录。"
    )


def _install_isolated_bridge_vendor_package(vendor_src: Path) -> str:
    package_dir = vendor_src / "GenshinUID"
    required_files = [
        package_dir / "sayu_protocol" / "__init__.py",
        package_dir / "protocols" / "__init__.py",
        package_dir / "protocols" / "onebot_v11.py",
        package_dir / "utils.py",
    ]
    if not all(path.exists() for path in required_files):
        raise BridgeDependencyUnavailable(
            "桥接依赖目录不完整，请重新执行 sidecar 运行时安装。"
        )

    # 不能直接 import GenshinUID.*：包根 __init__.py 会注册 NoneBot matcher
    # 和生命周期 hook，导致主进程误加载 sidecar 插件本体。这里用隔离包名
    # 只暴露协议文件目录，让相对导入继续工作但绕开包根副作用。
    module = sys.modules.get(_BRIDGE_VENDOR_PACKAGE)
    if module is None:
        module = ModuleType(_BRIDGE_VENDOR_PACKAGE)
        sys.modules[_BRIDGE_VENDOR_PACKAGE] = module
    else:
        old_paths = list(getattr(module, "__path__", []))
        new_paths = [str(package_dir)]
        if old_paths and old_paths != new_paths:
            # 运行时目录可能被 nbm 重建；清掉旧路径下的子模块，避免继续复用过期协议类。
            for module_name in list(sys.modules):
                if module_name.startswith(f"{_BRIDGE_VENDOR_PACKAGE}."):
                    sys.modules.pop(module_name, None)

    module.__file__ = str(package_dir / "__init__.py")
    module.__package__ = _BRIDGE_VENDOR_PACKAGE
    module.__path__ = [str(package_dir)]
    spec = importlib.machinery.ModuleSpec(
        _BRIDGE_VENDOR_PACKAGE,
        loader=None,
        is_package=True,
    )
    spec.submodule_search_locations = [str(package_dir)]
    module.__spec__ = spec
    return _BRIDGE_VENDOR_PACKAGE


def _build_bridge_gs_client_cls(
    vendor_gs_client_cls: type[Any],
    message_send_cls: type[Any],
) -> type[Any]:
    class BridgeGsClient(vendor_gs_client_cls):
        async def _recv(self) -> None:
            while self._client is not None and self._client.open:
                payload = json.loads(await self._client.recv())
                message_send = type_validate_python(message_send_cls, payload)
                if isinstance(payload, dict) and "source_plugin" in payload:
                    # 保持 vendor MessageSend 原版；source_plugin 是真寻桥接私有扩展，
                    # 从 raw JSON 中恢复为运行时属性即可，不要求上游模型声明字段。
                    object.__setattr__(
                        message_send,
                        "source_plugin",
                        str(payload.get("source_plugin") or ""),
                    )

                logger.info(
                    f"[{self.bot_id} <--] {message_send.target_id} "
                    f"- {message_send.target_type}",
                    "ExternalBotBridge",
                )
                try:
                    # 这里故意不再为每个回包创建独立 task：可信转发模式下
                    # 队列满时会等待入队，直接 await 可以把背压传回 WS 读取侧，
                    # 避免 QQ API 卡住时产生无限 callback task。
                    await self.callback(message_send)
                except Exception as exc:
                    logger.warning(
                        "桥接消息回调执行失败，已保留 WS 接收循环继续运行。",
                        "ExternalBotBridge",
                        e=exc,
                    )

    BridgeGsClient.__name__ = vendor_gs_client_cls.__name__
    BridgeGsClient.__qualname__ = vendor_gs_client_cls.__qualname__
    return BridgeGsClient


def _load_bridge_library() -> _BridgeLibrary:
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE is not None:
        return _LIBRARY_CACHE

    try:
        bridge_package = _install_isolated_bridge_vendor_package(
            _resolve_bridge_vendor_src()
        )
        sayu_protocol_module = importlib.import_module(
            f"{bridge_package}.sayu_protocol"
        )
        protocol_module = importlib.import_module(
            f"{bridge_package}.protocols.onebot_v11"
        )
    except ModuleNotFoundError as exc:
        raise BridgeDependencyUnavailable(
            "桥接依赖未安装完整，请确认 nonebot-plugin-genshinuid 及其依赖可导入"
        ) from exc

    _LIBRARY_CACHE = _BridgeLibrary(
        gs_client_cls=_build_bridge_gs_client_cls(
            sayu_protocol_module.GsClient,
            sayu_protocol_module.MessageSend,
        ),
        message_cls=sayu_protocol_module.Message,
        message_receive_cls=sayu_protocol_module.MessageReceive,
        message_send_cls=sayu_protocol_module.MessageSend,
        protocol_cls=protocol_module.OneBotV11Protocol,
    )
    return _LIBRARY_CACHE


class ExternalBotBridge:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._client_id: str | None = None
        self._connect_lock = asyncio.Lock()
        self._probe_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Queue[Any]] = {}
        self._source_routes: dict[str, _SourceRoute] = {}
        self._outbound_queue: asyncio.Queue[Any] | None = None
        self._outbound_worker_tasks: list[asyncio.Task[None]] = []
        self._reconcile_task: asyncio.Task[None] | None = None
        self._outbound_metrics: Counter[str] = Counter()
        self._last_log_times: dict[str, float] = {}
        self._ready_event = asyncio.Event()
        self._ready_ws_id: int | None = None
        self._last_ready_state: bool | None = None
        self._last_connect_state: bool | None = None

    @staticmethod
    def _build_request_key(bot_self_id: str, msg_id: str) -> str:
        return f"{bot_self_id}:{msg_id}"

    @staticmethod
    def _is_supported(bot: object, event: object) -> bool:
        return isinstance(bot, OneBotV11Bot) and isinstance(
            event, OneBotV11MessageEvent
        )

    def _is_connected(self) -> bool:
        ws = getattr(self._client, "_client", None)
        return bool(ws and getattr(ws, "open", False))

    def _current_ws_id(self) -> int | None:
        ws = getattr(self._client, "_client", None)
        return id(ws) if ws is not None else None

    def _prune_client_tasks(self) -> list[asyncio.Task[Any]]:
        client = self._client
        if client is None:
            return []

        tasks = list(getattr(client, "_tasks", []) or [])
        if not tasks:
            return []

        active_tasks: list[asyncio.Task[Any]] = []
        for task in tasks:
            done = getattr(task, "done", None)
            if not callable(done) or not done():
                active_tasks.append(task)
                continue

            cancelled = getattr(task, "cancelled", None)
            if callable(cancelled) and cancelled():
                continue

            exception = getattr(task, "exception", None)
            if not callable(exception):
                continue
            try:
                exc = exception()
            except asyncio.CancelledError:
                continue
            if exc is not None:
                logger.debug(
                    "外部侧车连接任务已结束，将由 bridge 后台重试。",
                    "ExternalBotBridge",
                    e=exc,
                )

        if len(active_tasks) != len(tasks):
            # vendor client 会保留已结束的连接 task；这里主动读取异常并清理，
            # 让下一轮 reconcile/request 能重新发起连接，
            # 且避免 asyncio 输出未取异常警告。
            client._tasks = active_tasks
        return active_tasks

    def _is_ready_for_requests(self) -> bool:
        # ready 标记必须绑定到“最近一次探活成功的具体 WS 连接”；
        # 否则 sidecar 断线重连后，旧 ready 状态会误放行新连接上的首个请求。
        current_ws_id = self._current_ws_id()
        return (
            self._ready_event.is_set()
            and self._is_connected()
            and current_ws_id is not None
            and current_ws_id == self._ready_ws_id
        )

    @staticmethod
    def _health_url(settings: Any) -> str:
        return f"http://{settings.host}:{settings.port}/api/system/health"

    def _set_connect_state(
        self,
        connected: bool,
        *,
        log_on_disconnect: bool = True,
        reason: str | None = None,
    ) -> None:
        previous = self._last_connect_state
        self._last_connect_state = connected
        if previous is connected:
            return

        if connected:
            logger.info(
                "外部侧车 WebSocket 已连接",
                "ExternalBotBridge",
                target=self._build_log_target(client_id=self._client_id),
            )
            return

        if previous is True and log_on_disconnect:
            logger.warning(
                "外部侧车 WebSocket 已断开，将继续后台重试。",
                "ExternalBotBridge",
                target=self._build_log_target(
                    client_id=self._client_id,
                    reason=reason,
                ),
            )

    def _set_ready_state(
        self,
        ready: bool,
        *,
        log_on_unready: bool = True,
        reason: str | None = None,
    ) -> None:
        previous = self._last_ready_state
        self._last_ready_state = ready
        if ready:
            self._ready_event.set()
        else:
            self._ready_event.clear()
            self._ready_ws_id = None

        if previous is ready:
            return

        if ready:
            logger.info(
                "外部侧车应用层已就绪",
                "ExternalBotBridge",
                target=self._build_log_target(client_id=self._client_id),
            )
            return

        if previous is True and log_on_unready:
            logger.warning(
                "外部侧车应用层已失去就绪状态，将继续后台探测。",
                "ExternalBotBridge",
                target=self._build_log_target(
                    client_id=self._client_id,
                    reason=reason,
                ),
            )

    def _reset_transport_state(self) -> None:
        self._set_ready_state(False, log_on_unready=False)
        self._set_connect_state(False, log_on_disconnect=False)

    def _mark_transport_unavailable(self, *, reason: str | None = None) -> None:
        self._set_ready_state(False, reason=reason)
        self._set_connect_state(False, reason=reason)

    @staticmethod
    def _normalize_message_segment(
        segment: Any, message_cls: type[Any]
    ) -> Any:
        # GsCore 经 JSON 往返后会把嵌套消息段退化成 dict；
        # 回放协议层仍要求拿到 Message 对象，否则 node 内层会在 send_message 阶段崩溃。
        if isinstance(segment, dict):
            segment_type = segment.get("type")
            segment_data = segment.get("data")
        else:
            segment_type = getattr(segment, "type", None)
            segment_data = getattr(segment, "data", None)

        if not segment_type:
            raise BridgeUnsupportedEvent("侧车返回了缺少 type 的消息段")

        if segment_type == "node":
            if segment_data is None:
                segment_data = []
            # node 段内部必须继续递归归一化，
            # 否则只修复顶层仍会在内层消息回放时触发属性错误。
            if not isinstance(segment_data, list):
                raise BridgeUnsupportedEvent("侧车返回的 node 消息结构无效")
            segment_data = [
                ExternalBotBridge._normalize_message_segment(node, message_cls)
                for node in segment_data
            ]

        return message_cls(type=segment_type, data=segment_data)

    @classmethod
    def _normalize_response_content(
        cls, content: Any, message_cls: type[Any]
    ) -> list[Any]:
        # 桥接层只接受协议约定的消息列表结构，尽早在这里报错，
        # 避免把半损坏数据送进协议发送器。
        if not isinstance(content, list):
            raise BridgeUnsupportedEvent("侧车返回的消息内容结构无效")
        return [
            cls._normalize_message_segment(segment, message_cls)
            for segment in content
        ]

    @staticmethod
    def _log_message(event_name: str) -> str:
        return f"桥接出站事件: {event_name}"

    @staticmethod
    def _build_log_target(**kwargs: Any) -> dict[str, Any] | None:
        target = {
            key: value
            for key, value in kwargs.items()
            if key != "e" and value is not None
        }
        return target or None

    def _record_outbound_event(
        self,
        event_name: str,
        level: str = "debug",
        **kwargs: Any,
    ) -> None:
        self._outbound_metrics[event_name] += 1
        log_kwargs = {"target": self._build_log_target(**kwargs)}
        if "e" in kwargs:
            log_kwargs["e"] = kwargs["e"]
        getattr(logger, level)(
            self._log_message(event_name),
            "ExternalBotBridge",
            **log_kwargs,
        )

    def _log_throttled(
        self,
        key: str,
        message: str,
        *,
        interval: float = 60.0,
        level: str = "warning",
        **kwargs: Any,
    ) -> None:
        now = time.monotonic()
        last = self._last_log_times.get(key, 0.0)
        if now - last < interval:
            return
        self._last_log_times[key] = now
        log_kwargs = {"target": self._build_log_target(**kwargs)}
        if "e" in kwargs:
            log_kwargs["e"] = kwargs["e"]
        getattr(logger, level)(message, "ExternalBotBridge", **log_kwargs)

    def register_source_route(
        self,
        source_plugin: str,
        wrapper_module: str,
        display_name: str | None = None,
    ) -> None:
        source_plugin = source_plugin.strip()
        wrapper_module = wrapper_module.strip()
        if not source_plugin or not wrapper_module:
            raise ValueError("source_plugin 与 wrapper_module 不能为空")
        self._source_routes[source_plugin] = _SourceRoute(
            wrapper_module=wrapper_module,
            display_name=(display_name or source_plugin).strip() or source_plugin,
        )

    def _get_online_onebot_bots(self) -> dict[str, OneBotV11Bot]:
        return {
            str(bot.self_id): bot
            for bot in nonebot.get_bots().values()
            if isinstance(bot, OneBotV11Bot)
        }

    @staticmethod
    def _should_run_background_reconcile(settings: Any) -> bool:
        return settings.connect_on_startup or settings.enable_proactive_push

    def _resolve_background_client_id(
        self, online_bots: dict[str, OneBotV11Bot]
    ) -> str | None:
        settings = get_external_bot_bridge_settings()
        if settings.client_id:
            return settings.client_id
        if len(online_bots) == 1:
            return next(iter(online_bots))
        if len(online_bots) > 1:
            self._log_throttled(
                "background_client_ambiguous",
                "bridge 启动预热未启用："
                "当前存在多个在线 OneBot V11 bot 且未配置 CLIENT_ID。",
            )
            return None
        self._log_throttled(
            "background_client_no_bot",
            "bridge 启动预热暂未建立：当前没有在线的 OneBot V11 bot。",
            level="info",
        )
        return None

    async def _handle_response(self, message_send: Any) -> None:
        msg_id = str(getattr(message_send, "msg_id", "") or "")
        bot_self_id = str(getattr(message_send, "bot_self_id", "") or "")
        if msg_id:
            key = self._build_request_key(bot_self_id, msg_id)
            queue = self._pending.get(key)
            if queue:
                await queue.put(message_send)
                return

        # GsCore 是本地可信上游：未命中 pending 不再按“未知来源”丢弃。
        # 迟到的多段回复、无 msg_id 的订阅通知和未携带 source_plugin 的主动消息，
        # 只要仍是可发送的 MessageSend，就统一进入出站队列尽量投递。
        await self._enqueue_outbound(message_send)

    @staticmethod
    def _has_sendable_target(message_send: Any) -> bool:
        return bool(
            str(getattr(message_send, "target_type", "") or "")
            and str(getattr(message_send, "target_id", "") or "")
            and getattr(message_send, "content", None)
        )

    async def _ensure_outbound_workers(self) -> None:
        settings = get_external_bot_bridge_settings()
        if self._outbound_queue is None:
            self._outbound_queue = asyncio.Queue(maxsize=settings.push_queue_size)

        while len(self._outbound_worker_tasks) < settings.push_workers:
            worker_id = len(self._outbound_worker_tasks)
            self._outbound_worker_tasks.append(
                asyncio.create_task(
                    self._outbound_worker(worker_id),
                    name=f"external-bot-bridge-outbound-{worker_id}",
                )
            )

    async def _enqueue_outbound(self, message_send: Any) -> None:
        if not self._has_sendable_target(message_send):
            self._record_outbound_event(
                "outbound_failed_invalid_message",
                level="warning",
                msg_id=str(getattr(message_send, "msg_id", "") or "") or None,
                bot_self_id=str(getattr(message_send, "bot_self_id", "") or "")
                or None,
                target_type=getattr(message_send, "target_type", None),
                target_id=getattr(message_send, "target_id", None),
            )
            return

        await self._ensure_outbound_workers()
        if self._outbound_queue is None:
            self._record_outbound_event(
                "outbound_failed_no_queue",
                level="warning",
                target_type=getattr(message_send, "target_type", None),
                target_id=getattr(message_send, "target_id", None),
            )
            return

        # 这里选择等待入队而不是队满丢弃：本地 GsCore 被视为可信上游，
        # 本轮目标是“不漏消息”。真正发送仍由 worker 串行/限量执行，
        # 避免回调里直接打 QQ API。
        maxsize = self._outbound_queue.maxsize
        if maxsize > 0 and self._outbound_queue.qsize() >= maxsize:
            self._log_throttled(
                "outbound_queue_full_wait",
                "桥接出站队列已满，将等待消费者释放空间。",
                level="warning",
                queue_size=maxsize,
            )
        await self._outbound_queue.put(message_send)

    async def _probe_ready(self) -> bool:
        async with self._probe_lock:
            if not self._is_connected():
                self._mark_transport_unavailable(reason="ws_not_connected")
                return False

            self._set_connect_state(True)
            settings = get_external_bot_bridge_settings()
            try:
                async with httpx.AsyncClient(
                    timeout=READY_PROBE_TIMEOUT_SECONDS
                ) as client:
                    response = await client.get(self._health_url(settings))
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                self._set_ready_state(False, reason="health_probe_failed")
                return False

            if not isinstance(payload, dict) or not isinstance(
                payload.get("data"), dict
            ):
                self._set_ready_state(False, reason="health_payload_invalid")
                return False

            is_ready = (
                response.status_code == 200
                and payload.get("status") == 0
                and payload["data"].get("status") == "healthy"
            )
            if is_ready:
                self._ready_ws_id = self._current_ws_id()
                self._set_ready_state(True)
                return True

            self._set_ready_state(False, reason="health_unhealthy")
            return False

    async def wait_until_ready(self, timeout: float) -> None:
        if self._is_ready_for_requests():
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if self._is_ready_for_requests():
                return

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise BridgeServiceWarmingUp("侧车应用层仍在启动中")

            await self._probe_ready()
            if self._is_ready_for_requests():
                return

            try:
                await asyncio.wait_for(
                    self._ready_event.wait(),
                    timeout=min(READY_PROBE_INTERVAL_SECONDS, remaining),
                )
            except asyncio.TimeoutError:
                continue

    async def _wait_until_connected(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._is_connected():
                return
            await asyncio.sleep(0.2)
        raise BridgeConnectionTimeout("连接侧车超时")

    async def ensure_connected(
        self, bot_id: str, *, wait_connected: bool = True
    ) -> None:
        library = _load_bridge_library()
        settings = get_external_bot_bridge_settings()
        desired_client_id = settings.client_id or bot_id

        if self._is_connected() and self._client_id == desired_client_id:
            self._set_connect_state(True)
            return

        async with self._connect_lock:
            if self._is_connected() and self._client_id == desired_client_id:
                self._set_connect_state(True)
                return

            if self._client is None or self._client_id != desired_client_id:
                if self._client is not None:
                    self._reset_transport_state()
                    await self.close(close_background_tasks=False)
                self._client = library.gs_client_cls(
                    settings.host,
                    settings.port,
                    desired_client_id,
                    self._handle_response,
                    is_retry=False,
                )
                self._client_id = desired_client_id
                await self._client.connect()
                logger.info(
                    (
                        "已启动外部侧车连接任务 "
                        f"{settings.host}:{settings.port} client_id={desired_client_id}"
                    ),
                    "ExternalBotBridge",
                    session=bot_id,
                )
            elif not self._prune_client_tasks():
                await self._client.connect()

        if not wait_connected:
            if self._is_connected():
                self._set_connect_state(True)
            else:
                self._set_ready_state(False, reason="ws_not_connected")
            return

        try:
            await self._wait_until_connected(min(settings.request_timeout, 5.0))
        except BridgeConnectionTimeout:
            self._mark_transport_unavailable(reason="connect_timeout")
            raise
        self._set_connect_state(True)

    async def send_request(
        self,
        bot: OneBotV11Bot,
        event: OneBotV11MessageEvent,
    ) -> list[Any]:
        if not self._is_supported(bot, event):
            raise BridgeUnsupportedEvent("当前仅支持 OneBot V11 消息事件桥接")

        library = _load_bridge_library()
        protocol = library.protocol_cls(bot)
        message_receive = await protocol.handle_message(event)
        if message_receive is None:
            raise BridgeUnsupportedEvent("当前消息无法转换为侧车协议")
        if not getattr(message_receive, "msg_id", ""):
            raise BridgeUnsupportedEvent("当前消息缺少可追踪的 msg_id")

        await self.ensure_connected(bot.self_id)
        await self.wait_until_ready(REQUEST_READY_GRACE_SECONDS)

        request_key = self._build_request_key(
            str(message_receive.bot_self_id),
            str(message_receive.msg_id),
        )
        if request_key in self._pending:
            raise BridgeUnsupportedEvent("检测到重复的桥接请求标识")

        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._pending[request_key] = queue
        settings = get_external_bot_bridge_settings()

        try:
            await self._client.send(message_receive)
            first_response = await asyncio.wait_for(
                queue.get(), timeout=settings.request_timeout
            )
            responses = [first_response]

            while True:
                try:
                    response = await asyncio.wait_for(
                        queue.get(), timeout=settings.reply_idle_seconds
                    )
                except asyncio.TimeoutError:
                    break
                responses.append(response)
            return responses
        except asyncio.TimeoutError as exc:
            raise BridgeResponseTimeout("等待侧车响应超时") from exc
        finally:
            self._pending.pop(request_key, None)

    async def stream_request(
        self,
        bot: OneBotV11Bot,
        event: OneBotV11MessageEvent,
    ) -> int:
        if not self._is_supported(bot, event):
            raise BridgeUnsupportedEvent("当前仅支持 OneBot V11 消息事件桥接")

        library = _load_bridge_library()
        protocol = library.protocol_cls(bot)
        message_receive = await protocol.handle_message(event)
        if message_receive is None:
            raise BridgeUnsupportedEvent("当前消息无法转换为侧车协议")
        if not getattr(message_receive, "msg_id", ""):
            raise BridgeUnsupportedEvent("当前消息缺少可追踪的 msg_id")

        await self.ensure_connected(bot.self_id)
        await self.wait_until_ready(REQUEST_READY_GRACE_SECONDS)

        request_key = self._build_request_key(
            str(message_receive.bot_self_id),
            str(message_receive.msg_id),
        )
        if request_key in self._pending:
            raise BridgeUnsupportedEvent("检测到重复的桥接请求标识")

        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._pending[request_key] = queue
        settings = get_external_bot_bridge_settings()
        sent_count = 0
        seen_response = False

        try:
            await self._client.send(message_receive)
            while True:
                timeout = (
                    settings.response_tail_idle_seconds
                    if seen_response
                    else settings.request_timeout
                )
                try:
                    response = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError as exc:
                    if not seen_response:
                        raise BridgeResponseTimeout("等待侧车响应超时") from exc
                    break

                seen_response = True
                try:
                    if await self._send_response_via_bot(bot, response):
                        sent_count += 1
                except BridgeUnsupportedEvent as exc:
                    raise BridgeUnsupportedResponse(
                        "侧车返回了当前协议暂不支持的响应内容"
                    ) from exc

            self._record_outbound_event(
                "request_stream_finished",
                msg_id=str(message_receive.msg_id),
                bot_self_id=str(message_receive.bot_self_id),
                sent_count=sent_count,
            )
            return sent_count
        finally:
            self._pending.pop(request_key, None)

    async def _send_response_via_bot(
        self,
        bot: OneBotV11Bot,
        response: Any,
    ) -> bool:
        if not isinstance(bot, OneBotV11Bot):
            raise BridgeUnsupportedEvent("当前仅支持 OneBot V11 回复发送")

        if not getattr(response, "content", None):
            return False

        target_id = getattr(response, "target_id", None)
        target_type = getattr(response, "target_type", None)
        if not target_id or not target_type:
            return False

        library = _load_bridge_library()
        protocol = library.protocol_cls(bot)
        normalized_content = self._normalize_response_content(
            response.content, library.message_cls
        )
        await protocol.send_message(normalized_content, target_id, target_type)
        return True

    async def replay_responses(
        self, bot: OneBotV11Bot, responses: list[Any]
    ) -> int:
        sent_count = 0
        for response in responses:
            if await self._send_response_via_bot(bot, response):
                sent_count += 1
        return sent_count

    async def start_background_tasks(self) -> None:
        settings = get_external_bot_bridge_settings()
        if not self._should_run_background_reconcile(settings):
            return

        await self._ensure_outbound_workers()

        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop(),
                name="external-bot-bridge-reconcile",
            )

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await self._reconcile_connection()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_throttled(
                    "background_reconcile_failed",
                    "外部侧车后台协调循环执行失败，将继续重试。",
                    e=exc,
                )
            settings = get_external_bot_bridge_settings()
            await asyncio.sleep(settings.push_reconcile_interval)

    async def _reconcile_connection(self) -> None:
        settings = get_external_bot_bridge_settings()
        if not self._should_run_background_reconcile(settings):
            return

        online_bots = self._get_online_onebot_bots()
        desired_client_id = self._resolve_background_client_id(online_bots)
        if not desired_client_id:
            return

        try:
            await self.ensure_connected(desired_client_id, wait_connected=False)
        except BridgeConnectionTimeout as exc:
            self._log_throttled(
                "background_connect_failed",
                "外部侧车后台连接超时，将在下个协调周期重试。",
                e=exc,
            )
            return

        if not self._is_connected():
            self._mark_transport_unavailable(reason="ws_not_connected")
            self._log_throttled(
                "background_wait_connect",
                "外部侧车连接任务已启动，等待 WebSocket 建立。",
                level="info",
            )
            return

        if not await self._probe_ready():
            self._log_throttled(
                "background_not_ready",
                "外部侧车连接已建立，但应用层尚未就绪，将继续后台探测。",
                level="info",
            )

    async def _outbound_worker(self, worker_id: int) -> None:
        if self._outbound_queue is None:
            return

        while True:
            message_send = await self._outbound_queue.get()
            try:
                await self._dispatch_outbound(message_send)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "桥接出站消费者执行失败",
                    "ExternalBotBridge",
                    target={"worker_id": worker_id},
                    e=exc,
                )
            finally:
                self._outbound_queue.task_done()

    async def _dispatch_outbound(self, message_send: Any) -> None:
        if not self._has_sendable_target(message_send):
            self._record_outbound_event(
                "outbound_failed_invalid_message",
                level="warning",
                target_type=getattr(message_send, "target_type", None),
                target_id=getattr(message_send, "target_id", None),
            )
            return

        candidate_bots, routed_randomly = self._resolve_outbound_bots(message_send)
        target_type = str(getattr(message_send, "target_type", "") or "")
        target_id = str(getattr(message_send, "target_id", "") or "")
        if not candidate_bots:
            self._record_outbound_event(
                "outbound_failed_no_bot",
                level="warning",
                target_type=target_type,
                target_id=target_id,
                bot_self_id=str(getattr(message_send, "bot_self_id", "") or "")
                or None,
            )
            return

        if routed_randomly:
            self._record_outbound_event(
                "outbound_routed_randomly",
                target_type=target_type,
                target_id=target_id,
                candidates=[bot.self_id for bot in candidate_bots],
            )

        for bot in candidate_bots:
            try:
                if await self._send_response_via_bot(bot, message_send):
                    self._record_outbound_event(
                        "outbound_delivered",
                        level="info",
                        bot_self_id=bot.self_id,
                        target_type=target_type,
                        target_id=target_id,
                    )
                    return
            except BridgeUnsupportedEvent as exc:
                self._record_outbound_event(
                    "outbound_failed_invalid_message",
                    level="warning",
                    bot_self_id=bot.self_id,
                    target_type=target_type,
                    target_id=target_id,
                    e=exc,
                )
                return
            except Exception as exc:
                self._record_outbound_event(
                    "outbound_failed_send",
                    level="warning",
                    bot_self_id=bot.self_id,
                    target_type=target_type,
                    target_id=target_id,
                    e=exc,
                )

        self._record_outbound_event(
            "outbound_failed_send",
            level="warning",
            target_type=target_type,
            target_id=target_id,
            reason="all_candidates_failed",
        )

    def _resolve_outbound_bots(
        self,
        message_send: Any,
    ) -> tuple[list[OneBotV11Bot], bool]:
        online_bots = self._get_online_onebot_bots()
        if not online_bots:
            return [], False

        candidates: list[OneBotV11Bot] = []
        seen_bot_ids: set[str] = set()

        def add_candidate(bot_id: str | None) -> None:
            if not bot_id:
                return
            bot = online_bots.get(str(bot_id))
            if bot is None or str(bot.self_id) in seen_bot_ids:
                return
            candidates.append(bot)
            seen_bot_ids.add(str(bot.self_id))

        # 精确 bot_self_id 仍然优先；如果该 bot 发送失败，后续候选会继续尝试，
        # 避免“同一后端多 Bot”场景下因为单个 Bot 不在群内而漏掉本地可信消息。
        add_candidate(str(getattr(message_send, "bot_self_id", "") or "").strip())
        add_candidate(self._client_id)
        has_explicit_candidate = bool(candidates)

        remaining_bots = [
            bot
            for bot in online_bots.values()
            if str(bot.self_id) not in seen_bot_ids
        ]
        routed_randomly = False
        if remaining_bots:
            if len(online_bots) == 1:
                candidates.extend(remaining_bots)
            elif (
                not has_explicit_candidate
                and get_external_bot_bridge_settings().ambiguous_bot_policy == "drop"
            ):
                self._record_outbound_event(
                    "outbound_failed_ambiguous_bot",
                    level="warning",
                    candidates=[bot.self_id for bot in remaining_bots],
                )
            else:
                random.shuffle(remaining_bots)
                routed_randomly = True
                candidates.extend(remaining_bots)

        return candidates, routed_randomly

    async def close(self, close_background_tasks: bool = True) -> None:
        self._reset_transport_state()

        if close_background_tasks:
            tasks = []
            if self._reconcile_task is not None:
                self._reconcile_task.cancel()
                tasks.append(self._reconcile_task)
                self._reconcile_task = None
            for task in self._outbound_worker_tasks:
                task.cancel()
                tasks.append(task)
            self._outbound_worker_tasks.clear()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._outbound_queue = None

        client = self._client
        self._client = None
        self._client_id = None
        if client is not None:
            await client.close()
        self._pending.clear()


external_bot_bridge = ExternalBotBridge()

driver = nonebot.get_driver()


@driver.on_startup
async def _start_external_bot_bridge() -> None:
    try:
        await external_bot_bridge.start_background_tasks()
    except Exception as exc:
        logger.warning(
            "外部侧车后台任务初始化失败，已跳过，不影响主进程启动。",
            "ExternalBotBridge",
            e=exc,
        )


driver.on_shutdown(external_bot_bridge.close)
