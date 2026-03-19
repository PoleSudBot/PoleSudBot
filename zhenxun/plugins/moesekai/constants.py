from __future__ import annotations

from pathlib import Path

from zhenxun.configs.path_config import DATA_PATH

MODULE_NAME = "moesekai"

SERVERS = ("cn", "jp", "tw")
SERVER_SET = set(SERVERS)
ALL_SERVERS_KEYWORD = "all"
SERVER_LABELS = {
    "cn": "国服",
    "jp": "日服",
    "tw": "台服",
}
PREDICTION_SUPPORTED_SERVERS = {"cn", "jp"}
YCX_SUPPORTED_SERVERS = {"cn", "jp"}

DIFFICULTY_ALIASES = {
    "easy": "easy",
    "ez": "easy",
    "normal": "normal",
    "nm": "normal",
    "hard": "hard",
    "hd": "hard",
    "expert": "expert",
    "ex": "expert",
    "master": "master",
    "ma": "master",
    "append": "append",
    "apd": "append",
}

LIVE_TYPE_ALIASES = {
    "multi": "multi",
    "多人": "multi",
    "协力": "multi",
    "solo": "solo",
    "单人": "solo",
    "auto": "auto",
    "自动": "auto",
    "cheerful": "cheerful",
    "cheer": "cheerful",
    "嘉年华": "cheerful",
}

PLUGIN_DATA_DIR = DATA_PATH / MODULE_NAME
MASTER_DATA_DIR = PLUGIN_DATA_DIR / "master"
SCREENSHOT_CACHE_DIR = PLUGIN_DATA_DIR / "screenshots"

PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
MASTER_DATA_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def server_path_prefix(server: str) -> str:
    return "" if server == "cn" else f"{server}/"


def server_label(server: str) -> str:
    return SERVER_LABELS.get(server, server.upper())


def normalize_deck_difficulty(value: str | None) -> str | None:
    if not value:
        return None
    return DIFFICULTY_ALIASES.get(value.lower().strip())


def normalize_live_type(value: str | None) -> str | None:
    if not value:
        return None
    return LIVE_TYPE_ALIASES.get(value.lower().strip())
