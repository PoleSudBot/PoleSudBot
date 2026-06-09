from __future__ import annotations

from pathlib import Path

import nonebot
import pytest

nonebot.init()

from zhenxun.services.sekai_resource.aliases import AliasProvider
from zhenxun.services.sekai_resource.storage import JsonStateStore


def _store(path: Path) -> JsonStateStore:
    return JsonStateStore(path)


@pytest.mark.asyncio
async def test_alias_provider_resolves_music_snapshot_and_official_name(
    monkeypatch,
    tmp_path: Path,
):
    snapshot = _store(tmp_path / "music_alias_snapshot.json")
    legacy = _store(tmp_path / "legacy_music_alias_snapshot.json")
    snapshot.save(
        [
            {
                "music_id": 1,
                "title": "Tell Your World",
                "aliases": ["tyw", "世界"],
            }
        ]
    )
    provider = AliasProvider(
        music_snapshot_store=snapshot,
        legacy_music_snapshot_store=legacy,
        character_seed_path=tmp_path / "missing.sql",
    )

    async def fake_get_musics(server: str):
        assert server == "jp"
        return [
            {
                "id": 1,
                "title": "Tell Your World",
                "pronunciation": "てるゆあわーるど",
            }
        ]

    from zhenxun.services.sekai_resource import aliases as aliases_module

    monkeypatch.setattr(
        aliases_module.master_data_provider,
        "get_musics",
        fake_get_musics,
    )

    by_alias = await provider.resolve_music("tyw", server="jp")
    by_title = await provider.resolve_music("Tell Your World", server="jp")

    assert by_alias is not None
    assert by_alias.target_id == "1"
    assert by_alias.matched_source == "system"
    assert by_title is not None
    assert by_title.target_id == "1"
    assert by_title.matched_source == "official"


@pytest.mark.asyncio
async def test_alias_provider_resolves_character_seed(monkeypatch, tmp_path: Path):
    seed = tmp_path / "character_alias.sql"
    seed.write_text(
        "INSERT INTO pjsk.character_alias (id, alias, character_id) "
        "VALUES (1, 'mnr', 5);\n",
        encoding="utf-8",
    )
    provider = AliasProvider(
        music_snapshot_store=_store(tmp_path / "music_alias_snapshot.json"),
        legacy_music_snapshot_store=_store(
            tmp_path / "legacy_music_alias_snapshot.json"
        ),
        character_seed_path=seed,
    )

    async def fake_get_game_characters(server: str):
        assert server == "jp"
        return [
            {
                "id": 5,
                "firstName": "花里",
                "givenName": "みのり",
                "firstNameEnglish": "HANASATO",
                "givenNameEnglish": "MINORI",
            }
        ]

    from zhenxun.services.sekai_resource import aliases as aliases_module

    monkeypatch.setattr(
        aliases_module.master_data_provider,
        "get_game_characters",
        fake_get_game_characters,
    )

    result = await provider.resolve_character("mnr", server="jp")

    assert result is not None
    assert result.target_id == "5"
    assert result.canonical_name == "花里みのり"
