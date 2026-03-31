from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import nonebot
import httpx
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.config import (
    MasterSourceConfig,
    _merge_master_sources_config,
)
from zhenxun.plugins.moesekai.master_data import (
    MasterDataService,
    RegionUpdateResult,
    SourceVersionInfo,
    master_data_service,
)
from zhenxun.plugins.moesekai.providers import masterdata as masterdata_module


@pytest.mark.asyncio
async def test_get_current_event_next_first(monkeypatch: pytest.MonkeyPatch):
    now = datetime.now()
    previous_event = {
        "id": 1,
        "startAt": int((now - timedelta(days=4)).timestamp() * 1000),
        "aggregateAt": int((now - timedelta(days=3)).timestamp() * 1000),
    }
    next_event = {
        "id": 2,
        "startAt": int((now + timedelta(days=1)).timestamp() * 1000),
        "aggregateAt": int((now + timedelta(days=2)).timestamp() * 1000),
    }

    async def fake_get_events(_cls: type[MasterDataService], server: str):
        assert server == "cn"
        return [previous_event, next_event]

    monkeypatch.setattr(
        MasterDataService,
        "get_events",
        classmethod(fake_get_events),
    )

    event = await master_data_service.get_current_event("cn", fallback="next_first")
    assert event is not None
    assert event["id"] == 2


def test_master_source_config_accepts_legacy_urls():
    config = MasterSourceConfig.model_validate(
        {
            "name": "legacy-jp",
            "region": "jp",
            "version_url": "https://example.com/master/versions.json",
            "events_url": "https://example.com/master/events.json",
            "version_field": "dataVersion",
        }
    )
    assert config.base_url == ""
    assert config.version_path == "https://example.com/master/versions.json"
    assert config.events_path == "https://example.com/master/events.json"
    assert config.version_url == "https://example.com/master/versions.json"
    assert config.events_url == "https://example.com/master/events.json"


def test_merge_master_sources_config_uses_new_default_order():
    merged = _merge_master_sources_config(
        [
            {
                "name": "haruki-jp",
                "region": "jp",
                "version_url": "https://example.com/haruki/versions/current_version.json",
                "events_url": "https://example.com/haruki/master/events.json",
                "version_field": "dataVersion",
            },
            {
                "name": "8823-jp",
                "region": "jp",
                "version_url": "https://example.com/8823/versions.json",
                "events_url": "https://example.com/8823/events.json",
                "version_field": "data_version",
            },
        ]
    )
    assert [item.name for item in merged[:6]] == [
        "8823-cn",
        "8823-jp",
        "8823-tw",
        "haruki-cn",
        "haruki-jp",
        "haruki-tw",
    ]
    assert any(item.name == "sekai-viewer-jp" for item in merged)


