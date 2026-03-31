from __future__ import annotations

import json
from typing import Any

from ..config import get_settings
from ..constants import CHARACTER_CACHE_DIR
from ..storage import BinaryFileCacheStore


class CharacterCacheProvider:
    def __init__(self, store: BinaryFileCacheStore | None = None) -> None:
        self._store = store or BinaryFileCacheStore(CHARACTER_CACHE_DIR)

    def _render_signature(self, character_id: int) -> dict[str, Any]:
        settings = get_settings()
        return {
            "character_id": character_id,
            "site_bases": list(settings.site_bases),
            "viewport_width": settings.deck_viewport_width,
            "top_crop": settings.character_top_crop,
            "screenshot_quality": settings.screenshot_quality,
            "viewer_color_scheme": "light",
            "viewer_asset_source": "uni",
            "viewer_server_source": "jp",
            "viewer_theme_char_id": str(character_id),
        }

    def build_cache_key(self, character_id: int) -> str:
        return json.dumps(
            self._render_signature(character_id),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def get(self, character_id: int) -> bytes | None:
        return self._store.load(
            self.build_cache_key(character_id),
            ttl_seconds=get_settings().character_cache_ttl_seconds,
            suffix=".png",
        )

    def set(self, character_id: int, payload: bytes) -> None:
        self._store.save(
            self.build_cache_key(character_id),
            payload,
            suffix=".png",
        )

    def delete(self, character_id: int) -> None:
        self._store.delete(self.build_cache_key(character_id), suffix=".png")


character_cache_provider = CharacterCacheProvider()
