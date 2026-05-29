from __future__ import annotations

import importlib
import os
import time
from types import SimpleNamespace
from typing import ClassVar

import httpx
import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.constants import SCOPE_GLOBAL
from zhenxun.plugins.moesekai.providers import suite as suite_module
from zhenxun.plugins.moesekai.providers.aliases import AliasProvider
from zhenxun.plugins.moesekai.providers.asset_cache import (
    AssetCacheProvider as CompatAssetCacheProvider,
)
from zhenxun.plugins.moesekai.providers.assets import (
    AssetProvider as CompatAssetProvider,
)
from zhenxun.plugins.moesekai.providers.character_cache import CharacterCacheProvider
from zhenxun.plugins.moesekai.providers.hub import hub_provider
from zhenxun.plugins.moesekai.providers.masterdata import (
    MasterDataProvider as CompatMasterDataProvider,
)
from zhenxun.plugins.moesekai.providers.profile import (
    ProfileProcessor,
    ProfileProvider,
    ProfileStaticAssetProvider,
)
from zhenxun.plugins.moesekai.providers.story_cache import StoryCacheProvider
from zhenxun.plugins.moesekai.providers.suite import SuiteProvider
from zhenxun.plugins.moesekai.storage import BinaryFileCacheStore, PathBinaryFileStore
from zhenxun.plugins.moesekai.storage.state import JsonStateStore
import zhenxun.services.sekai_resource.asset_cache as service_asset_cache_module
from zhenxun.services.sekai_resource.asset_cache import AssetCacheProvider
from zhenxun.services.sekai_resource.asset_fetcher import asset_fetcher
from zhenxun.services.sekai_resource.assets import AssetProvider
from zhenxun.services.sekai_resource.master_data import MasterDataProvider
from zhenxun.utils.exception import AllURIsFailedError


def test_suite_provider_build_url_adds_field_filter():
    url = SuiteProvider.build_url(
        "https://suite-api.haruki.seiunx.com/public/{server}/suite/{game_id}",
        "jp",
        "6540035398873094",
    )

    assert url == (
        "https://suite-api.haruki.seiunx.com/public/jp/suite/6540035398873094"
        "?key=upload_time%2CuserGamedata%2CuserMusicResults%2CuserDecks%2CuserCards"
    )


