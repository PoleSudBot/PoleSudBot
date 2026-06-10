from __future__ import annotations

import base64
from io import BytesIO
import json
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from zhenxun.plugins.mc_server import player_heads


def _skin_bytes() -> bytes:
    skin = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(8, 16):
        for x in range(8, 16):
            skin.putpixel((x, y), (10, 20, 30, 255))
    for y in range(8, 16):
        for x in range(40, 48):
            skin.putpixel((x, y), (200, 0, 0, 128))
    output = BytesIO()
    skin.save(output, format="PNG")
    return output.getvalue()


def _textures_payload(url: str) -> str:
    payload = {"textures": {"SKIN": {"url": url}}}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        request_timeout_seconds=3,
        player_heads_enabled=True,
        player_head_cache_seconds=604800,
        player_head_failure_cache_seconds=600,
        mojang_profile_url_template="https://profile.example/{name}",
        mojang_session_url_template="https://session.example/{uuid}",
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    player_heads._HEAD_CACHE.clear()
    player_heads._HEAD_INFLIGHT.clear()


def test_build_head_png_blends_hat_layer():
    result = player_heads.build_head_png(_skin_bytes(), size=8)

    with Image.open(BytesIO(result)) as image:
        pixel = image.convert("RGBA").getpixel((0, 0))

    assert pixel[0] > 10
    assert pixel[1] < 20
    assert pixel[2] < 30
    assert pixel[3] == 255


@pytest.mark.asyncio
async def test_get_player_head_uri_falls_back_from_offline_uuid_to_name(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    async def fake_get_json(url: str, **_kwargs):
        calls.append(url)
        if url == "https://session.example/00000000000000000000000000000001":
            return {}
        if url == "https://profile.example/Steve":
            return {"id": "00000000000000000000000000000002"}
        if url == "https://session.example/00000000000000000000000000000002":
            return {
                "properties": [
                    {
                        "name": "textures",
                        "value": _textures_payload("https://skin.example/steve.png"),
                    }
                ]
            }
        return {}

    async def fake_get_content(url: str, **_kwargs):
        calls.append(url)
        return _skin_bytes()

    monkeypatch.setattr(player_heads, "_get_settings", _settings)
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.utils.http_utils",
        SimpleNamespace(
            AsyncHttpx=SimpleNamespace(
                get_json=fake_get_json,
                get_content=fake_get_content,
            )
        ),
    )

    uri = await player_heads.get_player_head_uri(
        "Steve",
        "00000000-0000-0000-0000-000000000001",
    )

    assert uri.startswith("data:image/png;base64,")
    assert calls == [
        "https://session.example/00000000000000000000000000000001",
        "https://profile.example/Steve",
        "https://session.example/00000000000000000000000000000002",
        "https://skin.example/steve.png",
    ]


@pytest.mark.asyncio
async def test_get_player_head_uri_uses_fallback_head_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_json(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(player_heads, "_get_settings", _settings)
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.utils.http_utils",
        SimpleNamespace(
            AsyncHttpx=SimpleNamespace(
                get_json=fake_get_json,
                get_content=lambda *_args, **_kwargs: b"",
            )
        ),
    )

    uri = await player_heads.get_player_head_uri("OfflineOnly")

    assert uri == player_heads.default_player_head_uri()


@pytest.mark.asyncio
async def test_resolve_player_head_uris_respects_disabled_setting(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = _settings()
    settings.player_heads_enabled = False
    monkeypatch.setattr(player_heads, "_get_settings", lambda: settings)

    result = await player_heads.resolve_player_head_uris(
        [player_heads.PlayerHeadRequest("Steve")]
    )

    assert result == {}
