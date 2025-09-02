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
    requirements_file: str = "requirements.txt"
    lock_file: str = "uv.lock"
    sync_state_file: str = ".manage_sync_state.json"
    sync_log_dir: str = ".manage_history"
    github_org: str = "my-bot-workspace"
    max_workers: int = Field(default=8, gt=0)
    command_timeout: int = Field(default=1200, gt=0)
    dev_branch: str = "dev"
    default_scope: str = "all"


# --- Logger Initialization ---
logger = setup_logging()


# --- Configuration Loading ---
def load_settings() -> ManagerSettings:
    """Loads settings from 'manage.toml' at the project root."""
    config_file = PROJECT_ROOT / "manage.toml"
    if not config_file.exists():
        return ManagerSettings()  # Return defaults if file not found
    try:
        config_data = tomlkit.parse(config_file.read_text("utf-8"))
        return ManagerSettings.model_validate(config_data.get("manager", {}))
    except (ValidationError, Exception) as e:
        logger.error(f"❌ Failed to parse 'manage.toml': {e}\nUsing defaults.")
        return ManagerSettings()


# --- Exported Constants ---
# Load settings once at startup
settings = load_settings()

# Path constants derived from PROJECT_ROOT and loaded settings
PLUGINS_SRC_DIR = PROJECT_ROOT / settings.plugins_src_dir
PLUGINS_LIST_FILE = PROJECT_ROOT / settings.plugins_list_file
REQUIREMENTS_FILE = PROJECT_ROOT / settings.requirements_file
LOCK_FILE = PROJECT_ROOT / settings.lock_file
SYNC_STATE_FILE = PROJECT_ROOT / settings.sync_state_file
SYNC_LOG_DIR = PROJECT_ROOT / settings.sync_log_dir

# Behavior constants
YOUR_GITHUB_ORG = settings.github_org
MAX_WORKERS = settings.max_workers
COMMAND_TIMEOUT = settings.command_timeout
DEV_BRANCH = settings.dev_branch
DEFAULT_SCOPE = settings.default_scope
