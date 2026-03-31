from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

import zhenxun.ui as ui
from zhenxun.plugins.moesekai.adapters import reminder_renderer
from zhenxun.plugins.moesekai.adapters.viewmodels import ReminderCardViewModel
from zhenxun.utils.exception import RenderingError


class _FakeSettings:
    theme_primary = "#FF6699"
    theme_primary_dark = "#E64D80"
    theme_text_dark = "#333333"
    theme_text_muted = "#888888"
    theme_text_light = "#FFFFFF"
    theme_bg_main = "#FFFFFF"
    theme_bg_card = "#FFFFFF"
    theme_border_light = "#F0F0F0"


@pytest.mark.asyncio
async def test_render_reminder_card_uses_ui_template_when_render_succeeds(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_render_template(path, data, **kwargs):
        assert path == reminder_renderer._TEMPLATE_PATH
        assert data["title"] == "提醒标题"
        assert kwargs["is_page"] is True
        return b"ui-rendered"

    def fail_build_card_image(**_kwargs):
        raise AssertionError("UI 渲染成功时不应回退旧卡片样式")

    monkeypatch.setattr(reminder_renderer, "_TEST_MODE", False)
    monkeypatch.setattr("zhenxun.plugins.moesekai.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(ui, "render_template", fake_render_template)
    monkeypatch.setattr(reminder_renderer, "build_card_image", fail_build_card_image)

    result = await reminder_renderer.render_reminder_card(
        ReminderCardViewModel(
            title="提醒标题",
            subtitle="副标题",
            lines=["一行内容"],
            banner=None,
            accent="Live",
        )
    )

    assert result == b"ui-rendered"


@pytest.mark.asyncio
async def test_render_reminder_card_falls_back_only_on_rendering_error(
    monkeypatch: pytest.MonkeyPatch,
):
    warning_calls: list[tuple[tuple, dict]] = []

    async def fake_render_template(*_args, **_kwargs):
        raise RenderingError("render failed")

    def fake_build_card_image(**kwargs):
        assert kwargs["title"] == "提醒标题"
        assert kwargs["lines"] == ["副标题", "一行内容"]
        return b"fallback-card"

    monkeypatch.setattr(reminder_renderer, "_TEST_MODE", False)
    monkeypatch.setattr("zhenxun.plugins.moesekai.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(ui, "render_template", fake_render_template)
    monkeypatch.setattr(reminder_renderer, "build_card_image", fake_build_card_image)
    monkeypatch.setattr(
        reminder_renderer.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append((args, kwargs)),
    )

    result = await reminder_renderer.render_reminder_card(
        ReminderCardViewModel(
            title="提醒标题",
            subtitle="副标题",
            lines=["一行内容"],
            banner=None,
            accent="Live",
        )
    )

    assert result == b"fallback-card"
    assert len(warning_calls) == 1
