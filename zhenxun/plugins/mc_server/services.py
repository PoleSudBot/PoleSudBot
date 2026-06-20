from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles
import nonebot
from nonebot.adapters import Bot

from zhenxun.models.level_user import LevelUser
from zhenxun.services.log import logger
from zhenxun.utils.platform import PlatformUtils

from .config import McServerPreset, get_settings
from .constants import (
    MODULE_NAME,
    SWITCH_ALL,
    SWITCH_CHAT,
    SWITCH_CONN,
    SWITCH_JOIN,
    SWITCH_KEYS,
)
from .log_parser import parse_paper_log_line
from .models import McServer
from .rcon import McRconError, execute_rcon_command
from .renderer import (
    render_chart,
    render_personal_online,
    render_playtime,
    render_status,
)
from .repositories import (
    bind_server,
    chat_digest_exists,
    close_active_sessions,
    end_session,
    get_active_season,
    get_count_samples,
    get_group_binding,
    get_or_init_cursor,
    get_personal_online_data,
    get_personal_online_data_by_player_name,
    get_playtime_entries,
    get_server_for_group,
    list_active_sessions,
    list_poll_servers,
    list_server_group_bindings,
    record_count_sample,
    require_group_binding,
    start_session,
    update_cursor,
    upsert_binding,
    upsert_player,
)
from .stats import aggregate_sample_points
from .status import probe_server_status, query_server_status
from .types import ChartData, RenderedMessage
from .utils import (
    build_tellraw_command,
    classify_tellraw_response,
    format_duration,
    format_server_address,
    normalize_datetime,
    now_local,
    parse_rcon_list_online_count,
    parse_server_address,
    parse_time_range,
    resolve_latest_log_path,
)


@dataclass(frozen=True)
class PollResult:
    checked: int = 0
    notified: int = 0


@dataclass(frozen=True)
class _PendingLeave:
    occurred_at: datetime
    task: asyncio.Task[None]


def should_reset_log_cursor(
    *,
    cursor_inode: str,
    current_inode: str,
    cursor_offset: int,
    current_size: int,
) -> bool:
    if cursor_inode and cursor_inode != current_inode:
        return True
    return current_size < cursor_offset


