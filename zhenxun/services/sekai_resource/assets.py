from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx

from .asset_cache import asset_cache_provider
from .asset_fetcher import asset_fetcher
from .config import get_settings
from .constants import MODULE_NAME
from .runtime import logger

AssetSourceName = Literal["uni", "haruki-main", "haruki-jp-dedicated", "legacy-viewer"]


class AssetProvider:
    def __init__(self, cache_provider=asset_cache_provider) -> None:
        self._cache_provider = cache_provider

    @staticmethod
    def _has_training_costs(card: dict[str, object]) -> bool:
        costs = card.get("specialTrainingCosts")
        return isinstance(costs, list) and bool(costs)

    def has_after_training(self, card: dict[str, object]) -> bool:
        rarity = str(card.get("cardRarityType", "")).strip().lower()
        if rarity in {"rarity_1", "rarity_2", "rarity_birthday"}:
            return False
        if rarity not in {"rarity_3", "rarity_4"}:
            return False
        return self._has_training_costs(card)

    def only_has_after_training(self, card: dict[str, object]) -> bool:
        if not self.has_after_training(card):
            return False
        return (
            str(card.get("initialSpecialTrainingStatus", "")).strip().lower() == "done"
        )

    def _source_bases(self, server: str) -> dict[str, str]:
        return {
            "uni": "https://assets-direct.unipjsk.com",
            "haruki-main": f"https://sekai-assets.haruki.seiunx.com/{server}-assets",
            "haruki-jp-dedicated": "https://sekai-assets-jp.haruki.seiunx.com",
            "legacy-viewer": f"https://storage.sekai.best/sekai-{server}-assets",
        }

    def _ordered_sources(self, server: str) -> list[AssetSourceName]:
        result: list[AssetSourceName] = []
        for source in get_settings().asset_source_order:
            if source == "haruki-jp-dedicated" and server != "jp":
                continue
            if source in {"uni", "haruki-main", "haruki-jp-dedicated", "legacy-viewer"}:
                result.append(source)  # type: ignore[arg-type]
        return result

    def _build_url(self, base: str, relative_path: str) -> str:
        return f"{base.rstrip('/')}/{relative_path.lstrip('/')}"

    def _candidate_paths(
        self, source: AssetSourceName, *, kind: str, **kwargs: str
    ) -> list[str]:
        assetbundle = kwargs["assetbundle"]
        if source == "legacy-viewer":
            return {
                "card_normal": [f"character/member/{assetbundle}/card_normal.png"],
                "card_after_training": [
                    f"character/member/{assetbundle}/card_after_training.png"
                ],
                "card_thumbnail_normal": [f"thumbnail/chara/{assetbundle}_normal.png"],
                "card_thumbnail_after_training": [
                    f"thumbnail/chara/{assetbundle}_after_training.png"
                ],
                "music_jacket": [f"music/jacket/{assetbundle}/{assetbundle}.png"],
                "music_audio": [
                    f"music/short/{assetbundle}_short.{ext}"
                    for ext in get_settings().audio_format_priority
                ],
                "event_banner": [],
                "virtual_live_banner": [
                    f"virtual_live/select/banner/{assetbundle}/{assetbundle}.png"
                ],
                "stamp": [f"stamp/{assetbundle}/{assetbundle}.png"],
                "character": [f"character/member_cutout/{assetbundle}.png"],
            }.get(kind, [])
        return {
            "card_normal": [f"startapp/character/member/{assetbundle}/card_normal.png"],
            "card_after_training": [
                f"startapp/character/member/{assetbundle}/card_after_training.png"
            ],
            "card_thumbnail_normal": [
                f"startapp/thumbnail/chara/{assetbundle}_normal.png"
            ],
            "card_thumbnail_after_training": [
                f"startapp/thumbnail/chara/{assetbundle}_after_training.png"
            ],
            "music_jacket": [f"startapp/music/jacket/{assetbundle}/{assetbundle}.png"],
            "music_audio": [
                f"ondemand/music/short/{assetbundle}_short.{ext}"
                for ext in get_settings().audio_format_priority
            ],
            "event_banner": [
                f"ondemand/event_story/{assetbundle}/screen_image/banner_event_story.png"
            ],
            "virtual_live_banner": [
                f"ondemand/virtual_live/select/banner/{assetbundle}/{assetbundle}.png"
            ],
            "stamp": [f"startapp/stamp/{assetbundle}/{assetbundle}.png"],
            "character": [f"startapp/character/member_cutout/{assetbundle}.png"],
        }.get(kind, [])

    def build_candidate_urls(
        self, server: str, *, kind: str, assetbundle: str
    ) -> list[str]:
        urls: list[str] = []
        bases = self._source_bases(server)
        for source in self._ordered_sources(server):
            base = bases.get(source)
            if not base:
                continue
            for relative_path in self._candidate_paths(
                source,
                kind=kind,
                assetbundle=assetbundle,
            ):
                urls.append(self._build_url(base, relative_path))
        return urls

    def _cache_relative_paths(
        self,
        server: str,
        *,
        kind: str,
        assetbundle: str,
    ) -> list[str]:
        return self._cache_provider.canonical_relative_paths(
            server,
            kind=kind,
            assetbundle=assetbundle,
            audio_extensions=list(get_settings().audio_format_priority),
        )

    def _resolve_cache_relative_path(
        self,
        server: str,
        *,
        kind: str,
        assetbundle: str,
        source_url: str,
    ) -> str | None:
        if kind == "music_audio":
            suffix = urlsplit(source_url).path.rsplit(".", 1)
            if len(suffix) != 2:
                return None
            extension = suffix[1].strip().lower()
            paths = self._cache_provider.canonical_relative_paths(
                server,
                kind=kind,
                assetbundle=assetbundle,
                audio_extensions=[extension],
            )
            return paths[0] if paths else None
        paths = self._cache_relative_paths(server, kind=kind, assetbundle=assetbundle)
        return paths[0] if paths else None

    async def fetch_first_content(
        self,
        server: str,
        *,
        kind: str,
        assetbundle: str,
        timeout: float = 20,
    ) -> bytes | None:
        for relative_path in self._cache_relative_paths(
            server,
            kind=kind,
            assetbundle=assetbundle,
        ):
            if cached := self._cache_provider.get_by_relative_path(relative_path):
                return cached
        urls = self.build_candidate_urls(server, kind=kind, assetbundle=assetbundle)
        if not urls:
            return None
        last_error: Exception | None = None
        for candidate in urls:
            if self._cache_provider.is_known_missing(candidate):
                continue
            try:
                content = await asset_fetcher.fetch_content(candidate, timeout=timeout)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 404:
                    self._cache_provider.record_missing(candidate)
                continue
            except httpx.RequestError as exc:
                last_error = exc
                continue
            self._cache_provider.clear_missing(candidate)
            if relative_path := self._resolve_cache_relative_path(
                server,
                kind=kind,
                assetbundle=assetbundle,
                source_url=candidate,
            ):
                self._cache_provider.set_by_relative_path(
                    relative_path,
                    content,
                    server=server,
                    kind=kind,
                    assetbundle=assetbundle,
                    source_url=candidate,
                )
            return content
        if last_error:
            logger.warning("SekaiResource 资源抓取失败", MODULE_NAME, e=last_error)
        return None

    def get_first_url(self, server: str, *, kind: str, assetbundle: str) -> str | None:
        urls = self.build_candidate_urls(server, kind=kind, assetbundle=assetbundle)
        return urls[0] if urls else None

    def get_local_path(
        self, server: str, *, kind: str, assetbundle: str
    ) -> Path | None:
        return self._cache_provider.get_local_path(
            server,
            kind=kind,
            assetbundle=assetbundle,
        )

    async def get_card_image(
        self,
        server: str,
        assetbundle: str,
        *,
        after_training: bool = False,
        thumbnail: bool = False,
        timeout: float = 20,
    ) -> bytes | None:
        kind = "card_after_training" if after_training else "card_normal"
        if thumbnail:
            kind = (
                "card_thumbnail_after_training"
                if after_training
                else "card_thumbnail_normal"
            )
        return await self.fetch_first_content(
            server,
            kind=kind,
            assetbundle=assetbundle,
            timeout=timeout,
        )

    def get_card_image_local_path(
        self,
        server: str,
        assetbundle: str,
        *,
        after_training: bool = False,
        thumbnail: bool = False,
    ) -> Path | None:
        kind = "card_after_training" if after_training else "card_normal"
        if thumbnail:
            kind = (
                "card_thumbnail_after_training"
                if after_training
                else "card_thumbnail_normal"
            )
        return self.get_local_path(server, kind=kind, assetbundle=assetbundle)

    async def get_music_jacket(
        self,
        server: str,
        assetbundle: str,
        *,
        timeout: float = 20,
    ) -> bytes | None:
        return await self.fetch_first_content(
            server,
            kind="music_jacket",
            assetbundle=assetbundle,
            timeout=timeout,
        )

    async def get_music_audio(
        self,
        server: str,
        assetbundle: str,
        *,
        timeout: float = 20,
    ) -> bytes | None:
        return await self.fetch_first_content(
            server,
            kind="music_audio",
            assetbundle=assetbundle,
            timeout=timeout,
        )

    async def get_event_banner(
        self,
        server: str,
        assetbundle: str,
        *,
        timeout: float = 20,
    ) -> bytes | None:
        return await self.fetch_first_content(
            server,
            kind="event_banner",
            assetbundle=assetbundle,
            timeout=timeout,
        )

    async def get_virtual_live_banner(
        self,
        server: str,
        assetbundle: str,
        *,
        timeout: float = 20,
    ) -> bytes | None:
        return await self.fetch_first_content(
            server,
            kind="virtual_live_banner",
            assetbundle=assetbundle,
            timeout=timeout,
        )

    async def get_stamp_image(
        self,
        server: str,
        assetbundle: str,
        *,
        timeout: float = 20,
    ) -> bytes | None:
        return await self.fetch_first_content(
            server,
            kind="stamp",
            assetbundle=assetbundle,
            timeout=timeout,
        )

    def get_stamp_image_local_path(
        self,
        server: str,
        assetbundle: str,
    ) -> Path | None:
        return self.get_local_path(server, kind="stamp", assetbundle=assetbundle)

    async def get_character_image(
        self,
        server: str,
        assetbundle: str,
        *,
        timeout: float = 20,
    ) -> bytes | None:
        return await self.fetch_first_content(
            server,
            kind="character",
            assetbundle=assetbundle,
            timeout=timeout,
        )


asset_provider = AssetProvider()