@pytest.mark.asyncio
async def test_select_source_prefers_config_order_on_equal_versions(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = SimpleNamespace(
        master_sources=[
            MasterSourceConfig(
                name="8823-jp",
                region="jp",
                base_url="https://example.com/8823",
                version_path="versions.json",
                events_path="events.json",
                version_field="data_version",
            ),
            MasterSourceConfig(
                name="haruki-jp",
                region="jp",
                base_url="https://example.com/haruki",
                version_path="versions/current_version.json",
                events_path="master/events.json",
                version_field="dataVersion",
            ),
            MasterSourceConfig(
                name="sekai-viewer-jp",
                region="jp",
                base_url="https://example.com/viewer",
                version_path="versions.json",
                events_path="events.json",
                version_field="dataVersion",
            ),
        ]
    )

    async def fake_fetch_source_revision(
        _cls: type[MasterDataService], source: MasterSourceConfig
    ) -> SourceVersionInfo:
        return SourceVersionInfo(
            source=source,
            revision="abc123",
            version="6.0.0.1",
            success=True,
        )

    monkeypatch.setattr(masterdata_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        MasterDataService,
        "_fetch_source_revision",
        classmethod(fake_fetch_source_revision),
    )

    selected_source, source_versions = await MasterDataService._select_source("jp")
    assert selected_source is not None
    assert selected_source.name == "8823-jp"
    assert [item.source.name for item in source_versions] == [
        "8823-jp",
        "haruki-jp",
        "sekai-viewer-jp",
    ]


def test_probe_interval_seconds_uses_full_cycle(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        master_check_interval_seconds=600,
        master_source_order=["8823", "haruki", "sekai-viewer"],
        master_sources=[],
    )
    monkeypatch.setattr(masterdata_module, "get_settings", lambda: settings)

    assert master_data_service.get_probe_interval_seconds() == 200


@pytest.mark.asyncio
async def test_fetch_source_revision_uses_github_token_and_etag(
    monkeypatch: pytest.MonkeyPatch,
):
    source = MasterSourceConfig(
        name="haruki-jp",
        region="jp",
        owner="Team-Haruki",
        repo="haruki-sekai-master",
        branch="main",
        version_path="versions/current_version.json",
        version_field="dataVersion",
        datasets={"events": "master/events.json"},
    )

    settings = SimpleNamespace(
        master_check_mode="revision",
        github_token="ghp_test_token",
    )

    requests: list[dict[str, str]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str]):
            requests.append({"url": url, **headers})
            return httpx.Response(
                304,
                request=httpx.Request("GET", url),
                headers={"etag": '"etag-123"'},
            )

    async def fake_get_probe_source_state(_cls, _name: str):
        return {
            "etag": '"etag-123"',
            "last_revision": "old-sha",
            "last_version": "6.3.5.11",
        }

    updated_states: list[dict[str, str]] = []

    async def fake_update_probe_source_state(_cls, _name: str, **updates):
        updated_states.append(updates)
        return updates

    monkeypatch.setattr(masterdata_module, "get_settings", lambda: settings)
    monkeypatch.setattr(masterdata_module.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(
        MasterDataService,
        "_get_probe_source_state",
        classmethod(fake_get_probe_source_state),
    )
    monkeypatch.setattr(
        MasterDataService,
        "_update_probe_source_state",
        classmethod(fake_update_probe_source_state),
    )

    info = await MasterDataService._fetch_source_revision(source)

    assert info.success is True
    assert info.revision == "old-sha"
    assert info.version == "6.3.5.11"
    assert requests
    assert requests[0]["Authorization"] == "Bearer ghp_test_token"
    assert requests[0]["If-None-Match"] == '"etag-123"'
    assert requests[0]["Accept"] == "application/vnd.github+json"
    assert updated_states


def test_region_update_result_message_includes_all_sources():
    result = RegionUpdateResult(
        server="jp",
        selected_source_name="8823-jp",
        previous_revision="old-rev",
        current_revision="new-rev",
        previous_version="6.3.5.10",
        current_version="6.3.5.11",
        updated=True,
        source_versions=[
            SourceVersionInfo(
                source=MasterSourceConfig(
                    name="8823-jp",
                    region="jp",
                    base_url="https://example.com/8823",
                    version_path="versions.json",
                    events_path="events.json",
                    version_field="data_version",
                ),
                version="6.3.5.11",
                success=True,
            ),
            SourceVersionInfo(
                source=MasterSourceConfig(
                    name="haruki-jp",
                    region="jp",
                    base_url="https://example.com/haruki",
                    version_path="versions/current_version.json",
                    events_path="master/events.json",
                    version_field="dataVersion",
                ),
                version="6.3.5.11",
                success=True,
            ),
        ],
    )
    message = result.to_message()
    assert "来源: 8823" in message
    assert "Revision: old-rev -> new-rev" in message
    assert "日服 MasterData 数据源" in message
    assert "[8823] 6.3.5.11" in message
    assert "[Haruki] 6.3.5.11" in message
