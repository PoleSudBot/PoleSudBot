from __future__ import annotations

from tortoise.exceptions import OperationalError

from zhenxun.services.log import logger

from .constants import MODULE_NAME
from .models import (
    McChatDedup,
    McLogCursor,
    McOnlineSession,
    McPlayer,
    McPlayerCountSample,
    McQqBinding,
    McSeason,
    McServer,
    McServerGroupBinding,
)
from .repositories import (
    bind_group_to_server,
    get_active_season,
    normalize_server_identity,
)
from .utils import normalize_datetime

_MIGRATED = False


async def migrate_group_shared_servers() -> None:
    global _MIGRATED

    if _MIGRATED:
        return
    try:
        await _backfill_identity_keys()
        await _ensure_group_bindings()
        await _merge_duplicate_servers()
    except OperationalError as exc:
        logger.warning("MC服务器共享数据迁移跳过，数据库表尚未就绪", MODULE_NAME, e=exc)
        return
    _MIGRATED = True


async def _backfill_identity_keys() -> None:
    for server in await McServer.all():
        identity_key = str(getattr(server, "identity_key", "") or "").strip()
        if identity_key:
            continue
        # 旧数据没有服务器身份字段，只能按规范化地址端口归并。
        server.identity_key = normalize_server_identity(server.host, server.port)
        await server.save(update_fields=["identity_key", "updated_at"])


async def _ensure_group_bindings() -> None:
    for server in await McServer.all():
        group_id = str(getattr(server, "group_id", "") or "").strip()
        if not group_id:
            continue
        await bind_group_to_server(
            group_id,
            server,
            platform=str(getattr(server, "platform", "") or "qq"),
        )


async def _merge_duplicate_servers() -> None:
    grouped: dict[str, list[McServer]] = {}
    for server in await McServer.all():
        identity_key = str(server.identity_key or "").strip()
        if not identity_key:
            identity_key = normalize_server_identity(server.host, server.port)
        grouped.setdefault(identity_key, []).append(server)

    for identity_key, servers in grouped.items():
        if len(servers) <= 1:
            continue
        canonical = await _choose_canonical_server(servers)
        canonical.identity_key = identity_key
        await canonical.save(update_fields=["identity_key", "updated_at"])
        for source in sorted(servers, key=lambda item: int(item.id)):
            if int(source.id) == int(canonical.id):
                continue
            await _merge_server_into(source, canonical)


async def _choose_canonical_server(servers: list[McServer]) -> McServer:
    scored: list[tuple[int, int, McServer]] = []
    for server in servers:
        score = (
            await McOnlineSession.filter(server=server).count()
            + await McPlayerCountSample.filter(server=server).count()
            + await McPlayer.filter(server=server).count()
        )
        scored.append((-score, int(server.id), server))
    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][2]


async def _merge_server_into(source: McServer, canonical: McServer) -> None:
    player_map = await _merge_players(source, canonical)
    season_map = await _merge_seasons(source, canonical)

    # Session 是统计本体，迁移时同步清掉旧群重复记录造成的重叠在线段。
    for session in await McOnlineSession.filter(server=source).all():
        target_player_id = player_map.get(
            int(session.player_id),
            int(session.player_id),
        )
        target_season_id = season_map.get(
            int(session.season_id) if session.season_id else 0,
            session.season_id,
        )
        await _merge_online_session(
            session,
            canonical,
            player_id=target_player_id,
            season_id=target_season_id,
        )

    for sample in await McPlayerCountSample.filter(server=source).all():
        if sample.season_id in season_map:
            sample.season_id = season_map[sample.season_id]
        sample.server = canonical
        await sample.save(update_fields=["server_id", "season_id"])

    await _merge_qq_bindings(source, canonical, player_map)
    await _merge_log_cursors(source, canonical)
    await _merge_chat_dedup(source, canonical)
    await McServerGroupBinding.filter(server=source).update(server=canonical)

    await McSeason.filter(server=source).delete()
    await McPlayer.filter(server=source).delete()
    await source.delete()


async def _merge_players(source: McServer, canonical: McServer) -> dict[int, int]:
    player_map: dict[int, int] = {}
    for player in await McPlayer.filter(server=source).all():
        target = await McPlayer.get_or_none(
            server=canonical,
            name__iexact=player.name,
        )
        if target:
            player_map[int(player.id)] = int(target.id)
            continue
        player.server = canonical
        await player.save(update_fields=["server_id", "updated_at"])
        player_map[int(player.id)] = int(player.id)
    return player_map


