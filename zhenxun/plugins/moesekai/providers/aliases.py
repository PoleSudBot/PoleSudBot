from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..constants import (
    ALIAS_TARGET_CHARACTER,
    ALIAS_TARGET_MUSIC,
    SEEDS_DIR,
    SCOPE_GLOBAL,
    STATE_DIR,
    make_scope_key,
    normalize_alias,
)
from ..model import MoeSekaiAliasEntry
from ..repositories import (
    get_alias_entry,
    list_alias_entries,
    remove_alias_entry,
    resolve_alias,
    search_alias_entries,
    upsert_alias_entry,
)
from ..storage.state import JsonStateStore
from .hub import hub_provider
from .masterdata import master_data_provider

AliasTargetType = Literal["character", "music"]
AliasMatchSource = Literal["id", "group", "global", "system", "official"]

_MUSIC_ALIAS_SNAPSHOT = JsonStateStore(STATE_DIR / "music_alias_snapshot.json")
_MASTER_STATE = JsonStateStore(STATE_DIR / "master_state.json")


@dataclass
class AliasResolveResult:
    target_id: str
    canonical_name: str
    matched_text: str
    matched_source: AliasMatchSource


@dataclass
class AliasSearchHit:
    target_id: str
    canonical_name: str
    matched_text: str
    matched_source: AliasMatchSource


@dataclass
class AliasProfile:
    target_type: AliasTargetType
    target_id: str
    canonical_name: str
    display_title: str
    merged_aliases: list[str]
    group_aliases: list[str]


@dataclass
class _EntityRecord:
    target_id: str
    canonical_name: str
    official_names: list[str]


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


