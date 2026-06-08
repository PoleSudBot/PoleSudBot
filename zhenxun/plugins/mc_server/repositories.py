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
    McServerGroupBinding,
)
from .stats import (
    aggregate_daily_online_points,
    aggregate_hourly_online_points,
    aggregate_playtime,
    average_business_day_count,
    clip_online_segment,
    overlap_seconds,
)
from .types import (
    PersonalOnlineData,
    PersonalOnlineSegment,
    PlaytimeEntry,
    PlaytimeRow,
    TimeRange,
)
from .utils import business_day_count, normalize_datetime, now_local


def normalize_server_identity(host: str, port: int) -> str:
    normalized_host = str(host).strip().lower().strip("[]")
    return f"addr:{normalized_host}:{int(port)}"


def preset_server_identity(preset_name: str) -> str:
    return f"preset:{str(preset_name).strip()}"


def server_storage_group_id(identity_key: str) -> str:
    digest = hashlib.sha1(identity_key.encode("utf-8")).hexdigest()
    return f"shared:{digest[:32]}"


async def get_server_for_group(group_id: str, platform: str = "qq") -> McServer | None:
    binding = await get_group_binding(group_id, platform=platform)
    if binding:
        return await binding.server
    server = await McServer.get_or_none(platform=platform, group_id=str(group_id))
    if not server:
        return None
    await bind_group_to_server(group_id, server, platform=platform)
    return server


async def get_group_binding(
    group_id: str,
    platform: str = "qq",
) -> McServerGroupBinding | None:
    return await McServerGroupBinding.get_or_none(
        platform=platform,
        group_id=str(group_id),
    )


async def require_group_binding(
    group_id: str,
    platform: str = "qq",
) -> McServerGroupBinding:
    binding = await get_group_binding(group_id, platform=platform)
    if not binding:
        raise ValueError("当前群未绑定MC服务器，请先使用 mcbind <地址>。")
    return binding


async def bind_group_to_server(
    group_id: str,
    server: McServer,
    *,
    platform: str = "qq",
) -> McServerGroupBinding:
    existing = await get_group_binding(group_id, platform=platform)
    if existing:
        existing.server = server
        await existing.save(update_fields=["server_id", "updated_at"])
        return existing

    binding, _ = await McServerGroupBinding.update_or_create(
        platform=platform,
        group_id=str(group_id),
        defaults={
            "server": server,
            "join_notify_enabled": bool(
                getattr(server, "join_notify_enabled", False)
            ),
            "conn_notify_enabled": bool(
                getattr(server, "conn_notify_enabled", False)
            ),
            "chat_bridge_enabled": bool(
                getattr(server, "chat_bridge_enabled", False)
            ),
        },
    )
    return binding


async def list_server_group_bindings(
    server: McServer,
) -> list[McServerGroupBinding]:
    return await McServerGroupBinding.filter(server=server).all()


async def list_poll_servers() -> list[McServer]:
    return await McServer.all()


async def bind_server(
    group_id: str,
    *,
    host: str,
    port: int,
    platform: str = "qq",
    preset_name: str = "",
) -> McServer:
    identity_key = (
        preset_server_identity(preset_name)
        if preset_name
        else normalize_server_identity(host, port)
    )
    server = await McServer.filter(identity_key=identity_key).first()
    if not server:
        # 预设绑定和手动地址绑定可能交替使用，地址相同就继续复用旧统计实体。
        server = await McServer.filter(host=host, port=port).first()
    if server:
        server.host = host
        server.port = port
        server.identity_key = identity_key
        server.preset_name = preset_name
        await server.save(
            update_fields=["host", "port", "identity_key", "preset_name", "updated_at"]
        )
    else:
        # 旧唯一键仍在表结构里，server 行用稳定占位值，真实群关系由 binding 表维护。
        server = await McServer.create(
            platform=platform,
            group_id=server_storage_group_id(identity_key),
            host=host,
            port=port,
            name="默认服务器",
            identity_key=identity_key,
            preset_name=preset_name,
        )
    await bind_group_to_server(group_id, server, platform=platform)
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

    sessions = await query.all()
    first_seen_by_name = await _first_seen_by_player_names(
        server,
        [session.player_name for session in sessions if session.player_name],
    )

    rows = []
    for session in sessions:
        # 将会话裁剪到查询窗口内，避免跨天/跨周目的长会话把范围外时长算进去。
        segment = clip_online_segment(
            session.started_at,
            session.ended_at,
            time_range.start,
            time_range.end,
        )
        if not segment:
            continue
        rows.append(
            PlaytimeRow(
                player_name=session.player_name,
                qq_id=session.qq_id,
                seconds=segment.seconds,
                first_seen_at=first_seen_by_name.get(session.player_name.lower()),
                last_seen_at=segment.ended_at,
            )
        )
    return [
        PlaytimeEntry(
            player_name=entry.player_name,
            qq_id=entry.qq_id,
            seconds=entry.seconds,
            average_seconds=entry.seconds
            // average_business_day_count(
                time_range,
                entry.first_seen_at,
                entry.last_seen_at,
            ),
            first_seen_at=entry.first_seen_at,
            last_seen_at=entry.last_seen_at,
        )
        for entry in aggregate_playtime(rows)
    ]


