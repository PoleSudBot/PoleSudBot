from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time

from nonebot.adapters import Bot

from zhenxun.services.log import logger

LOGGER_COMMAND = "OneBotTransport"
CONTROL_COOLDOWN_SECONDS = 5.0
LOG_INTERVAL_SECONDS = 10.0


@dataclass(slots=True, frozen=True)
class TransportStatus:
    available: bool
    reason: str


_CONNECTED_AT: dict[str, float] = {}
_BLOCKED_UNTIL: dict[str, float] = {}
_LAST_WARNING_AT: dict[tuple[str, str, str], float] = {}


def _adapter_name(bot: Bot) -> str:
    adapter = getattr(bot, "adapter", None)
    if adapter is None:
        return ""
    get_name = getattr(adapter, "get_name", None)
    if get_name is None:
        return ""
    return str(get_name())


def is_onebot_v11(bot: Bot) -> bool:
    return _adapter_name(bot) == "OneBot V11"


def mark_connected(bot: Bot) -> None:
    if not is_onebot_v11(bot):
        return
    _CONNECTED_AT[str(bot.self_id)] = time.time()


def mark_disconnected(bot: Bot, cooldown: float = CONTROL_COOLDOWN_SECONDS) -> None:
    if not is_onebot_v11(bot):
        return
    self_id = str(bot.self_id)
    _BLOCKED_UNTIL[self_id] = time.monotonic() + max(0.0, cooldown)


def get_connected_at(bot_id: str) -> float | None:
    return _CONNECTED_AT.get(str(bot_id))


def _has_ws_connection(bot: Bot) -> bool:
    connections = getattr(getattr(bot, "adapter", None), "connections", None)
    if not isinstance(connections, dict):
        return False
    return str(bot.self_id) in connections and connections[str(bot.self_id)] is not None


def _has_http_api_root(bot: Bot) -> bool:
    onebot_config = getattr(getattr(bot, "adapter", None), "onebot_config", None)
    roots = getattr(onebot_config, "onebot_api_roots", None)
    if not roots or not hasattr(roots, "get"):
        return False
    # 必须直接判断原始值是否存在，避免适配器内部 str(None) 退化成 "None/...".
    api_root = roots.get(str(bot.self_id))
    api_root_text = str(api_root).strip() if api_root is not None else ""
    # 只有显式配置了合法 HTTP(S) 地址时才认为控制面可用，
    # 避免错误配置再次退化成 UnsupportedProtocol 风暴。
    return api_root_text.startswith(("http://", "https://"))


def get_status(bot: Bot) -> TransportStatus:
    if not is_onebot_v11(bot):
        return TransportStatus(True, "unsupported")
    now = time.monotonic()
    blocked_until = _BLOCKED_UNTIL.get(str(bot.self_id), 0.0)
    if blocked_until > now:
        return TransportStatus(False, "cooldown")
    if _has_ws_connection(bot):
        return TransportStatus(True, "ws")
    if _has_http_api_root(bot):
        return TransportStatus(True, "http")
    return TransportStatus(False, "missing_control_plane")


def is_available(bot: Bot) -> bool:
    return get_status(bot).available


def should_drop(bot: Bot) -> bool:
    return not get_status(bot).available


async def wait_until_ready(
    bot: Bot, timeout: float = 2.0, interval: float = 0.1
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if is_available(bot):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(max(0.01, interval))


def _status_reason_text(reason: str) -> str:
    if reason == "cooldown":
        return "处于断连冷却期"
    if reason == "missing_control_plane":
        return "未检测到可用的 WS / HTTP 控制通道"
    return reason


def note_unavailable(bot: Bot, action: str, extra: str | None = None) -> bool:
    status = get_status(bot)
    if status.available:
        return False

    now = time.monotonic()
    key = (str(bot.self_id), action, status.reason)
    last_warning_at = _LAST_WARNING_AT.get(key, 0.0)
    if now - last_warning_at < LOG_INTERVAL_SECONDS:
        return False

    _LAST_WARNING_AT[key] = now
    message = (
        f"OneBot 控制面不可用，已执行止血动作: {action}。"
        f"当前状态: {_status_reason_text(status.reason)}。"
    )
    if extra:
        message = f"{message}{extra}"
    logger.warning(
        message,
        LOGGER_COMMAND,
        session=str(bot.self_id),
        adapter=_adapter_name(bot),
    )
    return True


def reset_state() -> None:
    _CONNECTED_AT.clear()
    _BLOCKED_UNTIL.clear()
    _LAST_WARNING_AT.clear()
