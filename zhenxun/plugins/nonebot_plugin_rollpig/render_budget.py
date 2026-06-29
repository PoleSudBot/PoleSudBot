from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nonebot.log import logger

from .config import get_html_render_concurrency

_html_render_semaphore: asyncio.Semaphore | None = None
_html_render_limit: int | None = None
_html_render_lock = asyncio.Lock()


async def _get_html_render_semaphore() -> asyncio.Semaphore:
    """按当前配置懒加载渲染并发预算，配置变更后下一次请求自动换阀门。"""
    global _html_render_limit, _html_render_semaphore
    limit = get_html_render_concurrency()
    async with _html_render_lock:
        if _html_render_semaphore is None or _html_render_limit != limit:
            _html_render_semaphore = asyncio.Semaphore(limit)
            _html_render_limit = limit
            logger.debug(f"rollpig HTML 渲染并发预算已设置为 {limit}")
    return _html_render_semaphore


@asynccontextmanager
async def html_render_budget(_label: str) -> AsyncIterator[None]:
    """收束插件内 HTML 截图并发，避免多群同时触发时把 Chromium 打满。"""
    semaphore = await _get_html_render_semaphore()
    await semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()
