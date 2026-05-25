from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from zhenxun.plugins.mc_server.stats import (
    aggregate_playtime,
    aggregate_sample_points,
    clip_online_segment,
    overlap_seconds,
)
from zhenxun.plugins.mc_server.types import PlaytimeRow
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
    rows = [
        PlaytimeRow(player_name="Steve", qq_id="1", seconds=60),
        PlaytimeRow(player_name="Alex", qq_id="2", seconds=300),
        PlaytimeRow(player_name="Steve", qq_id="1", seconds=120),
    ]

    entries = aggregate_playtime(rows)

    assert [(entry.player_name, entry.seconds) for entry in entries] == [
        ("Alex", 300),
        ("Steve", 180),
    ]


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
