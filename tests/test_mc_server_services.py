from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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
config_module.McServerPreset = SimpleNamespace
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


async def _empty_async_list(*_args, **_kwargs):
    return []


repositories_module = ModuleType("zhenxun.plugins.mc_server.repositories")
for name in [
    "bind_server",
    "chat_digest_exists",
    "close_active_sessions",
    "end_session",
    "get_active_season",
    "get_count_samples",
    "get_group_binding",
    "get_or_init_cursor",
    "get_personal_online_data",
    "get_personal_online_data_by_player_name",
    "get_playtime_entries",
    "list_poll_servers",
    "list_server_group_bindings",
    "record_count_sample",
    "require_group_binding",
    "start_session",
    "update_cursor",
    "upsert_binding",
    "upsert_player",
]:
    setattr(repositories_module, name, _unused_async)
repositories_module.get_server_for_group = _unused_async
repositories_module.list_poll_servers = _empty_async_list
repositories_module.list_server_group_bindings = _empty_async_list
sys.modules.setdefault(
    "zhenxun.plugins.mc_server.repositories",
    repositories_module,
)

from zhenxun.plugins.mc_server import services as mc_services
from zhenxun.plugins.mc_server.services import McServerService, should_reset_log_cursor
from zhenxun.plugins.mc_server.types import TimeRange