async def _merge_online_session(
    session: McOnlineSession,
    canonical: McServer,
    *,
    player_id: int,
    season_id: int | None,
) -> None:
    overlapping = [
        item
        for item in await McOnlineSession.filter(
            server=canonical,
            player_id=player_id,
        ).all()
        if _sessions_overlap(session, item)
    ]
    if not overlapping:
        session.server = canonical
        session.player_id = player_id
        session.season_id = season_id
        await session.save(update_fields=["server_id", "player_id", "season_id"])
        return

    # 旧版本同一服务器按群拆行时会生成重复在线段；同一重叠组只保留覆盖时间最长的记录。
    best = max([session, *overlapping], key=_session_length_key)
    if int(best.id) != int(session.id):
        await session.delete()
        return

    for item in overlapping:
        await item.delete()
    session.server = canonical
    session.player_id = player_id
    session.season_id = season_id
    await session.save(update_fields=["server_id", "player_id", "season_id"])


def _sessions_overlap(left: McOnlineSession, right: McOnlineSession) -> bool:
    left_start = normalize_datetime(left.started_at)
    right_start = normalize_datetime(right.started_at)
    if not left_start or not right_start:
        return False
    left_end = normalize_datetime(left.ended_at)
    right_end = normalize_datetime(right.ended_at)

    # 进行中的在线段没有固定结束时间，迁移去重时按开放区间判断是否与历史段相交。
    left_after_right = right_end is not None and left_start >= right_end
    right_after_left = left_end is not None and right_start >= left_end
    return not (left_after_right or right_after_left)


def _session_length_key(session: McOnlineSession) -> tuple[int, int, int]:
    start = normalize_datetime(session.started_at)
    end = normalize_datetime(session.ended_at)
    if not start:
        return (0, 0, -int(session.id))
    if not end:
        # 开放 session 代表仍在线，优先保留开始更早的一条，避免被短闭合段截断。
        return (1, -int(start.timestamp()), -int(session.id))
    return (0, max(0, int((end - start).total_seconds())), -int(session.id))


async def _merge_seasons(
    source: McServer,
    canonical: McServer,
) -> dict[int, int | None]:
    season_map: dict[int, int | None] = {}
    canonical_active = await get_active_season(canonical)
    for season in await McSeason.filter(server=source).all():
        if season.active:
            season_map[int(season.id)] = int(canonical_active.id)
            continue
        season.server = canonical
        await season.save(update_fields=["server_id", "updated_at"])
        season_map[int(season.id)] = int(season.id)
    return season_map


async def _merge_qq_bindings(
    source: McServer,
    canonical: McServer,
    player_map: dict[int, int],
) -> None:
    for binding in await McQqBinding.filter(server=source).all():
        target_player_id = player_map.get(int(binding.player_id))
        if not target_player_id:
            await binding.delete()
            continue

        qq_conflict = await McQqBinding.get_or_none(
            server=canonical,
            platform=binding.platform,
            qq_id=binding.qq_id,
        )
        player_conflict = await McQqBinding.get_or_none(
            server=canonical,
            player_id=target_player_id,
        )
        if qq_conflict or player_conflict:
            await binding.delete()
            continue

        binding.server = canonical
        binding.player_id = target_player_id
        await binding.save(update_fields=["server_id", "player_id", "updated_at"])


async def _merge_log_cursors(source: McServer, canonical: McServer) -> None:
    for cursor in await McLogCursor.filter(server=source).all():
        target = await McLogCursor.get_or_none(server=canonical, path=cursor.path)
        if not target:
            cursor.server = canonical
            await cursor.save(update_fields=["server_id", "updated_at"])
            continue

        # 同一日志文件只保留推进更远的游标，避免迁移后重复读取旧日志。
        if int(cursor.offset or 0) > int(target.offset or 0):
            target.offset = cursor.offset
            target.inode = cursor.inode or target.inode
            await target.save(update_fields=["offset", "inode", "updated_at"])
        await cursor.delete()


async def _merge_chat_dedup(source: McServer, canonical: McServer) -> None:
    for item in await McChatDedup.filter(server=source).all():
        exists = await McChatDedup.exists(server=canonical, digest=item.digest)
        if exists:
            await item.delete()
            continue
        item.server = canonical
        await item.save(update_fields=["server_id"])
