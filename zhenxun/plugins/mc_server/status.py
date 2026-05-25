from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .bluemap import fetch_bluemap_players
from .constants import MODULE_NAME
from .types import BlueMapPlayer, PlayerStatus, ServerStatus
from .utils import format_server_address, normalize_datetime, now_local

if TYPE_CHECKING:
    from .models import McServer


async def query_server_status(server: McServer, *, timeout: int) -> ServerStatus:
    try:
        return await asyncio.wait_for(_query_status(server, timeout=timeout), timeout)
    except Exception as exc:
        return ServerStatus(
            name=server.name,
            address=format_server_address(server.host, server.port),
            online=False,
            error=str(exc),
        )


async def probe_server_status(
    host: str,
    port: int,
    *,
    timeout: int,
) -> ServerStatus:
    address = format_server_address(host, port)
    try:
        return await asyncio.wait_for(
            _query_raw_status(address, name="默认服务器"),
            timeout,
        )
    except Exception as exc:
        return ServerStatus(
            name="默认服务器",
            address=address,
            online=False,
            error=str(exc),
        )


async def _query_status(server: McServer, *, timeout: int) -> ServerStatus:
    address = format_server_address(server.host, server.port)
    status = await _query_raw_status(address, name=server.name)
    bluemap_players = await _safe_bluemap_players_by_name(server, timeout=timeout)
    players = list(status.players)
    known_names = {player.name.lower() for player in players}
    for key, player in bluemap_players.items():
        if key not in known_names:
            players.append(PlayerStatus(name=player.name, uuid=player.uuid))
    from .repositories import list_active_sessions

    active_sessions = {
        session.player_name.lower(): session
        for session in await list_active_sessions(server)
    }

    enriched: list[PlayerStatus] = []
    for player in players:
        session = active_sessions.get(player.name.lower())
        online_seconds = 0
        if session:
            started_at = normalize_datetime(session.started_at)
            if started_at:
                online_seconds = int((now_local() - started_at).total_seconds())
        bluemap_player = bluemap_players.get(player.name.lower())
        position = bluemap_player.position_text if bluemap_player else ""
        enriched.append(
            PlayerStatus(
                name=player.name,
                uuid=player.uuid,
                online_seconds=online_seconds,
                position=position,
            )
        )

    return ServerStatus(
        name=status.name,
        address=status.address,
        online=True,
        latency_ms=status.latency_ms,
        version=status.version,
        online_players=status.online_players,
        max_players=status.max_players,
        players=enriched,
        weather=status.weather,
    )


async def _query_raw_status(address: str, *, name: str) -> ServerStatus:
    try:
        from mcstatus import JavaServer
    except ImportError as exc:
        raise RuntimeError("缺少依赖 mcstatus，请先同步项目依赖。") from exc

    mc_server = await JavaServer.async_lookup(address)
    raw_status = await mc_server.async_status()
    players = await _players_from_status(raw_status)
    return ServerStatus(
        name=name,
        address=address,
        online=True,
        latency_ms=float(getattr(raw_status, "latency", 0.0) or 0.0),
        version=str(getattr(getattr(raw_status, "version", None), "name", "") or ""),
        online_players=int(
            getattr(getattr(raw_status, "players", None), "online", 0) or 0
        ),
        max_players=int(getattr(getattr(raw_status, "players", None), "max", 0) or 0),
        players=players,
    )


async def _players_from_status(raw_status) -> list[PlayerStatus]:
    sample = getattr(getattr(raw_status, "players", None), "sample", None) or []
    result: list[PlayerStatus] = []
    for item in sample:
        name = str(getattr(item, "name", "") or "").strip()
        if not name:
            continue
        result.append(PlayerStatus(name=name, uuid=str(getattr(item, "id", "") or "")))
    return result


async def _bluemap_players_by_name(
    server: McServer,
    *,
    timeout: int,
) -> dict[str, BlueMapPlayer]:
    if not server.bluemap_base_url or not server.bluemap_map_ids:
        return {}
    players = await fetch_bluemap_players(
        server.bluemap_base_url,
        [str(item) for item in server.bluemap_map_ids],
        timeout=timeout,
    )
    return {player.name.lower(): player for player in players}


async def _safe_bluemap_players_by_name(
    server: McServer,
    *,
    timeout: int,
) -> dict[str, BlueMapPlayer]:
    try:
        return await _bluemap_players_by_name(server, timeout=timeout)
    except Exception as exc:
        # BlueMap 是可选增强源，失败时保留 mcstatus 基础状态，避免误报服务器离线。
        from zhenxun.services.log import logger

        logger.warning("MC BlueMap玩家数据读取失败", MODULE_NAME, e=exc)
        return {}
