from __future__ import annotations

from datetime import datetime

from .types import PersonalOnlineSegment, PlaytimeEntry, PlaytimeRow, SamplePoint
from .utils import normalize_datetime


def overlap_seconds(
    start: datetime,
    end: datetime | None,
    range_start: datetime,
    range_end: datetime,
) -> int:
    normalized_start = normalize_datetime(start)
    normalized_end = normalize_datetime(end) or normalize_datetime(range_end)
    normalized_range_start = normalize_datetime(range_start)
    normalized_range_end = normalize_datetime(range_end)
    if not (normalized_start and normalized_end and normalized_range_start):
        return 0
    if not normalized_range_end:
        return 0
    actual_end = normalized_end
    left = max(normalized_start, normalized_range_start)
    right = min(actual_end, normalized_range_end)
    if right <= left:
        return 0
    return int((right - left).total_seconds())


def clip_online_segment(
    start: datetime,
    end: datetime | None,
    range_start: datetime,
    range_end: datetime,
) -> PersonalOnlineSegment | None:
    normalized_start = normalize_datetime(start)
    normalized_end = normalize_datetime(end) or normalize_datetime(range_end)
    normalized_range_start = normalize_datetime(range_start)
    normalized_range_end = normalize_datetime(range_end)
    if not (
        normalized_start
        and normalized_end
        and normalized_range_start
        and normalized_range_end
    ):
        return None

    # 会话可能跨越查询窗口，个人图只展示窗口内真实可见的一段。
    clipped_start = max(normalized_start, normalized_range_start)
    clipped_end = min(normalized_end, normalized_range_end)
    if clipped_end <= clipped_start:
        return None
    return PersonalOnlineSegment(
        started_at=clipped_start,
        ended_at=clipped_end,
        seconds=int((clipped_end - clipped_start).total_seconds()),
    )


def aggregate_playtime(rows: list[PlaytimeRow]) -> list[PlaytimeEntry]:
    totals: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.player_name, row.qq_id)
        totals[key] = totals.get(key, 0) + row.seconds
    entries = [
        PlaytimeEntry(player_name=player_name, qq_id=qq_id, seconds=seconds)
        for (player_name, qq_id), seconds in totals.items()
        if player_name and seconds > 0
    ]
    return sorted(entries, key=lambda item: (-item.seconds, item.player_name.lower()))


def aggregate_sample_points(rows: list[object]) -> list[SamplePoint]:
    return [
        SamplePoint(
            captured_at=getattr(row, "captured_at"),
            online_count=int(getattr(row, "online_count", 0) or 0),
        )
        for row in rows
    ]
