from __future__ import annotations

from datetime import datetime, timedelta
import importlib
import os
import time

import nonebot
import pytest
import httpx

nonebot.init()

from zhenxun.plugins.moesekai.providers.assets import AssetProvider
from zhenxun.plugins.moesekai.providers.asset_cache import AssetCacheProvider
from zhenxun.plugins.moesekai.providers.asset_fetcher import asset_fetcher
from zhenxun.plugins.moesekai.providers.character_cache import CharacterCacheProvider
from zhenxun.plugins.moesekai.providers.hub import hub_provider
from zhenxun.plugins.moesekai.providers.ranking import RankingSnapshot, ranking_provider
from zhenxun.plugins.moesekai.providers.story_cache import StoryCacheProvider
from zhenxun.plugins.moesekai.storage import BinaryFileCacheStore, PathBinaryFileStore
from zhenxun.plugins.moesekai.storage.state import JsonStateStore


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


def test_character_cache_provider_respects_ttl_and_refresh_signature(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    provider = CharacterCacheProvider(BinaryFileCacheStore(tmp_path))

    class FakeSettings:
        site_bases = ["https://snowyviewer.exmeaning.com", "https://pjsk.moe"]
        deck_viewport_width = 650
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

    cache_path = provider._store._path_for_key(provider.build_cache_key(21), suffix=".png")
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

    cache_path = provider._store._path_for_key(provider.build_cache_key(199), suffix=".png")
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
        urls[0]
        == "https://assets-direct.unipjsk.com/ondemand/event_story/"
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

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.asset_cache.get_settings",
        lambda: FakeSettings(),
    )

    assert provider.get("jp", kind="event_banner", assetbundle="event_wavering_2026") is None
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


@pytest.mark.asyncio
async def test_asset_fetcher_falls_back_between_urls(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_fetch_single_content(_client, url: str):
        calls.append(url)
        if "assets-direct" in url:
            raise httpx.ReadTimeout("primary failed")
        return b"image-bytes"

    asset_fetcher_module = importlib.import_module(
        "zhenxun.plugins.moesekai.providers.asset_fetcher"
    )
    monkeypatch.setattr(
        asset_fetcher_module.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(asset_fetcher, "_fetch_single_content", fake_fetch_single_content)

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
async def test_ranking_provider_get_snapshot_does_not_fallback_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime.now()
    current_event = {
        "id": 199,
        "startAt": int((now - timedelta(hours=1)).timestamp() * 1000),
        "aggregateAt": int((now + timedelta(hours=1)).timestamp() * 1000),
    }
    expected_snapshot = RankingSnapshot(
        server="jp",
        event_id=199,
        status="running",
        updated_at="2026-03-29T09:00:00+08:00",
        items=[],
    )

    async def fake_get_current_event(server: str, fallback: str = "prev"):
        assert server == "jp"
        assert fallback == "prev"
        return current_event

    async def fake_get_event_meta(server: str, event_id: int):
        assert server == "jp"
        assert event_id == 199
        return {"event_id": 199}

    async def fake_get_latest_snapshot(server: str, event_id: int):
        assert server == "jp"
        assert event_id == 199
        return expected_snapshot

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.providers.ranking.master_data_provider.get_current_event",
        fake_get_current_event,
    )
    monkeypatch.setattr(ranking_provider, "get_event_meta", fake_get_event_meta)
    monkeypatch.setattr(ranking_provider, "get_latest_snapshot", fake_get_latest_snapshot)

    snapshot, event_meta, used_previous_event = await ranking_provider.get_snapshot(
        "jp",
        fallback="prev",
    )

    assert snapshot is expected_snapshot
    assert event_meta == {"event_id": 199}
    assert used_previous_event is False
