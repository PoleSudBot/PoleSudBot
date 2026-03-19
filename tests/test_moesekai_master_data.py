from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import nonebot
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


def test_merge_master_sources_config_injects_new_defaults():
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
    assert [item.name for item in merged[:3]] == [
        "sekai-viewer-jp",
        "sekai-viewer-cn",
        "sekai-viewer-tw",
    ]
    assert any(item.name == "haruki-jp" for item in merged)
    assert any(item.name == "8823-jp" for item in merged)


@pytest.mark.asyncio
async def test_select_source_prefers_config_order_on_equal_versions(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = SimpleNamespace(
        master_sources=[
            MasterSourceConfig(
                name="sekai-viewer-jp",
                region="jp",
                base_url="https://example.com/viewer",
                version_path="versions.json",
                events_path="events.json",
                version_field="dataVersion",
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
                name="8823-jp",
                region="jp",
                base_url="https://example.com/8823",
                version_path="versions.json",
                events_path="events.json",
                version_field="data_version",
            ),
        ]
    )

    async def fake_fetch_source_version(
        _cls: type[MasterDataService], source: MasterSourceConfig
    ) -> SourceVersionInfo:
        return SourceVersionInfo(source=source, version="6.0.0.1", success=True)

    from zhenxun.plugins.moesekai import master_data as master_data_module

    monkeypatch.setattr(master_data_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        MasterDataService,
        "_fetch_source_version",
        classmethod(fake_fetch_source_version),
    )

    selected_source, source_versions = await MasterDataService._select_source("jp")
    assert selected_source is not None
    assert selected_source.name == "sekai-viewer-jp"
    assert [item.source.name for item in source_versions] == [
        "sekai-viewer-jp",
        "haruki-jp",
        "8823-jp",
    ]


def test_region_update_result_message_includes_all_sources():
    result = RegionUpdateResult(
        server="jp",
        selected_source_name="sekai-viewer-jp",
        previous_version="6.3.5.10",
        current_version="6.3.5.11",
        updated=True,
        source_versions=[
            SourceVersionInfo(
                source=MasterSourceConfig(
                    name="sekai-viewer-jp",
                    region="jp",
                    base_url="https://example.com/viewer",
                    version_path="versions.json",
                    events_path="events.json",
                    version_field="dataVersion",
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
    assert "来源: sekai.best" in message
    assert "日服MasterData数据源" in message
    assert "[sekai.best] 6.3.5.11" in message
    assert "[haruki] 6.3.5.11" in message
