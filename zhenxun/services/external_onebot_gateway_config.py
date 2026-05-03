from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from zhenxun.configs.config import Config
from zhenxun.configs.utils import RegisterConfig

MODULE_NAME = "pjsk"

DEFAULT_WS_URL = "ws://192.168.123.12:7878/ws"
DEFAULT_ACCESS_TOKEN = ""
DEFAULT_VIRTUAL_SELF_ID = ""
DEFAULT_ROUTE_BOT_SELF_ID = ""
DEFAULT_ATTRIBUTION_TTL_SECONDS = 30
DEFAULT_ATTRIBUTION_MAX_SIZE = 10000
DEFAULT_ATTRIBUTION_SWEEP_INTERVAL_SECONDS = 15
DEFAULT_EVENT_QUEUE_MAX_SIZE = 1000
DEFAULT_ENABLE_AUTO_SLASH = True
DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS: list[str] = []
DEFAULT_AUTO_SLASH_FUSE_ENABLED = True
DEFAULT_AUTO_SLASH_FUSE_ECHO_SOURCE_SECONDS = 3.0
DEFAULT_AUTO_SLASH_FUSE_WINDOW_SECONDS = 15.0
DEFAULT_AUTO_SLASH_FUSE_MAX_REPLIES = 4
DEFAULT_AUTO_SLASH_FUSE_SUSPEND_SECONDS = 30.0
DEFAULT_AUTO_SLASH_FUSE_GROUP_NOTICE_ENABLED = True
DEFAULT_AUTO_SLASH_FUSE_SUPERUSER_NOTICE_ENABLED = True
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 5.0

DEFAULT_USER_FILTER_MODE = "blacklist"
DEFAULT_GROUP_FILTER_MODE = "blacklist"
DEFAULT_CONTENT_FILTER_MODE = "blacklist"
# 默认只拦常见自然语言误触发；负向前瞻用于保留低误触发的 Haruki 无斜杠命令别名。
DEFAULT_CONTENT_FILTER_REGEX = [
    r"^生日(?!$|\s)",
    r"^活动(?!$|\s|\d|-|[A-Za-z]+\d|列表|一览|记录|组卡|组队|卡组|配)",
    r"^歌曲(?!列表|一览|定数|奖励|挖矿|进度|排行|别名|meta)",
    r"^乐曲(?!列表|一览)",
    "^音乐",
]

DEFAULT_ACTION_ALLOWLIST = [
    "send_group_msg",
    "send_private_msg",
    "send_msg",
    "get_group_member_info",
    "get_group_info",
    "get_login_info",
    "get_status",
    "get_version_info",
]
DEFAULT_HELP_IMAGE_PATH = "zhenxun/plugins/pjsk/assets/help.png"
DEFAULT_HELP_URLS = (
    "使用帮助: https://neo.haruki.seiunx.com\n"
    "Haruki工具箱：https://haruki.seiunx.com"
)

