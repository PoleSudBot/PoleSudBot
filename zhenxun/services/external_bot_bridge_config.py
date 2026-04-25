from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from zhenxun.configs.config import Config
from zhenxun.configs.utils import RegisterConfig

MODULE_NAME = "external_bot_bridge"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CLIENT_ID = ""
DEFAULT_REQUEST_TIMEOUT = 20
DEFAULT_REPLY_IDLE_MS = 600
DEFAULT_RESPONSE_TAIL_IDLE_MS = 3000
DEFAULT_RETRY = True
DEFAULT_CONNECT_ON_STARTUP = True
DEFAULT_ENABLE_PROACTIVE_PUSH = False
DEFAULT_PUSH_QUEUE_SIZE = 200
DEFAULT_PUSH_WORKERS = 1
DEFAULT_PUSH_RECONCILE_INTERVAL = 5.0
DEFAULT_AMBIGUOUS_BOT_POLICY = "random_order"

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
        help="旧聚合接口收到首包后继续聚合后续响应的空闲等待时间（毫秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="RESPONSE_TAIL_IDLE_MS",
        value=DEFAULT_RESPONSE_TAIL_IDLE_MS,
        default_value=DEFAULT_RESPONSE_TAIL_IDLE_MS,
        help="流式请求收到首包后继续等待后续响应的空闲时间（毫秒）",
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
    RegisterConfig(
        module=MODULE_NAME,
        key="CONNECT_ON_STARTUP",
        value=DEFAULT_CONNECT_ON_STARTUP,
        default_value=DEFAULT_CONNECT_ON_STARTUP,
        help="是否在真寻启动后后台预热 sidecar 连接",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ENABLE_PROACTIVE_PUSH",
        value=DEFAULT_ENABLE_PROACTIVE_PUSH,
        default_value=DEFAULT_ENABLE_PROACTIVE_PUSH,
        help=(
            "兼容开关：CONNECT_ON_STARTUP=false 时是否仍为主动推送启动后台连接；"
            "可信出站转发不以此为门槛"
        ),
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="PUSH_QUEUE_SIZE",
        value=DEFAULT_PUSH_QUEUE_SIZE,
        default_value=DEFAULT_PUSH_QUEUE_SIZE,
        help="bridge 出站缓冲队列大小",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="PUSH_WORKERS",
        value=DEFAULT_PUSH_WORKERS,
        default_value=DEFAULT_PUSH_WORKERS,
        help="bridge 出站消费者数量，默认为 1 以保持顺序稳定",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="PUSH_RECONCILE_INTERVAL",
        value=DEFAULT_PUSH_RECONCILE_INTERVAL,
        default_value=DEFAULT_PUSH_RECONCILE_INTERVAL,
        help="bridge 后台连接协调循环间隔（秒）",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AMBIGUOUS_BOT_POLICY",
        value=DEFAULT_AMBIGUOUS_BOT_POLICY,
        default_value=DEFAULT_AMBIGUOUS_BOT_POLICY,
        help="多 Bot 且无明确 bot_self_id/client_id 时的路由策略：drop 或 random_order",
        type=str,
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
    response_tail_idle_seconds: float
    retry: bool
    connect_on_startup: bool
    enable_proactive_push: bool
    push_queue_size: int
    push_workers: int
    push_reconcile_interval: float
    ambiguous_bot_policy: Literal["drop", "random_order"]


def get_external_bot_bridge_settings() -> ExternalBotBridgeSettings:
    config_group = Config.get(MODULE_NAME)
    ambiguous_bot_policy = str(
        config_group.get("AMBIGUOUS_BOT_POLICY", DEFAULT_AMBIGUOUS_BOT_POLICY)
    ).strip() or DEFAULT_AMBIGUOUS_BOT_POLICY
    if ambiguous_bot_policy not in {"drop", "random_order"}:
        ambiguous_bot_policy = DEFAULT_AMBIGUOUS_BOT_POLICY
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
        response_tail_idle_seconds=max(
            0.1,
            float(
                config_group.get(
                    "RESPONSE_TAIL_IDLE_MS",
                    DEFAULT_RESPONSE_TAIL_IDLE_MS,
                )
            )
            / 1000,
        ),
        retry=bool(config_group.get("RETRY", DEFAULT_RETRY)),
        connect_on_startup=bool(
            config_group.get(
                "CONNECT_ON_STARTUP",
                DEFAULT_CONNECT_ON_STARTUP,
            )
        ),
        enable_proactive_push=bool(
            config_group.get(
                "ENABLE_PROACTIVE_PUSH",
                DEFAULT_ENABLE_PROACTIVE_PUSH,
            )
        ),
        push_queue_size=max(
            1,
            int(config_group.get("PUSH_QUEUE_SIZE", DEFAULT_PUSH_QUEUE_SIZE)),
        ),
        push_workers=max(
            1,
            int(config_group.get("PUSH_WORKERS", DEFAULT_PUSH_WORKERS)),
        ),
        push_reconcile_interval=max(
            1.0,
            float(
                config_group.get(
                    "PUSH_RECONCILE_INTERVAL",
                    DEFAULT_PUSH_RECONCILE_INTERVAL,
                )
            ),
        ),
        ambiguous_bot_policy=ambiguous_bot_policy,
    )
