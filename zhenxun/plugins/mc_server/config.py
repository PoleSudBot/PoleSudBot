from __future__ import annotations

from dataclasses import dataclass

from zhenxun.configs.config import Config
from zhenxun.configs.utils import RegisterConfig

from .constants import DEFAULT_MC_PORT, DEFAULT_RCON_PORT, MODULE_NAME


@dataclass(frozen=True)
class McServerPreset:
    name: str
    host: str
    port: int
    rcon_host: str
    rcon_port: int
    rcon_password: str
    log_path: str
    bluemap_base_url: str
    bluemap_map_ids: list[str]


@dataclass(frozen=True)
class McServerSettings:
    poll_interval_seconds: int
    request_timeout_seconds: int
    disconnect_notify_threshold: int
    sample_interval_seconds: int
    bind_flow_timeout_seconds: int
    rejoin_suppress_seconds: int
    min_online_session_seconds: int
    admin_level: int
    rcon_level: int
    chat_format: str
    render_enabled: bool
    max_chart_points: int
    server_presets: dict[str, McServerPreset]


REGISTER_CONFIGS = [
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_POLL_INTERVAL_SECONDS",
        value=60,
        default_value=60,
        help="MC服务器状态轮询间隔（秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_REQUEST_TIMEOUT_SECONDS",
        value=8,
        default_value=8,
        help="MC状态、BlueMap、RCON请求超时时间（秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_DISCONNECT_NOTIFY_THRESHOLD",
        value=3,
        default_value=3,
        help="连续多少次状态查询失败后认为服务器断连",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_SAMPLE_INTERVAL_SECONDS",
        value=300,
        default_value=300,
        help="在线人数采样最小间隔（秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_BIND_FLOW_TIMEOUT_SECONDS",
        value=120,
        default_value=120,
        help="MC绑定向导单步等待超时时间（秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_REJOIN_SUPPRESS_SECONDS",
        value=30,
        default_value=30,
        help="玩家短时间重进时抑制进退服播报的窗口（秒）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_MIN_ONLINE_SESSION_SECONDS",
        value=180,
        default_value=180,
        help="低于该时长的在线段不进入统计（秒，0为不过滤）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_ADMIN_LEVEL",
        value=5,
        default_value=5,
        help="群内MC配置管理所需真寻权限等级",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_RCON_LEVEL",
        value=9,
        default_value=9,
        help="群内执行RCON命令所需真寻权限等级",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_CHAT_FORMAT",
        value="[{sender}] {message}",
        default_value="[{sender}] {message}",
        help="群消息同步到游戏内的显示格式，可用 {sender}/{message}",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_RENDER_ENABLED",
        value=True,
        default_value=True,
        help="是否优先使用htmlrender渲染MC状态与统计图片",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_MAX_CHART_POINTS",
        value=96,
        default_value=96,
        help="人数图最多渲染的数据点数量",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MC_SERVER_PRESETS",
        value={},
        default_value={},
        help=(
            "MC服务器预设，格式为 "
            "{name: {host, port, rcon_host, rcon_port, rcon_password, "
            "log_path, bluemap_base_url, bluemap_map_ids}}"
        ),
        type=dict,
    ),
]


def _as_int(value: object, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _as_port(value: object, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if not 1 <= port <= 65535:
        return default
    return port


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default if value is None else bool(value)


def _as_text(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _parse_server_presets(value: object) -> dict[str, McServerPreset]:
    if not isinstance(value, dict):
        return {}
    presets: dict[str, McServerPreset] = {}
    for raw_name, raw_config in value.items():
        if not isinstance(raw_config, dict):
            continue
        name = str(raw_name).strip()
        host = _as_text(raw_config.get("host"))
        if not name or not host:
            continue
        # 预设只负责把配置归一化，地址格式与连通性仍在绑定时统一验证。
        presets[name] = McServerPreset(
            name=name,
            host=host,
            port=_as_port(raw_config.get("port", DEFAULT_MC_PORT), DEFAULT_MC_PORT),
            rcon_host=_as_text(raw_config.get("rcon_host")),
            rcon_port=_as_port(
                raw_config.get("rcon_port", DEFAULT_RCON_PORT),
                DEFAULT_RCON_PORT,
            ),
            rcon_password=_as_text(raw_config.get("rcon_password")),
            log_path=_as_text(raw_config.get("log_path")),
            bluemap_base_url=_as_text(raw_config.get("bluemap_base_url")),
            bluemap_map_ids=_as_text_list(raw_config.get("bluemap_map_ids")),
        )
    return presets


def get_settings() -> McServerSettings:
    raw = Config.get(MODULE_NAME)
    return McServerSettings(
        poll_interval_seconds=_as_int(raw.get("MC_POLL_INTERVAL_SECONDS", 60), 60, 15),
        request_timeout_seconds=_as_int(raw.get("MC_REQUEST_TIMEOUT_SECONDS", 8), 8, 2),
        disconnect_notify_threshold=_as_int(
            raw.get("MC_DISCONNECT_NOTIFY_THRESHOLD", 3), 3, 1
        ),
        sample_interval_seconds=_as_int(
            raw.get("MC_SAMPLE_INTERVAL_SECONDS", 300), 300, 60
        ),
        bind_flow_timeout_seconds=_as_int(
            raw.get("MC_BIND_FLOW_TIMEOUT_SECONDS", 120), 120, 30
        ),
        rejoin_suppress_seconds=_as_int(
            raw.get("MC_REJOIN_SUPPRESS_SECONDS", 30), 30, 5
        ),
        min_online_session_seconds=_as_int(
            raw.get("MC_MIN_ONLINE_SESSION_SECONDS", 180), 180, 0
        ),
        admin_level=_as_int(raw.get("MC_ADMIN_LEVEL", 5), 5, 1),
        rcon_level=_as_int(raw.get("MC_RCON_LEVEL", 9), 9, 1),
        chat_format=str(raw.get("MC_CHAT_FORMAT", "[{sender}] {message}")),
        render_enabled=_as_bool(raw.get("MC_RENDER_ENABLED", True), True),
        max_chart_points=_as_int(raw.get("MC_MAX_CHART_POINTS", 96), 96, 12),
        server_presets=_parse_server_presets(raw.get("MC_SERVER_PRESETS", {})),
    )
