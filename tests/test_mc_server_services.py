from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


class _LoggerStub:
    def debug(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


# 只绕开 zhenxun.services.__init__ 的运行时插件加载，仍允许导入真实子模块。
services_module = ModuleType("zhenxun.services")
services_module.__path__ = [
    str(Path(__file__).resolve().parents[1] / "zhenxun" / "services")
]
sys.modules.setdefault("zhenxun.services", services_module)
sys.modules.setdefault("zhenxun.services.log", SimpleNamespace(logger=_LoggerStub()))

# 服务层测试只覆盖 toggle 行为，避免导入运行时平台/权限依赖时拉起完整 Bot。
config_module = ModuleType("zhenxun.plugins.mc_server.config")
config_module.get_settings = lambda: SimpleNamespace()
sys.modules.setdefault("zhenxun.plugins.mc_server.config", config_module)

models_module = ModuleType("zhenxun.plugins.mc_server.models")
models_module.McServer = object
sys.modules.setdefault("zhenxun.plugins.mc_server.models", models_module)

level_user_module = ModuleType("zhenxun.models.level_user")
level_user_module.LevelUser = SimpleNamespace(check_level=lambda *_args: False)
sys.modules.setdefault("zhenxun.models.level_user", level_user_module)

platform_module = ModuleType("zhenxun.utils.platform")
platform_module.PlatformUtils = SimpleNamespace(send_message=lambda *_args: None)
sys.modules.setdefault("zhenxun.utils.platform", platform_module)


async def _unused_async(*_args, **_kwargs):
    return None


repositories_module = ModuleType("zhenxun.plugins.mc_server.repositories")
for name in [
    "bind_server",
    "chat_digest_exists",
    "close_active_sessions",
    "end_session",
    "get_active_season",
    "get_count_samples",
    "get_or_init_cursor",
    "get_personal_online_data",
    "get_playtime_entries",
    "record_count_sample",
    "start_session",
    "update_cursor",
    "upsert_binding",
    "upsert_player",
]:
    setattr(repositories_module, name, _unused_async)
repositories_module.get_server_for_group = _unused_async
sys.modules.setdefault(
    "zhenxun.plugins.mc_server.repositories",
    repositories_module,
)

from zhenxun.plugins.mc_server import services as mc_services
from zhenxun.plugins.mc_server.services import McServerService, should_reset_log_cursor


class _FakeServer(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(
            join_notify_enabled=False,
            conn_notify_enabled=True,
            chat_bridge_enabled=False,
            saved_fields=None,
        )

    async def save(self, *, update_fields: list[str]) -> None:
        self.saved_fields = update_fields


class _FakeCursor(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_toggle_all_updates_three_switches(monkeypatch: pytest.MonkeyPatch):
    server = _FakeServer()

    async def fake_get_server_for_group(_group_id: str):
        return server

    monkeypatch.setattr(mc_services, "get_server_for_group", fake_get_server_for_group)

    message = await McServerService().toggle("123456", "all", True)

    assert message == "已开启全部MC播报/互通开关。"
    assert server.join_notify_enabled is True
    assert server.conn_notify_enabled is True
    assert server.chat_bridge_enabled is True
    assert server.saved_fields == [
        "join_notify_enabled",
        "conn_notify_enabled",
        "chat_bridge_enabled",
        "updated_at",
    ]


def test_should_reset_log_cursor_on_inode_change_or_truncate():
    assert should_reset_log_cursor(
        cursor_inode="old",
        current_inode="new",
        cursor_offset=100,
        current_size=1000,
    )
    assert should_reset_log_cursor(
        cursor_inode="same",
        current_inode="same",
        cursor_offset=100,
        current_size=20,
    )
    assert not should_reset_log_cursor(
        cursor_inode="same",
        current_inode="same",
        cursor_offset=100,
        current_size=1000,
    )
    assert not should_reset_log_cursor(
        cursor_inode="",
        current_inode="new",
        cursor_offset=100,
        current_size=1000,
    )


@pytest.mark.asyncio
async def test_process_log_starts_new_cursor_at_file_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    log_path = tmp_path / "latest.log"
    old_line = "[20:01:02] [Server thread/INFO]: Steve joined the game\n"
    log_path.write_text(old_line, encoding="utf-8")
    cursor = _FakeCursor(offset=log_path.stat().st_size, inode="")
    handled_events = []
    saved_cursor = {}
    server = SimpleNamespace(log_path=str(log_path))

    async def fake_get_or_init_cursor(*_args, **_kwargs):
        return cursor

    async def fake_update_cursor(_cursor, *, offset: int, inode: str = ""):
        saved_cursor.update(offset=offset, inode=inode)

    monkeypatch.setattr(mc_services, "get_or_init_cursor", fake_get_or_init_cursor)
    monkeypatch.setattr(mc_services, "update_cursor", fake_update_cursor)
    monkeypatch.setattr(mc_services, "chat_digest_exists", _unused_async)

    service = McServerService()

    async def fake_handle_log_event(_server, event):
        handled_events.append(event)

    monkeypatch.setattr(service, "_handle_log_event", fake_handle_log_event)

    await service.process_log(server)

    assert handled_events == []
    assert saved_cursor["offset"] == log_path.stat().st_size
    assert saved_cursor["inode"] == str(log_path.stat().st_ino)


@pytest.mark.asyncio
async def test_process_log_reads_from_start_after_inode_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    log_path = tmp_path / "latest.log"
    line = "[20:01:02] [Server thread/INFO]: Steve joined the game\n"
    log_path.write_text(line, encoding="utf-8")
    cursor = _FakeCursor(offset=9999, inode="old-inode")
    handled_events = []
    server = SimpleNamespace(log_path=str(log_path))

    async def fake_get_or_init_cursor(*_args, **_kwargs):
        return cursor

    async def fake_update_cursor(_cursor, *, offset: int, inode: str = ""):
        cursor.offset = offset
        cursor.inode = inode

    async def fake_chat_digest_exists(*_args, **_kwargs):
        return False

    monkeypatch.setattr(mc_services, "get_or_init_cursor", fake_get_or_init_cursor)
    monkeypatch.setattr(mc_services, "update_cursor", fake_update_cursor)
    monkeypatch.setattr(mc_services, "chat_digest_exists", fake_chat_digest_exists)

    service = McServerService()

    async def fake_handle_log_event(_server, event):
        handled_events.append(event)

    monkeypatch.setattr(service, "_handle_log_event", fake_handle_log_event)

    await service.process_log(server)

    assert cursor.offset == log_path.stat().st_size
    assert cursor.inode == str(log_path.stat().st_ino)
    assert [event.player_name for event in handled_events] == ["Steve"]