@pytest.mark.asyncio
async def test_suite_provider_get_b30_data_parses_raw_json_response(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeResponse:
        def json(self):
            return {
                "upload_time": 1779774627,
                "userGamedata": {
                    "userId": 6540035398873094,
                    "name": "<#f90>kiyu",
                    "rank": 494,
                    "deck": 10,
                },
                "userMusicResults": [{"musicId": 1, "playResult": "full_perfect"}],
                "userDecks": [{"deckId": 10, "leader": 1182}],
                "userCards": [{"cardId": 1182, "defaultImage": "original"}],
            }

    async def fake_get(url: str, *, timeout: float):
        assert (
            "key=upload_time%2CuserGamedata%2CuserMusicResults%2CuserDecks%2CuserCards"
            in url
        )
        assert timeout == 20.0
        return FakeResponse()

    monkeypatch.setattr(
        suite_module,
        "get_settings",
        lambda: SimpleNamespace(
            suite_api_url_pattern=(
                "https://suite-api.haruki.seiunx.com/public/{server}/suite/{game_id}"
            ),
            suite_api_timeout_seconds=20.0,
        ),
    )
    monkeypatch.setattr(suite_module.AsyncHttpx, "get", fake_get)

    data = await SuiteProvider().get_b30_data("jp", "6540035398873094")

    assert data.profile.user_id == "6540035398873094"
    assert data.profile.name == "kiyu"
    assert data.profile.name_color == "#ff9900"
    assert data.profile.rank == 494
    assert data.music_results == [{"musicId": 1, "playResult": "full_perfect"}]
    assert data.user_decks == [{"deckId": 10, "leader": 1182}]
    assert data.user_cards == [{"cardId": 1182, "defaultImage": "original"}]
    assert data.default_deck_id == 10


def test_suite_profile_name_without_color_keeps_empty_name_color():
    name, color = suite_module._parse_colored_name("kiyu")

    assert name == "kiyu"
    assert color == ""


def test_moesekai_resource_provider_compat_exports():
    assert CompatAssetProvider is AssetProvider
    assert CompatAssetCacheProvider is AssetCacheProvider
    assert CompatMasterDataProvider is MasterDataProvider


def test_moesekai_resource_provider_compat_monkeypatches_service_module(
    monkeypatch: pytest.MonkeyPatch,
):
    compat_module = importlib.import_module(
        "zhenxun.plugins.moesekai.providers.asset_cache"
    )

    monkeypatch.setattr(
        compat_module,
        "get_settings",
        lambda: SimpleNamespace(
            audio_format_priority=["ogg"],
            asset_miss_cache_ttl_seconds=1,
        ),
    )

    assert compat_module is service_asset_cache_module
    assert service_asset_cache_module.get_settings().audio_format_priority == ["ogg"]


@pytest.mark.asyncio
async def test_hub_provider_supports_mapping_manga_index(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_cache_get(_key: str):
        return None

    async def fake_cache_set(_key: str, _value, ttl: int = 0):
        return None

    async def fake_get_json(*_args, **_kwargs):
        return {
            "351": {
                "id": 351,
                "title": "量身定制的设计",
                "manga": "https://example.com/manga/351.png",
                "url": "https://www.bilibili.com/opus/1183974551994761216",
            }
        }

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.hub.MoeSekaiCache.get",
        fake_cache_get,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.hub.MoeSekaiCache.set",
        fake_cache_set,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.hub.AsyncHttpx.get_json",
        fake_get_json,
    )

    items = await hub_provider.get_manga_index()
    assert items == [
        {
            "id": 351,
            "title": "量身定制的设计",
            "image_url": "https://example.com/manga/351.png",
            "url": "https://www.bilibili.com/opus/1183974551994761216",
        }
    ]


@pytest.mark.asyncio
async def test_hub_provider_can_get_manga_by_id(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_manga_index():
        return [
            {
                "id": 351,
                "title": "量身定制的设计",
                "image_url": "https://example.com/manga/351.png",
                "url": "https://www.bilibili.com/opus/1183974551994761216",
            }
        ]

    monkeypatch.setattr(hub_provider, "get_manga_index", fake_get_manga_index)

    result = await hub_provider.get_manga_by_id(351)
    assert result == {
        "id": 351,
        "title": "量身定制的设计",
        "image_url": "https://example.com/manga/351.png",
        "url": "https://www.bilibili.com/opus/1183974551994761216",
    }


@pytest.mark.asyncio
async def test_hub_provider_treats_empty_cache_as_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_cache_get(_key: str):
        return []

    async def fail_get_json(*_args, **_kwargs):
        raise AssertionError("空列表缓存命中时不应再次请求远端")

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.hub.MoeSekaiCache.get",
        fake_cache_get,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.hub.AsyncHttpx.get_json",
        fail_get_json,
    )

    assert await hub_provider.get_music_alias_index() == []


@pytest.mark.asyncio
async def test_alias_provider_merges_system_and_group_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = AliasProvider(
        seed_path=tmp_path / "character_alias.sql",
        music_snapshot_store=JsonStateStore(tmp_path / "music_alias_snapshot.json"),
        master_state_store=JsonStateStore(tmp_path / "master_state.json"),
    )
    provider.refresh_music_alias_snapshot(
        [
            {
                "music_id": 277,
                "title": "フォニイ",
                "aliases": ["phony", "伪物"],
            }
        ]
    )
    provider._master_state_store.save(
        {"jp": {"version": "6.0.0.1", "updated_at": "2026-04-07T00:00:00"}}
    )

    async def fake_get_musics(_server: str):
        return [
            {
                "id": 277,
                "title": "フォニイ",
                "pronunciation": "ふぉにい",
            }
        ]

    async def fake_list_alias_entries(**_kwargs):
        return [
            SimpleNamespace(scope=SCOPE_GLOBAL, alias="phony", created_by="system"),
            SimpleNamespace(scope=SCOPE_GLOBAL, alias="凤梨", created_by="tester"),
            SimpleNamespace(scope="qq:123", alias="火泥", created_by="tester"),
            SimpleNamespace(scope="qq:999", alias="外群别名", created_by="tester"),
        ]

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.master_data_provider.get_musics",
        fake_get_musics,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.list_alias_entries",
        fake_list_alias_entries,
    )

    profile = await provider.get_music_profile("277", platform="qq", group_id="123")

    assert profile is not None
    assert profile.display_title == "277. フォニイ"
    assert profile.merged_aliases == ["phony", "伪物", "凤梨"]
    assert profile.group_aliases == ["火泥"]


