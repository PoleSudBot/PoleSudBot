from __future__ import annotations

import base64
from collections.abc import Mapping
import math
from typing import Any

import httpx

from zhenxun.utils.exception import AllURIsFailedError
from zhenxun.utils.http_utils import AsyncHttpx

from .config import PicSearchSettings
from .models import ImageInput, ImageTag, ImageTagResult


class ImageTagClientError(Exception):
    """图片 tag 服务的可展示错误。"""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


def image_to_data_url(image: ImageInput) -> str:
    """把用户图片 bytes 编码成 Gradio API 可接收的 data URL。"""

    mimetype = image.mimetype or "image/jpeg"
    encoded = base64.b64encode(image.content).decode("ascii")
    return f"data:{mimetype};base64,{encoded}"


def _http_status_from_exception(error: BaseException) -> int | None:
    """从 AsyncHttpx 的包装异常中提取 HTTP 状态码。"""

    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    if isinstance(error, AllURIsFailedError):
        for exc in reversed(error.exceptions):
            if status := _http_status_from_exception(exc):
                return status
    if cause := getattr(error, "__cause__", None):
        return _http_status_from_exception(cause)
    return None


def _request_error(error: BaseException) -> ImageTagClientError:
    """把常见鉴权、限流和服务错误转成明确提示。"""

    status = _http_status_from_exception(error)
    if status in (401, 403):
        return ImageTagClientError("图片 tag 服务鉴权失败，请检查 TAGGER_HF_TOKEN。")
    if status == 429:
        return ImageTagClientError("公共图片 tag 服务触发限流，请稍后再试。")
    if status is not None and status >= 500:
        return ImageTagClientError("公共图片 tag 服务暂时不可用，请稍后再试。")
    if status is not None:
        return ImageTagClientError(f"图片 tag 请求失败：HTTP {status}")
    return ImageTagClientError("图片 tag 请求失败，请稍后再试。")


def _parse_confidences(value: Any) -> list[ImageTag]:
    """从 Gradio Label 输出中提取置信度列表。"""

    if not isinstance(value, Mapping):
        return []
    raw_items = value.get("confidences")
    if not isinstance(raw_items, list):
        return []

    tags: list[ImageTag] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("label")
        raw_score = item.get("confidence")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score):
            continue
        tags.append(ImageTag(name=name.strip(), score=score))
    return sorted(tags, key=lambda tag: tag.score_ratio, reverse=True)


def _parse_tag_text(value: Any, *, threshold: float) -> list[ImageTag]:
    """从文本输出中兜底解析 tag 列表。"""

    if not isinstance(value, str):
        return []
    tags = []
    for raw_name in value.split(","):
        name = raw_name.strip()
        if name:
            tags.append(ImageTag(name=name, score=threshold))
    return tags


def parse_wd14_payload(
    payload: Any,
    *,
    threshold: float,
    model: str,
    result_limit: int,
) -> ImageTagResult:
    """把 WD14 Gradio 返回体解析成统一结果模型。"""

    if not isinstance(payload, Mapping):
        raise ImageTagClientError("图片 tag 服务返回格式异常。")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 3:
        raise ImageTagClientError("图片 tag 服务返回格式异常。")

    ratings = _parse_confidences(data[0])
    tags = _parse_confidences(data[2])
    if not tags:
        tags = _parse_tag_text(data[1], threshold=threshold)

    if not tags:
        raise ImageTagClientError("未识别到高于当前阈值的 tag。")

    result = ImageTagResult(
        tags=tags,
        ratings=ratings,
        threshold=threshold,
        model=model,
        total_count=len(tags),
    )
    return result.limited(result_limit)


def build_wd14_payload(image: ImageInput, settings: PicSearchSettings) -> dict:
    """构造 deepghs WD14 Gradio API 的请求体。"""

    return {
        "data": [
            image_to_data_url(image),
            settings.tagger_model,
            settings.tagger_confidence_threshold,
            False,
            True,
            False,
            True,
        ],
        "fn_index": 0,
    }


def build_request_headers(settings: PicSearchSettings) -> dict[str, str]:
    """根据配置构造请求头，支持可选 Hugging Face Token。"""

    headers: dict[str, str] = {}
    if settings.tagger_hf_token:
        headers["Authorization"] = f"Bearer {settings.tagger_hf_token}"
    return headers


class ImageTagClient:
    """图片 tag 识别 API 客户端。"""

    async def recognize(
        self,
        image: ImageInput,
        settings: PicSearchSettings,
    ) -> ImageTagResult:
        """调用远端 tagger 并返回规范化结果。"""

        try:
            payload = await AsyncHttpx.post_json(
                settings.tagger_api_url,
                json=build_wd14_payload(image, settings),
                headers=build_request_headers(settings),
                timeout=settings.tagger_request_timeout,
                raise_on_failure=True,
            )
        except AllURIsFailedError as e:
            raise _request_error(e) from e

        return parse_wd14_payload(
            payload,
            threshold=settings.tagger_confidence_threshold,
            model=settings.tagger_model,
            result_limit=settings.tagger_result_limit,
        )
