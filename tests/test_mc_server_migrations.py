from __future__ import annotations

from datetime import datetime, timezone
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
        "zhenxun.plugins.mc_server.models",
        "zhenxun.plugins.mc_server.repositories",
        "zhenxun.plugins.mc_server.migrations",
    ]:
        sys.modules.pop(name, None)

    package = ModuleType("zhenxun.plugins.mc_server")
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "zhenxun" / "plugins" / "mc_server")
    ]
    monkeypatch.setitem(sys.modules, "zhenxun.plugins.mc_server", package)

    return (
        import_module("zhenxun.plugins.mc_server.models"),
        import_module("zhenxun.plugins.mc_server.migrations"),
    )


@pytest.mark.asyncio
async def test_migrate_group_shared_servers_merges_manual_same_address(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    models, migrations = _load_mc_server_modules(monkeypatch)

    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'mc_server.sqlite3'}",
        modules={"models": ["zhenxun.plugins.mc_server.models"]},
        timezone="Asia/Shanghai",
    )
    await Tortoise.generate_schemas()
    try:
        first_server = await models.McServer.create(
            platform="qq",
            group_id="10001",
            host="127.0.0.1",
            port=25565,
            join_notify_enabled=True,
            conn_notify_enabled=False,
            chat_bridge_enabled=True,
        )
        second_server = await models.McServer.create(
            platform="qq",
            group_id="10002",
            host="127.0.0.1",
            port=25565,
            join_notify_enabled=False,
            conn_notify_enabled=True,
            chat_bridge_enabled=False,
        )
        first_season = await models.McSeason.create(
            server=first_server,
            name="旧周目A",
            started_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            active=True,
        )
        second_season = await models.McSeason.create(
            server=second_server,
            name="旧周目B",
            started_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
            active=True,
        )
        first_player = await models.McPlayer.create(
            server=first_server,
            name="Steve",
        )
        second_player = await models.McPlayer.create(
            server=second_server,
            name="Steve",
        )
        await models.McOnlineSession.create(
            server=first_server,
            season=first_season,
            player=first_player,
            player_name="Steve",
            started_at=datetime(2026, 5, 16, 10, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 16, 11, 0, tzinfo=timezone.utc),
        )
        await models.McOnlineSession.create(
            server=second_server,
            season=second_season,
            player=second_player,
            player_name="Steve",
            started_at=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
        )
        await models.McPlayerCountSample.create(
            server=second_server,
            season=second_season,
            online_count=3,
            max_players=20,
            captured_at=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
        )
        await models.McLogCursor.create(
            server=first_server,
            path="/srv/mc/logs/latest.log",
            offset=10,
        )
        await models.McLogCursor.create(
            server=second_server,
            path="/srv/mc/logs/latest.log",
            offset=99,
        )

        await migrations.migrate_group_shared_servers()

        servers = await models.McServer.all()
        assert len(servers) == 1
        canonical = servers[0]
        assert canonical.identity_key == "addr:127.0.0.1:25565"

        bindings = await models.McServerGroupBinding.all().order_by("group_id")
        assert [(item.group_id, item.server_id) for item in bindings] == [
            ("10001", canonical.id),
            ("10002", canonical.id),
        ]
        assert bindings[0].join_notify_enabled is True
        assert bindings[0].conn_notify_enabled is False
        assert bindings[0].chat_bridge_enabled is True
        assert bindings[1].join_notify_enabled is False
        assert bindings[1].conn_notify_enabled is True
        assert bindings[1].chat_bridge_enabled is False

        sessions = await models.McOnlineSession.all()
        assert len(sessions) == 2
        assert {session.server_id for session in sessions} == {canonical.id}
        assert await models.McPlayer.filter(server=canonical, name="Steve").count() == 1
        assert await models.McPlayerCountSample.filter(server=canonical).count() == 1

        cursor = await models.McLogCursor.get(server=canonical)
        assert cursor.path == "/srv/mc/logs/latest.log"
        assert cursor.offset == 99

        await migrations.migrate_group_shared_servers()
        assert await models.McServer.all().count() == 1
        assert await models.McServerGroupBinding.all().count() == 2
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_migrate_group_shared_servers_keeps_longest_overlapping_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    models, migrations = _load_mc_server_modules(monkeypatch)

    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'mc_server.sqlite3'}",
        modules={"models": ["zhenxun.plugins.mc_server.models"]},
        timezone="Asia/Shanghai",
    )
    await Tortoise.generate_schemas()
    try:
        first_server = await models.McServer.create(
            platform="qq",
            group_id="10001",
            host="127.0.0.1",
            port=25565,
        )
        second_server = await models.McServer.create(
            platform="qq",
            group_id="10002",
            host="127.0.0.1",
            port=25565,
        )
        first_season = await models.McSeason.create(
            server=first_server,
            name="旧周目A",
            started_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            active=True,
        )
        second_season = await models.McSeason.create(
            server=second_server,
            name="旧周目B",
            started_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            active=True,
        )
        first_player = await models.McPlayer.create(
            server=first_server,
            name="Steve",
        )
        second_player = await models.McPlayer.create(
            server=second_server,
            name="Steve",
        )
        await models.McOnlineSession.create(
            server=first_server,
            season=first_season,
            player=first_player,
            player_name="Steve",
            started_at=datetime(2026, 5, 16, 10, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 16, 11, 0, tzinfo=timezone.utc),
        )
        await models.McOnlineSession.create(
            server=first_server,
            season=first_season,
            player=first_player,
            player_name="Steve",
            started_at=datetime(2026, 5, 16, 15, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 16, 16, 0, tzinfo=timezone.utc),
        )
        await models.McOnlineSession.create(
            server=second_server,
            season=second_season,
            player=second_player,
            player_name="Steve",
            started_at=datetime(2026, 5, 16, 9, 30, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 16, 12, 30, tzinfo=timezone.utc),
        )

        await migrations.migrate_group_shared_servers()

        canonical = await models.McServer.get()
        sessions = await (
            models.McOnlineSession.filter(server=canonical)
            .order_by("started_at")
            .all()
        )
        # 重叠旧段来自同一真实在线记录，只保留覆盖时间最长的一段，避免 mct 迁移后叠加。
        assert [
            (session.started_at, session.ended_at)
            for session in sessions
        ] == [
            (
                datetime(2026, 5, 16, 9, 30, tzinfo=timezone.utc),
                datetime(2026, 5, 16, 12, 30, tzinfo=timezone.utc),
            ),
            (
                datetime(2026, 5, 16, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 16, 16, 0, tzinfo=timezone.utc),
            ),
        ]
    finally:
        await Tortoise.close_connections()
