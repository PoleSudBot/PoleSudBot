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
)
from .utils import downsample_points, format_duration

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
        return f"{status.name} 离线：{status.error or '无法连接'}"
    lines = [
        f"{status.name} ({status.address})",
        (
            f"状态：在线  延迟：{status.latency_ms:.0f}ms"
            if status.latency_ms is not None
            else "状态：在线"
        ),
        f"版本：{status.version or '未知'}",
        f"人数：{status.online_players}/{status.max_players}",
        f"天气：{status.weather}",
    ]
    if status.players:
        lines.append("在线玩家：")
        for player in status.players:
            detail = player.name
            if player.position:
                detail += f"  {player.position}"
            if player.online_seconds:
                detail += f"  在线{format_duration(player.online_seconds)}"
            lines.append(f"- {detail}")
    return "\n".join(lines)


def format_playtime_text(title: str, entries: list[PlaytimeEntry]) -> str:
    if not entries:
        return f"{title}\n暂无在线时长记录。"
    lines = [title]
    for index, entry in enumerate(entries, 1):
        lines.append(f"{index}. {entry.player_name}：{format_duration(entry.seconds)}")
    return "\n".join(lines)


def format_chart_text(data: ChartData) -> str:
    if not data.points:
        return f"{data.title}\n{data.range_label} 暂无人数采样。"
    peak, avg = chart_summary(data.points)
    return (
        f"{data.title}\n"
        f"范围：{data.range_label}\n"
        f"采样：{len(data.points)} 个  峰值：{peak}  平均：{avg:.1f}"
    )


def chart_summary(points: list) -> tuple[int, float]:
    if not points:
        return 0, 0
    peak = max(point.online_count for point in points)
    avg = sum(point.online_count for point in points) / len(points)
    return peak, avg


def _format_point_label(index: int, total: int, label: str) -> str:
    if total <= 1 or index in {0, total - 1}:
        return label
    if index == total // 2:
        return label
    return ""


def format_personal_online_text(data: PersonalOnlineData) -> str:
    player_label = (
        "、".join(data.player_names) if data.player_names else f"QQ {data.qq_id}"
    )
    lines = [
        data.title,
        f"范围：{data.range_label}",
        f"玩家：{player_label}",
        f"总时长：{format_duration(data.total_seconds)}",
        f"在线段：{len(data.segments)} 段",
    ]
    if not data.segments:
        lines.append("暂无在线记录。")
        return "\n".join(lines)

    for segment in data.segments[:8]:
        lines.append(
            "- "
            f"{segment.started_at.strftime('%m-%d %H:%M')} ~ "
            f"{segment.ended_at.strftime('%m-%d %H:%M')}："
            f"{format_duration(segment.seconds)}"
        )
    if len(data.segments) > 8:
        lines.append(f"... 其余 {len(data.segments) - 8} 段已省略")
    return "\n".join(lines)


async def render_status(status: ServerStatus) -> RenderedMessage:
    fallback = format_status_text(status)
    if not _get_settings().render_enabled:
        return RenderedMessage(image=None, fallback_text=fallback)
    try:
        return RenderedMessage(
            image=await _render_template(
                _STATUS_TEMPLATE,
                {
                    "status": status,
                    "players": [
                        {
                            "name": player.name,
                            "position": player.position,
                            "online": format_duration(player.online_seconds)
                            if player.online_seconds
                            else "",
                        }
                        for player in status.players
                    ],
                },
                viewport_width=760,
            ),
            fallback_text=fallback,
        )
    except RenderingError as exc:
        logger.warning("MC状态卡片渲染失败，回退文字", MODULE_NAME, e=exc)
        return RenderedMessage(image=None, fallback_text=fallback)


async def render_playtime(title: str, entries: list[PlaytimeEntry]) -> RenderedMessage:
    fallback = format_playtime_text(title, entries)
    if not _get_settings().render_enabled:
        return RenderedMessage(image=None, fallback_text=fallback)
    try:
        return RenderedMessage(
            image=await _render_template(
                _TIME_TEMPLATE,
                {
                    "title": title,
                    "items": [
                        {
                            "rank": index,
                            "name": entry.player_name,
                            "duration": format_duration(entry.seconds),
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
    points = downsample_points(data.points, max_points)
    peak, avg = chart_summary(data.points)
    point_count = len(points)
    try:
        return RenderedMessage(
            image=await _render_template(
                _CHART_TEMPLATE,
                {
                    "title": data.title,
                    "range_label": data.range_label,
                    "peak": peak,
                    "avg": round(avg, 1),
                    "points": [
                        {
                            "label": _format_point_label(
                                index,
                                point_count,
                                point.captured_at.strftime("%m-%d %H:%M"),
                            ),
                            "tooltip": point.captured_at.strftime("%m-%d %H:%M"),
                            "count": point.online_count,
                            "height": int((point.online_count / peak) * 120)
                            if peak
                            else 2,
                            "percent": int((point.online_count / peak) * 100)
                            if peak
                            else 0,
                        }
                        for index, point in enumerate(points)
                    ],
                },
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
    segments = _timeline_segments(data)
    try:
        return RenderedMessage(
            image=await _render_template(
                _PERSONAL_TEMPLATE,
                {
                    "title": data.title,
                    "range_label": data.range_label,
                    "player_label": "、".join(data.player_names)
                    if data.player_names
                    else f"QQ {data.qq_id}",
                    "total_duration": format_duration(data.total_seconds),
                    "segment_count": len(data.segments),
                    "segments": segments,
                    "segment_rows": segments[:8],
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


def _timeline_segments(data: PersonalOnlineData) -> list[dict[str, object]]:
    total_seconds = max(1, int((data.range_end - data.range_start).total_seconds()))
    items = []
    for segment in data.segments:
        left = (
            (segment.started_at - data.range_start).total_seconds() / total_seconds
        ) * 100
        width = (segment.seconds / total_seconds) * 100
        left = max(0.0, min(100.0, left))
        width = max(0.0, min(100.0 - left, width))
        # 很短的在线段在长时间范围里会窄到不可见，渲染时给一个最小可见宽度。
        display_width = min(100.0 - left, max(width, 0.8)) if width > 0 else 0
        items.append(
            {
                "left": round(left, 3),
                "width": round(display_width, 3),
                "start": segment.started_at.strftime("%m-%d %H:%M"),
                "end": segment.ended_at.strftime("%m-%d %H:%M"),
                "duration": format_duration(segment.seconds),
            }
        )
    return items


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
