from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from zhenxun.plugins.mc_server.stats import (
    active_business_day_count,
    aggregate_daily_online_points,
    aggregate_hourly_online_points,
    aggregate_playtime,
    aggregate_sample_points,
    average_business_day_count,
    clip_online_segment,
    overlap_seconds,
    should_use_hourly_online_points,
)
from zhenxun.plugins.mc_server.types import PlaytimeRow, TimeRange
from zhenxun.plugins.mc_server.utils import MC_TIMEZONE


def test_overlap_seconds_clamps_session_to_requested_window():
    start = datetime(2026, 5, 16, 10, 0, 0)
    end = datetime(2026, 5, 16, 12, 0, 0)
    window_start = datetime(2026, 5, 16, 11, 0, 0)
    window_end = datetime(2026, 5, 16, 13, 0, 0)

    assert overlap_seconds(start, end, window_start, window_end) == 3600
    assert overlap_seconds(start, None, window_start, window_end) == 7200
    assert overlap_seconds(end, start, window_start, window_end) == 0


def test_overlap_seconds_handles_mixed_timezone_awareness():
    start = datetime(2026, 5, 16, 10, 0, 0)
    end = datetime(2026, 5, 16, 12, 0, 0, tzinfo=MC_TIMEZONE)
    window_start = datetime(2026, 5, 16, 1, 0, 0, tzinfo=ZoneInfo("UTC"))
    window_end = datetime(2026, 5, 16, 3, 0, 0, tzinfo=ZoneInfo("UTC"))

    assert overlap_seconds(start, end, window_start, window_end) == 3600


def test_clip_online_segment_clamps_and_ignores_non_overlap():
    start = datetime(2026, 5, 16, 10, 0, 0)
    end = datetime(2026, 5, 16, 14, 0, 0)
    window_start = datetime(2026, 5, 16, 11, 0, 0)
    window_end = datetime(2026, 5, 16, 13, 0, 0)

    segment = clip_online_segment(start, end, window_start, window_end)

    assert segment is not None
    assert segment.started_at == window_start.replace(tzinfo=MC_TIMEZONE)
    assert segment.ended_at == window_end.replace(tzinfo=MC_TIMEZONE)
    assert segment.seconds == 7200
    assert clip_online_segment(end, start, window_start, window_end) is None


def test_clip_online_segment_truncates_active_session_to_range_end():
    start = datetime(2026, 5, 16, 10, 0, 0)
    window_start = datetime(2026, 5, 16, 9, 0, 0)
    window_end = datetime(2026, 5, 16, 11, 30, 0)

    segment = clip_online_segment(start, None, window_start, window_end)

    assert segment is not None
    assert segment.started_at == start.replace(tzinfo=MC_TIMEZONE)
    assert segment.ended_at == window_end.replace(tzinfo=MC_TIMEZONE)
    assert segment.seconds == 5400


def test_aggregate_playtime_sums_same_player_and_sorts_desc():
    first_seen = datetime(2026, 5, 16, 10, 0, 0, tzinfo=MC_TIMEZONE)
    last_seen = datetime(2026, 5, 17, 23, 0, 0, tzinfo=MC_TIMEZONE)
    rows = [
        PlaytimeRow(
            player_name="Steve",
            qq_id="",
            seconds=60,
            first_seen_at=first_seen,
            last_seen_at=last_seen - timedelta(days=1),
        ),
        PlaytimeRow(player_name="Alex", qq_id="2", seconds=300),
        PlaytimeRow(
            player_name="Steve",
            qq_id="1",
            seconds=120,
            first_seen_at=first_seen + timedelta(hours=1),
            last_seen_at=last_seen,
        ),
    ]

    entries = aggregate_playtime(rows)

    assert [(entry.player_name, entry.seconds) for entry in entries] == [
        ("Alex", 300),
        ("Steve", 180),
    ]
    assert entries[1].qq_id == "1"
    assert entries[1].first_seen_at == first_seen
    assert entries[1].last_seen_at == last_seen


def test_aggregate_playtime_merges_active_days_for_average_denominator():
    rows = [
        PlaytimeRow(
            player_name="Steve",
            qq_id="",
            seconds=3600,
            active_day_labels={"06-01"},
        ),
        PlaytimeRow(
            player_name="steve",
            qq_id="10000",
            seconds=7200,
            active_day_labels={"06-03"},
        ),
    ]

    entries = aggregate_playtime(rows)

    assert len(entries) == 1
    assert entries[0].seconds == 10800
    assert entries[0].active_day_count == 2


def test_aggregate_playtime_merges_same_player_across_qq_changes():
    last_seen = datetime(2026, 5, 18, 20, 0, 0, tzinfo=MC_TIMEZONE)
    rows = [
        PlaytimeRow(player_name="Steve", qq_id="", seconds=600),
        PlaytimeRow(
            player_name="steve",
            qq_id="10000",
            seconds=900,
            last_seen_at=last_seen,
        ),
    ]

    entries = aggregate_playtime(rows)

    assert len(entries) == 1
    assert entries[0].player_name == "Steve"
    assert entries[0].qq_id == "10000"
    assert entries[0].seconds == 1500
    assert entries[0].last_seen_at == last_seen


