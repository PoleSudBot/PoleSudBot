from __future__ import annotations

import base64
from pathlib import Path

from zhenxun.ui import render_template

from .models import ImageInput, ImageTagResult, SearchPresentation

TEMPLATE_PATH = Path(__file__).parent / "templates" / "result.html"
TAG_RESULT_TEMPLATE_PATH = Path(__file__).parent / "templates" / "tag_result.html"


def image_to_data_uri(image: ImageInput) -> str:
    """把用户图片 bytes 编码成模板可直接渲染的 data URI。"""

    mimetype = image.mimetype or "image/jpeg"
    encoded = base64.b64encode(image.content).decode("ascii")
    return f"data:{mimetype};base64,{encoded}"


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


async def render_tag_result(image: ImageInput, result: ImageTagResult) -> bytes:
    """使用项目 UI 渲染链路生成 tag 识别结果图。"""

    return await render_template(
        TAG_RESULT_TEMPLATE_PATH,
        {
            "image_data_uri": image_to_data_uri(image),
            "result": result,
        },
        is_page=True,
        viewport={"width": 860, "height": 10},
        clip_selector=".page",
        device_scale_factor=1,
    )
