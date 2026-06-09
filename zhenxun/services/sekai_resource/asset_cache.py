from __future__ import annotations

from pathlib import Path, PurePosixPath
import time
from typing import Any

from .config import get_settings
from .constants import ASSET_CACHE_INDEX_PATH, ASSET_MIRROR_DIR, STATE_DIR
from .storage import JsonStateStore, PathBinaryFileStore


class AssetCacheProvider:
    def __init__(
        self,
        store: PathBinaryFileStore | None = None,
        manifest_store: JsonStateStore | None = None,
        miss_store: JsonStateStore | None = None,
    ) -> None:
        self._store = store or PathBinaryFileStore(ASSET_MIRROR_DIR)
        self._manifest_store = manifest_store or JsonStateStore(ASSET_CACHE_INDEX_PATH)
        self._miss_store = miss_store or JsonStateStore(
            STATE_DIR / "asset_miss_cache.json"
        )
        self._miss_state_cache: dict[str, float] | None = None

    @staticmethod
    def canonical_relative_paths(
        server: str,
        *,
        kind: str,
        assetbundle: str,
        audio_extensions: list[str] | None = None,
    ) -> list[str]:
        normalized_server = str(server).strip().lower()
        normalized_bundle = str(assetbundle).strip()
        if not normalized_server or not normalized_bundle:
            return []
        paths = {
            "card_normal": [
                f"{normalized_server}/startapp/character/member/{normalized_bundle}/card_normal.png"
            ],
            "card_after_training": [
                f"{normalized_server}/startapp/character/member/{normalized_bundle}/card_after_training.png"
            ],
            "card_thumbnail_normal": [
                f"{normalized_server}/startapp/thumbnail/chara/{normalized_bundle}_normal.png"
            ],
            "card_thumbnail_after_training": [
                f"{normalized_server}/startapp/thumbnail/chara/{normalized_bundle}_after_training.png"
            ],
            "card_cutout_normal": [
                f"{normalized_server}/startapp/character/member_cutout/{normalized_bundle}/normal.png"
            ],
            "card_cutout_after_training": [
                f"{normalized_server}/startapp/character/member_cutout/{normalized_bundle}/after_training.png"
            ],
            "music_jacket": [
                f"{normalized_server}/startapp/music/jacket/{normalized_bundle}/{normalized_bundle}.png"
            ],
            "music_audio": [
                f"{normalized_server}/ondemand/music/short/{normalized_bundle}_short.{extension}"
                for extension in (audio_extensions or ["mp3", "flac"])
            ],
            "event_banner": [
                f"{normalized_server}/ondemand/event_story/{normalized_bundle}/screen_image/banner_event_story.png"
            ],
            "virtual_live_banner": [
                f"{normalized_server}/ondemand/virtual_live/select/banner/{normalized_bundle}/{normalized_bundle}.png"
            ],
            "stamp": [
                f"{normalized_server}/startapp/stamp/{normalized_bundle}/{normalized_bundle}.png"
            ],
            "character": [
                f"{normalized_server}/startapp/character/member_cutout/{normalized_bundle}.png"
            ],
        }
        return paths.get(kind, [])

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        normalized = PurePosixPath(str(relative_path).strip("/"))
        if not normalized.parts:
            raise ValueError("relative_path 不能为空")
        return normalized.as_posix()

    def get(self, server: str, *, kind: str, assetbundle: str) -> bytes | None:
        for relative_path in self.canonical_relative_paths(
            server,
            kind=kind,
            assetbundle=assetbundle,
            audio_extensions=list(get_settings().audio_format_priority),
        ):
            if payload := self.get_by_relative_path(relative_path):
                return payload
        return None

    def get_by_relative_path(self, relative_path: str) -> bytes | None:
        return self._store.load(self._normalize_relative_path(relative_path))

    def get_local_path(
        self,
        server: str,
        *,
        kind: str,
        assetbundle: str,
    ) -> Path | None:
        for relative_path in self.canonical_relative_paths(
            server,
            kind=kind,
            assetbundle=assetbundle,
            audio_extensions=list(get_settings().audio_format_priority),
        ):
            if path := self.get_local_path_by_relative_path(relative_path):
                return path
        return None

    def get_local_path_by_relative_path(self, relative_path: str) -> Path | None:
        normalized_path = self._normalize_relative_path(relative_path)
        manifest_entry = self._load_manifest().get(normalized_path)
        if manifest_entry:
            local_path_value = manifest_entry.get("local_path")
            if isinstance(local_path_value, str) and local_path_value.strip():
                local_path = Path(local_path_value)
                if local_path.is_file():
                    return local_path
        # manifest 可能因异常中断而缺失，但缓存文件本体仍在；这里回退到规范缓存路径，
        # 避免把本可直接走本地文件的图片重新退回 raw/base64 发送。
        local_path = self._store.resolve_path(normalized_path)
        return local_path if local_path.is_file() else None

    def set(
        self,
        server: str,
        *,
        kind: str,
        assetbundle: str,
        payload: bytes,
        source_url: str | None = None,
    ) -> str:
        relative_paths = self.canonical_relative_paths(
            server,
            kind=kind,
            assetbundle=assetbundle,
            audio_extensions=list(get_settings().audio_format_priority),
        )
        if not relative_paths:
            raise ValueError(f"不支持的 asset kind: {kind}")
        self.set_by_relative_path(
            relative_paths[0],
            payload,
            server=server,
            kind=kind,
            assetbundle=assetbundle,
            source_url=source_url,
        )
        return relative_paths[0]

    def set_by_relative_path(
        self,
        relative_path: str,
        payload: bytes,
        *,
        server: str,
        kind: str,
        assetbundle: str,
        source_url: str | None = None,
    ) -> str:
        normalized_path = self._normalize_relative_path(relative_path)
        absolute_path = self._store.save(normalized_path, payload)
        manifest = self._load_manifest()
        manifest[normalized_path] = {
            "local_path": str(absolute_path),
            "server": str(server).strip().lower(),
            "kind": str(kind).strip(),
            "assetbundle": str(assetbundle).strip(),
            "source_url": str(source_url).strip() if source_url else None,
            "size": len(payload),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        self._manifest_store.save(manifest)
        return normalized_path

    def get_manifest_entry(self, relative_path: str) -> dict[str, Any] | None:
        return self._load_manifest().get(self._normalize_relative_path(relative_path))

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        payload = self._manifest_store.load({})
        if not isinstance(payload, dict):
            return {}
        return {
            str(path): value
            for path, value in payload.items()
            if isinstance(path, str) and isinstance(value, dict)
        }

    @staticmethod
    def _normalize_miss_state(
        payload: Any, *, now: float
    ) -> tuple[dict[str, float], bool]:
        if not isinstance(payload, dict):
            return {}, False
        changed = False
        result: dict[str, float] = {}
        for url, expire_at in payload.items():
            try:
                expire_ts = float(expire_at)
            except (TypeError, ValueError):
                changed = True
                continue
            if expire_ts <= now:
                changed = True
                continue
            result[str(url)] = expire_ts
        return result, changed

    def _load_miss_state(self) -> dict[str, float]:
        now = time.time()
        if self._miss_state_cache is None:
            payload = self._miss_store.load({})
            normalized, changed = self._normalize_miss_state(payload, now=now)
            self._miss_state_cache = normalized
            if changed:
                self._miss_store.save(normalized)
            return self._miss_state_cache

        expired_urls = [
            url for url, expire_at in self._miss_state_cache.items() if expire_at <= now
        ]
        if not expired_urls:
            return self._miss_state_cache

        for url in expired_urls:
            self._miss_state_cache.pop(url, None)
        self._miss_store.save(self._miss_state_cache)
        return self._miss_state_cache

    def is_known_missing(self, url: str) -> bool:
        return url in self._load_miss_state()

    def record_missing(self, url: str) -> None:
        payload = self._load_miss_state()
        payload[url] = time.time() + get_settings().asset_miss_cache_ttl_seconds
        self._miss_state_cache = payload
        self._miss_store.save(payload)

    def clear_missing(self, url: str) -> None:
        payload = self._load_miss_state()
        if url in payload:
            payload.pop(url, None)
            self._miss_state_cache = payload
            self._miss_store.save(payload)


asset_cache_provider = AssetCacheProvider()
