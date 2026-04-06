from __future__ import annotations

import asyncio

import nonebot
import pytest

nonebot.init()

from zhenxun.services.renderer import engine as renderer_engine
from zhenxun.services.renderer.engine import PlaywrightEngine, _is_browser_closed_error


@pytest.mark.asyncio
async def test_initialize_disables_idle_recycle_by_default(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_get_config(_module: str, key: str, default=None):
        if key == "UI_RENDERER_PREWARM_ENABLED":
            return False
        return default

    async def fake_prewarm():
        return None

    async def fake_dispose():
        return None

    async def fake_shutdown():
        return None

    monkeypatch.setattr(renderer_engine.Config, "get_config", fake_get_config)
    monkeypatch.setattr(renderer_engine, "_shutdown_browser_instance", fake_shutdown)

    engine = PlaywrightEngine()
    monkeypatch.setattr(engine, "_prewarm_browser_and_pool", fake_prewarm)
    monkeypatch.setattr(engine, "_dispose_context_pool", fake_dispose)

    await engine.initialize()

    assert engine._idle_recycle_task is None

    await engine.close()
    assert engine._initialized is False


@pytest.mark.asyncio
async def test_initialize_creates_and_cancels_idle_recycle_task_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_get_config(_module: str, key: str, default=None):
        if key == "UI_RENDERER_IDLE_RECYCLE_ENABLED":
            return True
        if key == "UI_RENDERER_PREWARM_ENABLED":
            return False
        return default

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_idle_recycle_loop():
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_prewarm():
        return None

    async def fake_dispose():
        return None

    async def fake_shutdown():
        return None

    monkeypatch.setattr(renderer_engine.Config, "get_config", fake_get_config)
    monkeypatch.setattr(renderer_engine, "_shutdown_browser_instance", fake_shutdown)

    engine = PlaywrightEngine()
    monkeypatch.setattr(engine, "_idle_recycle_loop", fake_idle_recycle_loop)
    monkeypatch.setattr(engine, "_prewarm_browser_and_pool", fake_prewarm)
    monkeypatch.setattr(engine, "_dispose_context_pool", fake_dispose)

    await engine.initialize()
    await asyncio.wait_for(started.wait(), timeout=1)

    assert engine._idle_recycle_task is not None
    assert not engine._idle_recycle_task.done()

    await engine.close()

    assert cancelled.is_set()
    assert engine._idle_recycle_task is None
    assert engine._initialized is False


@pytest.mark.asyncio
async def test_render_html_retries_when_context_new_page_hits_closed_transport(
    monkeypatch: pytest.MonkeyPatch,
):
    engine = PlaywrightEngine()
    attempts = {"count": 0}
    recycle_calls: list[tuple[str, bool]] = []

    async def fake_render_with_context_pool(_html: str, _template_path: str, _render_options: dict):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError(
                "BrowserContext.new_page: unable to perform operation on "
                "<WriteUnixTransport closed=True reading=False 0x123>; the handler is closed"
            )
        return b"rendered-image"

    async def fake_recycle_browser(reason: str, *, prewarm_retry: bool = True):
        recycle_calls.append((reason, prewarm_retry))

    monkeypatch.setattr(engine, "_should_use_context_pool", lambda _options: True)
    monkeypatch.setattr(engine, "_render_with_context_pool", fake_render_with_context_pool)
    monkeypatch.setattr(engine, "_recycle_browser", fake_recycle_browser)

    result = await engine._render_html("<html></html>", "/", {})

    assert result == b"rendered-image"
    assert attempts["count"] == 2
    assert recycle_calls == [("recover", False)]


@pytest.mark.asyncio
async def test_render_html_does_not_retry_on_non_browser_error(
    monkeypatch: pytest.MonkeyPatch,
):
    engine = PlaywrightEngine()
    recycle_calls: list[tuple[str, bool]] = []

    async def fake_render_with_context_pool(_html: str, _template_path: str, _render_options: dict):
        raise ValueError("template data invalid")

    async def fake_recycle_browser(reason: str, *, prewarm_retry: bool = True):
        recycle_calls.append((reason, prewarm_retry))

    monkeypatch.setattr(engine, "_should_use_context_pool", lambda _options: True)
    monkeypatch.setattr(engine, "_render_with_context_pool", fake_render_with_context_pool)
    monkeypatch.setattr(engine, "_recycle_browser", fake_recycle_browser)

    with pytest.raises(ValueError, match="template data invalid"):
        await engine._render_html("<html></html>", "/", {})

    assert recycle_calls == []


def test_is_browser_closed_error_recognizes_closed_transport_handler_message():
    assert _is_browser_closed_error(
        RuntimeError(
            "BrowserContext.new_page: unable to perform operation on "
            "<WriteUnixTransport closed=True>; the handler is closed"
        )
    )