class McServerService:
    def __init__(self) -> None:
        self._polling = False
        self._startup_sessions_closed = False
        self._pending_leaves: dict[tuple[int, str], _PendingLeave] = {}
        self._log_locks: dict[str, asyncio.Lock] = {}

    async def bind_group_server(self, group_id: str, address: str) -> str:
        rendered = await self.bind_group_server_with_status(group_id, address)
        return rendered.lead_text or rendered.fallback_text

    async def bind_group_server_with_status(
        self,
        group_id: str,
        address: str,
    ) -> RenderedMessage:
        parsed = parse_server_address(address)
        status = await self._probe_bindable_server(parsed.host, parsed.port)
        await bind_server(str(group_id), host=parsed.host, port=parsed.port)
        return await self._render_bind_result(
            status,
            lead_text=f"已成功绑定：{parsed.display}",
        )

    async def bind_group_server_with_rcon(
        self,
        group_id: str,
        *,
        host: str,
        server_port: int,
        rcon_port: int,
    ) -> RenderedMessage:
        parsed = parse_server_address(host, default_port=server_port)
        status = await self._probe_bindable_server(parsed.host, parsed.port)
        server = await bind_server(
            str(group_id),
            host=parsed.host,
            port=parsed.port,
        )
        server.rcon_host = parsed.host
        server.rcon_port = rcon_port
        await server.save(update_fields=["rcon_host", "rcon_port", "updated_at"])
        return await self._render_bind_result(
            status,
            lead_text=(
                f"已成功绑定：{parsed.display}\n"
                f"已设置RCON地址：{format_server_address(parsed.host, rcon_port)}"
            ),
        )

    async def bind_group_server_preset(
        self,
        group_id: str,
        preset_name: str,
    ) -> RenderedMessage:
        preset = get_settings().server_presets.get(preset_name)
        if not preset:
            raise ValueError(f"未找到MC服务器预设：{preset_name}")

        parsed = parse_server_address(preset.host, default_port=preset.port)
        rcon_address = preset.rcon_host or format_server_address(
            parsed.host,
            preset.rcon_port,
        )
        rcon = parse_server_address(rcon_address, default_port=preset.rcon_port)
        status = await self._probe_bindable_server(parsed.host, parsed.port)
        server = await bind_server(
            str(group_id),
            host=parsed.host,
            port=parsed.port,
            preset_name=preset.name,
        )
        warning = await self._apply_preset_fields(
            server,
            preset,
            rcon_host=rcon.host,
            rcon_port=rcon.port,
        )
        address_lines = [
            f"已绑定预设 {preset.name}：{parsed.display}",
            f"已设置RCON地址：{rcon.display}",
        ]
        if warning:
            address_lines.append(warning)
        return await self._render_bind_result(
            status,
            lead_text="\n".join(address_lines),
        )

    async def _probe_bindable_server(self, host: str, port: int):
        status = await probe_server_status(
            host,
            port,
            timeout=get_settings().request_timeout_seconds,
        )
        if not status.online:
            raise ValueError(f"服务器探测失败：{status.error or '无法连接'}")
        return status

    async def _render_bind_result(
        self,
        status,
        *,
        lead_text: str,
    ) -> RenderedMessage:
        rendered = await render_status(status)
        fallback = f"{lead_text}\n\n{rendered.fallback_text}"
        return RenderedMessage(
            image=rendered.image,
            fallback_text=fallback,
            lead_text=lead_text,
        )

    async def _apply_preset_fields(
        self,
        server: McServer,
        preset: McServerPreset,
        *,
        rcon_host: str,
        rcon_port: int,
    ) -> str:
        update_fields = [
            "rcon_host",
            "rcon_port",
            "rcon_password",
            "log_path",
            "bluemap_base_url",
            "bluemap_map_ids",
            "updated_at",
        ]
        server.rcon_host = rcon_host
        server.rcon_port = rcon_port
        server.rcon_password = preset.rcon_password
        server.bluemap_base_url = _normalize_bluemap_base_url(preset.bluemap_base_url)
        server.bluemap_map_ids = list(preset.bluemap_map_ids)

        # 预设日志路径只有在可读时才覆盖旧值，避免一次路径填错破坏已有日志监听。
        warning = ""
        if preset.log_path:
            log_path = self.resolve_log_path(preset.log_path)
            if log_path:
                server.log_path = str(log_path)
            else:
                update_fields.remove("log_path")
                warning = f"日志路径不可读，已保留旧配置：{preset.log_path}"
        else:
            server.log_path = ""

        await server.save(update_fields=update_fields)
        return warning

    async def set_log_path(self, group_id: str, path: str) -> str:
        server = await self._require_server(group_id)
        log_path = self.resolve_log_path(path)
        if not log_path:
            raw_path = Path(path).expanduser()
            candidate = raw_path / "latest.log" if raw_path.is_dir() else raw_path
            return f"日志文件不存在或不可读：{candidate}"
        server.log_path = str(log_path)
        await server.save(update_fields=["log_path", "updated_at"])
        return f"已设置MC日志路径：{log_path}"

    def resolve_log_path(self, path: str) -> Path | None:
        return resolve_latest_log_path(path)

    async def set_bluemap(
        self,
        group_id: str,
        base_url: str,
        map_ids: list[str],
    ) -> str:
        server = await self._require_server(group_id)
        if not base_url.startswith(("http://", "https://")):
            base_url = "http://" + base_url
        server.bluemap_base_url = base_url.rstrip("/")
        server.bluemap_map_ids = [item for item in map_ids if item.strip()]
        await server.save(
            update_fields=["bluemap_base_url", "bluemap_map_ids", "updated_at"]
        )
        maps = ", ".join(server.bluemap_map_ids) or "未设置地图"
        return f"已设置BlueMap：{server.bluemap_base_url} / {maps}"

    async def set_rcon_address(self, group_id: str, address: str) -> str:
        parsed = parse_server_address(address, default_port=25575)
        server = await self._require_server(group_id)
        server.rcon_host = parsed.host
        server.rcon_port = parsed.port
        await server.save(update_fields=["rcon_host", "rcon_port", "updated_at"])
        return f"已设置RCON地址：{parsed.display}"

    async def set_rcon_password(self, group_id: str, password: str) -> str:
        server = await self._require_server(group_id)
        server.rcon_password = password
        await server.save(update_fields=["rcon_password", "updated_at"])
        return f"已为群 {group_id} 保存RCON密码。"

    async def list_config(self, group_id: str) -> str:
        binding = await get_group_binding(str(group_id))
        if not binding:
            return "当前群未绑定MC服务器，请先使用 mcbind <地址>。"
        server = await binding.server
        bluemap_maps = (
            ", ".join(server.bluemap_map_ids) if server.bluemap_map_ids else "未配置"
        )
        return "\n".join(
            [
                "MC服务器绑定状态",
                f"地址：{format_server_address(server.host, server.port)}",
                f"日志：{server.log_path or '未配置'}",
                f"BlueMap：{server.bluemap_base_url or '未配置'}",
                f"地图：{bluemap_maps}",
                f"RCON：{_rcon_label(server)}",
                f"进退服播报：{'开' if binding.join_notify_enabled else '关'}",
                f"连接播报：{'开' if binding.conn_notify_enabled else '关'}",
                f"聊天互通：{'开' if binding.chat_bridge_enabled else '关'}",
            ]
        )

    async def toggle(self, group_id: str, switch: str, enabled: bool) -> str:
        if switch == SWITCH_ALL:
            return await self.toggle_all(group_id, enabled)
        if switch not in SWITCH_KEYS:
            return "开关仅支持 join / conn / chat / all。"
        binding = await require_group_binding(group_id)
        field = {
            SWITCH_JOIN: "join_notify_enabled",
            SWITCH_CONN: "conn_notify_enabled",
            SWITCH_CHAT: "chat_bridge_enabled",
        }[switch]
        setattr(binding, field, enabled)
        await binding.save(update_fields=[field, "updated_at"])
        return f"已{'开启' if enabled else '关闭'} {switch}。"

    async def toggle_all(self, group_id: str, enabled: bool) -> str:
        binding = await require_group_binding(group_id)
        # 一键开关只覆盖三类播报/互通字段，不改变服务器地址、日志、RCON 等配置。
        binding.join_notify_enabled = enabled
        binding.conn_notify_enabled = enabled
        binding.chat_bridge_enabled = enabled
        await binding.save(
            update_fields=[
                "join_notify_enabled",
                "conn_notify_enabled",
                "chat_bridge_enabled",
                "updated_at",
            ]
        )
        return f"已{'开启' if enabled else '关闭'}全部MC播报/互通开关。"

    async def status_message(self, group_id: str) -> RenderedMessage:
        server = await self._require_server(group_id)
        status = await query_server_status(
            server,
            timeout=get_settings().request_timeout_seconds,
        )
        trend_range = parse_time_range("24h")
        samples = await get_count_samples(server, trend_range)
        trend_data = ChartData(
            title=f"{_server_title(server)} 过去24h 在线人数",
            range_label=trend_range.label,
            range_start=trend_range.start,
            range_end=trend_range.end,
            range_end_is_current=trend_range.end_is_current,
            points=aggregate_sample_points(samples),
        )
        return await render_status(status, trend_data)

    async def playtime_message(
        self,
        group_id: str,
        range_text: str | None = None,
    ) -> RenderedMessage:
        server = await self._require_server(group_id)
        season = await get_active_season(server)
        time_range = parse_time_range(range_text, season.started_at)
        entries = await get_playtime_entries(server, time_range)
        return await render_playtime(
            f"{_server_title(server)} {time_range.label} 在线时长",
            entries,
            time_range,
        )

    async def personal_online_message(
        self,
        group_id: str,
        range_text: str | None,
        *,
        qq_id: str,
    ) -> RenderedMessage:
        server = await self._require_server(group_id)
        season = await get_active_season(server)
        time_range = parse_time_range(range_text or "7d", season.started_at)
        data = await get_personal_online_data(
            server,
            time_range,
            qq_id=qq_id,
            title=f"{_server_title(server)} {time_range.label} 个人在线情况",
        )
        return await render_personal_online(data)

    async def personal_online_message_by_player_name(
        self,
        group_id: str,
        range_text: str | None,
        *,
        player_name: str,
    ) -> RenderedMessage:
        server = await self._require_server(group_id)
        season = await get_active_season(server)
        time_range = parse_time_range(range_text or "7d", season.started_at)
        data = await get_personal_online_data_by_player_name(
            server,
            time_range,
            player_name=player_name,
            title=f"{_server_title(server)} {time_range.label} 个人在线情况",
        )
        return await render_personal_online(data)

    async def chart_message(
        self, group_id: str, range_text: str | None = None
    ) -> RenderedMessage:
        server = await self._require_server(group_id)
        season = await get_active_season(server)
        time_range = parse_time_range(range_text or "3d", season.started_at)
        samples = await get_count_samples(server, time_range)
        data = ChartData(
            title=f"{_server_title(server)} 在线人数变化",
            range_label=time_range.label,
            range_start=time_range.start,
            range_end=time_range.end,
            range_end_is_current=time_range.end_is_current,
            points=aggregate_sample_points(samples),
        )
        return await render_chart(data)

    async def send_chat_to_game(
        self, group_id: str, sender: str, message: str
    ) -> str | None:
        binding = await require_group_binding(group_id)
        server = await binding.server
        if not binding.chat_bridge_enabled:
            return "聊天互通未开启，请先使用 mctoggle chat on。"
        list_response = await self.execute_rcon(server, "list")
        online_count = parse_rcon_list_online_count(list_response)
        if online_count == 0:
            return None
        command = build_tellraw_command(sender, message, get_settings().chat_format)
        response = await self.execute_rcon(server, command)
        if failure := classify_tellraw_response(response):
            return f"RCON已连通，但tellraw执行失败：{failure}"
        return None

    async def execute_rcon(self, server: McServer, command: str) -> str:
        address = (
            format_server_address(server.rcon_host, server.rcon_port)
            if server.rcon_host
            else format_server_address(server.host, 25575)
        )
        try:
            return await execute_rcon_command(
                address,
                server.rcon_password,
                command,
                timeout=get_settings().request_timeout_seconds,
            )
        except McRconError as exc:
            raise ValueError(f"RCON执行失败：{exc}") from exc

    async def execute_group_rcon(self, group_id: str, command: str) -> str:
        server = await self._require_server(group_id)
        response = await self.execute_rcon(server, command)
        return f"发送成功，响应：\n{response}" if response else "发送成功，无响应。"

    async def verify_group_rcon(self, group_id: str) -> str:
        server = await self._require_server(group_id)
        if not server.rcon_password:
            raise ValueError("RCON密码未配置，请先私聊发送密码。")
        response = await self.execute_rcon(server, "list")
        return f"RCON验证成功：\n{response}" if response else "RCON验证成功。"

    async def whitelist(
        self,
        group_id: str,
        player_name: str,
        *,
        qq_id: str,
        created_by: str,
    ) -> str:
        server = await self._require_server(group_id)
        player = await upsert_player(server, player_name)
        await upsert_binding(server, player, qq_id=qq_id, created_by=created_by)
        await self.execute_rcon(server, f"whitelist add {player_name}")
        return f"已添加白名单并绑定：{player_name} <-> QQ {qq_id}"

    async def poll_once(self) -> PollResult:
        checked = notified = 0
        await self._close_stale_sessions_once()
        for server in await list_poll_servers():
            checked += 1
            bindings = await list_server_group_bindings(server)
            observed_at = now_local()
            status = await query_server_status(
                server,
                timeout=get_settings().request_timeout_seconds,
            )
            if status.online:
                # 先保存旧断连状态，恢复通知只能在本轮状态落库前判断。
                was_disconnected = server.disconnected
                await self._mark_online(
                    server,
                    status.online_players,
                    status.max_players,
                    observed_at=observed_at,
                )
                await self._handle_visible_players(
                    server,
                    status.players,
                    observed_at=observed_at,
                    player_list_complete=status.player_list_complete,
                )
                if was_disconnected:
                    notified += await self._send_bound_group_notice(
                        bindings,
                        "MC服务器监听连接已恢复。",
                        switch=SWITCH_CONN,
                    )
            else:
                should_notify = await self._mark_failed(
                    server,
                    status.error,
                    observed_at=observed_at,
                )
                if server.disconnected:
                    await self._close_active_sessions_by_observation(
                        server,
                        occurred_at=observed_at,
                    )
                if should_notify:
                    notified += await self._send_bound_group_notice(
                        bindings,
                        f"MC服务器监听连接断开：{status.error or '无法连接'}",
                        switch=SWITCH_CONN,
                    )
            await self._process_log_locked(server)
        return PollResult(checked=checked, notified=notified)

    def _log_lock_key(self, server: McServer) -> str:
        server_id = getattr(server, "id", None)
        return str(server_id) if server_id is not None else f"object:{id(server)}"

    async def _process_log_locked(self, server: McServer) -> None:
        key = self._log_lock_key(server)
        lock = self._log_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._log_locks[key] = lock
        async with lock:
            await self.process_log(server)

    async def process_log(self, server: McServer) -> None:
        log_path = getattr(server, "log_path", "")
        if not log_path:
            return
        path = Path(log_path)
        if not path.exists():
            return
        stat_result = path.stat()
        size = stat_result.st_size
        inode = str(stat_result.st_ino)
        cursor = await get_or_init_cursor(server, str(path), size=size)
        if should_reset_log_cursor(
            cursor_inode=cursor.inode,
            current_inode=inode,
            cursor_offset=cursor.offset,
            current_size=size,
        ):
            # 文件轮转或截断后旧 offset 不再可信，必须从新文件头开始读取。
            cursor.offset = 0

        async with aiofiles.open(path, encoding="utf-8", errors="ignore") as f:
            await f.seek(cursor.offset)
            lines = await f.readlines()
            offset = await f.tell()
        for line in lines:
            event = parse_paper_log_line(line, now_local())
            if not event:
                continue
            if await chat_digest_exists(server, event.raw):
                continue
            await self._handle_log_event(server, event)
        await update_cursor(cursor, offset=offset, inode=inode)

    async def run_poll_loop(self) -> None:
        if self._polling:
            return
        self._polling = True
        try:
            while True:
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("MC服务器轮询失败", MODULE_NAME, e=exc)
                await asyncio.sleep(get_settings().poll_interval_seconds)
        finally:
            self._polling = False

    async def ensure_admin(self, bot: Bot, event: Any, group_id: str) -> bool:
        from nonebot.permission import SUPERUSER

        if await SUPERUSER(bot, event):
            return True
        sender = getattr(event, "sender", None)
        if getattr(sender, "role", "") in {"owner", "admin"}:
            return True
        user_id = str(event.get_user_id())
        if await LevelUser.check_level(
            user_id,
            str(group_id),
            get_settings().admin_level,
        ):
            return True
        return False

    async def ensure_rcon_permission(self, bot: Bot, event: Any, group_id: str) -> bool:
        from nonebot.permission import SUPERUSER

        if await SUPERUSER(bot, event):
            return True
        return await LevelUser.check_level(
            str(event.get_user_id()),
            str(group_id),
            get_settings().rcon_level,
        )

    async def _require_server(self, group_id: str) -> McServer:
        server = await get_server_for_group(str(group_id))
        if not server:
            raise ValueError("当前群未绑定MC服务器，请先使用 mcbind <地址>。")
        return server

    async def _mark_online(
        self,
        server: McServer,
        online_count: int,
        max_players: int,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        now = observed_at or now_local()
        last_sampled_at = normalize_datetime(server.last_sampled_at)
        should_sample = (
            last_sampled_at is None
            or (now - last_sampled_at).total_seconds()
            >= get_settings().sample_interval_seconds
        )
        server.online = True
        server.disconnected = False
        server.failed_count = 0
        server.last_error = ""
        server.last_checked_at = now
        await server.save(
            update_fields=[
                "online",
                "disconnected",
                "failed_count",
                "last_error",
                "last_checked_at",
                "updated_at",
            ]
        )
        if should_sample:
            await record_count_sample(
                server,
                online_count=online_count,
                max_players=max_players,
                captured_at=now,
            )

    async def _mark_failed(
        self,
        server: McServer,
        error: str,
        *,
        observed_at: datetime | None = None,
    ) -> bool:
        now = observed_at or now_local()
        server.online = False
        server.failed_count += 1
        server.last_error = error
        server.last_checked_at = now
        should_notify = (
            not server.disconnected
            and server.failed_count >= get_settings().disconnect_notify_threshold
        )
        if should_notify:
            server.disconnected = True
        await server.save(
            update_fields=[
                "online",
                "failed_count",
                "last_error",
                "last_checked_at",
                "disconnected",
                "updated_at",
            ]
        )
        return should_notify

    async def _close_stale_sessions_once(self) -> None:
        if self._startup_sessions_closed:
            return
        self._startup_sessions_closed = True
        observed_at = now_local()
        for server in await list_poll_servers():
            # Bot 离线期间缺少可靠事件源，旧 active 段先在重启观测点截断。
            await close_active_sessions(server, occurred_at=observed_at)

    async def _handle_log_event(self, server: McServer, event) -> None:
        occurred_at = event.occurred_at or now_local()
        if event.type == "join":
            if self._cancel_pending_leave(server, event.player_name):
                # 短时间重进视为同一次在线，保留原 active session 并压掉进退服播报。
                await start_session(server, event.player_name, occurred_at=occurred_at)
                return
            await start_session(server, event.player_name, occurred_at=occurred_at)
            await self._send_bound_group_notice(
                await list_server_group_bindings(server),
                f"{event.player_name} 加入了游戏",
                switch=SWITCH_JOIN,
            )
        elif event.type == "leave":
            self._schedule_pending_leave(server, event.player_name, occurred_at)
        elif event.type == "server_start":
            # 新一轮启动说明旧 MC 进程已结束，重启前没 leave 的在线段必须截断。
            await self._close_active_sessions_by_observation(
                server,
                occurred_at=occurred_at,
            )
        elif event.type == "chat":
            await self._send_bound_group_notice(
                await list_server_group_bindings(server),
                f"<{event.player_name}> {event.message}",
                switch=SWITCH_CHAT,
            )

    def _pending_leave_key(self, server: McServer, player_name: str) -> tuple[int, str]:
        return (int(server.id), player_name.lower())

    def _cancel_pending_leave(self, server: McServer, player_name: str) -> bool:
        pending = self._pending_leaves.pop(
            self._pending_leave_key(server, player_name),
            None,
        )
        if not pending:
            return False
        # 补读日志时可能由 pending task 自己读到重进事件，此时只移除 pending 标记。
        if pending.task is not asyncio.current_task():
            pending.task.cancel()
        return True

    def _schedule_pending_leave(
        self,
        server: McServer,
        player_name: str,
        occurred_at: datetime,
    ) -> None:
        self._cancel_pending_leave(server, player_name)
        key = self._pending_leave_key(server, player_name)
        # 离开事件先延迟提交，避免 AuthMe/网络波动导致“离开+加入”刷屏并切断在线段。
        task = asyncio.create_task(
            self._commit_pending_leave(server, player_name, occurred_at),
        )
        task.add_done_callback(self._log_pending_leave_error)
        self._pending_leaves[key] = _PendingLeave(occurred_at=occurred_at, task=task)

    async def _commit_pending_leave(
        self,
        server: McServer,
        player_name: str,
        occurred_at: datetime,
    ) -> None:
        key = self._pending_leave_key(server, player_name)
        try:
            await asyncio.sleep(get_settings().rejoin_suppress_seconds)
            # 到点前再消费一次新增日志，避免玩家已重进但常规轮询尚未读到日志。
            await self._process_log_locked(server)
            pending = self._pending_leaves.get(key)
            if not pending or pending.task is not asyncio.current_task():
                return
            session = await end_session(
                server,
                player_name,
                occurred_at=occurred_at,
            )
            suffix = self._format_leave_suffix(session)
            await self._send_bound_group_notice(
                await list_server_group_bindings(server),
                f"{player_name} 离开了游戏{suffix}",
                switch=SWITCH_JOIN,
            )
        except asyncio.CancelledError:
            raise
        finally:
            pending = self._pending_leaves.get(key)
            task = asyncio.current_task()
            if pending and pending.task is task:
                self._pending_leaves.pop(key, None)

    def _log_pending_leave_error(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("MC延迟退服处理失败", MODULE_NAME, e=exc)

    def _format_leave_suffix(self, session) -> str:
        if not session or not session.ended_at:
            return ""
        ended_at = normalize_datetime(session.ended_at)
        started_at = normalize_datetime(session.started_at)
        seconds = (
            int((ended_at - started_at).total_seconds())
            if ended_at and started_at
            else 0
        )
        return f"，本次在线 {format_duration(seconds)}"

    async def _handle_visible_players(
        self,
        server: McServer,
        players,
        *,
        observed_at: datetime,
        player_list_complete: bool,
    ) -> None:
        visible_names: set[str] = set()
        for player in players:
            player_name = str(getattr(player, "name", "") or "").strip()
            if not player_name:
                continue
            visible_names.add(player_name.lower())
            # 状态源只能证明“此刻在线”，不能回推 Bot 离线期间的真实上线时间。
            await start_session(
                server,
                player_name,
                occurred_at=observed_at,
                uuid=str(getattr(player, "uuid", "") or ""),
                source="status",
            )
        # 不完整 sample 不能反推“名单外玩家已下线”；只有完整名单才能做反向关闭。
        if not player_list_complete:
            return
        active_sessions = await list_active_sessions(server)
        for session in active_sessions:
            player_name = str(getattr(session, "player_name", "") or "").strip()
            if not player_name or player_name.lower() in visible_names:
                continue
            await end_session(
                server,
                player_name,
                occurred_at=observed_at,
            )

    async def _close_active_sessions_by_observation(
        self,
        server: McServer,
        *,
        occurred_at: datetime,
    ) -> None:
        closed = await close_active_sessions(server, occurred_at=occurred_at)
        if closed:
            logger.info(
                "MC服务器状态观测后已截断在线会话",
                MODULE_NAME,
                server=str(getattr(server, "id", "")),
                closed=closed,
            )

    async def _send_group_notice(self, server: McServer, message: str) -> None:
        await self._send_notice_to_group(server.group_id, message)

    async def _send_bound_group_notice(
        self,
        bindings,
        message: str,
        *,
        switch: str,
    ) -> int:
        notified = 0
        for binding in bindings:
            if not _binding_switch_enabled(binding, switch):
                continue
            await self._send_notice_to_group(binding.group_id, message)
            notified += 1
        return notified

    async def _send_notice_to_group(self, group_id: str, message: str) -> None:
        bot = _first_available_bot()
        if not bot:
            return
        try:
            await PlatformUtils.send_message(
                bot,
                None,
                group_id,
                f"[Server] {message}",
            )
        except Exception as exc:
            logger.warning(
                "MC群通知发送失败",
                MODULE_NAME,
                target=group_id,
                e=exc,
            )


def _first_available_bot() -> Bot | None:
    bots = list(nonebot.get_bots().values())
    return bots[0] if bots else None


def _normalize_bluemap_base_url(base_url: str) -> str:
    text = base_url.strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    return text.rstrip("/")


def _server_title(server: McServer) -> str:
    name = str(getattr(server, "name", "") or "").strip()
    if not name or name == "默认服务器":
        return "MC 服务器"
    return name


def _rcon_label(server: McServer) -> str:
    address = (
        format_server_address(server.rcon_host, server.rcon_port)
        if server.rcon_host
        else "未配置"
    )
    password = "已保存" if server.rcon_password else "未保存密码"
    return f"{address}（{password}）"


def _binding_switch_enabled(binding, switch: str) -> bool:
    field = {
        SWITCH_JOIN: "join_notify_enabled",
        SWITCH_CONN: "conn_notify_enabled",
        SWITCH_CHAT: "chat_bridge_enabled",
    }.get(switch)
    return bool(field and getattr(binding, field, False))


mc_server_service = McServerService()
