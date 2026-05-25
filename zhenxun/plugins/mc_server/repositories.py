from __future__ import annotations

from datetime import datetime
import hashlib

from tortoise.expressions import Q

from .models import (
    McChatDedup,
    McLogCursor,
    McOnlineSession,
    McPlayer,
    McPlayerCountSample,
    McQqBinding,
    McSeason,
    McServer,
)
from .stats import aggregate_playtime, clip_online_segment
from .types import PersonalOnlineData, PlaytimeEntry, PlaytimeRow, TimeRange
from .utils import now_local


async def get_server_for_group(group_id: str, platform: str = "qq") -> McServer | None:
    return await McServer.get_or_none(platform=platform, group_id=str(group_id))


async def bind_server(
    group_id: str,
    *,
    host: str,
    port: int,
    platform: str = "qq",
) -> McServer:
    server, _ = await McServer.update_or_create(
        platform=platform,
        group_id=str(group_id),
        defaults={"host": host, "port": port, "name": "默认服务器"},
    )
    await get_active_season(server)
    return server


async def get_active_season(server: McServer) -> McSeason:
    season = await McSeason.get_or_none(server=server, active=True)
    if season:
        return season
    return await McSeason.create(
        server=server,
        name="默认周目",
        started_at=now_local(),
        active=True,
    )


async def upsert_player(server: McServer, player_name: str, uuid: str = "") -> McPlayer:
    player, _ = await McPlayer.update_or_create(
        server=server,
        name=player_name,
        defaults={"uuid": uuid, "last_seen_at": now_local()},
    )
    return player


async def get_binding_for_player(
    server: McServer,
    player: McPlayer,
) -> McQqBinding | None:
    return await McQqBinding.get_or_none(server=server, player=player)


async def upsert_binding(
    server: McServer,
    player: McPlayer,
    *,
    qq_id: str,
    created_by: str,
    platform: str = "qq",
) -> McQqBinding:
    binding, _ = await McQqBinding.update_or_create(
        server=server,
        platform=platform,
        qq_id=str(qq_id),
        defaults={"player": player, "created_by": str(created_by)},
    )
    await (
        McQqBinding.filter(server=server, player=player)
        .exclude(id=binding.id)
        .delete()
    )
    return binding


async def start_session(
    server: McServer,
    player_name: str,
    *,
    occurred_at: datetime,
    uuid: str = "",
    source: str = "log",
) -> McOnlineSession:
    player = await upsert_player(server, player_name, uuid)
    season = await get_active_season(server)
    existing = await McOnlineSession.get_or_none(
        server=server,
        player=player,
        ended_at__isnull=True,
    )
    if existing:
        return existing
    binding = await get_binding_for_player(server, player)
    return await McOnlineSession.create(
        server=server,
        season=season,
        player=player,
        player_name=player.name,
        qq_id=binding.qq_id if binding else "",
        started_at=occurred_at,
        source=source,
    )


async def end_session(
    server: McServer,
    player_name: str,
    *,
    occurred_at: datetime,
) -> McOnlineSession | None:
    player = await McPlayer.get_or_none(server=server, name=player_name)
    if not player:
        return None
    session = await McOnlineSession.get_or_none(
        server=server,
        player=player,
        ended_at__isnull=True,
    )
    if not session:
        return None
    session.ended_at = occurred_at
    await session.save(update_fields=["ended_at", "updated_at"])
    return session


async def close_active_sessions(server: McServer, *, occurred_at: datetime) -> int:
    return await McOnlineSession.filter(
        server=server,
        ended_at__isnull=True,
    ).update(ended_at=occurred_at, updated_at=now_local())


async def list_active_sessions(server: McServer) -> list[McOnlineSession]:
    return await McOnlineSession.filter(server=server, ended_at__isnull=True).all()


