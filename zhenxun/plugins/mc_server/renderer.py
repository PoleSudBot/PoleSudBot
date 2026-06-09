from __future__ import annotations

from pathlib import Path

from zhenxun.services.log import logger
from zhenxun.utils.exception import RenderingError

from .constants import MODULE_NAME
from .types import (
    ChartData,
    PersonalOnlineData,
    PlaytimeEntry,
    RenderedMessage,
    ServerStatus,
    TimeRange,
)
from .utils import (
    downsample_points,
    format_duration,
    format_time_range_hint,
    should_show_today_online,
)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATUS_TEMPLATE = _TEMPLATE_DIR / "status_card.html"
_CHART_TEMPLATE = _TEMPLATE_DIR / "chart_card.html"
_TIME_TEMPLATE = _TEMPLATE_DIR / "time_card.html"
_PERSONAL_TEMPLATE = _TEMPLATE_DIR / "personal_online_card.html"


def _get_settings():
    from .config import get_settings

    return get_settings()


def format_status_text(status: ServerStatus) -> str:
    if not status.online:
        return f"{_display_title(status.name)} 离线：{status.error or '无法连接'}"
    lines = [
        f"{_display_title(status.name)} ({status.address})",
        (
            f"状态：在线  延迟：{status.latency_ms:.0f}ms"
            if status.latency_ms is not None
            else "状态：在线"
        ),
        f"版本：{status.version or '未知'}",
        f"人数：{status.online_players}/{status.max_players}",
    ]
    if status.players:
        lines.append("在线玩家：")
        for player in status.players:
            detail = player.name
            if player.position:
                detail += f"  {player.position}"
            if player.online_seconds:
                detail += f"  当前{format_duration(player.online_seconds)}"
            if player.total_seconds:
                detail += f"  累计{format_duration(player.total_seconds)}"
            lines.append(f"- {detail}")
    return "\n".join(lines)


def format_playtime_text(
    title: str,
    entries: list[PlaytimeEntry],
    time_range: TimeRange,
) -> str:
    show_today = should_show_today_online(time_range)
    range_hint = format_time_range_hint(time_range)
    if not entries:
        return f"{title}\n时间：{range_hint}\n暂无在线时长记录。"
    lines = [title, f"时间：{range_hint}"]
    for index, entry in enumerate(entries, 1):
        today_text = (
            f"，本日在线 {format_duration(entry.today_seconds)}"
            if show_today
            else ""
        )
        lines.append(
            f"{index}. {entry.player_name}：{format_duration(entry.seconds)}"
            f"（日均 {format_duration(entry.average_seconds)}{today_text}）"
        )
    return "\n".join(lines)


def format_chart_text(data: ChartData) -> str:
    range_hint = (
        _range_hint(
            data.range_label,
            data.range_start,
            data.range_end,
            end_is_current=data.range_end_is_current,
        )
        if data.range_start and data.range_end
        else data.range_label
    )
    if not data.points:
        return f"{data.title}\n时间：{range_hint}\n暂无人数采样。"
    return (
        f"{data.title}\n"
        f"范围：{data.range_label}\n"
        f"时间：{range_hint}\n"
        f"采样：{len(data.points)} 个"
    )


def chart_summary(points: list) -> tuple[int, float]:
    if not points:
        return 0, 0
    peak = max(point.online_count for point in points)
    avg = sum(point.online_count for point in points) / len(points)
    return peak, avg


def _timestamp_ms(point_time) -> int:
    # 时间轴按真实采样时间定位，传毫秒时间戳可避免 category 轴把稀疏采样均分。
    return int(point_time.timestamp() * 1000)


