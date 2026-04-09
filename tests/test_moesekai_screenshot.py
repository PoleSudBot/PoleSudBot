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
        assert job.wait_function is None
        assert job.wait_callback is not None
        assert "body > main > nav" in (job.prepare_script or "")
        assert "scroll-behavior: auto" in (job.prepare_script or "")
        assert job.scroll_if_function is not None
        assert "window.innerHeight + 80" in (job.scroll_if_function or "")
        assert "读取到的用户数据格式异常" in (job.scroll_if_function or "")
        assert job.before_capture_script is not None
        assert ".dr-result-row" in (job.before_capture_script or "")
        assert "Promise.allSettled" in (job.before_capture_script or "")
        assert job.log_timing is True
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
            kind="活动组卡",
            urls=["https://example.com/deck-recommend?token=secret"],
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
        "第1次命中站点 https://example.com/deck-recommend?token=%2A%2A%2A"
        in combined
    )


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
async def test_wait_for_deck_terminal_state_clicks_start_button_once_when_idle(
    monkeypatch: pytest.MonkeyPatch,
):
    service = type(screenshot_service)()
    monkeypatch.setattr(service, "_DECK_IDLE_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(service, "_DECK_POLL_INTERVAL_SECONDS", 0.001)

    states = iter(
        [
            {"phase": "idle", "hasStartButton": True, "startButtonDisabled": False},
            {"phase": "running"},
            {"phase": "success"},
        ]
    )
    clicked = 0

    async def fake_read(_page):
        return next(states)

    async def fake_click(_page):
        nonlocal clicked
        clicked += 1
        return True

    monkeypatch.setattr(service, "_read_deck_page_state", fake_read)
    monkeypatch.setattr(service, "_click_deck_start_button", fake_click)

    attempt_meta: dict[str, str] = {}
    await service._wait_for_deck_terminal_state(object(), 100, attempt_meta)

    assert clicked == 1
    assert attempt_meta["page_state"] == "success"
    assert attempt_meta["fallback_clicked"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "detail"),
    [
        ("success", ""),
        ("empty", ""),
        (
            "error",
            "用户数据未找到，请确认用户ID/所选服务器是否正确，并已在 Haruki 上传数据。",
        ),
    ],
)
async def test_wait_for_deck_terminal_state_accepts_terminal_states(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    detail: str,
):
    service = type(screenshot_service)()

    async def fake_read(_page):
        return {"phase": phase, "detail": detail}

    monkeypatch.setattr(service, "_read_deck_page_state", fake_read)

    attempt_meta: dict[str, str] = {}
    await service._wait_for_deck_terminal_state(object(), 50, attempt_meta)

    assert attempt_meta["page_state"] == phase
    if detail:
        assert attempt_meta["page_state_detail"] == detail


@pytest.mark.asyncio
async def test_wait_for_deck_terminal_state_marks_stalled_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    service = type(screenshot_service)()
    monkeypatch.setattr(service, "_DECK_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(service, "_DECK_IDLE_GRACE_SECONDS", 999.0)

    async def fake_read(_page):
        return {"phase": "loading"}

    monkeypatch.setattr(service, "_read_deck_page_state", fake_read)

    attempt_meta: dict[str, str] = {}
    with pytest.raises(TimeoutError, match="loading"):
        await service._wait_for_deck_terminal_state(object(), 5, attempt_meta)

    assert attempt_meta["page_state"] == "stalled"


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
        kind="活动组卡",
        urls=["https://example.com/deck-recommend"],
        viewport={"width": 650, "height": 932},
        device_scale_factor=1.0,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.capture(job)
