from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import tomlkit

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .models import ManagerSettings

CONFIG_MODULE = "update-manager"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_SETTING_KEYS = {
    "AUTO_ENABLED",
    "AUTO_SCOPE",
    "AUTO_TIME",
    "AUTO_RESTART",
    "AUTO_RUN_UV_SYNC",
    "GIT_TIMEOUT",
    "CLONE_TIMEOUT",
    "MAX_PARALLEL",
}
BOOL_SETTING_KEYS = {"AUTO_ENABLED", "AUTO_RESTART", "AUTO_RUN_UV_SYNC"}
SCOPE_VALUES = ("root", "resources", "plugin")
AUTO_TIME_RE = re.compile(r"^(\d{2}):(\d{2})$")


def _manager_table(project_root: Path) -> dict[str, Any]:
    """读取 manage.toml 的 manager 表；缺失或解析失败时回退到空配置。"""
    config_file = project_root / "manage.toml"
    if not config_file.exists():
        return {}
    try:
        data = tomlkit.parse(config_file.read_text("utf-8"))
    except Exception:
        logger.warning(f"manage.toml 解析失败，回退到空配置: {config_file}")
        return {}
    table = data.get("manager", {})
    return dict(table) if isinstance(table, dict) else {}


def register_plugin_configs() -> None:
    """注册更新管理自己的运行时配置，默认保持自动更新关闭。"""
    Config.set_name(CONFIG_MODULE, "更新管理")
    Config.add_plugin_config(
        CONFIG_MODULE,
        "AUTO_ENABLED",
        False,
        help="是否启用更新管理自动更新",
        default_value=False,
        type=bool,
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "AUTO_SCOPE",
        ["root", "resources", "plugin"],
        help="自动更新范围",
        default_value=["root", "resources", "plugin"],
        type=list[str],
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "AUTO_TIME",
        "04:30",
        help="自动更新执行时间，格式 HH:MM",
        default_value="04:30",
        type=str,
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "AUTO_RESTART",
        False,
        help="自动更新后是否自动重启，当前默认关闭且不会执行重启",
        default_value=False,
        type=bool,
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "AUTO_RUN_UV_SYNC",
        False,
        help="自动更新后是否自动执行 uv sync，当前默认关闭",
        default_value=False,
        type=bool,
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "GIT_TIMEOUT",
        120,
        help="普通 git 命令超时时间",
        default_value=120,
        type=int,
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "CLONE_TIMEOUT",
        600,
        help="clone 命令超时时间",
        default_value=600,
        type=int,
    )
    Config.add_plugin_config(
        CONFIG_MODULE,
        "MAX_PARALLEL",
        4,
        help="批量仓库操作并发数",
        default_value=4,
        type=int,
    )


def load_settings(project_root: Path | None = None) -> ManagerSettings:
    """合并 manage.toml 与插件配置，生成更新管理运行时设置。"""
    root = project_root or PROJECT_ROOT
    manager = _manager_table(root)
    plugin_branch = manager.get("plugin_branch") or manager.get("dev_branch") or "dev"
    auto_scope = Config.get_config(
        CONFIG_MODULE, "AUTO_SCOPE", ["root", "resources", "plugin"]
    )
    return ManagerSettings(
        project_root=root,
        github_org=str(manager.get("github_org", "PoleSudBot")),
        project_branch=str(manager.get("project_branch", "dev")),
        resources_branch=str(manager.get("resources_branch", "dev")),
        plugin_branch=str(plugin_branch),
        resources_repo=str(
            manager.get("resources_repo", "https://github.com/PoleSudBot/resources.git")
        ),
        plugins_src_dir=str(manager.get("plugins_src_dir", "plugins")),
        plugins_list_file=str(manager.get("plugins_list_file", "plugins.txt")),
        git_timeout=max(5, int(Config.get_config(CONFIG_MODULE, "GIT_TIMEOUT", 120))),
        clone_timeout=max(
            30, int(Config.get_config(CONFIG_MODULE, "CLONE_TIMEOUT", 600))
        ),
        max_parallel=max(1, int(Config.get_config(CONFIG_MODULE, "MAX_PARALLEL", 4))),
        auto_enabled=bool(Config.get_config(CONFIG_MODULE, "AUTO_ENABLED", False)),
        auto_scope=[s for s in auto_scope if s in {"root", "resources", "plugin"}],
        auto_time=str(Config.get_config(CONFIG_MODULE, "AUTO_TIME", "04:30")),
        auto_restart=bool(Config.get_config(CONFIG_MODULE, "AUTO_RESTART", False)),
        auto_run_uv_sync=bool(
            Config.get_config(CONFIG_MODULE, "AUTO_RUN_UV_SYNC", False)
        ),
    )


def _validate_bool(key: str, value: Any) -> bool:
    """校验布尔开关，避免字符串 false 被 bool() 误判为真。"""
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


def _validate_int(key: str, value: Any, minimum: int) -> int:
    """校验数值配置，避免坏值落盘后破坏设置读取。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} 必须是整数")
    if value < minimum:
        raise ValueError(f"{key} 不能小于 {minimum}")
    return value


def _validate_auto_scope(value: Any) -> list[str]:
    """校验自动更新范围，只保留明确支持的仓库类型。"""
    if not isinstance(value, list):
        raise ValueError("AUTO_SCOPE 必须是列表")
    seen: set[str] = set()
    scopes: list[str] = []
    for item in value:
        if item not in SCOPE_VALUES:
            raise ValueError(f"AUTO_SCOPE 包含不支持的范围：{item}")
        if item not in seen:
            scopes.append(item)
            seen.add(item)
    return scopes


def _validate_auto_time(value: Any) -> str:
    """校验定时任务时间格式，防止调度注册阶段才失败。"""
    if not isinstance(value, str):
        raise ValueError("AUTO_TIME 必须是 HH:MM 字符串")
    match = AUTO_TIME_RE.match(value)
    if not match:
        raise ValueError("AUTO_TIME 必须是 HH:MM 格式")
    hour, minute = (int(part) for part in match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("AUTO_TIME 必须是有效时间")
    return value


def _normalize_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    """一次性校验并归一化设置，确保写配置前不会留下半更新状态。"""
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        upper_key = key.upper()
        if upper_key not in ALLOWED_SETTING_KEYS:
            raise ValueError(f"未知配置项：{key}")
        if upper_key in BOOL_SETTING_KEYS:
            normalized[upper_key] = _validate_bool(upper_key, value)
        elif upper_key == "AUTO_SCOPE":
            normalized[upper_key] = _validate_auto_scope(value)
        elif upper_key == "AUTO_TIME":
            normalized[upper_key] = _validate_auto_time(value)
        elif upper_key == "GIT_TIMEOUT":
            normalized[upper_key] = _validate_int(upper_key, value, 5)
        elif upper_key == "CLONE_TIMEOUT":
            normalized[upper_key] = _validate_int(upper_key, value, 30)
        elif upper_key == "MAX_PARALLEL":
            normalized[upper_key] = _validate_int(upper_key, value, 1)
    return normalized


def save_runtime_settings(values: dict[str, Any]) -> ManagerSettings:
    """写入 update-manager 模块配置，只允许修改本功能自己的开关。"""
    normalized = _normalize_runtime_settings(values)
    for key, value in normalized.items():
        Config.set_config(CONFIG_MODULE, key, value, auto_save=True)
    return load_settings()