@pytest.mark.asyncio
async def test_alias_provider_resolve_prefers_group_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = AliasProvider(
        seed_path=tmp_path / "character_alias.sql",
        music_snapshot_store=JsonStateStore(tmp_path / "music_alias_snapshot.json"),
        master_state_store=JsonStateStore(tmp_path / "master_state.json"),
    )
    provider._master_state_store.save(
        {"jp": {"version": "6.0.0.1", "updated_at": "2026-04-07T00:00:00"}}
    )

    async def fake_get_musics(_server: str):
        return [{"id": 277, "title": "フォニイ", "pronunciation": "ふぉにい"}]

    async def fake_resolve_alias(**_kwargs):
        return SimpleNamespace(
            target_value="277",
            alias="phony",
            scope="qq:123",
            created_by="tester",
        )

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.master_data_provider.get_musics",
        fake_get_musics,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.resolve_alias",
        fake_resolve_alias,
    )

    result = await provider.resolve_music("phony", platform="qq", group_id="123")

    assert result is not None
    assert result.target_id == "277"
    assert result.canonical_name == "フォニイ"
    assert result.matched_source == "group"


@pytest.mark.asyncio
async def test_alias_provider_remove_reports_system_for_snapshot_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = AliasProvider(
        seed_path=tmp_path / "character_alias.sql",
        music_snapshot_store=JsonStateStore(tmp_path / "music_alias_snapshot.json"),
        master_state_store=JsonStateStore(tmp_path / "master_state.json"),
    )
    provider.refresh_music_alias_snapshot(
        [{"music_id": 277, "title": "フォニイ", "aliases": ["phony"]}]
    )

    async def fake_get_alias_entry(**_kwargs):
        return None

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.get_alias_entry",
        fake_get_alias_entry,
    )

    result = await provider.remove_managed_alias(
        target_type="music",
        alias="phony",
        scope=SCOPE_GLOBAL,
    )

    assert result == "system"


@pytest.mark.asyncio
async def test_alias_provider_remove_reports_system_for_group_scope_snapshot_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = AliasProvider(
        seed_path=tmp_path / "character_alias.sql",
        music_snapshot_store=JsonStateStore(tmp_path / "music_alias_snapshot.json"),
        master_state_store=JsonStateStore(tmp_path / "master_state.json"),
    )
    provider.refresh_music_alias_snapshot(
        [{"music_id": 277, "title": "フォニイ", "aliases": ["phony"]}]
    )

    async def fake_get_alias_entry(**kwargs):
        if kwargs["scope"] == SCOPE_GLOBAL:
            return None
        return None

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.get_alias_entry",
        fake_get_alias_entry,
    )

    result = await provider.remove_managed_alias(
        target_type="music",
        alias="phony",
        scope="qq:123",
    )

    assert result == "system"


