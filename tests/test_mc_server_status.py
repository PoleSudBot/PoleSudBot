from __future__ import annotations

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
