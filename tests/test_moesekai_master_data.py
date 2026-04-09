from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.config import (
    MASTER_DATASET_KEYS,
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
    default_datasets = {key: f"{key}.json" for key in MASTER_DATASET_KEYS}
    settings = SimpleNamespace(
        master_source_order=["8823", "haruki", "sekai-viewer"],
        master_sources=[
            MasterSourceConfig(
                name="8823-jp",
                region="jp",
                base_url="https://example.com/8823",
                version_path="versions.json",
                version_field="data_version",
                datasets=default_datasets,
            ),
            MasterSourceConfig(
                name="haruki-jp",
                region="jp",
                base_url="https://example.com/haruki",
                version_path="versions/current_version.json",
                version_field="dataVersion",
                datasets={key: f"master/{key}.json" for key in MASTER_DATASET_KEYS},
            ),
            MasterSourceConfig(
                name="sekai-viewer-jp",
                region="jp",
                base_url="https://example.com/viewer",
                version_path="versions.json",
                version_field="dataVersion",
                datasets=default_datasets,
                auto_probe=False,
            ),
        ],
    )

    async def fake_fetch_source_snapshot(
        _cls: type[MasterDataService], source: MasterSourceConfig
    ) -> SourceVersionInfo:
        return SourceVersionInfo(
            source=source,
            version="6.0.0.1",
            success=True,
        )

    async def fake_fetch_selected_revision(
        _cls: type[MasterDataService], source: MasterSourceConfig
    ) -> str:
        assert source.name == "8823-jp"
        return "abc123"

    monkeypatch.setattr(masterdata_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        MasterDataService,
        "_fetch_source_snapshot",
        classmethod(fake_fetch_source_snapshot),
    )
    monkeypatch.setattr(
        MasterDataService,
        "_fetch_selected_revision",
        classmethod(fake_fetch_selected_revision),
    )

    selected_source, source_versions = await MasterDataService._select_source(
        "jp",
        include_lazy=True,
    )

    assert selected_source is not None
    assert selected_source.name == "8823-jp"
    assert [item.source.name for item in source_versions] == [
        "8823-jp",
        "haruki-jp",
        "sekai-viewer-jp",
    ]


def test_probe_interval_seconds_uses_full_interval(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        master_check_interval_seconds=600,
        master_source_order=["8823", "haruki", "sekai-viewer"],
        master_sources=[],
    )
    monkeypatch.setattr(masterdata_module, "get_settings", lambda: settings)

    assert master_data_service.get_probe_interval_seconds() == 600


@pytest.mark.asyncio
async def test_fetch_selected_revision_uses_github_token(
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

    settings = SimpleNamespace(github_token="ghp_test_token")
    requests: list[dict[str, str]] = []
    updated_states: list[dict[str, str | None]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str]):
            requests.append({"url": url, **headers})
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"sha": "new-sha"},
            )

    async def fake_get_probe_source_state(_cls, _name: str):
        return {"last_revision": "old-sha"}

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

    revision = await MasterDataService._fetch_selected_revision(source)

    assert revision == "new-sha"
    assert requests
    assert requests[0]["Authorization"] == "Bearer ghp_test_token"
    assert requests[0]["Accept"] == "application/vnd.github+json"
    assert requests[0]["X-GitHub-Api-Version"] == "2022-11-28"
    assert updated_states


@pytest.mark.asyncio
async def test_get_dataset_does_not_cache_missing_payload_on_failed_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    update_calls = 0
    original_cache = MasterDataService._cache
    MasterDataService._cache = {}

    def fake_dataset_path(_cls: type[MasterDataService], server: str, dataset: str):
        path = tmp_path / server / f"{dataset}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def fake_update_region(
        _cls: type[MasterDataService],
        server: str,
        *,
        force: bool = False,
    ):
        nonlocal update_calls
        assert force is False
        update_calls += 1
        return RegionUpdateResult(server=server, error="下载失败")

    monkeypatch.setattr(
        MasterDataService,
        "_dataset_path",
        classmethod(fake_dataset_path),
    )
    monkeypatch.setattr(
        MasterDataService,
        "update_region",
        classmethod(fake_update_region),
    )

    try:
        first = await MasterDataService.get_dataset("jp", "events")
        second = await MasterDataService.get_dataset("jp", "events")
    finally:
        MasterDataService._cache = original_cache

    assert first == []
    assert second == []
    assert update_calls == 2
    assert ("jp", "events") not in MasterDataService._cache


@pytest.mark.asyncio
async def test_update_region_skips_auto_downgrade_when_local_file_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    source = MasterSourceConfig(
        name="8823-jp",
        region="jp",
        base_url="https://example.com/8823",
        version_path="versions.json",
        version_field="dataVersion",
        datasets={key: f"{key}.json" for key in MASTER_DATASET_KEYS},
    )
    original_cache = MasterDataService._cache
    MasterDataService._cache = {}
    saved_state: dict[str, dict[str, str]] = {}

    def fake_dataset_path(_cls: type[MasterDataService], server: str, dataset: str):
        path = tmp_path / server / f"{dataset}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def fake_select_source(
        _cls: type[MasterDataService],
        server: str,
        *,
        include_lazy: bool,
    ):
        assert server == "jp"
        assert include_lazy is False
        return source, [
            SourceVersionInfo(
                source=source,
                revision="older-rev",
                version="6.0.0.1",
                success=True,
            )
        ]

    async def fail_fetch_selected_payloads(*_args, **_kwargs):
        raise AssertionError("自动路径遇到更低版本时不应因为缺文件而回退下载")

    for dataset in MASTER_DATASET_KEYS:
        if dataset == "events":
            continue
        fake_dataset_path(MasterDataService, "jp", dataset).write_text("[]", encoding="utf-8")

    monkeypatch.setattr(
        MasterDataService,
        "_dataset_path",
        classmethod(fake_dataset_path),
    )
    monkeypatch.setattr(
        MasterDataService,
        "_select_source",
        classmethod(fake_select_source),
    )
    monkeypatch.setattr(
        MasterDataService,
        "_fetch_selected_payloads",
        classmethod(fail_fetch_selected_payloads),
    )
    monkeypatch.setattr(
        MasterDataService,
        "_load_state",
        classmethod(
            lambda _cls: {
                "jp": {
                    "source_name": "sekai-viewer-jp",
                    "revision": "newer-rev",
                    "version": "6.0.0.2",
                }
            }
        ),
    )
    monkeypatch.setattr(
        MasterDataService,
        "_save_state",
        classmethod(lambda _cls, payload: saved_state.update(payload)),
    )
    monkeypatch.setattr(
        masterdata_module,
        "get_settings",
        lambda: SimpleNamespace(
            master_check_mode="version",
            master_source_order=["8823", "haruki", "sekai-viewer"],
            master_sources=[source],
            github_token="",
            master_check_interval_seconds=180,
        ),
    )

    try:
        result = await MasterDataService.update_region("jp", force=False)
    finally:
        MasterDataService._cache = original_cache

    assert result.updated is False
    assert result.download_success is True
    assert result.current_version == "6.0.0.1"
    assert result.previous_version == "6.0.0.2"
    assert result.selection_note == "候选版本低于当前已落地版本，已跳过自动回退"
    assert not saved_state
    assert not fake_dataset_path(MasterDataService, "jp", "events").exists()


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
                    version_field="data_version",
                    datasets={"events": "events.json"},
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
                    version_field="dataVersion",
                    datasets={"events": "master/events.json"},
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
