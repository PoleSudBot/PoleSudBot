from __future__ import annotations

from datetime import datetime, timedelta

from .types import (
    OnlineDurationPoint,
    PersonalOnlineSegment,
    PlaytimeEntry,
    PlaytimeRow,
    SamplePoint,
    TimeRange,
)
from .utils import business_day_count, iter_business_days, normalize_datetime


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
    totals: dict[str, dict[str, object]] = {}
    for row in rows:
        player_name = row.player_name.strip()
        if not player_name:
            continue
        key = player_name.lower()
        first_seen_at = normalize_datetime(row.first_seen_at)
        last_seen_at = normalize_datetime(row.last_seen_at)
        item = totals.setdefault(
            key,
            {
                "player_name": player_name,
                "qq_id": row.qq_id,
                "seconds": 0,
                "first_seen_at": first_seen_at,
                "last_seen_at": last_seen_at,
            },
        )
        item["seconds"] = int(item["seconds"]) + row.seconds
        if row.qq_id and not item["qq_id"]:
            item["qq_id"] = row.qq_id
        # 同一 MC 名在绑定前后可能有不同 qq_id，日均口径要合并最早和最晚可观测在线点。
        if first_seen_at:
            current_first_seen = normalize_datetime(item["first_seen_at"])
            if current_first_seen is None or first_seen_at < current_first_seen:
                item["first_seen_at"] = first_seen_at
        if last_seen_at:
            current_last_seen = normalize_datetime(item["last_seen_at"])
            if current_last_seen is None or last_seen_at > current_last_seen:
                item["last_seen_at"] = last_seen_at
    entries = [
        PlaytimeEntry(
            player_name=str(item["player_name"]),
            qq_id=str(item["qq_id"]),
            seconds=int(item["seconds"]),
            first_seen_at=item["first_seen_at"],
            last_seen_at=item["last_seen_at"],
        )
        for item in totals.values()
        if int(item["seconds"]) > 0
    ]
    return sorted(entries, key=lambda item: (-item.seconds, item.player_name.lower()))


def average_business_day_count(
    time_range: TimeRange,
    first_seen_at: datetime | None,
    last_seen_at: datetime | None,
) -> int:
    range_start = normalize_datetime(time_range.start) or time_range.start
    range_end = normalize_datetime(time_range.end) or time_range.end
    first_seen = normalize_datetime(first_seen_at)
    last_seen = normalize_datetime(last_seen_at)
    average_start = max(range_start, first_seen) if first_seen else range_start
    average_end = min(range_end, last_seen) if last_seen else range_end
    if average_end <= average_start:
        return 1

    # 离开服务器后的日期不再进入分母，避免退坑玩家被后续查询窗口持续稀释。
    return business_day_count(average_start, average_end)


def aggregate_sample_points(rows: list[object]) -> list[SamplePoint]:
    return [
        SamplePoint(
            captured_at=getattr(row, "captured_at"),
            online_count=int(getattr(row, "online_count", 0) or 0),
        )
        for row in rows
    ]


def aggregate_daily_online_points(
    segments: list[PersonalOnlineSegment],
    range_start: datetime,
    range_end: datetime,
) -> list[OnlineDurationPoint]:
    points = []
    for day_range in iter_business_days(range_start, range_end):
        # 日趋势按 06:00 业务日切分，避免跨午夜长会话被硬拆到自然日。
        seconds = sum(
            overlap_seconds(
                segment.started_at,
                segment.ended_at,
                day_range.start,
                day_range.end,
            )
            for segment in segments
        )
        points.append(OnlineDurationPoint(label=day_range.label, seconds=seconds))
    return points


def aggregate_hourly_online_points(
    segments: list[PersonalOnlineSegment],
    range_start: datetime,
    range_end: datetime,
) -> list[OnlineDurationPoint]:
    start = normalize_datetime(range_start)
    end = normalize_datetime(range_end)
    if not start or not end or end <= start:
        return []

    # 小范围个人图按小时分桶，既保留短期细节，也继续用同一套 overlap 裁剪逻辑。
    cursor = start.replace(minute=0, second=0, microsecond=0)
    points = []
    while cursor < end:
        next_cursor = cursor + timedelta(hours=1)
        bucket_start = max(cursor, start)
        bucket_end = min(next_cursor, end)
        if bucket_end > bucket_start:
            seconds = sum(
                overlap_seconds(
                    segment.started_at,
                    segment.ended_at,
                    bucket_start,
                    bucket_end,
                )
                for segment in segments
            )
            points.append(
                OnlineDurationPoint(
                    label=cursor.strftime("%m-%d %H:00"),
                    seconds=seconds,
                )
            )
        cursor = next_cursor
    return points
