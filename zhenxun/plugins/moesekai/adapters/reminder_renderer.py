from __future__ import annotations

import base64
import os
from pathlib import Path
import sys

from zhenxun.utils.exception import RenderingError

from .results import build_card_image
from .runtime import logger
from .viewmodels import ReminderCardViewModel
from ..constants import MODULE_NAME

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "reminder_card.html"
_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def _to_data_uri(image_bytes: bytes | None) -> str | None:
    if not image_bytes:
        return None
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def render_reminder_card(
    view_model: ReminderCardViewModel,
    *,
    viewport_width: int = 760,
) -> bytes:
    from ..config import get_settings

    settings = get_settings()
    payload = {
        "title": view_model.title,
        "subtitle": view_model.subtitle,
        "lines": view_model.lines,
        "banner_data_uri": _to_data_uri(view_model.banner),
        "accent": view_model.accent,
        "theme_config": {
            "primary": settings.theme_primary,
            "primary_dark": settings.theme_primary_dark,
            "text_dark": settings.theme_text_dark,
            "text_muted": settings.theme_text_muted,
            "text_light": settings.theme_text_light,
            "bg_main": settings.theme_bg_main,
            "bg_card": settings.theme_bg_card,
            "border_light": settings.theme_border_light,
        }
    }
    fallback_lines = ([view_model.subtitle] if view_model.subtitle else []) + view_model.lines
    if _TEST_MODE:
        return build_card_image(
            title=view_model.title,
            lines=fallback_lines,
            banner=view_model.banner,
        )
    try:
        from zhenxun import ui

        return await ui.render_template(
            _TEMPLATE_PATH,
            payload,
            use_cache=False,
            is_page=True,
            viewport={"width": viewport_width, "height": 10},
        )
    except RenderingError as exc:
        logger.warning(
            "MoeSekai 提醒卡片渲染失败，回退旧卡片样式",
            MODULE_NAME,
            e=exc,
        )
        return build_card_image(
            title=view_model.title,
            lines=fallback_lines,
            banner=view_model.banner,
        )
