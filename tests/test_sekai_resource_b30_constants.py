from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from zhenxun.services.sekai_resource import b30_constants as constants_module
from zhenxun.services.sekai_resource.b30_constants import B30ConstantsProvider
from zhenxun.services.sekai_resource.storage import JsonStateStore


def _settings(**overrides):
    payload = {
        "b30_constants_url": "https://example.com/b30.csv",
        "b30_constants_timeout_seconds": 10.0,
        "b30_constants_refresh_interval_seconds": 86_400,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


@pytest.mark.asyncio
async def test_b30_constants_provider_saves_and_reuses_local_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    cache_path = tmp_path / "b30_constants.csv"
    state_store = JsonStateStore(tmp_path / "state.json")
    calls = 0

    class FakeResponse:
        text = (
            "Song,JP Name,Constant,Level,Note Count,Difficulty,Song ID,Notes\n"
            "Kaiju ni Naritai,怪獣になりたい,38.1,APD 38,2800,Append,671,help\n"
        )

    async def fake_get(url: str, *, timeout: float):
        nonlocal calls
        calls += 1
        assert (url, timeout) == ("https://example.com/b30.csv", 10.0)
        return FakeResponse()

    monkeypatch.setattr(constants_module, "_CACHE_PATH", cache_path)
    monkeypatch.setattr(constants_module, "_STATE_STORE", state_store)
    monkeypatch.setattr(constants_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(constants_module.AsyncHttpx, "get", fake_get)

    provider = B30ConstantsProvider()
    first = await provider.get_table()
    second = await provider.get_table()

    assert calls == 1
    assert first.get(671, "append").constant == 38.1
    assert first.get(671, "append").level_label == "38"
    assert second.get(671, "append").level == 38
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_b30_constants_provider_falls_back_to_cache_on_refresh_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    cache_path = tmp_path / "b30_constants.csv"
    cache_path.write_text(
        "Song,JP Name,Constant,Level,Note Count,Difficulty,Song ID,Notes\n"
        "YAMINABE!!!!!,ヤミナベ!!!!,37.6,37+,2015,Master,329,\n",
        encoding="utf-8",
    )
    state_store = JsonStateStore(tmp_path / "state.json")
    state_store.save(
        {
            "source_url": "https://example.com/b30.csv",
            "checked_at": 0,
            "updated_at": 1,
        }
    )

    async def fake_get(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(constants_module, "_CACHE_PATH", cache_path)
    monkeypatch.setattr(constants_module, "_STATE_STORE", state_store)
    monkeypatch.setattr(
        constants_module,
        "get_settings",
        lambda: _settings(b30_constants_refresh_interval_seconds=60),
    )
    monkeypatch.setattr(constants_module.AsyncHttpx, "get", fake_get)

    table = await B30ConstantsProvider().get_table()

    assert table.get(329, "master").level_label == "37"
    assert state_store.load({})["error"].startswith("RuntimeError: network down")
