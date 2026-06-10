from __future__ import annotations

from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


class _LoggerStub:
    def info(self, *_args, **_kwargs):
        return None

    def debug(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


# 渲染单测只验证 mc_server 的格式化与失败回退，同时保留子模块导入能力。
services_module = ModuleType("zhenxun.services")
services_module.__path__ = [
    str(Path(__file__).resolve().parents[1] / "zhenxun" / "services")
]
sys.modules.setdefault("zhenxun.services", services_module)
sys.modules.setdefault("zhenxun.services.log", SimpleNamespace(logger=_LoggerStub()))

renderer = import_module("zhenxun.plugins.mc_server.renderer")
mc_types = import_module("zhenxun.plugins.mc_server.types")
exceptions = import_module("zhenxun.utils.exception")

PlayerStatus = mc_types.PlayerStatus
PersonalOnlineData = mc_types.PersonalOnlineData
PersonalOnlineSegment = mc_types.PersonalOnlineSegment
OnlineDurationPoint = mc_types.OnlineDurationPoint
ServerStatus = mc_types.ServerStatus
ChartData = mc_types.ChartData
SamplePoint = mc_types.SamplePoint
PlaytimeEntry = mc_types.PlaytimeEntry
TimeRange = mc_types.TimeRange
RenderingError = exceptions.RenderingError
chart_summary = renderer.chart_summary
format_personal_online_text = renderer.format_personal_online_text
format_status_text = renderer.format_status_text
format_playtime_text = renderer.format_playtime_text
render_playtime = renderer.render_playtime
render_chart = renderer.render_chart
render_personal_online = renderer.render_personal_online
render_status = renderer.render_status


@pytest.fixture(autouse=True)
def _stub_player_heads(monkeypatch: pytest.MonkeyPatch):
    async def fake_player_head_uri_map(players):
        return {
            player.name.lower(): f"head://{player.name.lower()}"
            for player in players
        }

    monkeypatch.setattr(renderer, "_player_head_uri_map", fake_player_head_uri_map)


def _status() -> ServerStatus:
    return ServerStatus(
        name="主服",
        address="mc.example.com:25565",
        online=True,
        latency_ms=12.3,
        version="1.21.6",
        online_players=1,
        max_players=20,
        players=[
            PlayerStatus(
                name="Steve",
                uuid="uuid-steve",
                online_seconds=3660,
                total_seconds=9000,
                position="world (1.0, 64.0, 2.0)",
            )
        ],
    )


def _personal_online_data() -> PersonalOnlineData:
    start = datetime(2026, 5, 16, 10, 0, 0)
    end = start + timedelta(hours=3)
    segment = PersonalOnlineSegment(
        started_at=start,
        ended_at=start + timedelta(hours=1, minutes=30),
        seconds=5400,
    )
    return PersonalOnlineData(
        title="主服 今日 个人在线情况",
        range_label="今日",
        range_start=start,
        range_end=end,
        range_end_is_current=True,
        qq_id="10000",
        player_names=["Steve"],
        segments=[segment],
        daily_points=[OnlineDurationPoint(label="05-16", seconds=segment.seconds)],
        chart_points=[
            OnlineDurationPoint(label="05-16 10:00", seconds=3600),
            OnlineDurationPoint(label="05-16 11:00", seconds=1800),
        ],
        chart_granularity="hourly",
        total_seconds=segment.seconds,
        average_seconds=segment.seconds,
    )


def test_format_status_text_contains_core_fields():
    text = format_status_text(_status())

    assert "主服 (mc.example.com:25565)" in text
    assert "延迟：12ms" in text
    assert "人数：1/20" in text
    assert "Steve" in text
    assert "world (1.0, 64.0, 2.0)" in text
    assert "当前1小时01分" in text
    assert "累计2小时30分" in text
    assert "天气" not in text


def test_format_personal_online_text_contains_core_fields():
    text = format_personal_online_text(_personal_online_data())

    assert "主服 今日 个人在线情况" in text
    assert "玩家：Steve" in text
    assert "总时长：1小时30分" in text
    assert "日均：1小时30分" in text
    assert "图表：小时级在线时长" in text
    assert "在线段" not in text


def test_format_personal_online_text_handles_empty_state():
    data = _personal_online_data()
    empty = PersonalOnlineData(
        title=data.title,
        range_label=data.range_label,
        range_start=data.range_start,
        range_end=data.range_end,
        range_end_is_current=data.range_end_is_current,
        qq_id=data.qq_id,
    )

    text = format_personal_online_text(empty)

    assert "总时长：0分钟" in text
    assert "日均：0分钟" in text
    assert "暂无在线记录" in text


def test_chart_summary_uses_all_points():
    points = [
        SamplePoint(captured_at=datetime(2026, 5, 16, 10, 0, 0), online_count=1),
        SamplePoint(captured_at=datetime(2026, 5, 16, 11, 0, 0), online_count=9),
        SamplePoint(captured_at=datetime(2026, 5, 16, 12, 0, 0), online_count=2),
    ]

    assert chart_summary(points) == (9, 4)


def test_format_playtime_text_contains_daily_average():
    entries = [
        PlaytimeEntry(player_name="Steve", seconds=7200, average_seconds=3600)
    ]
    time_range = TimeRange(
        "今日",
        datetime(2026, 5, 16, 6, 0, 0),
        datetime(2026, 5, 16, 12, 0, 0),
        end_is_current=True,
    )

    text = format_playtime_text("主服 今日 在线时长", entries, time_range)

    assert "时间：26-05-16 06:00 ~ " in text
    assert "Steve：2小时00分（日均 1小时00分）" in text


@pytest.mark.asyncio
async def test_render_status_falls_back_to_text_when_template_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    import zhenxun

    async def fake_render_template(*_args, **_kwargs):
        raise RenderingError("render failed")

    fake_ui = SimpleNamespace(render_template=fake_render_template)
    monkeypatch.setitem(sys.modules, "zhenxun.ui", fake_ui)
    monkeypatch.setattr(zhenxun, "ui", fake_ui, raising=False)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True),
    )

    result = await render_status(_status())

    assert result.image is None
    assert result.fallback_text == format_status_text(_status())



