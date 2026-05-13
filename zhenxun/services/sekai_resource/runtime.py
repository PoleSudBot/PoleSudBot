from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import httpx

_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def _load_logger():
    # 服务层只复用日志与 HTTP 适配，避免引入消息构建或平台发送上下文。
    if _TEST_MODE:
        from nonebot.log import logger as fallback_logger

        return fallback_logger
    try:
        from zhenxun.services.log import logger as zhenxun_logger

        return zhenxun_logger
    except Exception:
        from nonebot.log import logger as fallback_logger

        return fallback_logger


logger = _load_logger()


class AsyncHttpxAdapter:
    @staticmethod
    async def get(
        url: str | list[str],
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
        accept_status_codes: tuple[int, ...] = (),
    ):
        urls = [url] if isinstance(url, str) else list(url)
        if not urls:
            raise ValueError("url 不能为空")
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout or 20,
        ) as client:
            for item in urls:
                try:
                    response = await client.get(item, headers=headers)
                    if response.status_code not in accept_status_codes:
                        response.raise_for_status()
                    return response
                except Exception as exc:
                    last_error = exc
                    continue
        if last_error:
            raise last_error
        raise ValueError("url 不能为空")

    @staticmethod
    async def get_json(
        url: str,
        *,
        raise_on_failure: bool = True,
        default: Any = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout or 20,
        ) as client:
            response = await client.get(url, headers=headers)
            if raise_on_failure:
                response.raise_for_status()
            elif response.is_error:
                return default
            return response.json()

    @staticmethod
    async def get_content(
        url: str | list[str],
        *,
        raise_on_failure: bool = True,
        timeout: float | None = None,
    ) -> bytes | None:
        urls = [url] if isinstance(url, str) else list(url)
        if not urls:
            return None
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout or 20,
        ) as client:
            for item in urls:
                try:
                    response = await client.get(item)
                    if raise_on_failure:
                        response.raise_for_status()
                    elif response.is_error:
                        continue
                    return response.content
                except Exception as exc:
                    last_error = exc
                    continue
        if raise_on_failure and last_error:
            raise last_error
        return None


def _load_async_httpx():
    if _TEST_MODE:
        return AsyncHttpxAdapter
    try:
        from zhenxun.utils.http_utils import AsyncHttpx as zhenxun_async_httpx

        return zhenxun_async_httpx
    except Exception:
        return AsyncHttpxAdapter


AsyncHttpx = _load_async_httpx()


def get_cache_runtime_config():
    try:
        from zhenxun.configs.config import BotConfig

        return BotConfig
    except Exception:
        return SimpleNamespace(
            redis_host=None,
            redis_port=None,
            redis_password=None,
        )
