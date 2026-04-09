from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

from ..adapters.runtime import AsyncHttpx
from ..cache import MoeSekaiCache


class HubProvider:
    MANGA_INDEX_URL = (
        "https://raw.githubusercontent.com/moe-sekai/MoeSekai-Hub/main/mangas/mangas.json"
    )
    EVENT_BVID_URL = (
        "https://raw.githubusercontent.com/moe-sekai/MoeSekai-Hub/main/data/event_bvid/events_bilibili.json"
    )
    MUSIC_ALIAS_INDEX_URL = (
        "https://raw.githubusercontent.com/moe-sekai/MoeSekai-Hub/main/"
        "data/music_alias/music_aliases.json"
    )
    HARUKI_MUSIC_ALIAS_API = "https://public-api.haruki.seiunx.com/alias/v1/music/{music_id}"

    async def get_manga_index(self) -> list[dict[str, Any]]:
        cache_key = "hub:mangas:index"
        cached = await MoeSekaiCache.get(cache_key)
        if cached is not None:
            return cached
        payload = await AsyncHttpx.get_json(self.MANGA_INDEX_URL, raise_on_failure=True)
        items: list[Any]
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("items"), list):
                items = payload["items"]
            elif isinstance(payload.get("mangas"), list):
                items = payload["mangas"]
            else:
                items = list(payload.values())
        else:
            items = []
        result: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            image_url = str(item.get("manga") or item.get("image_url") or "").strip()
            bilibili_url = str(item.get("url") or item.get("bilibili_url") or "").strip()
            try:
                manga_id = int(item.get("id"))
            except (TypeError, ValueError):
                manga_id = 0
            result.append(
                {
                    "id": manga_id,
                    "title": str(item.get("title", "")).strip(),
                    "image_url": image_url,
                    "url": bilibili_url,
                }
            )
        await MoeSekaiCache.set(cache_key, result, ttl=3600)
        return result

    async def get_random_manga(self) -> dict[str, Any] | None:
        items = await self.get_manga_index()
        if not items:
            return None
        selected = dict(random.choice(items))
        manga_id = selected.get("id")
        if manga_id:
            selected.setdefault("image_url", f"https://moe.exmeaning.com/mangas/{manga_id}.png")
        return selected

    async def get_manga_by_id(self, manga_id: int) -> dict[str, Any] | None:
        items = await self.get_manga_index()
        for item in items:
            try:
                item_id = int(item.get("id", 0))
            except (TypeError, ValueError):
                continue
            if item_id != manga_id:
                continue
            selected = dict(item)
            selected.setdefault(
                "image_url",
                f"https://moe.exmeaning.com/mangas/{manga_id}.png",
            )
            return selected
        return None

    async def get_event_bvid_mapping(self) -> list[dict[str, Any]]:
        cache_key = "hub:event_bvid"
        cached = await MoeSekaiCache.get(cache_key)
        if cached is not None:
            return cached
        payload = await AsyncHttpx.get_json(self.EVENT_BVID_URL, raise_on_failure=True)
        result = payload.get("events", []) if isinstance(payload, dict) else []
        if not isinstance(result, list):
            result = []
        await MoeSekaiCache.set(cache_key, result, ttl=3600)
        return result

    async def get_event_bilibili_url(self, event_id: int) -> str | None:
        for item in await self.get_event_bvid_mapping():
            try:
                if int(item.get("event_id")) == event_id:
                    return item.get("bilibili_url")
            except (TypeError, ValueError):
                continue
        return None

    async def get_music_alias_index(self) -> list[dict[str, Any]]:
        cache_key = "hub:music_alias:index"
        cached = await MoeSekaiCache.get(cache_key)
        if cached is not None:
            return cached
        payload = await AsyncHttpx.get_json(
            self.MUSIC_ALIAS_INDEX_URL,
            raise_on_failure=True,
        )
        musics = payload.get("musics", []) if isinstance(payload, dict) else []
        result: list[dict[str, Any]] = []
        if isinstance(musics, list):
            for item in musics:
                if not isinstance(item, dict):
                    continue
                try:
                    music_id = int(item.get("music_id"))
                except (TypeError, ValueError):
                    continue
                aliases = item.get("aliases", [])
                normalized_aliases = (
                    [str(alias).strip() for alias in aliases if str(alias).strip()]
                    if isinstance(aliases, list)
                    else []
                )
                result.append(
                    {
                        "music_id": music_id,
                        "title": str(item.get("title", "")).strip(),
                        "aliases": normalized_aliases,
                    }
                )
        await MoeSekaiCache.set(cache_key, result, ttl=21600)
        return result

    async def get_music_aliases(self, music_id: int) -> list[str]:
        for item in await self.get_music_alias_index():
            try:
                if int(item.get("music_id", 0)) == music_id:
                    aliases = item.get("aliases", [])
                    return aliases if isinstance(aliases, list) else []
            except (TypeError, ValueError):
                continue
        return []

    def read_character_alias_seed(self, sql_path: Path) -> list[tuple[str, str]]:
        if not sql_path.exists():
            return []
        pattern = re.compile(
            r"VALUES\s*\(\s*\d+\s*,\s*'(?P<alias>.*?)'\s*,\s*(?P<character_id>\d+)\s*\)",
            re.IGNORECASE,
        )
        result: list[tuple[str, str]] = []
        for line in sql_path.read_text(encoding="utf-8").splitlines():
            matched = pattern.search(line)
            if not matched:
                continue
            alias = matched.group("alias").replace("\\'", "'").strip()
            if alias:
                result.append((alias, matched.group("character_id")))
        return result


hub_provider = HubProvider()