@pytest.mark.asyncio
async def test_alias_provider_character_profile_uses_lazy_seed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    seed_path = tmp_path / "character_alias.sql"
    seed_path.write_text(
        "INSERT INTO pjsk.character_alias (id, alias, character_id) "
        "VALUES (1, '一歌', 1);\n",
        encoding="utf-8",
    )
    provider = AliasProvider(
        seed_path=seed_path,
        music_snapshot_store=JsonStateStore(tmp_path / "music_alias_snapshot.json"),
        master_state_store=JsonStateStore(tmp_path / "master_state.json"),
    )
    provider._master_state_store.save(
        {"jp": {"version": "6.0.0.1", "updated_at": "2026-04-07T00:00:00"}}
    )

    async def fake_get_game_characters(_server: str):
        return [
            {
                "id": 1,
                "firstName": "星乃",
                "givenName": "一歌",
                "firstNameEnglish": "Hoshino",
                "givenNameEnglish": "Ichika",
            }
        ]

    async def fake_list_alias_entries(**_kwargs):
        return []

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.master_data_provider.get_game_characters",
        fake_get_game_characters,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.aliases.list_alias_entries",
        fake_list_alias_entries,
    )

    profile = await provider.get_character_profile("1", platform="qq", group_id="123")

    assert profile is not None
    assert profile.display_title == "1. 星乃一歌"
    assert profile.merged_aliases == ["一歌"]


def test_character_cache_provider_respects_ttl_and_refresh_signature(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = CharacterCacheProvider(BinaryFileCacheStore(tmp_path))

    class FakeSettings:
        site_bases: ClassVar[list[str]] = [
            "https://snowyviewer.exmeaning.com",
            "https://pjsk.moe",
        ]
        character_viewport_width = 650
        character_top_crop = 100
        screenshot_quality = 85
        character_cache_ttl_seconds = 10

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.character_cache.get_settings",
        lambda: FakeSettings(),
    )

    assert provider.get(21) is None
    provider.set(21, b"character-image")
    assert provider.get(21) == b"character-image"

    cache_path = provider._store._path_for_key(
        provider.build_cache_key(21), suffix=".png"
    )
    stale_timestamp = time.time() - 60
    os.utime(cache_path, (stale_timestamp, stale_timestamp))

    expired_settings = FakeSettings()
    expired_settings.character_cache_ttl_seconds = 1
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.character_cache.get_settings",
        lambda: expired_settings,
    )
    assert provider.get(21) is None


def test_story_cache_provider_never_expires_when_ttl_is_zero(tmp_path):
    provider = StoryCacheProvider(BinaryFileCacheStore(tmp_path))

    assert provider.get(199) is None
    provider.set(199, b"story-image")
    assert provider.get(199) == b"story-image"

    cache_path = provider._store._path_for_key(
        provider.build_cache_key(199), suffix=".png"
    )
    stale_timestamp = time.time() - 60 * 60 * 24 * 30
    os.utime(cache_path, (stale_timestamp, stale_timestamp))

    assert provider.get(199) == b"story-image"


def test_asset_provider_prefers_event_story_banner_path():
    provider = AssetProvider()

    urls = provider.build_candidate_urls(
        "jp",
        kind="event_banner",
        assetbundle="event_wavering_2026",
    )

    assert urls
    assert (
        urls[0] == "https://assets-direct.unipjsk.com/ondemand/event_story/"
        "event_wavering_2026/screen_image/banner_event_story.png"
    )
    assert not any("startapp/home/banner" in url for url in urls)
    assert not any("storage.sekai.best" in url for url in urls)


def test_asset_provider_training_rules():
    provider = AssetProvider()

    assert provider.has_after_training(
        {
            "cardRarityType": "rarity_4",
            "specialTrainingCosts": [{"resourceId": 1}],
        }
    )
    assert provider.only_has_after_training(
        {
            "cardRarityType": "rarity_4",
            "specialTrainingCosts": [{"resourceId": 1}],
            "initialSpecialTrainingStatus": "done",
        }
    )
    assert not provider.has_after_training(
        {
            "cardRarityType": "rarity_2",
            "specialTrainingCosts": [{"resourceId": 1}],
        }
    )
    assert not provider.has_after_training(
        {
            "cardRarityType": "rarity_4",
            "specialTrainingCosts": [],
        }
    )