def _chart_payload(data: ChartData, max_points: int) -> dict:
    points = downsample_points(data.points, max_points)
    return {
        "sample_count": len(data.points),
        "labels": [point.captured_at.strftime("%m-%d %H:%M") for point in points],
        "counts": [point.online_count for point in points],
        "series_points": [
            [_timestamp_ms(point.captured_at), point.online_count] for point in points
        ],
        "points": [
            {
                "timestamp": _timestamp_ms(point.captured_at),
                "tooltip": point.captured_at.strftime("%m-%d %H:%M"),
                "count": point.online_count,
            }
            for point in points
        ],
        "title": data.title,
        "range_label": data.range_label,
        "range_hint": _range_hint(
            data.range_label,
            data.range_start,
            data.range_end,
            end_is_current=data.range_end_is_current,
        )
        if data.range_start and data.range_end
        else data.range_label,
    }


def _format_point_label(index: int, total: int, label: str) -> str:
    if total <= 1 or index in {0, total - 1}:
        return label
    if index == total // 2:
        return label
    return ""


def _display_title(name: str) -> str:
    text = str(name or "").strip()
    if not text or text == "默认服务器":
        return "MC 服务器"
    return text


def _range_hint(
    label: str,
    start,
    end,
    *,
    end_is_current: bool = False,
) -> str:
    return format_time_range_hint(
        TimeRange(label, start, end, end_is_current=end_is_current)
    )


def format_personal_online_text(data: PersonalOnlineData) -> str:
    player_label = (
        "、".join(data.player_names) if data.player_names else f"QQ {data.qq_id}"
    )
    lines = [
        data.title,
        f"范围：{data.range_label}",
        "时间："
        + _range_hint(
            data.range_label,
            data.range_start,
            data.range_end,
            end_is_current=data.range_end_is_current,
        ),
        f"玩家：{player_label}",
        f"总时长：{format_duration(data.total_seconds)}",
        f"日均：{format_duration(data.average_seconds)}",
    ]
    if data.show_today_online:
        lines.append(f"本日在线：{format_duration(data.today_seconds)}")
    if not data.segments:
        lines.append("暂无在线记录。")
    else:
        granularity = "小时级" if data.chart_granularity == "hourly" else "每日"
        lines.append(f"图表：{granularity}在线时长")
    return "\n".join(lines)


async def render_status(
    status: ServerStatus,
    count_trend: ChartData | None = None,
) -> RenderedMessage:
    fallback = format_status_text(status)
    settings = _get_settings()
    if not settings.render_enabled:
        return RenderedMessage(image=None, fallback_text=fallback)
    try:
        return RenderedMessage(
            image=await _render_template(
                _STATUS_TEMPLATE,
                {
                    "status": status,
                    "display_title": _display_title(status.name),
                    "address": status.address,
                    "players": [
                        {
                            "name": player.name,
                            "position": player.position,
                            "online": format_duration(player.online_seconds)
                            if player.online_seconds
                            else "",
                            "total": format_duration(player.total_seconds)
                            if player.total_seconds
                            else "",
                        }
                        for player in status.players
                    ],
                    "count_trend": _chart_payload(
                        count_trend,
                        getattr(settings, "max_chart_points", 80),
                    )
                    if count_trend
                    else None,
                },
                viewport_width=820,
            ),
            fallback_text=fallback,
        )
    except RenderingError as exc:
        logger.warning("MC状态卡片渲染失败，回退文字", MODULE_NAME, e=exc)
        return RenderedMessage(image=None, fallback_text=fallback)


