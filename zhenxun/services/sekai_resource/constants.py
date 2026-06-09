from __future__ import annotations

from pathlib import Path

from zhenxun.configs.path_config import DATA_PATH

MODULE_NAME = "sekai_resource"
LEGACY_MODULE_NAME = "moesekai"

SERVERS = ("cn", "jp", "tw")
SERVER_SET = set(SERVERS)
SERVER_LABELS = {
    "cn": "国服",
    "jp": "日服",
    "tw": "台服",
}

# 资源服务使用独立数据根，避免后续共享插件继续绑定 MoeSekai 的缓存目录语义。
RESOURCE_DATA_DIR = DATA_PATH / MODULE_NAME
MASTER_DATA_DIR = RESOURCE_DATA_DIR / "master"
STATE_DIR = RESOURCE_DATA_DIR / "state"
ASSET_MIRROR_DIR = RESOURCE_DATA_DIR / "assets"
ASSET_CACHE_INDEX_PATH = STATE_DIR / "asset_cache_index.json"
LEGACY_DATA_DIR = DATA_PATH / LEGACY_MODULE_NAME
LEGACY_MASTER_DATA_DIR = LEGACY_DATA_DIR / "master"
LEGACY_STATE_DIR = LEGACY_DATA_DIR / "state"

for path in (
    RESOURCE_DATA_DIR,
    MASTER_DATA_DIR,
    STATE_DIR,
    ASSET_MIRROR_DIR,
):
    path.mkdir(parents=True, exist_ok=True)


def server_label(server: str | None) -> str:
    if not server:
        return "未设置"
    return SERVER_LABELS.get(server, server.upper())


def resource_data_path(*parts: str) -> Path:
    path = RESOURCE_DATA_DIR
    for part in parts:
        path = path / part
    return path
