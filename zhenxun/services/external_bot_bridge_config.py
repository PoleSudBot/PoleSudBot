from __future__ import annotations

from dataclasses import dataclass

from zhenxun.configs.config import Config
from zhenxun.configs.utils import RegisterConfig

MODULE_NAME = "external_bot_bridge"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CLIENT_ID = ""
DEFAULT_REQUEST_TIMEOUT = 20
DEFAULT_REPLY_IDLE_MS = 600
DEFAULT_RETRY = True

REGISTER_CONFIGS = [
    RegisterConfig(
        module=MODULE_NAME,
        key="HOST",
        value=DEFAULT_HOST,
        default_value=DEFAULT_HOST,
        help="侧车服务地址，默认本机 127.0.0.1",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="PORT",
        value=DEFAULT_PORT,
        default_value=DEFAULT_PORT,
        help="侧车服务端口",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="CLIENT_ID",
        value=DEFAULT_CLIENT_ID,
        default_value=DEFAULT_CLIENT_ID,
        help="连接侧车时使用的客户端标识；留空时使用当前 bot.self_id",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="REQUEST_TIMEOUT",
        value=DEFAULT_REQUEST_TIMEOUT,
        default_value=DEFAULT_REQUEST_TIMEOUT,
        help="等待首个上游响应的超时时间（秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="REPLY_IDLE_MS",
        value=DEFAULT_REPLY_IDLE_MS,
        default_value=DEFAULT_REPLY_IDLE_MS,
        help="收到首包后继续聚合后续响应的空闲等待时间（毫秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="RETRY",
        value=DEFAULT_RETRY,
        default_value=DEFAULT_RETRY,
        help="连接失败后是否自动重试",
        type=bool,
    ),
]


for config in REGISTER_CONFIGS:
    Config.add_plugin_config(
        config.module or MODULE_NAME,
        config.key,
        config.value,
        help=config.help,
        default_value=config.default_value,
        type=config.type,
    )


@dataclass(frozen=True)
class ExternalBotBridgeSettings:
    host: str
    port: int
    client_id: str
    request_timeout: float
    reply_idle_seconds: float
    retry: bool


def get_external_bot_bridge_settings() -> ExternalBotBridgeSettings:
    config_group = Config.get(MODULE_NAME)
    return ExternalBotBridgeSettings(
        host=config_group.get("HOST", DEFAULT_HOST),
        port=int(config_group.get("PORT", DEFAULT_PORT)),
        client_id=str(config_group.get("CLIENT_ID", DEFAULT_CLIENT_ID) or "").strip(),
        request_timeout=float(
            config_group.get("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)
        ),
        reply_idle_seconds=float(
            config_group.get("REPLY_IDLE_MS", DEFAULT_REPLY_IDLE_MS)
        )
        / 1000,
        retry=bool(config_group.get("RETRY", DEFAULT_RETRY)),
    )
