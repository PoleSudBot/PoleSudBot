from __future__ import annotations

import asyncio
from importlib import import_module
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_scheduler_shutdown_cancels_polling_tasks(
    monkeypatch: pytest.MonkeyPatch,
):
    callbacks = {}

    class FakeDriver:
        def on_startup(self, func):
            callbacks["startup"] = func
            return func

        def on_shutdown(self, func):
            callbacks["shutdown"] = func
            return func

    fake_nonebot = ModuleType("nonebot")
    fake_nonebot.get_driver = lambda: FakeDriver()
    monkeypatch.setitem(sys.modules, "nonebot", fake_nonebot)
    sys.modules.pop("zhenxun.plugins.mc_server.scheduler", None)

    scheduler = import_module("zhenxun.plugins.mc_server.scheduler")
    scheduler._TASKS.clear()

    async def never_end():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(never_end())
    scheduler._TASKS.append(task)

    await callbacks["shutdown"]()

    assert task.cancelled()
    assert scheduler._TASKS == []

    # 还原成轻量 nonebot stub，避免影响同进程后续导入。
    fake_nonebot.get_driver = lambda: SimpleNamespace()
