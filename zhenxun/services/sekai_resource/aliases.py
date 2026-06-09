from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Literal

from .constants import LEGACY_STATE_DIR, STATE_DIR
from .master_data import master_data_provider
from .runtime import AsyncHttpx
from .storage import JsonStateStore

AliasTargetType = Literal["character", "music"]
AliasMatchSource = Literal["id", "system", "official"]

MUSIC_ALIAS_INDEX_URL = (
    "https://raw.githubusercontent.com/moe-sekai/MoeSekai-Hub/main/"
    "data/music_alias/music_aliases.json"
)
MUSIC_ALIAS_SYNC_INTERVAL_SECONDS = 21_600
_MUSIC_ALIAS_SNAPSHOT = JsonStateStore(STATE_DIR / "music_alias_snapshot.json")
_MUSIC_ALIAS_STATE = JsonStateStore(STATE_DIR / "alias_state.json")
_LEGACY_MUSIC_ALIAS_SNAPSHOT = JsonStateStore(
    LEGACY_STATE_DIR / "music_alias_snapshot.json"
)
_CHARACTER_ALIAS_SEED = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "moesekai"
    / "seeds"
    / "character_alias.sql"
)


@dataclass(frozen=True)
class AliasResolveResult:
    target_type: AliasTargetType
    target_id: str
    canonical_name: str
    matched_text: str
    matched_source: AliasMatchSource


@dataclass(frozen=True)
class AliasProfile:
    target_type: AliasTargetType
    target_id: str
    canonical_name: str
    aliases: list[str]


@dataclass(frozen=True)
class _EntityRecord:
    target_id: str
    canonical_name: str
    official_names: list[str]


def normalize_alias(value: str) -> str:
    return "".join(str(value).lower().strip().split())


def _dedupe_texts(values: list[str], *, exclude: list[str] | None = None) -> list[str]:
    excluded = {normalize_alias(item) for item in (exclude or []) if item.strip()}
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        normalized = normalize_alias(text)
        if not normalized or normalized in excluded or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return result


def _normalize_snapshot_payload(payload: Any) -> list[dict[str, Any]]:
    items = payload.get("musics", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("music_id") or item.get("id") or "").strip()
        if not target_id.isdigit():
            continue
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        result.append(
            {
                "music_id": int(target_id),
                "title": str(item.get("title", "")).strip(),
                "aliases": _dedupe_texts([str(alias) for alias in aliases]),
            }
        )
    return result


async def sync_music_aliases(*, force: bool = False) -> list[dict[str, Any]]:
    state = _MUSIC_ALIAS_STATE.load({})
    last_sync = state.get("music_alias_sync_at") if isinstance(state, dict) else None
    if not force and isinstance(last_sync, int | float):
        import time

        if time.time() - float(last_sync) < MUSIC_ALIAS_SYNC_INTERVAL_SECONDS:
            snapshot = _MUSIC_ALIAS_SNAPSHOT.load([])
            return _normalize_snapshot_payload(snapshot)

    payload = await AsyncHttpx.get_json(MUSIC_ALIAS_INDEX_URL, raise_on_failure=True)
    items = _normalize_snapshot_payload(payload)
    _MUSIC_ALIAS_SNAPSHOT.save(items)

    import time

    _MUSIC_ALIAS_STATE.save(
        {
            "music_alias_source": "MoeSekai-Hub/music_aliases.json",
            "music_alias_sync_count": len(items),
            "music_alias_sync_at": time.time(),
        }
    )
    return items