async def get_total_online_seconds_by_player_names(
    server: McServer,
    player_names: list[str],
) -> dict[str, int]:
    names = _unique_non_empty(player_names)
    if not names:
        return {}
    query_filter = _player_name_filter(names)
    totals = {name.lower(): 0 for name in names}
    now = now_local()
    for session in await (
        McOnlineSession.filter(server=server).filter(query_filter).all()
    ):
        # 进行中的 session 按当前时间截断，状态卡才能展示实时累计在线。
        seconds = overlap_seconds(
            session.started_at,
            session.ended_at,
            session.started_at,
            now,
        )
        key = session.player_name.lower()
        totals[key] = totals.get(key, 0) + seconds
    return totals


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

    query = _online_sessions_in_range(server, time_range)

    # 兼容“先游玩后绑定”：旧 session 的 qq_id 可能为空。
    # player 外键或名称仍能把历史在线段归到当前绑定用户。
    target_filter = Q(qq_id=target_qq)
    if player_ids:
        target_filter |= Q(player_id__in=player_ids)
    if player_names:
        target_filter |= _player_name_filter(player_names)

    first_seen_at = await _first_seen_for_filter(server, target_filter)
    sessions = await query.filter(target_filter).order_by("started_at").all()
    segments = _segments_from_sessions(sessions, time_range)
    for session in sessions:
        if session.player_name:
            player_names.append(session.player_name)

    player_names = _unique_non_empty(player_names)
    return _build_personal_online_data(
        title=title,
        range_label=time_range.label,
        range_start=time_range.start,
        range_end=time_range.end,
        qq_id=target_qq,
        player_names=player_names,
        segments=segments,
        time_range=time_range,
        first_seen_at=first_seen_at,
    )


async def get_personal_online_data_by_player_name(
    server: McServer,
    time_range: TimeRange,
    *,
    player_name: str,
    title: str,
) -> PersonalOnlineData:
    target_name = player_name.strip()
    if not target_name:
        raise ValueError("玩家名不能为空。")

    player = await McPlayer.get_or_none(server=server, name__iexact=target_name)
    name_filter = Q(player_name__iexact=target_name)
    if player:
        name_filter |= Q(player=player)
    exists = player is not None or await McOnlineSession.exists(
        server=server,
        player_name__iexact=target_name,
    )
    if not exists:
        raise ValueError(f"未找到玩家在线记录：{target_name}")

    first_seen_at = await _first_seen_for_filter(server, name_filter)
    sessions = await (
        _online_sessions_in_range(server, time_range)
        .filter(name_filter)
        .order_by("started_at")
        .all()
    )
    player_names = _unique_non_empty(
        [player.name if player else target_name]
        + [session.player_name for session in sessions if session.player_name]
    )
    segments = _segments_from_sessions(sessions, time_range)
    return _build_personal_online_data(
        title=title,
        range_label=time_range.label,
        range_start=time_range.start,
        range_end=time_range.end,
        qq_id="",
        player_names=player_names,
        segments=segments,
        time_range=time_range,
        first_seen_at=first_seen_at,
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


def _online_sessions_in_range(server: McServer, time_range: TimeRange):
    return McOnlineSession.filter(
        server=server,
        started_at__lt=time_range.end,
    ).filter(Q(ended_at__gte=time_range.start) | Q(ended_at__isnull=True))


def _segments_from_sessions(
    sessions: list[McOnlineSession],
    time_range: TimeRange,
) -> list[PersonalOnlineSegment]:
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
    return segments


def _build_personal_online_data(
    *,
    title: str,
    range_label: str,
    range_start: datetime,
    range_end: datetime,
    qq_id: str,
    player_names: list[str],
    segments: list[PersonalOnlineSegment],
    time_range: TimeRange,
    first_seen_at: datetime | None,
) -> PersonalOnlineData:
    total_seconds = sum(segment.seconds for segment in segments)
    daily_points = aggregate_daily_online_points(segments, range_start, range_end)
    chart_granularity = (
        "hourly" if business_day_count(range_start, range_end) <= 5 else "daily"
    )
    chart_points = (
        aggregate_hourly_online_points(segments, range_start, range_end)
        if chart_granularity == "hourly"
        else daily_points
    )
    # 个人日均只平均到本范围内最后一次在线日；仍在线的 segment 已被裁剪到查询结束。
    last_seen_at = max((segment.ended_at for segment in segments), default=None)
    return PersonalOnlineData(
        title=title,
        range_label=range_label,
        range_start=range_start,
        range_end=range_end,
        qq_id=qq_id,
        player_names=player_names,
        segments=segments,
        daily_points=daily_points,
        chart_points=chart_points,
        chart_granularity=chart_granularity,
        total_seconds=total_seconds,
        average_seconds=total_seconds
        // average_business_day_count(time_range, first_seen_at, last_seen_at),
    )


async def _first_seen_by_player_names(
    server: McServer,
    player_names: list[str],
) -> dict[str, datetime]:
    names = _unique_non_empty(player_names)
    if not names:
        return {}
    result: dict[str, datetime] = {}
    for session in await (
        McOnlineSession.filter(server=server)
        .filter(_player_name_filter(names))
        .order_by("started_at")
        .all()
    ):
        key = session.player_name.lower()
        if key not in result:
            first_seen = normalize_datetime(session.started_at)
            if first_seen:
                result[key] = first_seen
    return result


async def _first_seen_for_filter(server: McServer, query_filter: Q) -> datetime | None:
    session = (
        await McOnlineSession.filter(server=server)
        .filter(query_filter)
        .order_by("started_at")
        .first()
    )
    return normalize_datetime(session.started_at) if session else None


def _player_name_filter(player_names: list[str]) -> Q:
    names = _unique_non_empty(player_names)
    query_filter = Q(player_name__iexact=names[0])
    for name in names[1:]:
        query_filter |= Q(player_name__iexact=name)
    return query_filter


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