REGISTER_CONFIGS = [
    RegisterConfig(
        module=MODULE_NAME,
        key="WS_URL",
        value=DEFAULT_WS_URL,
        default_value=DEFAULT_WS_URL,
        help="HarukiClient 反向 WebSocket 地址",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ACCESS_TOKEN",
        value=DEFAULT_ACCESS_TOKEN,
        default_value=DEFAULT_ACCESS_TOKEN,
        help="可选 OneBot 反向 WS token；留空则不发送 Authorization 头",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="VIRTUAL_SELF_ID",
        value=DEFAULT_VIRTUAL_SELF_ID,
        default_value=DEFAULT_VIRTUAL_SELF_ID,
        help="上报给 Haruki 的 bot QQ；留空时使用接收消息的真寻 bot self_id",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ROUTE_BOT_SELF_ID",
        value=DEFAULT_ROUTE_BOT_SELF_ID,
        default_value=DEFAULT_ROUTE_BOT_SELF_ID,
        help="Haruki 主动发送时使用的真寻 bot self_id；留空时自动选择",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ATTRIBUTION_TTL_SECONDS",
        value=DEFAULT_ATTRIBUTION_TTL_SECONDS,
        default_value=DEFAULT_ATTRIBUTION_TTL_SECONDS,
        help="reply 归因缓存 TTL 秒数，过期后仍转发但不计统计",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ATTRIBUTION_MAX_SIZE",
        value=DEFAULT_ATTRIBUTION_MAX_SIZE,
        default_value=DEFAULT_ATTRIBUTION_MAX_SIZE,
        help="reply 归因缓存最大条目数，超过后按 FIFO 驱逐",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ATTRIBUTION_SWEEP_INTERVAL_SECONDS",
        value=DEFAULT_ATTRIBUTION_SWEEP_INTERVAL_SECONDS,
        default_value=DEFAULT_ATTRIBUTION_SWEEP_INTERVAL_SECONDS,
        help="reply 归因缓存主动清理间隔秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="EVENT_QUEUE_MAX_SIZE",
        value=DEFAULT_EVENT_QUEUE_MAX_SIZE,
        default_value=DEFAULT_EVENT_QUEUE_MAX_SIZE,
        help="连接状态下待转发事件队列上限；满时丢弃新消息并节流日志",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ENABLE_AUTO_SLASH",
        value=DEFAULT_ENABLE_AUTO_SLASH,
        default_value=DEFAULT_ENABLE_AUTO_SLASH,
        help="是否把无斜杠文本指令自动改写为 /指令",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_DISABLED_GROUP_IDS",
        value=DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS,
        default_value=DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS,
        help="这些群只使用显式 / 指令，不自动补 /",
        type=list,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_ENABLED",
        value=DEFAULT_AUTO_SLASH_FUSE_ENABLED,
        default_value=DEFAULT_AUTO_SLASH_FUSE_ENABLED,
        help="是否启用 auto-slash 用户级回声熔断兜底",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_ECHO_SOURCE_SECONDS",
        value=DEFAULT_AUTO_SLASH_FUSE_ECHO_SOURCE_SECONDS,
        default_value=DEFAULT_AUTO_SLASH_FUSE_ECHO_SOURCE_SECONDS,
        help="PJSK 回复后多少秒内同账号 auto-slash 会被视为回响嫌疑",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_WINDOW_SECONDS",
        value=DEFAULT_AUTO_SLASH_FUSE_WINDOW_SECONDS,
        default_value=DEFAULT_AUTO_SLASH_FUSE_WINDOW_SECONDS,
        help="auto-slash 熔断统计窗口秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_MAX_REPLIES",
        value=DEFAULT_AUTO_SLASH_FUSE_MAX_REPLIES,
        default_value=DEFAULT_AUTO_SLASH_FUSE_MAX_REPLIES,
        help="统计窗口内触发多少次有效回复后暂停该用户 auto-slash",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_SUSPEND_SECONDS",
        value=DEFAULT_AUTO_SLASH_FUSE_SUSPEND_SECONDS,
        default_value=DEFAULT_AUTO_SLASH_FUSE_SUSPEND_SECONDS,
        help="auto-slash 熔断后暂停该用户自动补 / 的秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_GROUP_NOTICE_ENABLED",
        value=DEFAULT_AUTO_SLASH_FUSE_GROUP_NOTICE_ENABLED,
        default_value=DEFAULT_AUTO_SLASH_FUSE_GROUP_NOTICE_ENABLED,
        help="auto-slash 熔断触发时是否在群内发送提示",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="AUTO_SLASH_FUSE_SUPERUSER_NOTICE_ENABLED",
        value=DEFAULT_AUTO_SLASH_FUSE_SUPERUSER_NOTICE_ENABLED,
        default_value=DEFAULT_AUTO_SLASH_FUSE_SUPERUSER_NOTICE_ENABLED,
        help="auto-slash 熔断触发时是否私聊通知超级用户",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="HEARTBEAT_INTERVAL_SECONDS",
        value=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        default_value=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        help="OneBot 应用层 heartbeat 间隔秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ACTION_ALLOWLIST",
        value=DEFAULT_ACTION_ALLOWLIST,
        default_value=DEFAULT_ACTION_ALLOWLIST,
        help="允许外部 bot 调用的 OneBot action 白名单",
        type=list,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="USER_FILTER_MODE",
        value=DEFAULT_USER_FILTER_MODE,
        default_value=DEFAULT_USER_FILTER_MODE,
        help="用户过滤模式：blacklist 或 whitelist",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="USER_FILTER_IDS",
        value=[],
        default_value=[],
        help="用户过滤 QQ 列表",
        type=list,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="GROUP_FILTER_MODE",
        value=DEFAULT_GROUP_FILTER_MODE,
        default_value=DEFAULT_GROUP_FILTER_MODE,
        help="高级群硬拦截模式：blacklist 或 whitelist；日常群启停请用 WebUI 插件开关",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="GROUP_FILTER_IDS",
        value=[],
        default_value=[],
        help="高级群硬拦截 QQ 列表；命中后显式 / 与无斜杠消息都不转发",
        type=list,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="CONTENT_FILTER_MODE",
        value=DEFAULT_CONTENT_FILTER_MODE,
        default_value=DEFAULT_CONTENT_FILTER_MODE,
        help="消息内容过滤模式：on、off、blacklist 或 whitelist",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="CONTENT_FILTER_REGEX",
        value=DEFAULT_CONTENT_FILTER_REGEX,
        default_value=DEFAULT_CONTENT_FILTER_REGEX,
        help="消息内容过滤正则列表",
        type=list,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="EXTRA_PREFIXES",
        value=[],
        default_value=[],
        help="额外前缀；命中后绕过内容过滤，并按 PREFIX_REPLACE 改写",
        type=list,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="PREFIX_REPLACE",
        value="",
        default_value="",
        help="额外前缀命中后的替换内容；空字符串表示删除前缀",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TOKEN_REWRITES",
        value={},
        default_value={},
        help="首 token 精确改写表，例如 {'/hyw': '/q'}",
        type=dict,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="HELP_IMAGE_PATH",
        value=DEFAULT_HELP_IMAGE_PATH,
        default_value=DEFAULT_HELP_IMAGE_PATH,
        help="PJSK 本地帮助截图路径；不存在时只发送文字网址",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="HELP_URLS",
        value=DEFAULT_HELP_URLS,
        default_value=DEFAULT_HELP_URLS,
        help="PJSK 本地帮助指令附带发送的网址文本",
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


FilterMode = Literal["on", "off", "blacklist", "whitelist"]


@dataclass(frozen=True)
class IdFilterSettings:
    mode: Literal["blacklist", "whitelist"] = "blacklist"
    ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ContentFilterSettings:
    mode: FilterMode = "on"
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalOneBotAppSettings:
    ws_url: str
    access_token: str
    virtual_self_id: str
    route_bot_self_id: str
    attribution_ttl_seconds: float
    attribution_max_size: int
    attribution_sweep_interval_seconds: float
    event_queue_max_size: int
    enable_auto_slash: bool
    auto_slash_disabled_group_ids: set[str]
    auto_slash_fuse_enabled: bool
    auto_slash_fuse_echo_source_seconds: float
    auto_slash_fuse_window_seconds: float
    auto_slash_fuse_max_replies: int
    auto_slash_fuse_suspend_seconds: float
    auto_slash_fuse_group_notice_enabled: bool
    auto_slash_fuse_superuser_notice_enabled: bool
    heartbeat_interval_seconds: float
    action_allowlist: set[str]
    user_filter: IdFilterSettings
    group_filter: IdFilterSettings
    content_filter: ContentFilterSettings
    extra_prefixes: tuple[str, ...]
    prefix_replace: str
    token_rewrites: dict[str, str]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _as_str_set(value: Any) -> set[str]:
    return {str(item).strip() for item in _as_list(value) if str(item).strip()}


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in _as_list(value) if str(item).strip())


def _as_str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip(): str(target).strip()
        for source, target in value.items()
        if str(source).strip() and str(target).strip()
    }


