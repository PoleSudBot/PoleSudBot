from __future__ import annotations

from pathlib import Path

from zhenxun.ui import render_template

from .models import SearchPresentation

TEMPLATE_PATH = Path(__file__).parent / "templates" / "result.html"


async def render_search_result(presentation: SearchPresentation) -> bytes:
    """使用项目 UI 渲染链路生成结果汇总图。"""

    # 直接传 dataclass 给 Jinja，避免 dict 的 items 方法覆盖 section.items 字段。
    return await render_template(
        TEMPLATE_PATH,
        {"result": presentation},
        is_page=True,
        # 截图按内容容器裁剪，避免少量结果时保留固定 1200px 的大块空白。
        viewport={"width": 900, "height": 10},
        clip_selector=".page",
        device_scale_factor=1,
    )
