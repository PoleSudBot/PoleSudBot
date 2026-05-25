from __future__ import annotations

import asyncio

from nonebot import get_driver

from .services import mc_server_service

_driver = get_driver()
_TASKS: list[asyncio.Task[None]] = []


@_driver.on_startup
async def _start_mc_server_poll_loop() -> None:
    _TASKS.append(asyncio.create_task(mc_server_service.run_poll_loop()))


@_driver.on_shutdown
async def _stop_mc_server_poll_loop() -> None:
    pending_tasks = [task for task in _TASKS if not task.done()]
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    _TASKS.clear()
