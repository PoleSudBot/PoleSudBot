from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
import nonebot
import pytest

nonebot.init()

from zhenxun import ui
from zhenxun.plugins.moesekai.adapters import profile_renderer


class _FakeSettings:
    profile_viewport_width = 625


class _FakeResponse:
    def __init__(
        self,
        text: str,
        payload: object | None = None,
        *,
        json_error: Exception | None = None,
    ) -> None:
        self.text = text
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _render_profile_template(
    theme_color: str,
    *,
    theme_light: str | None = None,
    theme_dark: str | None = None,
) -> str:
    template_dir = Path(profile_renderer._TEMPLATE_PATH).parent
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("index.html")
    return template.render(
        themeColor=theme_color,
        themeLight=theme_light or f"{theme_color}22",
        themeDark=theme_dark or theme_color,
        avatarUri="file:///tmp/avatar.png",
        displayName="测试玩家",
        displayRank=88,
        displayPower="350,000",
        displayWord="测试签名",
        serverCode="JP",
        processed={
            "userId": "1234567890123",
            "mvp": 10,
            "superStar": 20,
            "deck": {"name": "测试编队"},
            "characterRanks": {"3": 25},
        },
        challengeInfo=None,
        honors=[],
        deckMembers=[],
        creditsList=[],
        musicStats=[],
        footerCharacterName="穗波",
        announcementHtml=None,
        backgroundRows=[0, 1],
    )


@pytest.mark.asyncio
async def test_render_profile_image_uses_ui_template_with_expected_options(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_build_render_payload(server: str, game_id: str):
        assert server == "jp"
        assert game_id == "1234567890123"
        return {"displayName": "测试玩家"}

    async def fake_render_template(path, data, **kwargs):
        assert path == profile_renderer._TEMPLATE_PATH
        assert data["displayName"] == "测试玩家"
        assert kwargs["viewport"] == {"width": 625, "height": 10}
        assert kwargs["wait"] == 150
        assert kwargs["disable_animations"] is True
        assert kwargs["is_page"] is True
        return b"profile-ui"

    monkeypatch.setattr(
        profile_renderer,
        "_build_render_payload",
        fake_build_render_payload,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.adapters.profile_renderer.get_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(ui, "render_template", fake_render_template)

    result = await profile_renderer.render_profile_image("jp", "1234567890123")

    assert result == b"profile-ui"


@pytest.mark.asyncio
async def test_build_render_payload_prefers_leader_character_for_theme_and_footer(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_raw_profile(server: str, game_id: str):
        assert (server, game_id) == ("jp", "123")
        return {"raw": True}

    async def fake_get_cards(server: str):
        assert server == "jp"
        return []

    async def fake_get_honors(server: str):
        assert server == "jp"
        return []

    async def fake_get_honor_groups(server: str):
        assert server == "jp"
        return []

    def fake_process(*args, **kwargs):
        return {
            "name": "测试玩家",
            "rank": 88,
            "word": "",
            "totalPower": 350000,
            "topCharacterId": 21,
            "deck": {
                "members": [
                    {
                        "cardId": 1001,
                        "characterId": 3,
                        "isLeader": True,
                        "rarity": "rarity_4",
                    },
                    {
                        "cardId": 1002,
                        "characterId": 21,
                        "isLeader": False,
                        "rarity": "rarity_4",
                    },
                ]
            },
            "honors": [],
            "musicStats": [],
            "challengeLive": {},
        }

    async def fake_thumbnail(server: str, member: dict[str, object]) -> str:
        return f"file:///tmp/{member['cardId']}.png"

    async def fake_chibi(member: dict[str, object]) -> str:
        return f"file:///tmp/{member['cardId']}-chibi.png"

    async def fake_get_credits() -> dict[str, dict[str, str]]:
        return {}

    async def fake_load_announcement() -> None:
        return None

    monkeypatch.setattr(
        profile_renderer.profile_provider,
        "get_raw_profile",
        fake_get_raw_profile,
    )
    monkeypatch.setattr(profile_renderer.master_data_provider, "get_cards", fake_get_cards)
    monkeypatch.setattr(profile_renderer.master_data_provider, "get_honors", fake_get_honors)
    monkeypatch.setattr(
        profile_renderer.master_data_provider,
        "get_honor_groups",
        fake_get_honor_groups,
    )
    monkeypatch.setattr(profile_renderer.ProfileProcessor, "process", fake_process)
    monkeypatch.setattr(profile_renderer, "_ensure_card_thumbnail_uri", fake_thumbnail)
    monkeypatch.setattr(profile_renderer, "_ensure_chibi_uri", fake_chibi)
    monkeypatch.setattr(
        profile_renderer.profile_static_asset_provider,
        "get_credits",
        fake_get_credits,
    )
    monkeypatch.setattr(profile_renderer, "_load_announcement_html", fake_load_announcement)

    payload = await profile_renderer._build_render_payload("jp", "123")

    assert payload["themeColor"] == "#ee6666"
    assert payload["footerCharacterName"] == "穗波"
    assert payload["avatarUri"] == "file:///tmp/1001.png"


@pytest.mark.asyncio
async def test_load_announcement_html_parses_json_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    responses = [
        _FakeResponse(
            '{"enabled": true, "content": "<b>维护通知</b>"}',
            {"enabled": True, "content": "<b>维护通知</b>"},
        ),
        _FakeResponse(
            '{"enabled": false, "content": "<b>维护通知</b>"}',
            {"enabled": False, "content": "<b>维护通知</b>"},
        ),
        _FakeResponse('"纯文本公告"', "纯文本公告"),
        _FakeResponse(
            "直接文本公告",
            json_error=json.JSONDecodeError("invalid", "直接文本公告", 0),
        ),
    ]

    async def fake_get(url: str, timeout: int):
        assert url == "https://example.com/announcement.json"
        assert timeout == 20
        return responses.pop(0)

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.adapters.profile_renderer.get_settings",
        lambda: type(
            "Settings",
            (),
            {"profile_announcement_url": "https://example.com/announcement.json"},
        )(),
    )
    monkeypatch.setattr(profile_renderer.AsyncHttpx, "get", fake_get)

    assert await profile_renderer._load_announcement_html() == "<b>维护通知</b>"
    assert await profile_renderer._load_announcement_html() is None
    assert await profile_renderer._load_announcement_html() == "纯文本公告"
    assert await profile_renderer._load_announcement_html() == "直接文本公告"


def test_profile_template_applies_runtime_theme_variables_after_stylesheet():
    html = _render_profile_template("#ee6666", theme_dark="#d95a5a")

    stylesheet_index = html.index('<link rel="stylesheet" href="./style.css">')
    theme_style_index = html.index("<style>", stylesheet_index)

    assert stylesheet_index < theme_style_index
    assert "--theme-color: #ee6666;" in html
    assert "--theme-light: #ee666622;" in html
    assert "--theme-dark: #d95a5a;" in html
    assert "主题色 <span style=\"color: #ee6666; font-weight: bold;\">#ee6666</span>" in html


def test_profile_template_keeps_default_theme_fallback_values_renderable():
    html = _render_profile_template("#33ccbb", theme_dark="#2ab3a3")

    stylesheet_index = html.index('<link rel="stylesheet" href="./style.css">')
    theme_style_index = html.index("<style>", stylesheet_index)

    assert stylesheet_index < theme_style_index
    assert "--theme-color: #33ccbb;" in html
    assert "--theme-light: #33ccbb22;" in html
    assert "--theme-dark: #2ab3a3;" in html
