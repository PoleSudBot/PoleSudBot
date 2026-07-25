import asyncio
from dataclasses import dataclass
from html import unescape
import json
from pathlib import Path
import re
import time
from typing import Any, ClassVar

from httpx import AsyncClient, Response

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx


class PaddleOCRAPIError(RuntimeError):
    """PaddleOCR API 返回了无法继续处理的结果。"""


@dataclass(slots=True)
class PaddleOCRAPISettings:
    token: str
    job_url: str
    model: str
    poll_interval_seconds: float
    timeout_seconds: float


class PaddleOCRAPIClient:
    """PaddleOCR 官方异步任务 API 客户端。"""

    _REQUEST_TIMEOUT_SECONDS = 30.0
    _OPTIONAL_PAYLOAD: ClassVar[dict[str, bool]] = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }
    _MARKDOWN_IMAGE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"!\[[^\]]*\]\([^)]*\)"
    )
    _HTML_TAG_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"<[^>]+>")

    def __init__(self, settings: PaddleOCRAPISettings):
        self.settings = settings
        self._monotonic = time.monotonic

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.token)

    async def recognize(self, image_path: str | Path) -> str:
        if not self.is_configured:
            raise PaddleOCRAPIError("未配置 PADDLEOCR_API_TOKEN")

        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"PaddleOCR API 待识别图片不存在: {path}")

        async with AsyncHttpx.temporary_client(
            follow_redirects=True,
            timeout=self._REQUEST_TIMEOUT_SECONDS,
        ) as client:
            job_id = await self._submit_job(client, path)
            result_url = await self._wait_for_result(client, job_id)
            text = await self._download_result(client, result_url)

        logger.info(
            f"PaddleOCR API 识别完成，文本长度: {len(text)}",
            "群聊语录-PaddleOCR API",
        )
        return text

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self.settings.token}"}

    async def _submit_job(self, client: AsyncClient, image_path: Path) -> str:
        image_data = await asyncio.to_thread(image_path.read_bytes)
        response = await AsyncHttpx.post(
            self.settings.job_url,
            client=client,
            headers=self._headers,
            data={
                "model": self.settings.model,
                "optionalPayload": json.dumps(self._OPTIONAL_PAYLOAD),
            },
            files={"file": (image_path.name, image_data, "image/png")},
        )
        data = self._response_data(response, "提交任务")
        job_id = data.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise PaddleOCRAPIError("PaddleOCR API 提交响应缺少 jobId")

        logger.debug(
            f"PaddleOCR API 任务已提交: {job_id}",
            "群聊语录-PaddleOCR API",
        )
        return job_id

    async def _wait_for_result(self, client: AsyncClient, job_id: str) -> str:
        deadline = self._monotonic() + self.settings.timeout_seconds
        job_url = f"{self.settings.job_url.rstrip('/')}/{job_id}"

        while self._monotonic() < deadline:
            response = await AsyncHttpx.get(
                job_url,
                client=client,
                headers=self._headers,
            )
            data = self._response_data(response, "查询任务")
            state = data.get("state")

            if state == "done":
                result_url = data.get("resultUrl")
                json_url = (
                    result_url.get("jsonUrl") if isinstance(result_url, dict) else None
                )
                if not isinstance(json_url, str) or not json_url:
                    raise PaddleOCRAPIError(
                        "PaddleOCR API 完成响应缺少 resultUrl.jsonUrl"
                    )
                return json_url

            if state == "failed":
                error_message = data.get("errorMsg") or "未知原因"
                raise PaddleOCRAPIError(f"PaddleOCR API 任务失败: {error_message}")

            if state not in {"pending", "running"}:
                raise PaddleOCRAPIError(f"PaddleOCR API 返回未知任务状态: {state}")

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self.settings.poll_interval_seconds, remaining))

        raise TimeoutError(
            f"PaddleOCR API 任务在 {self.settings.timeout_seconds:g} 秒内未完成"
        )

    async def _download_result(self, client: AsyncClient, result_url: str) -> str:
        # 结果 URL 携带短期签名参数，避免通用 GET 封装把完整地址写入日志。
        try:
            response = await client.get(result_url)
        except Exception:
            raise PaddleOCRAPIError("PaddleOCR API 结果下载请求失败") from None
        if response.is_error:
            raise PaddleOCRAPIError(
                f"PaddleOCR API 结果下载失败: HTTP {response.status_code}"
            )
        text_parts: list[str] = []
        for line_number, line in enumerate(response.text.splitlines(), 1):
            if not (line := line.strip()):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as e:
                raise PaddleOCRAPIError(
                    f"PaddleOCR API JSONL 第 {line_number} 行解析失败"
                ) from e

            result = payload.get("result")
            if not isinstance(result, dict):
                raise PaddleOCRAPIError(
                    f"PaddleOCR API JSONL 第 {line_number} 行缺少 result"
                )
            layouts = result.get("layoutParsingResults") or []
            if not isinstance(layouts, list):
                raise PaddleOCRAPIError(
                    f"PaddleOCR API JSONL 第 {line_number} 行结果格式错误"
                )
            for layout in layouts:
                if not isinstance(layout, dict):
                    continue
                markdown = layout.get("markdown")
                markdown_text = (
                    markdown.get("text") if isinstance(markdown, dict) else None
                )
                if isinstance(markdown_text, str) and (
                    text := self._clean_markdown_text(markdown_text)
                ):
                    text_parts.append(text)

        return "\n".join(text_parts)

    @classmethod
    def _clean_markdown_text(cls, markdown_text: str) -> str:
        text = cls._MARKDOWN_IMAGE_PATTERN.sub("", markdown_text)
        text = cls._HTML_TAG_PATTERN.sub("", text)
        text = unescape(text)
        lines = [" ".join(line.split()) for line in text.splitlines()]
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _response_data(response: Response, action: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except json.JSONDecodeError as e:
            raise PaddleOCRAPIError(f"PaddleOCR API {action}响应不是有效 JSON") from e

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise PaddleOCRAPIError(f"PaddleOCR API {action}响应缺少 data")
        return data
