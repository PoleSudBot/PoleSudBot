from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from zhenxun.utils.exception import RenderingError

from ..b30 import B30Result
from ..config import get_settings
from ..constants import server_label
from ..providers.suite import SuiteProfile

_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "best30" / "index.html"
)


class Best30RenderError(RuntimeError):
    """B30 内部模板渲染失败。"""


def _format_time(timestamp: int) -> str:
    if timestamp <= 0:
        return "-"
    if timestamp > 10_000_000_000:
        timestamp = int(timestamp / 1000)
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


async def render_best30_image(
    *,
    server: str,
    profile: SuiteProfile,
    result: B30Result,
    sources: list[str],
    warnings: list[str],
) -> bytes:
    payload: dict[str, Any] = {
        "server": server,
        "serverLabel": server_label(server),
        "profile": asdict(profile),
        "updatedText": _format_time(profile.upload_time),
        "averageText": f"{result.average:.2f}",
        "entries": [asdict(entry) for entry in result.entries],
        "candidateCount": result.candidate_count,
        "apCount": result.ap_count,
        "fcCount": result.fc_count,
        "missingConstantsCount": result.missing_constants_count,
        "totalResultCount": result.total_result_count,
        "sources": sources,
        "warnings": warnings,
    }
    try:
        from zhenxun import ui

        return await ui.render_template(
            _TEMPLATE_PATH,
            payload,
            use_cache=False,
            is_page=True,
            viewport={"width": get_settings().b30_viewport_width, "height": 10},
            wait=150,
            disable_animations=True,
        )
    except RenderingError:
        raise
    except Exception as exc:
        raise Best30RenderError("B30 内部模板渲染失败") from exc
