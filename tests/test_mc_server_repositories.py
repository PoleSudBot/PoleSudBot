from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from tortoise import Tortoise


class _LoggerStub:
    def debug(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


services_module = ModuleType("zhenxun.services")
services_module.__path__ = [
    str(Path(__file__).resolve().parents[1] / "zhenxun" / "services")
]
sys.modules.setdefault("zhenxun.services", services_module)
sys.modules.setdefault("zhenxun.services.log", SimpleNamespace(logger=_LoggerStub()))


def _load_mc_server_modules(monkeypatch: pytest.MonkeyPatch):
    for name in [
        "zhenxun.plugins.mc_server.config",
        "zhenxun.plugins.mc_server.models",
        "zhenxun.plugins.mc_server.repositories",
    ]:
        sys.modules.pop(name, None)

    package = ModuleType("zhenxun.plugins.mc_server")
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "zhenxun" / "plugins" / "mc_server")
    ]
    monkeypatch.setitem(sys.modules, "zhenxun.plugins.mc_server", package)

    return (
        import_module("zhenxun.plugins.mc_server.models"),
        import_module("zhenxun.plugins.mc_server.repositories"),
    )


async def _init_db(tmp_path: Path) -> None:
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'mc_server.sqlite3'}",
        modules={"models": ["zhenxun.plugins.mc_server.models"]},
        timezone="Asia/Shanghai",
    )
    await Tortoise.generate_schemas()


async def _create_server(models):
    return await models.McServer.create(
        platform="qq",
        group_id="10001",
        host="127.0.0.1",
        port=25565,
    )


@pytest.mark.asyncio
async def test_end_session_returns_duration_snapshot_when_short_session_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    models, repositories = _load_mc_server_modules(monkeypatch)
    await _init_db(tmp_path)
    try:
        server = await _create_server(models)
        started_at = datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc)
        ended_at = started_at + timedelta(seconds=60)
        monkeypatch.setattr(
            repositories,
            "get_settings",
            lambda: SimpleNamespace(
                min_online_session_seconds=180,
                poll_interval_seconds=60,
                rejoin_suppress_seconds=30,
            ),
        )

        await repositories.start_session(server, "Steve", occurred_at=started_at)
        snapshot = await repositories.end_session(
            server,
            "Steve",
            occurred_at=ended_at,
        )

        assert snapshot is not None
        assert snapshot.started_at == started_at
        assert snapshot.ended_at == ended_at
        assert await models.McOnlineSession.all().count() == 0
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_end_session_reuses_recent_closed_session_from_status_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    models, repositories = _load_mc_server_modules(monkeypatch)
    await _init_db(tmp_path)
    try:
        server = await _create_server(models)
        started_at = datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc)
        observed_at = started_at + timedelta(minutes=10)
        leave_at = observed_at - timedelta(seconds=5)
        monkeypatch.setattr(
            repositories,
            "get_settings",
            lambda: SimpleNamespace(
                min_online_session_seconds=180,
                poll_interval_seconds=60,
                rejoin_suppress_seconds=30,
            ),
        )

        await repositories.start_session(server, "Steve", occurred_at=started_at)
        await repositories.close_active_sessions(server, occurred_at=observed_at)
        snapshot = await repositories.end_session(
            server,
            "Steve",
            occurred_at=leave_at,
        )

        assert snapshot is not None
        assert snapshot.started_at == started_at
        assert snapshot.ended_at == observed_at
        assert await models.McOnlineSession.all().count() == 1
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_end_session_ignores_old_closed_session_for_leave_notice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    models, repositories = _load_mc_server_modules(monkeypatch)
    await _init_db(tmp_path)
    try:
        server = await _create_server(models)
        started_at = datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc)
        old_end = started_at + timedelta(minutes=10)
        leave_at = old_end + timedelta(minutes=10)
        monkeypatch.setattr(
            repositories,
            "get_settings",
            lambda: SimpleNamespace(
                min_online_session_seconds=180,
                poll_interval_seconds=60,
                rejoin_suppress_seconds=30,
            ),
        )

        await repositories.start_session(server, "Steve", occurred_at=started_at)
        await repositories.close_active_sessions(server, occurred_at=old_end)
        snapshot = await repositories.end_session(
            server,
            "Steve",
            occurred_at=leave_at,
        )

        assert snapshot is None
        assert await models.McOnlineSession.all().count() == 1
    finally:
        await Tortoise.close_connections()