class AliasProvider:
    def __init__(
        self,
        *,
        music_snapshot_store: JsonStateStore | None = None,
        legacy_music_snapshot_store: JsonStateStore | None = None,
        character_seed_path: Path | None = None,
    ) -> None:
        self._music_snapshot_store = music_snapshot_store or _MUSIC_ALIAS_SNAPSHOT
        self._legacy_music_snapshot_store = (
            legacy_music_snapshot_store or _LEGACY_MUSIC_ALIAS_SNAPSHOT
        )
        self._character_seed_path = character_seed_path or _CHARACTER_ALIAS_SEED

        self._music_snapshot_signature: str | None = None
        self._music_snapshot_aliases: dict[str, list[str]] = {}
        self._music_snapshot_lookup: dict[str, str] = {}

        self._character_seed_signature: str | None = None
        self._character_seed_aliases: dict[str, list[str]] = {}
        self._character_seed_lookup: dict[str, str] = {}

        self._music_entity_signature: dict[str, str] = {}
        self._music_entities: dict[str, dict[str, _EntityRecord]] = {}
        self._music_official_lookup: dict[str, dict[str, str]] = {}

        self._character_entity_signature: dict[str, str] = {}
        self._character_entities: dict[str, dict[str, _EntityRecord]] = {}
        self._character_official_lookup: dict[str, dict[str, str]] = {}

    @staticmethod
    def _file_signature(path: Path) -> str:
        if not path.exists():
            return "missing"
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    @staticmethod
    def _records_signature(records: list[dict[str, Any]]) -> str:
        if not records:
            return "empty"
        last_id = records[-1].get("id") or records[-1].get("music_id")
        return f"{len(records)}:{last_id}"

    def _load_music_snapshot(self) -> list[dict[str, Any]]:
        payload = self._music_snapshot_store.load([])
        items = _normalize_snapshot_payload(payload)
        if items:
            return items
        legacy_payload = self._legacy_music_snapshot_store.load([])
        return _normalize_snapshot_payload(legacy_payload)

    async def _ensure_music_snapshot_cache(self) -> None:
        signature = (
            f"{self._file_signature(self._music_snapshot_store.path)}|"
            f"{self._file_signature(self._legacy_music_snapshot_store.path)}"
        )
        if signature == self._music_snapshot_signature:
            return
        alias_map: dict[str, list[str]] = {}
        alias_lookup: dict[str, str] = {}
        for item in self._load_music_snapshot():
            target_id = str(item.get("music_id") or "").strip()
            if not target_id:
                continue
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                continue
            for alias in aliases:
                text = str(alias).strip()
                if not text:
                    continue
                normalized = normalize_alias(text)
                if normalized in alias_lookup:
                    continue
                alias_lookup[normalized] = target_id
                alias_map.setdefault(target_id, []).append(text)
        self._music_snapshot_signature = signature
        self._music_snapshot_aliases = alias_map
        self._music_snapshot_lookup = alias_lookup

    async def _ensure_character_seed_cache(self) -> None:
        signature = self._file_signature(self._character_seed_path)
        if signature == self._character_seed_signature:
            return
        pattern = re.compile(
            r"VALUES\s*\(\s*\d+\s*,\s*'(?P<alias>.*?)'\s*,\s*(?P<character_id>\d+)\s*\)",
            re.IGNORECASE,
        )
        alias_map: dict[str, list[str]] = {}
        alias_lookup: dict[str, str] = {}
        if self._character_seed_path.exists():
            for line in self._character_seed_path.read_text(
                encoding="utf-8"
            ).splitlines():
                matched = pattern.search(line)
                if not matched:
                    continue
                text = matched.group("alias").replace("\\'", "'").strip()
                target_id = matched.group("character_id").strip()
                normalized = normalize_alias(text)
                if not normalized or normalized in alias_lookup:
                    continue
                alias_lookup[normalized] = target_id
                alias_map.setdefault(target_id, []).append(text)
        self._character_seed_signature = signature
        self._character_seed_aliases = alias_map
        self._character_seed_lookup = alias_lookup

    @staticmethod
    def _music_names(music: dict[str, Any]) -> list[str]:
        return _dedupe_texts(
            [
                str(music.get("title", "")).strip(),
                str(music.get("pronunciation", "")).strip(),
            ]
        )

    @staticmethod
    def _character_names(character: dict[str, Any]) -> list[str]:
        first_name = str(character.get("firstName", "")).strip()
        given_name = str(character.get("givenName", "")).strip()
        first_name_en = str(character.get("firstNameEnglish", "")).strip()
        given_name_en = str(character.get("givenNameEnglish", "")).strip()
        return _dedupe_texts(
            [
                f"{first_name}{given_name}".strip(),
                given_name,
                f"{first_name_en}{given_name_en}".strip(),
                given_name_en,
            ]
        )

    async def _ensure_music_entities(self, server: str) -> None:
        musics = await master_data_provider.get_musics(server)
        signature = self._records_signature(musics)
        if self._music_entity_signature.get(server) == signature:
            return
        entities: dict[str, _EntityRecord] = {}
        lookup: dict[str, str] = {}
        for music in musics:
            target_id = str(music.get("id") or "").strip()
            if not target_id:
                continue
            official_names = self._music_names(music)
            canonical_name = official_names[0] if official_names else target_id
            entities[target_id] = _EntityRecord(
                target_id=target_id,
                canonical_name=canonical_name,
                official_names=official_names,
            )
            for name in official_names:
                lookup.setdefault(normalize_alias(name), target_id)
        self._music_entity_signature[server] = signature
        self._music_entities[server] = entities
        self._music_official_lookup[server] = lookup

    async def _ensure_character_entities(self, server: str) -> None:
        characters = await master_data_provider.get_game_characters(server)
        signature = self._records_signature(characters)
        if self._character_entity_signature.get(server) == signature:
            return
        entities: dict[str, _EntityRecord] = {}
        lookup: dict[str, str] = {}
        for character in characters:
            target_id = str(character.get("id") or "").strip()
            if not target_id:
                continue
            official_names = self._character_names(character)
            canonical_name = official_names[0] if official_names else target_id
            entities[target_id] = _EntityRecord(
                target_id=target_id,
                canonical_name=canonical_name,
                official_names=official_names,
            )
            for name in official_names:
                lookup.setdefault(normalize_alias(name), target_id)
        self._character_entity_signature[server] = signature
        self._character_entities[server] = entities
        self._character_official_lookup[server] = lookup

    async def _entity_record(
        self,
        target_type: AliasTargetType,
        target_id: str,
        *,
        server: str,
    ) -> _EntityRecord | None:
        if target_type == "music":
            await self._ensure_music_entities(server)
            return self._music_entities.get(server, {}).get(target_id)
        await self._ensure_character_entities(server)
        return self._character_entities.get(server, {}).get(target_id)

    async def resolve(
        self,
        target_type: AliasTargetType,
        query: str,
        *,
        server: str = "jp",
    ) -> AliasResolveResult | None:
        text = str(query).strip()
        if not text:
            return None
        if text.isdigit():
            record = await self._entity_record(target_type, text, server=server)
            if record is None:
                return None
            return AliasResolveResult(
                target_type=target_type,
                target_id=text,
                canonical_name=record.canonical_name,
                matched_text=text,
                matched_source="id",
            )

        normalized = normalize_alias(text)
        if target_type == "music":
            await self._ensure_music_snapshot_cache()
            if target_id := self._music_snapshot_lookup.get(normalized):
                record = await self._entity_record("music", target_id, server=server)
                if record is not None:
                    return AliasResolveResult(
                        target_type="music",
                        target_id=target_id,
                        canonical_name=record.canonical_name,
                        matched_text=text,
                        matched_source="system",
                    )
            await self._ensure_music_entities(server)
            official_lookup = self._music_official_lookup.get(server, {})
        else:
            await self._ensure_character_seed_cache()
            if target_id := self._character_seed_lookup.get(normalized):
                record = await self._entity_record(
                    "character",
                    target_id,
                    server=server,
                )
                if record is not None:
                    return AliasResolveResult(
                        target_type="character",
                        target_id=target_id,
                        canonical_name=record.canonical_name,
                        matched_text=text,
                        matched_source="system",
                    )
            await self._ensure_character_entities(server)
            official_lookup = self._character_official_lookup.get(server, {})

        if target_id := official_lookup.get(normalized):
            record = await self._entity_record(target_type, target_id, server=server)
            if record is not None:
                return AliasResolveResult(
                    target_type=target_type,
                    target_id=target_id,
                    canonical_name=record.canonical_name,
                    matched_text=text,
                    matched_source="official",
                )
        return None

    async def resolve_music(
        self,
        query: str,
        *,
        server: str = "jp",
    ) -> AliasResolveResult | None:
        return await self.resolve("music", query, server=server)

    async def resolve_character(
        self,
        query: str,
        *,
        server: str = "jp",
    ) -> AliasResolveResult | None:
        return await self.resolve("character", query, server=server)

    async def profile(
        self,
        target_type: AliasTargetType,
        target_id: str,
        *,
        server: str = "jp",
    ) -> AliasProfile | None:
        normalized_id = str(target_id).strip()
        if not normalized_id:
            return None
        record = await self._entity_record(target_type, normalized_id, server=server)
        if record is None:
            return None
        if target_type == "music":
            await self._ensure_music_snapshot_cache()
            aliases = self._music_snapshot_aliases.get(normalized_id, [])
        else:
            await self._ensure_character_seed_cache()
            aliases = self._character_seed_aliases.get(normalized_id, [])
        return AliasProfile(
            target_type=target_type,
            target_id=normalized_id,
            canonical_name=record.canonical_name,
            aliases=_dedupe_texts(
                [*record.official_names, *aliases],
                exclude=[normalized_id],
            ),
        )

    async def get_music_profile(
        self,
        target_id: str,
        *,
        server: str = "jp",
    ) -> AliasProfile | None:
        return await self.profile("music", target_id, server=server)

    async def get_character_profile(
        self,
        target_id: str,
        *,
        server: str = "jp",
    ) -> AliasProfile | None:
        return await self.profile("character", target_id, server=server)

    def refresh_music_alias_snapshot(
        self,
        alias_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        items = _normalize_snapshot_payload(alias_items)
        self._music_snapshot_store.save(items)
        self._music_snapshot_signature = None
        self._music_snapshot_aliases = {}
        self._music_snapshot_lookup = {}
        return items


alias_provider = AliasProvider()
