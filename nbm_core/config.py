# nbm_core/config.py
"""
Centralized configuration hub for the nbm application.

This module is the single source of truth for all configurations, including
paths, settings from manage.toml, and the application logger. It establishes
the absolute PROJECT_ROOT to ensure all path operations are robust.
"""

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
import tomlkit

from .log import setup_logging

# --- Absolute Path Anchor ---
# This is the cornerstone of the refactoring, ensuring path stability.
# Path(__file__) is nbm_core/config.py, so .parent.parent is the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --- Pydantic Settings Model ---
class ManagerSettings(BaseModel):
    """Data model for settings loaded from manage.toml."""

    plugins_src_dir: str = "plugins"
    plugins_list_file: str = "plugins.txt"
    bridge_vendor_repo: str = (
        "https://github.com/Genshin-bots/nonebot-plugin-genshinuid.git"
    )
    bridge_vendor_ref: str = ""
    sidecar_core_repo: str = "https://github.com/Genshin-bots/gsuid_core.git"
    sidecar_core_ref: str = ""
    sidecar_runtime_dir: str = "sidecar/.runtime"
    sidecar_plugins_file: str = "sidecar/plugins.toml"
    requirements_file: str = "requirements.txt"
    lock_file: str = "uv.lock"
    sync_state_file: str = ".manage_sync_state.json"
    sync_log_dir: str = ".manage_history"
    github_org: str = "my-bot-workspace"
    max_workers: int = Field(default=8, gt=0)
    command_timeout: int = Field(default=1200, gt=0)
    dev_branch: str = "dev"
    project_branch: str = "dev"
    resources_branch: str = "dev"
    plugin_branch: str | None = None
    resources_repo: str = "https://github.com/PoleSudBot/resources.git"
    prod_sync_extras: list[str] = Field(default_factory=list)
    default_scope: str = "all"


# --- Logger Initialization ---
logger = setup_logging()


# --- Configuration Loading ---
def load_settings() -> ManagerSettings:
    """Loads settings from 'manage.toml' at the project root."""
    config_file = PROJECT_ROOT / "manage.toml"
    if not config_file.exists():
        settings = ManagerSettings()
        settings.plugin_branch = settings.dev_branch
        return settings
    try:
        config_data = tomlkit.parse(config_file.read_text("utf-8"))
        manager_data = config_data.get("manager", {})
        settings = ManagerSettings.model_validate(manager_data)
        if settings.plugin_branch is None:
            settings.plugin_branch = settings.dev_branch
            if "dev_branch" in manager_data and "plugin_branch" not in manager_data:
                logger.warning(
                    "⚠️ `manage.toml` 中的 `dev_branch` 已作为兼容配置读取。"
                    "建议迁移到 `plugin_branch`。"
                )
        if settings.sidecar_runtime_dir != "sidecar/.runtime":
            logger.warning(
                "⚠️ `sidecar_runtime_dir` 当前必须与 "
                "pyproject 中的本地路径依赖保持一致。"
                "已强制回退到 'sidecar/.runtime'。"
            )
            settings.sidecar_runtime_dir = "sidecar/.runtime"
        return settings
    except (ValidationError, Exception) as e:
        logger.error(f"❌ Failed to parse 'manage.toml': {e}\nUsing defaults.")
        settings = ManagerSettings()
        settings.plugin_branch = settings.dev_branch
        return settings


# --- Exported Constants ---
# Load settings once at startup
settings = load_settings()

# Path constants derived from PROJECT_ROOT and loaded settings
PLUGINS_SRC_DIR = PROJECT_ROOT / settings.plugins_src_dir
PLUGINS_LIST_FILE = PROJECT_ROOT / settings.plugins_list_file
SIDECAR_RUNTIME_DIR = PROJECT_ROOT / settings.sidecar_runtime_dir
SIDECAR_DIR = SIDECAR_RUNTIME_DIR.parent
SIDECAR_PLUGINS_FILE = PROJECT_ROOT / settings.sidecar_plugins_file
SIDECAR_COMPOSE_FILE = SIDECAR_DIR / "docker-compose.yml"
SIDECAR_ENV_FILE = SIDECAR_DIR / ".env"
SIDECAR_ENV_EXAMPLE_FILE = SIDECAR_DIR / ".env.example"
BRIDGE_VENDOR_DIR = SIDECAR_RUNTIME_DIR / "vendors" / "nonebot-plugin-genshinuid"
SIDECAR_CORE_DIR = SIDECAR_RUNTIME_DIR / "gsuid_core"
SIDECAR_PLUGIN_DEPENDENCIES_FILE = SIDECAR_RUNTIME_DIR / "sidecar_dependencies.json"
REQUIREMENTS_FILE = PROJECT_ROOT / settings.requirements_file
LOCK_FILE = PROJECT_ROOT / settings.lock_file
SYNC_STATE_FILE = PROJECT_ROOT / settings.sync_state_file
SYNC_LOG_DIR = PROJECT_ROOT / settings.sync_log_dir

# Behavior constants
YOUR_GITHUB_ORG = settings.github_org
MAX_WORKERS = settings.max_workers
COMMAND_TIMEOUT = settings.command_timeout
DEV_BRANCH = settings.dev_branch
PROJECT_BRANCH = settings.project_branch
RESOURCES_BRANCH = settings.resources_branch
PLUGIN_BRANCH = settings.plugin_branch or settings.dev_branch
RESOURCES_REPO = settings.resources_repo
BRIDGE_VENDOR_REPO = settings.bridge_vendor_repo
BRIDGE_VENDOR_REF = settings.bridge_vendor_ref.strip()
SIDECAR_CORE_REPO = settings.sidecar_core_repo
SIDECAR_CORE_REF = settings.sidecar_core_ref.strip()
PROD_SYNC_EXTRAS = tuple(settings.prod_sync_extras)
DEFAULT_SCOPE = settings.default_scope