class _FakeServer(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(
            join_notify_enabled=False,
            conn_notify_enabled=True,
            chat_bridge_enabled=False,
            rcon_host="",
            rcon_port=25575,
            rcon_password="",
            log_path="",
            bluemap_base_url="",
            bluemap_map_ids=[],
            saved_fields=None,
        )

    async def save(self, *, update_fields: list[str]) -> None:
        self.saved_fields = update_fields


class _FakeBinding(SimpleNamespace):
    def __init__(self, server: _FakeServer | None = None) -> None:
        super().__init__(
            server=server or _FakeServer(),
            group_id="123456",
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
    binding = _FakeBinding()

    async def fake_require_group_binding(_group_id: str):
        return binding

    monkeypatch.setattr(
        mc_services,
        "require_group_binding",
        fake_require_group_binding,
    )

    message = await McServerService().toggle("123456", "all", True)

    assert message == "已开启全部MC播报/互通开关。"
    assert binding.join_notify_enabled is True
    assert binding.conn_notify_enabled is True
    assert binding.chat_bridge_enabled is True
    assert binding.saved_fields == [
        "join_notify_enabled",
        "conn_notify_enabled",
        "chat_bridge_enabled",
        "updated_at",
    ]


@pytest.mark.asyncio
async def test_bind_group_server_preset_copies_config_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    server = _FakeServer()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    latest_log = log_dir / "latest.log"
    latest_log.write_text("[Server thread/INFO]: Done\n", encoding="utf-8")
    preset = SimpleNamespace(
        name="1",
        host="mc.example.com",
        port=25566,
        rcon_host="",
        rcon_port=25576,
        rcon_password="secret",
        log_path=str(log_dir),
        bluemap_base_url="map.example.com",
        bluemap_map_ids=["world", "nether"],
    )

    async def fake_bind_server(
        group_id: str,
        *,
        host: str,
        port: int,
        preset_name: str = "",
    ):
        server.group_id = group_id
        server.host = host
        server.port = port
        server.preset_name = preset_name
        return server

    async def fake_probe_server_status(*_args, **_kwargs):
        return SimpleNamespace(online=True)

    async def fake_render_status(_status):
        return mc_services.RenderedMessage(image=None, fallback_text="status ok")

    monkeypatch.setattr(
        mc_services,
        "get_settings",
        lambda: SimpleNamespace(
            request_timeout_seconds=8,
            server_presets={"1": preset},
        ),
    )
    monkeypatch.setattr(mc_services, "bind_server", fake_bind_server)
    monkeypatch.setattr(mc_services, "probe_server_status", fake_probe_server_status)
    monkeypatch.setattr(mc_services, "render_status", fake_render_status)

    rendered = await McServerService().bind_group_server_preset("123456", "1")

    assert rendered.lead_text.startswith("已绑定预设 1：mc.example.com:25566")
    assert server.host == "mc.example.com"
    assert server.port == 25566
    assert server.rcon_host == "mc.example.com"
    assert server.rcon_port == 25576
    assert server.rcon_password == "secret"
    assert server.log_path == str(latest_log)
    assert server.bluemap_base_url == "http://map.example.com"
    assert server.bluemap_map_ids == ["world", "nether"]
    assert server.saved_fields == [
        "rcon_host",
        "rcon_port",
        "rcon_password",
        "log_path",
        "bluemap_base_url",
        "bluemap_map_ids",
        "updated_at",
    ]


@pytest.mark.asyncio
async def test_bind_group_server_with_rcon_uses_same_host_for_rcon(
    monkeypatch: pytest.MonkeyPatch,
):
    server = _FakeServer()

    async def fake_bind_server(
        _group_id: str,
        *,
        host: str,
        port: int,
        preset_name: str = "",
    ):
        _ = preset_name
        server.host = host
        server.port = port
        return server

    async def fake_probe_server_status(*_args, **_kwargs):
        return SimpleNamespace(online=True)

    async def fake_render_status(_status):
        return mc_services.RenderedMessage(image=None, fallback_text="status ok")

    monkeypatch.setattr(
        mc_services,
        "get_settings",
        lambda: SimpleNamespace(request_timeout_seconds=8),
    )
    monkeypatch.setattr(mc_services, "bind_server", fake_bind_server)
    monkeypatch.setattr(mc_services, "probe_server_status", fake_probe_server_status)
    monkeypatch.setattr(mc_services, "render_status", fake_render_status)

    rendered = await McServerService().bind_group_server_with_rcon(
        "123456",
        host="mc.example.com",
        server_port=25566,
        rcon_port=25576,
    )

    assert "已设置RCON地址：mc.example.com:25576" in rendered.lead_text
    assert server.host == "mc.example.com"
    assert server.port == 25566
    assert server.rcon_host == "mc.example.com"
    assert server.rcon_port == 25576
    assert server.saved_fields == ["rcon_host", "rcon_port", "updated_at"]


@pytest.mark.asyncio
async def test_chart_message_defaults_to_business_today(
    monkeypatch: pytest.MonkeyPatch,
):
    server = _FakeServer()
    server.name = "默认服务器"
    captured = {}

    async def fake_get_server_for_group(_group_id: str):
        return server

    async def fake_get_active_season(_server):
        return SimpleNamespace(started_at=datetime(2026, 5, 1, 12, 0, 0))

    async def fake_get_count_samples(_server, time_range):
        captured["time_range"] = time_range
        return []

    async def fake_render_chart(data):
        captured["chart"] = data
        return mc_services.RenderedMessage(image=None, fallback_text="chart")

    def fake_parse_time_range(range_text, season_start):
        captured["range_text"] = range_text
        captured["season_start"] = season_start
        return TimeRange(
            "今日",
            datetime(2026, 5, 16, 6, 0, 0),
            datetime(2026, 5, 16, 12, 0, 0),
        )

    monkeypatch.setattr(mc_services, "get_server_for_group", fake_get_server_for_group)
    monkeypatch.setattr(mc_services, "get_active_season", fake_get_active_season)
    monkeypatch.setattr(mc_services, "get_count_samples", fake_get_count_samples)
    monkeypatch.setattr(mc_services, "render_chart", fake_render_chart)
    monkeypatch.setattr(mc_services, "parse_time_range", fake_parse_time_range)

    rendered = await McServerService().chart_message("123456")

    assert rendered.fallback_text == "chart"
    assert captured["range_text"] == "今日"
    assert captured["time_range"].label == "今日"
    assert captured["chart"].title == "MC 服务器 在线人数变化"


@pytest.mark.asyncio
async def test_personal_online_messages_default_to_week(
    monkeypatch: pytest.MonkeyPatch,
):
    server = _FakeServer()
    server.name = "主服"
    captured = {}

    async def fake_get_server_for_group(_group_id: str):
        return server

    async def fake_get_active_season(_server):
        return SimpleNamespace(started_at=datetime(2026, 5, 1, 12, 0, 0))

    async def fake_get_personal_online_data(_server, time_range, *, qq_id, title):
        captured["qq"] = (time_range.label, qq_id, title)
        return SimpleNamespace()

    async def fake_get_personal_online_data_by_player_name(
        _server,
        time_range,
        *,
        player_name,
        title,
    ):
        captured["player"] = (time_range.label, player_name, title)
        return SimpleNamespace()

    async def fake_render_personal_online(_data):
        return mc_services.RenderedMessage(image=None, fallback_text="personal")

    monkeypatch.setattr(mc_services, "get_server_for_group", fake_get_server_for_group)
    monkeypatch.setattr(mc_services, "get_active_season", fake_get_active_season)
    monkeypatch.setattr(
        mc_services,
        "get_personal_online_data",
        fake_get_personal_online_data,
    )
    monkeypatch.setattr(
        mc_services,
        "get_personal_online_data_by_player_name",
        fake_get_personal_online_data_by_player_name,
    )
    monkeypatch.setattr(
        mc_services,
        "render_personal_online",
        fake_render_personal_online,
    )

    await McServerService().personal_online_message("123456", None, qq_id="10000")
    await McServerService().personal_online_message_by_player_name(
        "123456",
        None,
        player_name="Letemps",
    )

    assert captured["qq"] == ("本周", "10000", "主服 本周 个人在线情况")
    assert captured["player"] == ("本周", "Letemps", "主服 本周 个人在线情况")


@pytest.mark.asyncio
async def test_playtime_message_keeps_season_default(
    monkeypatch: pytest.MonkeyPatch,
):
    server = _FakeServer()
    server.name = "主服"
    captured = {}

    async def fake_get_server_for_group(_group_id: str):
        return server

    async def fake_get_active_season(_server):
        return SimpleNamespace(started_at=datetime(2026, 5, 1, 12, 0, 0))

    async def fake_get_playtime_entries(_server, time_range):
        captured["range_label"] = time_range.label
        return []

    async def fake_render_playtime(title, entries):
        captured["title"] = title
        captured["entries"] = entries
        return mc_services.RenderedMessage(image=None, fallback_text="time")

    monkeypatch.setattr(mc_services, "get_server_for_group", fake_get_server_for_group)
    monkeypatch.setattr(mc_services, "get_active_season", fake_get_active_season)
    monkeypatch.setattr(mc_services, "get_playtime_entries", fake_get_playtime_entries)
    monkeypatch.setattr(mc_services, "render_playtime", fake_render_playtime)

    await McServerService().playtime_message("123456")

    assert captured["range_label"] == "本周目"
    assert captured["title"] == "主服 本周目 在线时长"


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
async def test_handle_log_event_sends_join_notice(monkeypatch: pytest.MonkeyPatch):
    server = SimpleNamespace(id=1, group_id="123456")
    binding = _FakeBinding()
    binding.join_notify_enabled = True
    event = SimpleNamespace(
        type="join",
        player_name="Steve",
        occurred_at=datetime(2026, 5, 16, 20, 0, 0),
    )
    starts = []
    notices = []
    service = McServerService()

    async def fake_start_session(_server, player_name: str, *, occurred_at):
        starts.append((player_name, occurred_at))
        return None

    async def fake_list_server_group_bindings(_server):
        return [binding]

    async def fake_send_notice_to_group(group_id: str, message: str):
        notices.append((group_id, message))

    monkeypatch.setattr(mc_services, "start_session", fake_start_session)
    monkeypatch.setattr(
        mc_services,
        "list_server_group_bindings",
        fake_list_server_group_bindings,
    )
    monkeypatch.setattr(service, "_send_notice_to_group", fake_send_notice_to_group)

    await service._handle_log_event(server, event)

    assert starts == [("Steve", event.occurred_at)]
    assert notices == [("123456", "Steve 加入了游戏")]


@pytest.mark.asyncio
async def test_handle_log_event_delays_leave_notice_and_uses_original_time(
    monkeypatch: pytest.MonkeyPatch,
):
    started_at = datetime(2026, 5, 16, 20, 0, 0)
    left_at = started_at + timedelta(minutes=5)
    server = SimpleNamespace(id=1, group_id="123456")
    binding = _FakeBinding()
    binding.join_notify_enabled = True
    event = SimpleNamespace(type="leave", player_name="Steve", occurred_at=left_at)
    ended_sessions = []
    notices = []
    service = McServerService()

    async def fake_end_session(_server, player_name: str, *, occurred_at):
        ended_sessions.append((player_name, occurred_at))
        return SimpleNamespace(started_at=started_at, ended_at=occurred_at)

    async def fake_list_server_group_bindings(_server):
        return [binding]

    async def fake_send_notice_to_group(group_id: str, message: str):
        notices.append((group_id, message))

    monkeypatch.setattr(
        mc_services,
        "get_settings",
        lambda: SimpleNamespace(rejoin_suppress_seconds=0.01),
    )
    monkeypatch.setattr(mc_services, "end_session", fake_end_session)
    monkeypatch.setattr(
        mc_services,
        "list_server_group_bindings",
        fake_list_server_group_bindings,
    )
    monkeypatch.setattr(service, "_send_notice_to_group", fake_send_notice_to_group)

    await service._handle_log_event(server, event)
    assert ended_sessions == []
    await asyncio.sleep(0.02)

    assert ended_sessions == [("Steve", left_at)]
    assert notices == [("123456", "Steve 离开了游戏，本次在线 5分钟")]


@pytest.mark.asyncio
async def test_handle_log_event_suppresses_short_rejoin_and_keeps_session(
    monkeypatch: pytest.MonkeyPatch,
):
    started_at = datetime(2026, 5, 16, 20, 0, 0)
    left_at = started_at + timedelta(minutes=5)
    rejoined_at = left_at + timedelta(seconds=3)
    server = SimpleNamespace(id=1, group_id="123456")
    binding = _FakeBinding()
    binding.join_notify_enabled = True
    service = McServerService()
    starts = []
    ended_sessions = []
    notices = []

    async def fake_start_session(_server, player_name: str, *, occurred_at):
        starts.append((player_name, occurred_at))
        return None

    async def fake_end_session(_server, player_name: str, *, occurred_at):
        ended_sessions.append((player_name, occurred_at))
        return SimpleNamespace(started_at=started_at, ended_at=occurred_at)

    async def fake_list_server_group_bindings(_server):
        return [binding]

    async def fake_send_notice_to_group(group_id: str, message: str):
        notices.append((group_id, message))

    monkeypatch.setattr(
        mc_services,
        "get_settings",
        lambda: SimpleNamespace(rejoin_suppress_seconds=0.05),
    )
    monkeypatch.setattr(mc_services, "start_session", fake_start_session)
    monkeypatch.setattr(mc_services, "end_session", fake_end_session)
    monkeypatch.setattr(
        mc_services,
        "list_server_group_bindings",
        fake_list_server_group_bindings,
    )
    monkeypatch.setattr(service, "_send_notice_to_group", fake_send_notice_to_group)

    await service._handle_log_event(
        server,
        SimpleNamespace(type="leave", player_name="Steve", occurred_at=left_at),
    )
    await service._handle_log_event(
        server,
        SimpleNamespace(type="join", player_name="Steve", occurred_at=rejoined_at),
    )
    await asyncio.sleep(0.06)

    assert starts == [("Steve", rejoined_at)]
    assert ended_sessions == []
    assert notices == []


@pytest.mark.asyncio
async def test_handle_log_event_counts_online_from_first_join_after_short_rejoin(
    monkeypatch: pytest.MonkeyPatch,
):
    started_at = datetime(2026, 5, 16, 20, 0, 0)
    first_left_at = started_at + timedelta(minutes=5)
    rejoined_at = first_left_at + timedelta(seconds=3)
    final_left_at = started_at + timedelta(minutes=20)
    server = SimpleNamespace(id=1, group_id="123456")
    binding = _FakeBinding()
    binding.join_notify_enabled = True
    service = McServerService()
    notices = []

    async def fake_start_session(_server, _player_name: str, *, occurred_at):
        return SimpleNamespace(started_at=started_at, ended_at=None)

    async def fake_end_session(_server, _player_name: str, *, occurred_at):
        return SimpleNamespace(started_at=started_at, ended_at=occurred_at)

    async def fake_list_server_group_bindings(_server):
        return [binding]

    async def fake_send_notice_to_group(group_id: str, message: str):
        notices.append((group_id, message))

    monkeypatch.setattr(
        mc_services,
        "get_settings",
        lambda: SimpleNamespace(rejoin_suppress_seconds=0.01),
    )
    monkeypatch.setattr(mc_services, "start_session", fake_start_session)
    monkeypatch.setattr(mc_services, "end_session", fake_end_session)
    monkeypatch.setattr(
        mc_services,
        "list_server_group_bindings",
        fake_list_server_group_bindings,
    )
    monkeypatch.setattr(service, "_send_notice_to_group", fake_send_notice_to_group)

    await service._handle_log_event(
        server,
        SimpleNamespace(type="leave", player_name="Steve", occurred_at=first_left_at),
    )
    await service._handle_log_event(
        server,
        SimpleNamespace(type="join", player_name="Steve", occurred_at=rejoined_at),
    )
    await service._handle_log_event(
        server,
        SimpleNamespace(type="leave", player_name="Steve", occurred_at=final_left_at),
    )
    await asyncio.sleep(0.02)

    assert notices == [("123456", "Steve 离开了游戏，本次在线 20分钟")]


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
