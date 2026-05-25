from __future__ import annotations

import sys
import types

import pytest

from zhenxun.plugins.mc_server.bluemap import (
    fetch_bluemap_players,
    parse_bluemap_players,
)


def test_parse_bluemap_players_extracts_valid_players_only():
    payload = {
        "players": [
            {
                "uuid": "uuid-1",
                "name": "Steve",
                "position": {"x": 12.5, "y": 64, "z": -7},
            },
            {"name": "", "position": {"x": 1, "y": 2, "z": 3}},
            {"name": "Broken", "position": {"x": None, "y": 2, "z": 3}},
        ]
    }

    players = parse_bluemap_players(payload, "world")

    assert len(players) == 1
    assert players[0].name == "Steve"
    assert players[0].uuid == "uuid-1"
    assert players[0].position_text == "world (12.5, 64.0, -7.0)"


@pytest.mark.asyncio
async def test_fetch_bluemap_players_reads_each_map(monkeypatch):
    calls: list[str] = []

    async def fake_get_json(url: str, **_kwargs):
        calls.append(url)
        return {
            "players": [
                {
                    "uuid": "uuid-1",
                    "name": "Steve",
                    "position": {"x": 1, "y": 2, "z": 3},
                }
            ]
        }

    http_utils_module = types.ModuleType("zhenxun.utils.http_utils")
    http_utils_module.AsyncHttpx = types.SimpleNamespace(get_json=fake_get_json)
    monkeypatch.setitem(sys.modules, "zhenxun.utils.http_utils", http_utils_module)

    players = await fetch_bluemap_players(
        "https://map.example",
        ["world", "/nether/"],
        timeout=3,
    )

    assert [player.map_id for player in players] == ["world", "nether"]
    assert calls == [
        "https://map.example/maps/world/live/players.json",
        "https://map.example/maps/nether/live/players.json",
    ]