async def render_playtime(
    title: str,
    entries: list[PlaytimeEntry],
    time_range: TimeRange,
) -> RenderedMessage:
    fallback = format_playtime_text(title, entries, time_range)
    if not _get_settings().render_enabled:
        return RenderedMessage(image=None, fallback_text=fallback)
    show_today = should_show_today_online(time_range)
    try:
        return RenderedMessage(
            image=await _render_template(
                _TIME_TEMPLATE,
                {
                    "title": title,
                    "range_label": time_range.label,
                    "range_hint": format_time_range_hint(time_range),
                    "show_today_online": show_today,
                    "items": [
                        {
                            "rank": index,
                            "name": entry.player_name,
                            "duration": format_duration(entry.seconds),
                            "average_duration": format_duration(
                                entry.average_seconds
                            ),
                            "today_duration": format_duration(entry.today_seconds),
                            "seconds": entry.seconds,
                        }
                        for index, entry in enumerate(entries[:20], 1)
                    ],
                },
                viewport_width=720,
            ),
            fallback_text=fallback,
        )
    except RenderingError as exc:
        logger.warning("MC在线时长卡片渲染失败，回退文字", MODULE_NAME, e=exc)
        return RenderedMessage(image=None, fallback_text=fallback)


async def render_chart(data: ChartData) -> RenderedMessage:
    fallback = format_chart_text(data)
    settings = _get_settings()
    if not settings.render_enabled:
        return RenderedMessage(image=None, fallback_text=fallback)
    max_points = settings.max_chart_points
    chart_payload = _chart_payload(data, max_points)
    try:
        return RenderedMessage(
            image=await _render_template(
                _CHART_TEMPLATE,
                chart_payload,
                viewport_width=820,
            ),
            fallback_text=fallback,
        )
    except RenderingError as exc:
        logger.warning("MC人数图渲染失败，回退文字", MODULE_NAME, e=exc)
        return RenderedMessage(image=None, fallback_text=fallback)


async def render_personal_online(data: PersonalOnlineData) -> RenderedMessage:
    fallback = format_personal_online_text(data)
    if not _get_settings().render_enabled:
        return RenderedMessage(image=None, fallback_text=fallback)
    chart_points = data.chart_points or data.daily_points
    chart_seconds = [point.seconds for point in chart_points]
    is_hourly = data.chart_granularity == "hourly"
    try:
        return RenderedMessage(
            image=await _render_template(
                _PERSONAL_TEMPLATE,
                {
                    "title": data.title,
                    "range_label": data.range_label,
                    "range_hint": _range_hint(
                        data.range_label,
                        data.range_start,
                        data.range_end,
                        end_is_current=data.range_end_is_current,
                    ),
                    "player_label": "、".join(data.player_names)
                    if data.player_names
                    else f"QQ {data.qq_id}",
                    "total_duration": format_duration(data.total_seconds),
                    "average_duration": format_duration(data.average_seconds),
                    "today_duration": format_duration(data.today_seconds),
                    "show_today_online": data.show_today_online,
                    "stat_grid_class": "four"
                    if data.show_today_online
                    else "three",
                    "chart_granularity": data.chart_granularity,
                    "chart_granularity_label": "小时级"
                    if data.chart_granularity == "hourly"
                    else "每日",
                    "has_chart": bool(chart_points),
                    "has_online": bool(data.segments),
                    "chart_axis_label_rotate": 55 if is_hourly else 45,
                    "chart_axis_label_font_size": 9 if is_hourly else 10,
                    "chart_grid_bottom": 76 if is_hourly else 68,
                    "chart_labels": [point.label for point in chart_points],
                    "chart_values": chart_seconds,
                    "chart_durations": [
                        format_duration(seconds) for seconds in chart_seconds
                    ],
                    "range_start": data.range_start.strftime("%m-%d %H:%M"),
                    "range_end": data.range_end.strftime("%m-%d %H:%M"),
                },
                viewport_width=820,
            ),
            fallback_text=fallback,
        )
    except RenderingError as exc:
        logger.warning("MC个人在线图渲染失败，回退文字", MODULE_NAME, e=exc)
        return RenderedMessage(image=None, fallback_text=fallback)


async def _render_template(path: Path, payload: dict, *, viewport_width: int) -> bytes:
    from zhenxun import ui

    return await ui.render_template(
        path,
        payload,
        use_cache=False,
        is_page=True,
        viewport={"width": viewport_width, "height": 10},
        device_scale_factor=2,
    )
