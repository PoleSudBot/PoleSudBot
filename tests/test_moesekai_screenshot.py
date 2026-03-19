from __future__ import annotations

import time

import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.config import MoeSekaiSettings
from zhenxun.plugins.moesekai.screenshot import screenshot_service


def test_default_profile_viewport_width_is_625():
    assert MoeSekaiSettings().profile_viewport_width == 625


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
