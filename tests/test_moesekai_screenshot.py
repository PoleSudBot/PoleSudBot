from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.config import MoeSekaiSettings
from zhenxun.plugins.moesekai.screenshot import (
    ScreenshotJob,
    _redact_url_secrets,
    screenshot_service,
)


def test_default_profile_viewport_width_is_625():
    assert MoeSekaiSettings().profile_viewport_width == 625


def test_default_profile_render_mode_is_internal_first():
    assert MoeSekaiSettings().profile_render_mode == "internal_first"


def test_default_profile_api_base_jp_uses_uni_url():
    assert (
        MoeSekaiSettings().profile_api_base_jp
        == "https://api.unipjsk.com/api/user/%7Buser_id%7D"
    )


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
async def test_capture_logs_stage_timings_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    service = type(screenshot_service)()
    debug_messages: list[str] = []

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(
            screenshot_retry_times=1, screenshot_retry_delay_seconds=0
        ),
    )

    async def fake_capture_once(_job, _url, *, stage_timings=None, attempt_meta=None):
        if stage_timings is not None:
            stage_timings.extend(
                [
                    ("browser/get", 12.0),
                    ("context/new", 8.0),
                    ("page/new", 4.0),
                    ("goto", 31.5),
                    ("wait_callback", 48.2),
                ]
            )
        if attempt_meta is not None:
            attempt_meta["page_state"] = "success"
            attempt_meta["fallback_clicked"] = "1"
        return b"image"

    def fake_debug(message: str, *_args, **_kwargs):
        debug_messages.append(message)

    monkeypatch.setattr(service, "_capture_once", fake_capture_once)
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.logger",
        SimpleNamespace(debug=fake_debug, warning=lambda *_args, **_kwargs: None),
    )

    result = await service.capture(
        ScreenshotJob(
            kind="查角色",
            urls=["https://example.com/character/21?token=secret"],
            viewport={"width": 650, "height": 932},
            device_scale_factor=1.0,
            log_timing=True,
        )
    )

    assert result == b"image"
    combined = "\n".join(debug_messages)
    assert "候选站点选择" in combined
    assert "browser/get=12.0ms" in combined
    assert "context/new=8.0ms" in combined
    assert "page/new=4.0ms" in combined
    assert "goto=31.5ms" in combined
    assert "状态=success" in combined
    assert "兜底点击=1" in combined
    assert (
        "第1次命中站点 https://example.com/character/21?token=%2A%2A%2A"
        in combined
    )


@pytest.mark.asyncio
async def test_capture_story_waits_for_content_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(character_viewport_width=650, story_top_crop=100),
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
        lambda: MoeSekaiSettings(character_viewport_width=650, character_top_crop=100),
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
        assert job.scroll_through_page is False
        assert job.scroll_if_function is not None
        assert "svg image" in (job.before_capture_script or "")
        assert "Promise.allSettled" in (job.before_capture_script or "")
        assert job.extra_wait_seconds == 0
        return b"character"

    monkeypatch.setattr(screenshot_service, "capture", fake_capture)
    result = await screenshot_service.capture_character(21)
    assert result == b"character"


def test_filter_urls_prefers_last_successful_site():
    service = type(screenshot_service)()
    service._last_success_site["查角色"] = "https://pjsk.moe"
    service._site_failures["https://snowyviewer.exmeaning.com"] = time.time() + 60

    urls = service._filter_urls(
        "查角色",
        [
            "https://snowyviewer.exmeaning.com/character/21",
            "https://pjsk.moe/character/21",
        ],
    )

    assert urls[0] == "https://pjsk.moe/character/21"


@pytest.mark.asyncio
async def test_capture_limits_concurrency_with_semaphore(
    monkeypatch: pytest.MonkeyPatch,
):
    service = type(screenshot_service)()
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(
            screenshot_retry_times=1, screenshot_retry_delay_seconds=0
        ),
    )

    current = 0
    max_current = 0
    release = asyncio.Event()

    async def fake_capture_once(*_args, **_kwargs):
        nonlocal current, max_current
        current += 1
        max_current = max(max_current, current)
        try:
            await release.wait()
            return b"image"
        finally:
            current -= 1

    monkeypatch.setattr(service, "_capture_once", fake_capture_once)

    job = ScreenshotJob(
        kind="查角色",
        urls=["https://example.com/a"],
        viewport={"width": 650, "height": 932},
        device_scale_factor=1.0,
    )

    tasks = [asyncio.create_task(service.capture(job)) for _ in range(3)]
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(*tasks)

    assert max_current == 2


@pytest.mark.asyncio
async def test_capture_propagates_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
):
    service = type(screenshot_service)()
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.screenshot.get_settings",
        lambda: MoeSekaiSettings(
            screenshot_retry_times=3, screenshot_retry_delay_seconds=0
        ),
    )

    async def fake_capture_once(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_capture_once", fake_capture_once)

    job = ScreenshotJob(
        kind="查角色",
        urls=["https://example.com/character/21"],
        viewport={"width": 650, "height": 932},
        device_scale_factor=1.0,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.capture(job)
