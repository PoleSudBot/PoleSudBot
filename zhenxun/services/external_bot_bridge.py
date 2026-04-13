from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from typing import Any

import nonebot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11 import MessageEvent as OneBotV11MessageEvent

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


class BridgeUnsupportedEvent(ExternalBotBridgeError):
    """当前事件不支持桥接。"""


@dataclass(frozen=True)
class _BridgeLibrary:
    gs_client_cls: type[Any]
    message_cls: type[Any]
    message_receive_cls: type[Any]
    message_send_cls: type[Any]
    protocol_cls: type[Any]


_LIBRARY_CACHE: _BridgeLibrary | None = None


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


def _ensure_bridge_vendor_path() -> None:
    for vendor_src in _get_vendor_src_paths():
        if not vendor_src.exists():
            continue
        vendor_src_str = str(vendor_src)
        if vendor_src_str not in sys.path:
            sys.path.insert(0, vendor_src_str)
        return

    raise BridgeDependencyUnavailable(
        "未找到桥接依赖目录，请先执行 `uv run --no-project nbm.py init --install` "
        "或 `uv run --no-project nbm.py prod-setup` 准备 sidecar 运行时目录。"
    )


def _load_bridge_library() -> _BridgeLibrary:
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE is not None:
        return _LIBRARY_CACHE

    try:
        _ensure_bridge_vendor_path()
        protocol_module = importlib.import_module("GenshinUID.protocols.onebot_v11")
        sayu_protocol_module = importlib.import_module("GenshinUID.sayu_protocol")
    except ModuleNotFoundError as exc:
        raise BridgeDependencyUnavailable(
            "桥接依赖未安装完整，请确认 nonebot-plugin-genshinuid 及其依赖可导入"
        ) from exc

    _LIBRARY_CACHE = _BridgeLibrary(
        gs_client_cls=sayu_protocol_module.GsClient,
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
        self._pending: dict[str, asyncio.Queue[Any]] = {}

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
            # node 段内部必须继续递归归一化，否则只修复顶层仍会在内层消息回放时触发属性错误。
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
        # 桥接层只接受协议约定的消息列表结构，尽早在这里报错，避免把半损坏数据送进协议发送器。
        if not isinstance(content, list):
            raise BridgeUnsupportedEvent("侧车返回的消息内容结构无效")
        return [
            cls._normalize_message_segment(segment, message_cls)
            for segment in content
        ]

    async def _handle_response(self, message_send: Any) -> None:
        if not getattr(message_send, "msg_id", ""):
            logger.debug("桥接侧收到无 msg_id 的消息，已忽略")
            return
        key = self._build_request_key(
            str(getattr(message_send, "bot_self_id", "")),
            str(getattr(message_send, "msg_id", "")),
        )
        queue = self._pending.get(key)
        if not queue:
            logger.debug(f"桥接侧收到未匹配请求的响应，已忽略: {key}")
            return
        await queue.put(message_send)

    async def _wait_until_connected(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._is_connected():
                return
            await asyncio.sleep(0.2)
        raise BridgeConnectionTimeout("连接侧车超时")

    async def ensure_connected(self, bot_id: str) -> None:
        library = _load_bridge_library()
        settings = get_external_bot_bridge_settings()
        desired_client_id = settings.client_id or bot_id

        if self._is_connected() and self._client_id == desired_client_id:
            return

        async with self._connect_lock:
            if self._is_connected() and self._client_id == desired_client_id:
                return

            if self._client is None or self._client_id != desired_client_id:
                if self._client is not None:
                    await self.close()
                self._client = library.gs_client_cls(
                    settings.host,
                    settings.port,
                    desired_client_id,
                    self._handle_response,
                    is_retry=settings.retry,
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
            elif not getattr(self._client, "_tasks", None):
                await self._client.connect()

        await self._wait_until_connected(min(settings.request_timeout, 5.0))

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

    async def replay_responses(
        self, bot: OneBotV11Bot, responses: list[Any]
    ) -> int:
        if not isinstance(bot, OneBotV11Bot):
            raise BridgeUnsupportedEvent("当前仅支持 OneBot V11 回复发送")

        library = _load_bridge_library()
        protocol = library.protocol_cls(bot)
        sent_count = 0
        for response in responses:
            if not getattr(response, "content", None):
                continue
            target_id = getattr(response, "target_id", None)
            target_type = getattr(response, "target_type", None)
            if not target_id or not target_type:
                continue
            normalized_content = self._normalize_response_content(
                response.content, library.message_cls
            )
            await protocol.send_message(
                normalized_content, target_id, target_type
            )
            sent_count += 1
        return sent_count

    async def close(self) -> None:
        client = self._client
        self._client = None
        self._client_id = None
        if client is not None:
            await client.close()
        self._pending.clear()


external_bot_bridge = ExternalBotBridge()

driver = nonebot.get_driver()
driver.on_shutdown(external_bot_bridge.close)