def _parse_id_filter(mode_value: Any, ids_value: Any) -> IdFilterSettings:
    mode = str(mode_value or "blacklist").strip().lower()
    if mode not in {"blacklist", "whitelist"}:
        mode = "blacklist"
    return IdFilterSettings(mode=mode, ids=_as_str_set(ids_value))


def _parse_content_filter(
    mode_value: Any,
    patterns_value: Any,
) -> ContentFilterSettings:
    mode = str(mode_value or "on").strip().lower()
    if mode not in {"on", "off", "blacklist", "whitelist"}:
        mode = "on"
    return ContentFilterSettings(
        mode=mode,
        patterns=_as_str_tuple(patterns_value),
    )


def get_pjsk_app_settings() -> ExternalOneBotAppSettings:
    config_group = Config.get(MODULE_NAME)
    return ExternalOneBotAppSettings(
        ws_url=str(config_group.get("WS_URL", DEFAULT_WS_URL) or DEFAULT_WS_URL),
        access_token=str(
            config_group.get("ACCESS_TOKEN", DEFAULT_ACCESS_TOKEN) or ""
        ).strip(),
        virtual_self_id=str(
            config_group.get("VIRTUAL_SELF_ID", DEFAULT_VIRTUAL_SELF_ID) or ""
        ).strip(),
        route_bot_self_id=str(
            config_group.get("ROUTE_BOT_SELF_ID", DEFAULT_ROUTE_BOT_SELF_ID) or ""
        ).strip(),
        attribution_ttl_seconds=max(
            1.0,
            float(
                config_group.get(
                    "ATTRIBUTION_TTL_SECONDS",
                    DEFAULT_ATTRIBUTION_TTL_SECONDS,
                )
            ),
        ),
        attribution_max_size=max(
            1,
            int(
                config_group.get(
                    "ATTRIBUTION_MAX_SIZE",
                    DEFAULT_ATTRIBUTION_MAX_SIZE,
                )
            ),
        ),
        attribution_sweep_interval_seconds=max(
            1.0,
            float(
                config_group.get(
                    "ATTRIBUTION_SWEEP_INTERVAL_SECONDS",
                    DEFAULT_ATTRIBUTION_SWEEP_INTERVAL_SECONDS,
                )
            ),
        ),
        event_queue_max_size=max(
            1,
            int(
                config_group.get(
                    "EVENT_QUEUE_MAX_SIZE",
                    DEFAULT_EVENT_QUEUE_MAX_SIZE,
                )
            ),
        ),
        enable_auto_slash=bool(
            config_group.get("ENABLE_AUTO_SLASH", DEFAULT_ENABLE_AUTO_SLASH)
        ),
        auto_slash_disabled_group_ids=_as_str_set(
            config_group.get(
                "AUTO_SLASH_DISABLED_GROUP_IDS",
                DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS,
            )
        ),
        auto_slash_fuse_enabled=bool(
            config_group.get(
                "AUTO_SLASH_FUSE_ENABLED",
                DEFAULT_AUTO_SLASH_FUSE_ENABLED,
            )
        ),
        auto_slash_fuse_echo_source_seconds=max(
            1.0,
            float(
                config_group.get(
                    "AUTO_SLASH_FUSE_ECHO_SOURCE_SECONDS",
                    DEFAULT_AUTO_SLASH_FUSE_ECHO_SOURCE_SECONDS,
                )
            ),
        ),
        auto_slash_fuse_window_seconds=max(
            1.0,
            float(
                config_group.get(
                    "AUTO_SLASH_FUSE_WINDOW_SECONDS",
                    DEFAULT_AUTO_SLASH_FUSE_WINDOW_SECONDS,
                )
            ),
        ),
        auto_slash_fuse_max_replies=max(
            1,
            int(
                config_group.get(
                    "AUTO_SLASH_FUSE_MAX_REPLIES",
                    DEFAULT_AUTO_SLASH_FUSE_MAX_REPLIES,
                )
            ),
        ),
        auto_slash_fuse_suspend_seconds=max(
            1.0,
            float(
                config_group.get(
                    "AUTO_SLASH_FUSE_SUSPEND_SECONDS",
                    DEFAULT_AUTO_SLASH_FUSE_SUSPEND_SECONDS,
                )
            ),
        ),
        auto_slash_fuse_group_notice_enabled=bool(
            config_group.get(
                "AUTO_SLASH_FUSE_GROUP_NOTICE_ENABLED",
                DEFAULT_AUTO_SLASH_FUSE_GROUP_NOTICE_ENABLED,
            )
        ),
        auto_slash_fuse_superuser_notice_enabled=bool(
            config_group.get(
                "AUTO_SLASH_FUSE_SUPERUSER_NOTICE_ENABLED",
                DEFAULT_AUTO_SLASH_FUSE_SUPERUSER_NOTICE_ENABLED,
            )
        ),
        heartbeat_interval_seconds=max(
            1.0,
            float(
                config_group.get(
                    "HEARTBEAT_INTERVAL_SECONDS",
                    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
                )
            ),
        ),
        action_allowlist=_as_str_set(
            config_group.get("ACTION_ALLOWLIST", DEFAULT_ACTION_ALLOWLIST)
        ),
        user_filter=_parse_id_filter(
            config_group.get("USER_FILTER_MODE", DEFAULT_USER_FILTER_MODE),
            config_group.get("USER_FILTER_IDS", []),
        ),
        group_filter=_parse_id_filter(
            config_group.get("GROUP_FILTER_MODE", DEFAULT_GROUP_FILTER_MODE),
            config_group.get("GROUP_FILTER_IDS", []),
        ),
        content_filter=_parse_content_filter(
            config_group.get("CONTENT_FILTER_MODE", DEFAULT_CONTENT_FILTER_MODE),
            config_group.get("CONTENT_FILTER_REGEX", []),
        ),
        extra_prefixes=_as_str_tuple(config_group.get("EXTRA_PREFIXES", [])),
        prefix_replace=str(config_group.get("PREFIX_REPLACE", "") or ""),
        token_rewrites=_as_str_dict(config_group.get("TOKEN_REWRITES", {})),
    )
