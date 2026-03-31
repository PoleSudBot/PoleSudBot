from __future__ import annotations

import nonebot
import pytest

nonebot.init()

from zhenxun.services.renderer.engine import PlaywrightEngine, _is_browser_closed_error


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