async def record_count_sample(
    server: McServer,
    *,
    online_count: int,
    max_players: int,
    captured_at: datetime,
) -> McPlayerCountSample:
    season = await get_active_season(server)
    sample = await McPlayerCountSample.create(
        server=server,
        season=season,
        online_count=online_count,
        max_players=max_players,
        captured_at=captured_at,
    )
    server.last_sampled_at = captured_at
    await server.save(update_fields=["last_sampled_at", "updated_at"])
    return sample


async def get_playtime_entries(
    server: McServer,
    time_range: TimeRange,
    *,
    qq_id: str | None = None,
) -> list[PlaytimeEntry]:
    query = McOnlineSession.filter(
        server=server,
        started_at__lt=time_range.end,
    ).filter(Q(ended_at__gte=time_range.start) | Q(ended_at__isnull=True))
    if qq_id:
        query = query.filter(qq_id=str(qq_id))

    rows = []
    for session in await query.all():
        from .stats import overlap_seconds

        # 将会话裁剪到查询窗口内，避免跨天/跨周目的长会话把范围外时长算进去。
        seconds = overlap_seconds(
            session.started_at,
            session.ended_at,
            time_range.start,
            time_range.end,
        )
        rows.append(
            PlaytimeRow(
                player_name=session.player_name,
                qq_id=session.qq_id,
                seconds=seconds,
            )
        )
    return aggregate_playtime(rows)


async def get_personal_online_data(
    server: McServer,
    time_range: TimeRange,
    *,
    qq_id: str,
    title: str,
) -> PersonalOnlineData:
    target_qq = str(qq_id)
    bindings = (
        await McQqBinding.filter(server=server, platform="qq", qq_id=target_qq)
        .select_related("player")
        .all()
    )
    player_ids = [binding.player_id for binding in bindings if binding.player_id]
    player_names = _unique_non_empty(
        [binding.player.name for binding in bindings if binding.player]
    )

    query = McOnlineSession.filter(
        server=server,
        started_at__lt=time_range.end,
    ).filter(Q(ended_at__gte=time_range.start) | Q(ended_at__isnull=True))

    # 兼容“先游玩后绑定”：旧 session 的 qq_id 可能为空。
    # player 外键或名称仍能把历史在线段归到当前绑定用户。
    target_filter = Q(qq_id=target_qq)
    if player_ids:
        target_filter |= Q(player_id__in=player_ids)
    if player_names:
        target_filter |= Q(player_name__in=player_names)

    sessions = await query.filter(target_filter).order_by("started_at").all()
    segments = []
    for session in sessions:
        segment = clip_online_segment(
            session.started_at,
            session.ended_at,
            time_range.start,
            time_range.end,
        )
        if segment:
            segments.append(segment)
            if session.player_name:
                player_names.append(session.player_name)

    player_names = _unique_non_empty(player_names)
    return PersonalOnlineData(
        title=title,
        range_label=time_range.label,
        range_start=time_range.start,
        range_end=time_range.end,
        qq_id=target_qq,
        player_names=player_names,
        segments=segments,
        total_seconds=sum(segment.seconds for segment in segments),
    )


async def get_count_samples(
    server: McServer,
    time_range: TimeRange,
) -> list[McPlayerCountSample]:
    return (
        await McPlayerCountSample.filter(
            server=server,
            captured_at__gte=time_range.start,
            captured_at__lte=time_range.end,
        )
        .order_by("captured_at")
        .all()
    )


async def get_or_init_cursor(server: McServer, path: str, *, size: int) -> McLogCursor:
    cursor, created = await McLogCursor.get_or_create(
        server=server,
        path=path,
        defaults={"offset": size},
    )
    if created:
        return cursor
    return cursor


async def update_cursor(cursor: McLogCursor, *, offset: int, inode: str = "") -> None:
    cursor.offset = offset
    cursor.inode = inode
    await cursor.save(update_fields=["offset", "inode", "updated_at"])


async def chat_digest_exists(server: McServer, raw: str) -> bool:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if await McChatDedup.exists(server=server, digest=digest):
        return True
    await McChatDedup.create(server=server, digest=digest)
    return False


def _unique_non_empty(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        value = item.strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result