@pytest.mark.asyncio
async def test_render_status_passes_count_trend_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_payload = {}

    async def fake_render_template(_path, payload, **_kwargs):
        captured_payload.update(payload)
        return b"image"

    monkeypatch.setattr(renderer, "_render_template", fake_render_template)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True, max_chart_points=2),
    )
    trend = ChartData(
        title="主服 过去24h 在线人数",
        range_label="24h",
        range_start=datetime(2026, 5, 15, 12, 0, 0),
        range_end=datetime(2026, 5, 16, 12, 0, 0),
        range_end_is_current=True,
        points=[
            SamplePoint(datetime(2026, 5, 15, 12, 0, 0), 1),
            SamplePoint(datetime(2026, 5, 16, 0, 0, 0), 3),
            SamplePoint(datetime(2026, 5, 16, 12, 0, 0), 2),
        ],
    )

    result = await render_status(_status(), trend)

    assert result.image == b"image"
    assert captured_payload["players"][0]["head_uri"] == "head://steve"
    assert captured_payload["count_trend"]["sample_count"] == 3
    assert captured_payload["count_trend"]["labels"] == [
        "05-15 12:00",
        "05-16 12:00",
    ]
    assert captured_payload["count_trend"]["counts"] == [1, 2]
    assert captured_payload["count_trend"]["series_points"] == [
        [int(trend.points[0].captured_at.timestamp() * 1000), 1],
        [int(trend.points[-1].captured_at.timestamp() * 1000), 2],
    ]


@pytest.mark.asyncio
async def test_render_personal_online_falls_back_to_text_when_template_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    import zhenxun

    async def fake_render_template(*_args, **_kwargs):
        raise RenderingError("render failed")

    fake_ui = SimpleNamespace(render_template=fake_render_template)
    monkeypatch.setitem(sys.modules, "zhenxun.ui", fake_ui)
    monkeypatch.setattr(zhenxun, "ui", fake_ui, raising=False)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True),
    )

    data = _personal_online_data()
    result = await render_personal_online(data)

    assert result.image is None
    assert result.fallback_text == format_personal_online_text(data)



@pytest.mark.asyncio
async def test_render_personal_online_keeps_zero_chart_without_segments(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_payload = {}

    async def fake_render_template(_path, payload, **_kwargs):
        captured_payload.update(payload)
        return b"image"

    monkeypatch.setattr(renderer, "_render_template", fake_render_template)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True),
    )
    data = _personal_online_data()
    empty = PersonalOnlineData(
        title="主服 7d 个人在线情况",
        range_label="7d",
        range_start=data.range_start,
        range_end=data.range_end,
        range_end_is_current=True,
        qq_id="10000",
        chart_points=[
            OnlineDurationPoint(label="05-15", seconds=0),
            OnlineDurationPoint(label="05-16", seconds=0),
        ],
        chart_granularity="daily",
    )

    result = await render_personal_online(empty)

    assert result.image == b"image"
    assert captured_payload["has_chart"] is True
    assert captured_payload["has_online"] is False
    assert captured_payload["chart_values"] == [0, 0]
    assert captured_payload["chart_axis_label_rotate"] == 45
    assert captured_payload["chart_axis_label_font_size"] == 10
    assert captured_payload["chart_grid_bottom"] == 68



