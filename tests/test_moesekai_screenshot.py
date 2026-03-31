from __future__ import annotations

import time

import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.config import MoeSekaiSettings
from zhenxun.plugins.moesekai.screenshot import _redact_url_secrets, screenshot_service


def test_default_profile_viewport_width_is_625():
    assert MoeSekaiSettings().profile_viewport_width == 625


def test_build_profile_url_omits_empty_token():
    url = screenshot_service._build_profile_url(
        "https://example.com/profile/{server}/{game_id}?mode=screenshot&token={token}",
        server="jp",
        game_id="1234567890123",
        token="",
    )
    assert url == "https://example.com/profile/jp/1234567890123?mode=screenshot"


def test_build_profile_url_appends_token_when_template_has_no_token_param():
    url = screenshot_service._build_profile_url(
        "https://example.com/profile/{server}/{game_id}?mode=screenshot",
        server="jp",
        game_id="1234567890123",
        token="test-token",
    )
    assert (
        url
        == "https://example.com/profile/jp/1234567890123?mode=screenshot&token=test-token"
    )


def test_redact_url_secrets_masks_token_query_param():
    assert (
        _redact_url_secrets(
            "https://example.com/profile/jp/123?mode=screenshot&token=secret-token"
        )
        == "https://example.com/profile/jp/123?mode=screenshot&token=%2A%2A%2A"
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://rk.exmeaning.com", "https://rk.exmeaning.com"),
        ("https://rk.exmeaning.com/", "https://rk.exmeaning.com"),
        ("rk.exmeaning.com", "https://rk.exmeaning.com"),
        ("https://rk.exmeaning.com/public", "https://rk.exmeaning.com/public"),
    ],
)
def test_ranking_api_base_normalizes_only_current_host_shape(
    configured: str,
    expected: str,
):
    assert MoeSekaiSettings(ranking_api_base=configured).ranking_api_base == expected


@pytest.mark.asyncio
async def test_capture_profile_uses_configured_width(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(profile_viewport_width=625),
    )

    async def fake_capture(job):
        assert job.viewport["width"] == 625
        assert job.scroll_through_page is False
        assert job.scroll_if_function is not None
        assert job.stability_wait_ms == 40
        assert job.extra_wait_seconds == 0
        assert "animation-duration: 0s" in (job.before_capture_script or "")
        return b"profile"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_profile("jp", "1234567890123")
    assert result == b"profile"


@pytest.mark.asyncio
async def test_capture_ranking_uses_configured_width_without_scroll(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(ranking_viewport_width=850),
    )

    async def fake_capture(job):
        assert job.viewport["width"] == 850
        assert job.wait_selector == ".rank-list"
        assert job.scroll_through_page is False
        assert job.full_page is True
        assert job.stability_wait_ms == 36
        assert job.extra_wait_seconds == 0
        return b"ranking"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_ranking("cn")
    assert result == b"ranking"


@pytest.mark.asyncio
async def test_capture_historical_ranking_does_not_require_canvas(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(ranking_viewport_width=850),
    )

    async def fake_capture(job):
        assert job.viewport["width"] == 850
        assert job.wait_selector == ".header-wrapper"
        assert "loading-overlay" in (job.wait_function or "")
        assert "canvas" not in (job.wait_function or "")
        assert job.stability_wait_ms == 24
        assert job.extra_wait_seconds == 0
        return b"historical-ranking"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_ranking("jp", event_id=178)
    assert result == b"historical-ranking"


@pytest.mark.asyncio
async def test_capture_deck_uses_crop_config(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(deck_viewport_width=650, deck_top_crop=100),
    )

    async def fake_capture(job):
        assert job.viewport["width"] == 650
        assert job.top_crop_css_pixels == 100
        assert job.full_page is True
        assert job.stability_wait_ms == 42
        assert job.extra_wait_seconds == 0
        assert "body > main > nav" in (job.prepare_script or "")
        return b"deck"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_deck(
        server="cn",
        game_id="1234567890123",
        event_id=195,
        music_id=226,
        difficulty="hard",
        live_type="multi",
    )
    assert result == b"deck"


@pytest.mark.asyncio
async def test_capture_story_waits_for_content_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(deck_viewport_width=650, story_top_crop=100),
    )

    async def fake_capture(job):
        assert job.viewport["width"] == 650
        assert job.top_crop_css_pixels == 100
        assert job.full_page is True
        assert "活动概要" in (job.wait_function or "")
        assert "章节列表" in (job.wait_function or "")
        assert "正在加载" in (job.wait_function or "")
        assert "img.complete" in (job.wait_function or "")
        assert job.scroll_if_function is not None
        return b"story"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_story(199)
    assert result == b"story"


@pytest.mark.asyncio
async def test_capture_character_waits_for_related_cards_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(deck_viewport_width=650, character_top_crop=100),
    )

    async def fake_capture(job):
        assert job.viewport["width"] == 650
        assert job.top_crop_css_pixels == 100
        assert job.full_page is True
        assert "正在加载角色信息" in (job.wait_function or "")
        assert "Character Trim" in (job.wait_function or "")
        assert "基本信息" in (job.wait_function or "")
        assert "个人档案" in (job.wait_function or "")
        assert "相关卡牌" in (job.wait_function or "")
        assert "\\/cards\\/" in (job.wait_function or "")
        assert job.scroll_through_page is True
        assert "svg image" in (job.before_capture_script or "")
        assert "Promise.allSettled" in (job.before_capture_script or "")
        assert job.extra_wait_seconds == 3.0
        return b"character"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_character(21)
    assert result == b"character"


def test_filter_urls_prefers_last_successful_site():
    service = type(screenshot_service)()
    service._last_success_site["活动组卡"] = "https://pjsk.moe"
    service._site_failures["https://snowyviewer.exmeaning.com"] = time.time() + 60

    urls = service._filter_urls(
        "活动组卡",
        [
            "https://snowyviewer.exmeaning.com/deck-recommend",
            "https://pjsk.moe/deck-recommend",
        ],
    )

    assert urls[0] == "https://pjsk.moe/deck-recommend"