def test_aggregate_sample_points_normalizes_row_objects():
    captured_at = datetime(2026, 5, 16, 20, 0, 0)
    rows = [
        SimpleNamespace(captured_at=captured_at, online_count="3"),
        SimpleNamespace(captured_at=captured_at + timedelta(minutes=5)),
    ]

    points = aggregate_sample_points(rows)

    assert points[0].captured_at == captured_at
    assert points[0].online_count == 3
    assert points[1].online_count == 0


def test_aggregate_hourly_online_points_keeps_short_range_precision():
    start = datetime(2026, 5, 16, 6, 0, 0, tzinfo=MC_TIMEZONE)
    end = datetime(2026, 5, 16, 9, 0, 0, tzinfo=MC_TIMEZONE)
    segments = [
        clip_online_segment(
            datetime(2026, 5, 16, 6, 30, 0, tzinfo=MC_TIMEZONE),
            datetime(2026, 5, 16, 8, 15, 0, tzinfo=MC_TIMEZONE),
            start,
            end,
        )
    ]

    points = aggregate_hourly_online_points(
        [item for item in segments if item],
        start,
        end,
    )

    assert [(point.label, point.seconds) for point in points] == [
        ("05-16 06:00", 1800),
        ("05-16 07:00", 3600),
        ("05-16 08:00", 900),
    ]




def test_aggregate_daily_online_points_uses_rolling_day_buckets_with_zero_fill():
    end = datetime(2026, 6, 9, 20, 0, 0, tzinfo=MC_TIMEZONE)
    time_range = TimeRange(
        "7d",
        end - timedelta(days=7),
        end,
        end_is_current=True,
        bucket_mode="rolling",
    )

    points = aggregate_daily_online_points([], time_range)

    assert len(points) == 7
    assert [point.label for point in points] == [
        "06-03",
        "06-04",
        "06-05",
        "06-06",
        "06-07",
        "06-08",
        "06-09",
    ]
    assert [point.seconds for point in points] == [0, 0, 0, 0, 0, 0, 0]


def test_should_use_hourly_online_points_uses_real_72_hour_threshold():
    end = datetime(2026, 6, 9, 20, 0, 0, tzinfo=MC_TIMEZONE)
    three_days = TimeRange("3d", end - timedelta(hours=72), end)
    over_three_days = TimeRange("3d+", end - timedelta(hours=72, seconds=1), end)

    assert should_use_hourly_online_points(three_days)
    assert not should_use_hourly_online_points(over_three_days)


def test_active_business_day_count_uses_only_days_with_real_playtime():
    range_start = datetime(2026, 6, 1, 6, 0, 0, tzinfo=MC_TIMEZONE)
    range_end = datetime(2026, 6, 8, 6, 0, 0, tzinfo=MC_TIMEZONE)
    segments = [
        clip_online_segment(
            datetime(2026, 6, 1, 10, 0, 0, tzinfo=MC_TIMEZONE),
            datetime(2026, 6, 1, 12, 0, 0, tzinfo=MC_TIMEZONE),
            range_start,
            range_end,
        ),
        clip_online_segment(
            datetime(2026, 6, 3, 20, 0, 0, tzinfo=MC_TIMEZONE),
            datetime(2026, 6, 3, 22, 0, 0, tzinfo=MC_TIMEZONE),
            range_start,
            range_end,
        ),
    ]

    valid_segments = [item for item in segments if item]

    assert active_business_day_count(valid_segments, range_start, range_end) == 2


def test_average_business_day_count_stops_at_last_seen_day():
    time_range = TimeRange(
        "本周",
        datetime(2026, 6, 1, 6, 0, 0, tzinfo=MC_TIMEZONE),
        datetime(2026, 6, 8, 12, 0, 0, tzinfo=MC_TIMEZONE),
    )

    day_count = average_business_day_count(
        time_range,
        datetime(2026, 6, 1, 10, 0, 0, tzinfo=MC_TIMEZONE),
        datetime(2026, 6, 3, 23, 0, 0, tzinfo=MC_TIMEZONE),
    )

    assert day_count == 3


def test_average_business_day_count_uses_first_and_last_observed_days():
    time_range = TimeRange(
        "本周",
        datetime(2026, 6, 1, 6, 0, 0, tzinfo=MC_TIMEZONE),
        datetime(2026, 6, 8, 12, 0, 0, tzinfo=MC_TIMEZONE),
    )

    day_count = average_business_day_count(
        time_range,
        datetime(2026, 6, 3, 10, 0, 0, tzinfo=MC_TIMEZONE),
        datetime(2026, 6, 3, 23, 0, 0, tzinfo=MC_TIMEZONE),
    )

    assert day_count == 1
