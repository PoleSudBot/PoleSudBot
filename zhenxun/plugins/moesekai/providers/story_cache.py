from __future__ import annotations

import json
from typing import Any

from ..config import get_settings
from ..constants import STORY_CACHE_DIR
from ..storage import BinaryFileCacheStore


class StoryCacheProvider:
    def __init__(self, store: BinaryFileCacheStore | None = None) -> None:
        self._store = store or BinaryFileCacheStore(STORY_CACHE_DIR)

    def _render_signature(self, event_id: int) -> dict[str, Any]:
        settings = get_settings()
        return {
            "event_id": event_id,
            "site_bases": list(settings.site_bases),
            "viewport_width": settings.character_viewport_width,
            "top_crop": settings.story_top_crop,
            "screenshot_quality": settings.screenshot_quality,
        }

    def build_cache_key(self, event_id: int) -> str:
        return json.dumps(
            self._render_signature(event_id),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def get(self, event_id: int) -> bytes | None:
        return self._store.load(
            self.build_cache_key(event_id),
            ttl_seconds=0,
            suffix=".png",
        )

    def set(self, event_id: int, payload: bytes) -> None:
        self._store.save(
            self.build_cache_key(event_id),
            payload,
            suffix=".png",
        )

    def delete(self, event_id: int) -> None:
        self._store.delete(self.build_cache_key(event_id), suffix=".png")


story_cache_provider = StoryCacheProvider()
