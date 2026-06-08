from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest

from zhenxun.plugins.mc_server import status as mc_status
from zhenxun.plugins.mc_server.types import ServerStatus


@pytest.mark.asyncio
async def test_probe_server_status_returns_raw_status(monkeypatch):
    async def fake_query_raw_status(address: str, *, name: str) -> ServerStatus:
        return ServerStatus(name=name, address=address, online=True, version="1.21.6")

    monkeypatch.setattr(mc_status, "_query_raw_status", fake_query_raw_status)

    result = await mc_status.probe_server_status("mc.example.com", 25565, timeout=1)

    assert result.online is True
    assert result.address == "mc.example.com:25565"
    assert result.version == "1.21.6"


@pytest.mark.asyncio
async def test_probe_server_status_converts_failure_to_offline(monkeypatch):
    async def fake_query_raw_status(_address: str, *, name: str) -> ServerStatus:
        _ = name
        raise RuntimeError("connection failed")

    monkeypatch.setattr(mc_status, "_query_raw_status", fake_query_raw_status)

    result = await mc_status.probe_server_status("mc.example.com", 25565, timeout=1)

    assert result.online is False
    assert result.error == "connection failed"


@pytest.mark.asyncio
async def test_query_status_marks_player_list_complete_only_when_counts_match(
    monkeypatch,
):
    async def fake_query_raw_status(_address: str, *, name: str) -> ServerStatus:
        return ServerStatus(
            name=name,
            address="mc.example.com:25565",
            online=True,
            online_players=2,
            max_players=20,
            players=[
                SimpleNamespace(name="Steve", uuid="uuid-steve"),
                SimpleNamespace(name="Alex", uuid="uuid-alex"),
            ],
        )

    async def fake_safe_bluemap_players_by_name(_server, *, timeout):
        _ = timeout
        return {}

    async def fake_list_active_sessions(_server):
        return []

    async def fake_get_total_online_seconds_by_player_names(_server, names):
        return {name.lower(): 0 for name in names}

    repositories = import_module("zhenxun.plugins.mc_server.repositories")
    monkeypatch.setattr(mc_status, "_query_raw_status", fake_query_raw_status)
    monkeypatch.setattr(
        mc_status,
        "_safe_bluemap_players_by_name",
        fake_safe_bluemap_players_by_name,
    )
    monkeypatch.setattr(repositories, "list_active_sessions", fake_list_active_sessions)
    monkeypatch.setattr(
        repositories,
        "get_total_online_seconds_by_player_names",
        fake_get_total_online_seconds_by_player_names,
    )

    server = SimpleNamespace(host="mc.example.com", port=25565, name="主服")
    result = await mc_status._query_status(server, timeout=1)

    assert result.player_list_complete is True

    async def fake_query_raw_status_mismatch(
        _address: str,
        *,
        name: str,
    ) -> ServerStatus:
        return ServerStatus(
            name=name,
            address="mc.example.com:25565",
            online=True,
            online_players=2,
            max_players=20,
            players=[SimpleNamespace(name="Steve", uuid="uuid-steve")],
        )

    monkeypatch.setattr(mc_status, "_query_raw_status", fake_query_raw_status_mismatch)
    result = await mc_status._query_status(server, timeout=1)

    assert result.player_list_complete is False