def test_time_axis_templates_rotate_bottom_labels():
    chart_template = (renderer._TEMPLATE_DIR / "chart_card.html").read_text(
        encoding="utf-8"
    )
    status_template = (renderer._TEMPLATE_DIR / "status_card.html").read_text(
        encoding="utf-8"
    )
    personal_template = (
        renderer._TEMPLATE_DIR / "personal_online_card.html"
    ).read_text(encoding="utf-8")

    assert "rotate: 45" in chart_template
    assert "bottom: 68" in chart_template
    assert 'type: "time"' in chart_template
    assert "series_points" in chart_template
    assert "rotate: 45" in status_template
    assert "bottom: 68" in status_template
    assert 'type: "time"' in status_template
    assert "series_points" in status_template
    assert "rotate: {{ chart_axis_label_rotate }}" in personal_template
    assert 'type: "category"' in personal_template
    assert 'align: "right"' in personal_template
    assert "alignWithLabel: true" in chart_template
    assert "alignWithLabel: true" in status_template
    assert "alignWithLabel: true" in personal_template
    assert "length: 6" in chart_template
    assert "length: 6" in status_template
    assert "length: 6" in personal_template
    assert "inside: true" in chart_template
    assert "inside: true" in status_template
    assert "inside: true" in personal_template


@pytest.mark.asyncio
async def test_render_chart_summary_uses_full_points_after_downsample(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_payload = {}
    points = [
        SamplePoint(captured_at=datetime(2026, 5, 16, hour, 0, 0), online_count=count)
        for hour, count in enumerate([1, 99, 2, 3, 4])
    ]

    async def fake_render_template(_path, payload, **_kwargs):
        captured_payload.update(payload)
        return b"image"

    monkeypatch.setattr(renderer, "_render_template", fake_render_template)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True, max_chart_points=3),
    )

    result = await render_chart(
        ChartData(
            title="人数图",
            range_label="今日",
            range_start=datetime(2026, 5, 16, 6, 0, 0),
            range_end=datetime(2026, 5, 16, 12, 0, 0),
            range_end_is_current=True,
            points=points,
        )
    )

    assert result.image == b"image"
    assert captured_payload["range_hint"] == "26-05-16 06:00 ~ "
    assert "peak" not in captured_payload
    assert "avg" not in captured_payload
    assert captured_payload["sample_count"] == 5
    assert captured_payload["counts"][0] == 1
    assert captured_payload["counts"][-1] == 4
    assert captured_payload["series_points"][0] == [
        int(points[0].captured_at.timestamp() * 1000),
        1,
    ]
    assert captured_payload["series_points"][-1] == [
        int(points[-1].captured_at.timestamp() * 1000),
        4,
    ]
    rendered_counts = [point["count"] for point in captured_payload["points"]]
    assert rendered_counts[0] == 1
    assert rendered_counts[-1] == 4


@pytest.mark.asyncio
async def test_render_playtime_includes_average_duration(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_payload = {}

    async def fake_render_template(_path, payload, **_kwargs):
        captured_payload.update(payload)
        return b"image"

    monkeypatch.setattr(renderer, "_render_template", fake_render_template)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True),
    )

    result = await render_playtime(
        "在线时长",
        [
            PlaytimeEntry(
                player_name="Steve",
                seconds=7200,
                average_seconds=3600,
                today_seconds=1800,
            )
        ],
        TimeRange(
            "本周",
            datetime(2026, 5, 11, 6, 0, 0),
            datetime(2026, 5, 16, 12, 0, 0),
            end_is_current=True,
        ),
    )

    assert result.image == b"image"
    assert captured_payload["range_hint"] == "26-05-11 06:00 ~ "
    assert captured_payload["show_today_online"] is True
    assert captured_payload["items"][0]["head_uri"] == "head://steve"
    assert captured_payload["items"][0]["average_duration"] == "1小时00分"
    assert captured_payload["items"][0]["today_duration"] == "30分钟"


@pytest.mark.asyncio
async def test_render_personal_online_passes_chart_payload_without_segments(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_payload = {}

    async def fake_render_template(_path, payload, **_kwargs):
        captured_payload.update(payload)
        return b"image"

    monkeypatch.setattr(renderer, "_render_template", fake_render_template)
    monkeypatch.setattr(
        renderer,
        "_get_settings",
        lambda: SimpleNamespace(render_enabled=True),
    )

    result = await render_personal_online(_personal_online_data())

    assert result.image == b"image"
    assert captured_payload["range_hint"] == "26-05-16 10:00 ~ "
    assert captured_payload["player_heads"] == [
        {"name": "Steve", "head_uri": "head://steve"}
    ]
    assert captured_payload["show_today_online"] is False
    assert captured_payload["average_duration"] == "1小时30分"
    assert captured_payload["chart_granularity"] == "hourly"
    assert captured_payload["chart_labels"] == ["05-16 10:00", "05-16 11:00"]
    assert captured_payload["chart_values"] == [3600, 1800]
    assert captured_payload["chart_durations"] == ["1小时00分", "30分钟"]
    assert captured_payload["chart_axis_label_rotate"] == 55
    assert captured_payload["chart_axis_label_font_size"] == 9
    assert captured_payload["chart_grid_bottom"] == 76
    assert "segment_rows" not in captured_payload