def test_asset_cache_provider_supports_positive_and_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = AssetCacheProvider(
        PathBinaryFileStore(tmp_path / "assets"),
        JsonStateStore(tmp_path / "asset_index.json"),
        JsonStateStore(tmp_path / "asset_miss.json"),
    )

    class FakeSettings:
        asset_miss_cache_ttl_seconds = 60
        audio_format_priority: ClassVar[list[str]] = ["mp3"]

    monkeypatch.setattr(
        "zhenxun.services.sekai_resource.asset_cache.get_settings",
        lambda: FakeSettings(),
    )

    assert (
        provider.get("jp", kind="event_banner", assetbundle="event_wavering_2026")
        is None
    )
    provider.set(
        "jp",
        kind="event_banner",
        assetbundle="event_wavering_2026",
        payload=b"banner-bytes",
        source_url="https://assets-direct.unipjsk.com/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png",
    )
    assert (
        provider.get("jp", kind="event_banner", assetbundle="event_wavering_2026")
        == b"banner-bytes"
    )
    manifest_entry = provider.get_manifest_entry(
        "jp/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png"
    )
    assert manifest_entry is not None
    assert manifest_entry["server"] == "jp"
    assert manifest_entry["kind"] == "event_banner"
    assert manifest_entry["assetbundle"] == "event_wavering_2026"
    assert manifest_entry["local_path"].endswith(
        "jp/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png"
    )

    missing_url = "https://example.com/404.png"
    assert provider.is_known_missing(missing_url) is False
    provider.record_missing(missing_url)
    assert provider.is_known_missing(missing_url) is True


def test_asset_cache_provider_reuses_in_memory_miss_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    class FakeMissStore:
        def __init__(self) -> None:
            self.payload: dict[str, float] = {}
            self.load_calls = 0
            self.save_calls = 0

        def load(self, default):
            self.load_calls += 1
            return self.payload.copy() if self.payload else default

        def save(self, payload) -> None:
            self.save_calls += 1
            self.payload = dict(payload)

    miss_store = FakeMissStore()
    provider = AssetCacheProvider(
        PathBinaryFileStore(tmp_path / "assets"),
        JsonStateStore(tmp_path / "asset_index.json"),
        miss_store,
    )

    class FakeSettings:
        asset_miss_cache_ttl_seconds = 60
        audio_format_priority: ClassVar[list[str]] = ["mp3"]

    monkeypatch.setattr(
        "zhenxun.services.sekai_resource.asset_cache.get_settings",
        lambda: FakeSettings(),
    )

    missing_url = "https://example.com/missing.png"
    assert provider.is_known_missing(missing_url) is False
    provider.record_missing(missing_url)
    assert provider.is_known_missing(missing_url) is True
    assert miss_store.load_calls == 1


