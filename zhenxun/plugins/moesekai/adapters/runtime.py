from __future__ import annotations

from io import BytesIO
import os
from types import SimpleNamespace
import sys
from typing import Any

import httpx
from nonebot.log import logger as fallback_logger
from nonebot_plugin_alconna import CustomNode, Image, Reference, Text, UniMessage

_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def _load_logger():
    if _TEST_MODE:
        return fallback_logger
    try:
        from zhenxun.services.log import logger as zhenxun_logger

        return zhenxun_logger
    except Exception:
        return fallback_logger


logger = _load_logger()


class MessageUtilsAdapter:
    @staticmethod
    def build_message(
        message: str | bytes | UniMessage | list[str | bytes | UniMessage],
    ) -> UniMessage:
        if isinstance(message, UniMessage):
            return message
        if isinstance(message, list):
            built = UniMessage()
            for item in message:
                built += MessageUtilsAdapter.build_message(item)
            return built
        if isinstance(message, bytes):
            return UniMessage([Image(raw=BytesIO(message))])
        return UniMessage([Text(str(message))])

    @staticmethod
    def alc_forward_msg(
        messages: list[str | bytes | UniMessage],
        sender_id: str,
        sender_name: str,
    ) -> UniMessage:
        nodes: list[CustomNode] = []
        for message in messages:
            nodes.append(
                CustomNode(
                    uid=sender_id,
                    name=sender_name,
                    content=MessageUtilsAdapter.build_message(message),
                )
            )
        return UniMessage(Reference(nodes=nodes))


def _load_message_utils():
    if _TEST_MODE:
        return MessageUtilsAdapter
    try:
        from zhenxun.utils.message import MessageUtils as zhenxun_message_utils

        return zhenxun_message_utils
    except Exception:
        return MessageUtilsAdapter


MessageUtils = _load_message_utils()


class AsyncHttpxAdapter:
    @staticmethod
    async def get(
        url: str | list[str],
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ):
        urls = [url] if isinstance(url, str) else list(url)
        if not urls:
            raise ValueError("url 不能为空")
        last_error: Exception | None = None
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout or 20) as client:
            for item in urls:
                try:
                    response = await client.get(item, headers=headers)
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
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout or 20) as client:
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
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout or 20) as client:
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


class PlatformUtilsAdapter:
    @staticmethod
    def get_platform(session: Any) -> str:
        if getattr(session, "platform", None):
            return str(session.platform)
        if getattr(session, "scope", None):
            return str(session.scope)
        basic = getattr(session, "basic", None)
        if isinstance(basic, dict) and basic.get("scope"):
            return str(basic["scope"])
        adapter = getattr(session, "adapter", "")
        adapter_text = str(adapter).lower()
        if "qq" in adapter_text or "onebot" in adapter_text:
            return "qq"
        return "unknown"

    @staticmethod
    async def send_message(*_args, **_kwargs):
        raise RuntimeError("PlatformUtils.send_message 在当前环境不可用")

    @staticmethod
    async def send_superuser(*_args, **_kwargs):
        raise RuntimeError("PlatformUtils.send_superuser 在当前环境不可用")


def _load_platform_utils():
    if _TEST_MODE:
        return PlatformUtilsAdapter
    try:
        from zhenxun.utils.platform import PlatformUtils as zhenxun_platform_utils

        return zhenxun_platform_utils
    except Exception:
        return PlatformUtilsAdapter


PlatformUtils = _load_platform_utils()


def get_cache_runtime_config() -> Any:
    if _TEST_MODE:
        return SimpleNamespace(
            redis_host="",
            redis_port=0,
            redis_password="",
        )
    try:
        from zhenxun.services.cache import cache_config as zhenxun_cache_config

        return zhenxun_cache_config
    except Exception:
        return SimpleNamespace(
            redis_host="",
            redis_port=0,
            redis_password="",
        )


__all__ = [
    "AsyncHttpx",
    "MessageUtils",
    "PlatformUtils",
    "get_cache_runtime_config",
    "logger",
]