class AliasProvider:
    def __init__(
        self,
        *,
        seed_path: Path | None = None,
        music_snapshot_store: JsonStateStore | None = None,
        master_state_store: JsonStateStore | None = None,
    ) -> None:
        self._seed_path = seed_path or (SEEDS_DIR / "character_alias.sql")
        self._music_snapshot_store = music_snapshot_store or _MUSIC_ALIAS_SNAPSHOT
        self._master_state_store = master_state_store or _MASTER_STATE

        self._character_seed_signature: str | None = None
        self._character_seed_aliases: dict[str, list[str]] = {}
        self._character_seed_lookup: dict[str, str] = {}

        self._music_snapshot_signature: str | None = None
        self._music_snapshot_aliases: dict[str, list[str]] = {}
        self._music_snapshot_lookup: dict[str, str] = {}

        self._character_entity_signature: str | None = None
        self._character_entities: dict[str, _EntityRecord] = {}
        self._character_official_lookup: dict[str, str] = {}

        self._music_entity_signature: str | None = None
        self._music_entities: dict[str, _EntityRecord] = {}
        self._music_official_lookup: dict[str, str] = {}

    def _master_signature(self) -> str:
        payload = self._master_state_store.load({})
        if not isinstance(payload, dict):
            return "missing"
        jp_state = payload.get("jp", {})
        if not isinstance(jp_state, dict):
            return "missing"
        version = str(jp_state.get("version") or "")
        updated_at = str(jp_state.get("updated_at") or jp_state.get("checked_at") or "")
        return f"{version}|{updated_at}"

    def _file_signature(self, path: Path) -> str:
        if not path.exists():
            return "missing"
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    async def _ensure_character_seed_cache(self) -> None:
        signature = self._file_signature(self._seed_path)
        if signature == self._character_seed_signature:
            return
        alias_map: dict[str, list[str]] = {}
        alias_lookup: dict[str, str] = {}
        for alias, character_id in hub_provider.read_character_alias_seed(self._seed_path):
            text = str(alias).strip()
            target_id = str(character_id).strip()
            if not text or not target_id:
                continue
            normalized = normalize_alias(text)
            if normalized in alias_lookup:
                continue
            alias_lookup[normalized] = target_id
            alias_map.setdefault(target_id, []).append(text)
        self._character_seed_signature = signature
        self._character_seed_aliases = alias_map
        self._character_seed_lookup = alias_lookup

    async def _ensure_music_snapshot_cache(self) -> None:
        signature = self._file_signature(self._music_snapshot_store.path)
        if signature == self._music_snapshot_signature:
            return
        payload = self._music_snapshot_store.load([])
        alias_map: dict[str, list[str]] = {}
        alias_lookup: dict[str, str] = {}
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
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

    @staticmethod
    def _character_names(character: dict[str, Any]) -> list[str]:
        first_name = str(character.get("firstName", "")).strip()
        given_name = str(character.get("givenName", "")).strip()
        first_name_en = str(character.get("firstNameEnglish", "")).strip()
        given_name_en = str(character.get("givenNameEnglish", "")).strip()
        names = [
            f"{first_name}{given_name}".strip(),
            given_name,
            f"{first_name_en}{given_name_en}".strip(),
            given_name_en,
        ]
        return _dedupe_texts(names)

    async def _ensure_character_entities(self) -> None:
        signature = self._master_signature()
        if signature == self._character_entity_signature:
            return
        entities: dict[str, _EntityRecord] = {}
        lookup: dict[str, str] = {}
        for character in await master_data_provider.get_game_characters("jp"):
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
        self._character_entity_signature = signature
        self._character_entities = entities
        self._character_official_lookup = lookup

    async def _ensure_music_entities(self) -> None:
        signature = self._master_signature()
        if signature == self._music_entity_signature:
            return
        entities: dict[str, _EntityRecord] = {}
        lookup: dict[str, str] = {}
        for music in await master_data_provider.get_musics("jp"):
            target_id = str(music.get("id") or "").strip()
            if not target_id:
                continue
            title = str(music.get("title", "")).strip()
            pronunciation = str(music.get("pronunciation", "")).strip()
            official_names = _dedupe_texts([title, pronunciation])
            canonical_name = title or (official_names[0] if official_names else target_id)
            entities[target_id] = _EntityRecord(
                target_id=target_id,
                canonical_name=canonical_name,
                official_names=official_names,
            )
            for name in official_names:
                lookup.setdefault(normalize_alias(name), target_id)
        self._music_entity_signature = signature
        self._music_entities = entities
        self._music_official_lookup = lookup

    async def _entity_record(
        self,
        target_type: AliasTargetType,
        target_id: str,
    ) -> _EntityRecord | None:
        normalized_id = str(target_id).strip()
        if target_type == ALIAS_TARGET_CHARACTER:
            await self._ensure_character_entities()
            return self._character_entities.get(normalized_id)
        await self._ensure_music_entities()
        return self._music_entities.get(normalized_id)

    async def _system_lookup(self, target_type: AliasTargetType) -> dict[str, str]:
        if target_type == ALIAS_TARGET_CHARACTER:
            await self._ensure_character_seed_cache()
            return self._character_seed_lookup
        await self._ensure_music_snapshot_cache()
        return self._music_snapshot_lookup

    async def _system_aliases(self, target_type: AliasTargetType) -> dict[str, list[str]]:
        if target_type == ALIAS_TARGET_CHARACTER:
            await self._ensure_character_seed_cache()
            return self._character_seed_aliases
        await self._ensure_music_snapshot_cache()
        return self._music_snapshot_aliases

    async def _official_lookup(self, target_type: AliasTargetType) -> dict[str, str]:
        if target_type == ALIAS_TARGET_CHARACTER:
            await self._ensure_character_entities()
            return self._character_official_lookup
        await self._ensure_music_entities()
        return self._music_official_lookup

    @staticmethod
    def _profile_from_record(
        *,
        target_type: AliasTargetType,
        target_id: str,
        record: _EntityRecord | None,
        merged_aliases: list[str],
        group_aliases: list[str],
    ) -> AliasProfile:
        title = record.canonical_name if record else target_id
        return AliasProfile(
            target_type=target_type,
            target_id=target_id,
            canonical_name=title,
            display_title=f"{target_id}. {title}",
            merged_aliases=merged_aliases,
            group_aliases=group_aliases,
        )

    async def _scoped_db_entries(
        self,
        *,
        target_type: AliasTargetType,
        target_id: str,
        platform: str | None,
        group_id: str | None,
    ) -> tuple[list[MoeSekaiAliasEntry], list[MoeSekaiAliasEntry], list[MoeSekaiAliasEntry]]:
        group_scope = make_scope_key(group_id, platform=platform)
        global_entries: list[MoeSekaiAliasEntry] = []
        group_entries: list[MoeSekaiAliasEntry] = []
        legacy_system_entries: list[MoeSekaiAliasEntry] = []
        for entry in await list_alias_entries(target_type=target_type, target_value=target_id):
            if entry.scope == group_scope and group_scope != SCOPE_GLOBAL:
                group_entries.append(entry)
                continue
            if entry.scope != SCOPE_GLOBAL:
                continue
            if str(entry.created_by).strip() == "system":
                legacy_system_entries.append(entry)
            else:
                global_entries.append(entry)
        return global_entries, group_entries, legacy_system_entries

    async def _resolve(
        self,
        target_type: AliasTargetType,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> AliasResolveResult | None:
        text = str(query).strip()
        if not text:
            return None
        if text.isdigit():
            record = await self._entity_record(target_type, text)
            title = record.canonical_name if record else text
            return AliasResolveResult(
                target_id=text,
                canonical_name=title,
                matched_text=text,
                matched_source="id",
            )
        scopes: list[str] = []
        if group_id:
            scopes.append(make_scope_key(group_id, platform=platform))
        scopes.append(SCOPE_GLOBAL)
        entry = await resolve_alias(target_type=target_type, alias=text, scopes=scopes)
        if entry:
            record = await self._entity_record(target_type, entry.target_value)
            title = record.canonical_name if record else entry.target_value
            if entry.scope != SCOPE_GLOBAL:
                source: AliasMatchSource = "group"
            elif str(entry.created_by).strip() == "system":
                source = "system"
            else:
                source = "global"
            return AliasResolveResult(
                target_id=entry.target_value,
                canonical_name=title,
                matched_text=entry.alias,
                matched_source=source,
            )

        normalized = normalize_alias(text)
        system_lookup = await self._system_lookup(target_type)
        if target_id := system_lookup.get(normalized):
            record = await self._entity_record(target_type, target_id)
            title = record.canonical_name if record else target_id
            return AliasResolveResult(
                target_id=target_id,
                canonical_name=title,
                matched_text=text,
                matched_source="system",
            )

        official_lookup = await self._official_lookup(target_type)
        if target_id := official_lookup.get(normalized):
            record = await self._entity_record(target_type, target_id)
            title = record.canonical_name if record else target_id
            return AliasResolveResult(
                target_id=target_id,
                canonical_name=title,
                matched_text=text,
                matched_source="official",
            )
        return None

    async def resolve_music(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> AliasResolveResult | None:
        return await self._resolve(
            ALIAS_TARGET_MUSIC,
            query,
            platform=platform,
            group_id=group_id,
        )

    async def resolve_character(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> AliasResolveResult | None:
        return await self._resolve(
            ALIAS_TARGET_CHARACTER,
            query,
            platform=platform,
            group_id=group_id,
        )

    async def _profile(
        self,
        target_type: AliasTargetType,
        target_id: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> AliasProfile | None:
        normalized_id = str(target_id).strip()
        if not normalized_id:
            return None
        record = await self._entity_record(target_type, normalized_id)
        global_entries, group_entries, legacy_system_entries = await self._scoped_db_entries(
            target_type=target_type,
            target_id=normalized_id,
            platform=platform,
            group_id=group_id,
        )
        system_aliases = list((await self._system_aliases(target_type)).get(normalized_id, []))
        global_aliases = [entry.alias for entry in global_entries]
        legacy_aliases = [entry.alias for entry in legacy_system_entries]
        merged_aliases = _dedupe_texts(
            system_aliases + legacy_aliases + global_aliases,
            exclude=[normalized_id, record.canonical_name if record else normalized_id],
        )
        group_aliases = _dedupe_texts(
            [entry.alias for entry in group_entries],
            exclude=[normalized_id, record.canonical_name if record else normalized_id],
        )
        if record is None and not merged_aliases and not group_aliases and not normalized_id.isdigit():
            return None
        return self._profile_from_record(
            target_type=target_type,
            target_id=normalized_id,
            record=record,
            merged_aliases=merged_aliases,
            group_aliases=group_aliases,
        )

    async def get_music_profile(
        self,
        target_id: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> AliasProfile | None:
        return await self._profile(
            ALIAS_TARGET_MUSIC,
            target_id,
            platform=platform,
            group_id=group_id,
        )

    async def get_character_profile(
        self,
        target_id: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> AliasProfile | None:
        return await self._profile(
            ALIAS_TARGET_CHARACTER,
            target_id,
            platform=platform,
            group_id=group_id,
        )

    @staticmethod
    def _contains(keyword: str, candidate: str) -> bool:
        text = str(candidate).strip()
        if not text:
            return False
        normalized_candidate = normalize_alias(text)
        return keyword in normalized_candidate or keyword in text

    async def _search(
        self,
        target_type: AliasTargetType,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
        limit: int = 20,
    ) -> list[AliasSearchHit]:
        keyword = str(query).strip()
        normalized = normalize_alias(keyword)
        if not normalized:
            return []
        group_scope = make_scope_key(group_id, platform=platform)
        scopes = [group_scope, SCOPE_GLOBAL] if group_scope != SCOPE_GLOBAL else [SCOPE_GLOBAL]
        hits: list[AliasSearchHit] = []
        seen_ids: set[str] = set()

        def append_hit(target_id: str, matched_text: str, source: AliasMatchSource) -> None:
            if target_id in seen_ids or len(hits) >= limit:
                return
            record = self._character_entities.get(target_id) if target_type == ALIAS_TARGET_CHARACTER else self._music_entities.get(target_id)
            title = record.canonical_name if record else target_id
            seen_ids.add(target_id)
            hits.append(
                AliasSearchHit(
                    target_id=target_id,
                    canonical_name=title,
                    matched_text=matched_text,
                    matched_source=source,
                )
            )

        if target_type == ALIAS_TARGET_CHARACTER:
            await self._ensure_character_entities()
        else:
            await self._ensure_music_entities()

        for entry in await search_alias_entries(
            target_type=target_type,
            keyword=keyword,
            scopes=scopes,
            limit=limit,
        ):
            if entry.scope != SCOPE_GLOBAL and entry.scope == group_scope:
                source: AliasMatchSource = "group"
            elif str(entry.created_by).strip() == "system":
                source = "system"
            else:
                source = "global"
            append_hit(entry.target_value, entry.alias, source)

        system_aliases = await self._system_aliases(target_type)
        if len(hits) < limit:
            for target_id, aliases in system_aliases.items():
                for alias in aliases:
                    if self._contains(normalized, alias):
                        append_hit(target_id, alias, "system")
                        break
                if len(hits) >= limit:
                    break

        entities = self._character_entities if target_type == ALIAS_TARGET_CHARACTER else self._music_entities
        if len(hits) < limit:
            for target_id, record in entities.items():
                for name in record.official_names:
                    if self._contains(normalized, name):
                        append_hit(target_id, name, "official")
                        break
                if len(hits) >= limit:
                    break

        return hits

    async def search_music(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
        limit: int = 20,
    ) -> list[AliasSearchHit]:
        return await self._search(
            ALIAS_TARGET_MUSIC,
            query,
            platform=platform,
            group_id=group_id,
            limit=limit,
        )

    async def search_character(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
        limit: int = 20,
    ) -> list[AliasSearchHit]:
        return await self._search(
            ALIAS_TARGET_CHARACTER,
            query,
            platform=platform,
            group_id=group_id,
            limit=limit,
        )

    async def add_managed_alias(
        self,
        *,
        target_type: AliasTargetType,
        target_value: str,
        alias: str,
        scope: str,
        created_by: str,
    ) -> MoeSekaiAliasEntry:
        return await upsert_alias_entry(
            target_type=target_type,
            target_value=target_value,
            alias=alias,
            scope=scope,
            created_by=created_by,
        )

    async def remove_managed_alias(
        self,
        *,
        target_type: AliasTargetType,
        alias: str,
        scope: str,
    ) -> Literal["deleted", "system", "not_found"]:
        entry = await get_alias_entry(target_type=target_type, alias=alias, scope=scope)
        if entry:
            if str(entry.created_by).strip() == "system":
                return "system"
            removed = await remove_alias_entry(
                target_type=target_type,
                alias=alias,
                scope=scope,
            )
            return "deleted" if removed else "not_found"
        normalized = normalize_alias(alias)
        system_lookup = await self._system_lookup(target_type)
        if normalized in system_lookup:
            return "system"
        global_entry = await get_alias_entry(
            target_type=target_type,
            alias=alias,
            scope=SCOPE_GLOBAL,
        )
        if global_entry and str(global_entry.created_by).strip() == "system":
            return "system"
        return "not_found"

    def refresh_music_alias_snapshot(
        self,
        alias_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized_items: list[dict[str, Any]] = []
        for item in alias_items:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("music_id") or "").strip()
            if not target_id:
                continue
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                continue
            normalized_items.append(
                {
                    "music_id": int(target_id),
                    "title": str(item.get("title", "")).strip(),
                    "aliases": _dedupe_texts([str(alias) for alias in aliases]),
                }
            )
        self._music_snapshot_store.save(normalized_items)
        self._music_snapshot_signature = None
        self._music_snapshot_aliases = {}
        self._music_snapshot_lookup = {}
        return normalized_items


alias_provider = AliasProvider()