@pytest.mark.asyncio
async def test_asset_fetcher_falls_back_between_urls(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def fake_get_content(url: str, **_kwargs):
        calls.append(url)
        if "assets-direct" in url:
            raise AllURIsFailedError([url], [httpx.ReadTimeout("primary failed")])
        return b"image-bytes"

    asset_fetcher_module = importlib.import_module(
        "zhenxun.plugins.moesekai.providers.asset_fetcher"
    )
    monkeypatch.setattr(
        asset_fetcher_module.AsyncHttpx, "get_content", fake_get_content
    )

    result = await asset_fetcher.fetch_first_content(
        [
            "https://assets-direct.unipjsk.com/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png",
            "https://sekai-assets.haruki.seiunx.com/jp-assets/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png",
        ],
        timeout=12,
    )

    assert result == b"image-bytes"
    assert calls == [
        "https://assets-direct.unipjsk.com/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png",
        "https://sekai-assets.haruki.seiunx.com/jp-assets/ondemand/event_story/event_wavering_2026/screen_image/banner_event_story.png",
    ]


@pytest.mark.asyncio
async def test_asset_fetcher_unwraps_all_uris_failed_to_httpx_error(
    monkeypatch: pytest.MonkeyPatch,
):
    target_url = "https://assets-direct.unipjsk.com/example.png"

    async def fake_get_content(url: str, **_kwargs):
        raise AllURIsFailedError(
            [url],
            [
                httpx.HTTPStatusError(
                    "404",
                    request=httpx.Request("GET", url),
                    response=httpx.Response(404, request=httpx.Request("GET", url)),
                )
            ],
        )

    asset_fetcher_module = importlib.import_module(
        "zhenxun.plugins.moesekai.providers.asset_fetcher"
    )
    monkeypatch.setattr(
        asset_fetcher_module.AsyncHttpx, "get_content", fake_get_content
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await asset_fetcher.fetch_content(target_url)

    assert exc_info.value.response.status_code == 404


def test_profile_provider_build_profile_url_keeps_encoded_uni_placeholder():
    assert (
        ProfileProvider.build_profile_url(
            "https://api.unipjsk.com/api/user/%7Buser_id%7D",
            "6540035398873094",
        )
        == "https://api.unipjsk.com/api/user/%7Buser_id%7D/6540035398873094/profile"
    )


def test_profile_provider_build_url_keeps_encoded_uni_placeholder_profile_suffix():
    assert (
        ProfileProvider.build_profile_url(
            "https://api.unipjsk.com/api/user/%7Buser_id%7D/profile",
            "6540035398873094",
        )
        == "https://api.unipjsk.com/api/user/%7Buser_id%7D/6540035398873094/profile"
    )


def test_profile_provider_build_profile_url_supports_plain_placeholder():
    assert (
        ProfileProvider.build_profile_url(
            "https://example.com/api/user/{user_id}",
            "6540035398873094",
        )
        == "https://example.com/api/user/6540035398873094/profile"
    )


@pytest.mark.asyncio
async def test_profile_provider_get_raw_profile_sends_token_header(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = ProfileProvider()
    captured: dict[str, object] = {}

    class _FakeResponse:
        def json(self):
            return {"user": {"userId": "1"}}

    class _FakeSettings:
        profile_api_base_jp = "https://example.com/api/jp"
        profile_api_base_cn = "https://example.com/api/cn"
        profile_api_base_tw = "https://example.com/api/tw"
        profile_api_token = "token-123"

    async def fake_get(url: str, *, timeout: float | None = None, headers=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.get_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.AsyncHttpx.get",
        fake_get,
    )

    payload = await provider.get_raw_profile("jp", "1234567890123")

    assert payload == {"user": {"userId": "1"}}
    assert captured["url"] == "https://example.com/api/jp/1234567890123/profile"
    assert captured["timeout"] == 30
    assert captured["headers"] == {
        "Accept": "application/json",
        "User-Agent": "MoeSekai/1.0",
        "X-Haruki-Sekai-Token": "token-123",
    }


def test_profile_processor_matches_go_side_key_fields():
    raw = {
        "user": {"userId": 6540035398873094, "name": "测试玩家", "rank": 278},
        "userProfile": {"word": "愿你也能找到自己的歌", "twitterId": ""},
        "userDeck": {
            "name": "主力编队",
            "leader": 1001,
            "member1": 1001,
            "member2": 1002,
            "member3": 1003,
            "member4": 1004,
            "member5": 1005,
        },
        "userCards": [
            {
                "cardId": 1001,
                "level": 60,
                "masterRank": 5,
                "defaultImage": "special_training",
            },
            {"cardId": 1002, "level": 50, "masterRank": 1, "defaultImage": "original"},
        ],
        "userCharacters": [
            {"characterId": 1, "characterRank": 55},
            {"characterId": 21, "characterRank": 62},
        ],
        "userHonors": [[501, 3], {"honorId": 601, "level": 1}],
        "userBondsHonors": [{"bondsHonorId": 801, "level": 5}],
        "userProfileHonors": [
            {"seq": 2, "honorId": 501, "profileHonorType": "normal"},
            {"seq": 1, "honorId": 801, "profileHonorType": "bonds"},
            {"seq": 3, "honorId": 601, "profileHonorType": "normal"},
        ],
        "userMusicDifficultyClearCount": [
            {
                "musicDifficultyType": "master",
                "liveClear": 10,
                "fullCombo": 4,
                "allPerfect": 1,
            },
            {
                "musicDifficultyType": "easy",
                "liveClear": 120,
                "fullCombo": 120,
                "allPerfect": 118,
            },
        ],
        "userChallengeLiveSoloResult": {"characterId": 21, "highScore": 1234567},
        "userChallengeLiveSoloStages": [
            {"characterId": 21, "rank": 18},
            {"characterId": 21, "rank": 15},
            {"characterId": 1, "rank": 12},
        ],
        "userMultiLiveTopScoreCount": {"mvp": 12, "superStar": 34},
        "totalPower": {"totalPower": 321654},
    }
    cards = [
        {
            "id": 1001,
            "characterId": 21,
            "cardRarityType": "rarity_4",
            "attr": "cool",
            "supportUnit": "street",
            "assetbundleName": "card_1001",
        },
        {
            "id": 1002,
            "characterId": 1,
            "cardRarityType": "rarity_3",
            "attr": "cute",
            "supportUnit": "light_sound",
            "assetbundleName": "card_1002",
        },
        {
            "id": 1003,
            "characterId": 2,
            "cardRarityType": "rarity_2",
            "attr": "pure",
            "supportUnit": "light_sound",
            "assetbundleName": "card_1003",
        },
        {
            "id": 1004,
            "characterId": 3,
            "cardRarityType": "rarity_1",
            "attr": "happy",
            "supportUnit": "light_sound",
            "assetbundleName": "card_1004",
        },
        {
            "id": 1005,
            "characterId": 4,
            "cardRarityType": "rarity_birthday",
            "attr": "mysterious",
            "supportUnit": "light_sound",
            "assetbundleName": "card_1005",
        },
    ]
    honors = [
        {
            "id": 501,
            "groupId": 71,
            "honorRarity": "highest",
            "name": "活动第一名",
            "assetbundleName": "honor_top_0001",
            "levels": [{"description": "活动 1 位"}],
        },
        {
            "id": 601,
            "groupId": 88,
            "honorRarity": "middle",
            "name": "世界回响",
            "assetbundleName": "honor_0601",
            "levels": [{"description": "章节徽章"}],
        },
        {
            "id": 801,
            "groupId": 99,
            "honorRarity": "low",
            "name": "心羽&Miku",
            "assetbundleName": "honor_0801",
            "levels": [{"description": "羁绊 5"}],
        },
    ]
    honor_groups = [
        {"id": 71, "name": "雨上がりの一番星", "honorType": "event"},
        {"id": 88, "name": "世界回响 第一章", "honorType": "sekai_echo"},
        {"id": 99, "name": "羁绊徽章", "honorType": "bonds"},
    ]

    processed = ProfileProcessor.process(
        raw,
        cards=cards,
        honors=honors,
        honor_groups=honor_groups,
    )

    assert processed["userId"] == "6540035398873094"
    assert processed["topCharacterId"] == 21
    assert processed["deck"]["members"][0]["assetbundleName"] == "card_1001"
    assert processed["deck"]["members"][0]["defaultImage"] == "special_training"
    assert [item["honorId"] for item in processed["honors"]] == [801, 501, 601]
    assert processed["honors"][1]["levelDisplay"] == "雨上がりの一番星"
    assert processed["honors"][2]["levelDisplay"] == "世界回响 第一章"
    assert processed["challengeLive"]["characterStages"] == {21: 18, 1: 12}
    assert processed["musicStats"][0]["difficulty"] == "easy"
    assert processed["musicStats"][-1]["difficulty"] == "append"


@pytest.mark.asyncio
async def test_master_data_provider_exposes_honor_datasets(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_dataset(server: str, dataset: str):
        assert server == "jp"
        return [{"dataset": dataset}]

    monkeypatch.setattr(MasterDataProvider, "get_dataset", fake_get_dataset)

    assert await MasterDataProvider.get_honors("jp") == [{"dataset": "honors"}]
    assert await MasterDataProvider.get_honor_groups("jp") == [
        {"dataset": "honorGroups"}
    ]


@pytest.mark.asyncio
async def test_profile_static_asset_provider_uses_source_fallback_and_local_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = ProfileStaticAssetProvider(
        store=PathBinaryFileStore(tmp_path / "profile_static"),
        miss_store=JsonStateStore(tmp_path / "profile_static_miss.json"),
    )
    calls: list[str] = []

    class _FakeSettings:
        asset_miss_cache_ttl_seconds = 3600
        profile_static_asset_bases: ClassVar[list[str]] = [
            "https://a.example.com",
            "https://b.example.com",
        ]

    async def fake_get_content(url: str, *, timeout: float | None = None, **_kwargs):
        calls.append(url)
        if url == "https://a.example.com/credits.json":
            request = httpx.Request("GET", url)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404", request=request, response=response)
        assert timeout == 20
        return b'{"1001":{"author":"tester","source_type":"original"}}'

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.get_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.AsyncHttpx.get_content",
        fake_get_content,
    )

    payload = await provider.get_json("credits.json")
    second_path = await provider.ensure_local_path(["credits.json"])

    assert payload == {"1001": {"author": "tester", "source_type": "original"}}
    assert second_path is not None
    assert second_path.is_file()
    assert calls == [
        "https://a.example.com/credits.json",
        "https://b.example.com/credits.json",
    ]


def test_profile_static_asset_provider_uses_resource_miss_ttl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = ProfileStaticAssetProvider(
        store=PathBinaryFileStore(tmp_path / "profile_static"),
        miss_store=JsonStateStore(tmp_path / "profile_static_miss.json"),
    )

    monkeypatch.setattr(time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.get_resource_settings",
        lambda: SimpleNamespace(asset_miss_cache_ttl_seconds=42),
    )

    provider._record_missing("https://a.example.com/missing.png")

    assert provider._miss_store.load({}) == {
        "https://a.example.com/missing.png": 1042.0
    }


@pytest.mark.asyncio
async def test_profile_static_asset_provider_unwraps_all_uris_failed_404(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = ProfileStaticAssetProvider(
        store=PathBinaryFileStore(tmp_path / "profile_static"),
        miss_store=JsonStateStore(tmp_path / "profile_static_miss.json"),
    )
    calls: list[str] = []

    class _FakeSettings:
        asset_miss_cache_ttl_seconds = 3600
        profile_static_asset_bases: ClassVar[list[str]] = [
            "https://a.example.com",
            "https://b.example.com",
        ]

    async def fake_get_content(url: str, *, timeout: float | None = None, **_kwargs):
        calls.append(url)
        if url == "https://a.example.com/credits.json":
            request = httpx.Request("GET", url)
            response = httpx.Response(404, request=request)
            raise AllURIsFailedError(
                [url],
                [httpx.HTTPStatusError("404", request=request, response=response)],
            )
        return b'{"1001":{"author":"tester","source_type":"original"}}'

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.get_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.profile.AsyncHttpx.get_content",
        fake_get_content,
    )

    payload = await provider.get_json("credits.json")

    assert payload == {"1001": {"author": "tester", "source_type": "original"}}
    assert calls == [
        "https://a.example.com/credits.json",
        "https://b.example.com/credits.json",
    ]
