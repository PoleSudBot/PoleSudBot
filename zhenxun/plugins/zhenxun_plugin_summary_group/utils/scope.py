from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from ..config import summary_config

COUNT_RE = re.compile(r"^\d+$")
RELATIVE_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^(?:(?P<year>\d{4})-)?(?P<month>\d{1,2})-(?P<day>\d{1,2})$")


def get_scope_timezone() -> ZoneInfo:
    try:
        tzinfo = datetime.now().astimezone().tzinfo
        if isinstance(tzinfo, ZoneInfo):
            return tzinfo
    except Exception:
        pass
    return ZoneInfo("Asia/Shanghai")


def parse_clock_time(value: str) -> tuple[int, int]:
    value = value.strip()
    if not value:
        raise ValueError("时间不能为空")

    if ":" in value:
        hour_str, minute_str = value.split(":", maxsplit=1)
    elif value.isdigit() and len(value) in {3, 4}:
        hour_str = value[:-2]
        minute_str = value[-2:]
    else:
        raise ValueError("时间格式无效，请使用 HH:MM 或 HHMM")

    if not (hour_str.isdigit() and minute_str.isdigit()):
        raise ValueError("时间格式无效，请使用 HH:MM 或 HHMM")

    hour = int(hour_str)
    minute = int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间超出有效范围")
    return hour, minute


@dataclass(frozen=True)
class SummaryScope:
    mode: str
    label: str
    raw: str
    count: int | None = None
    start_ts: int | None = None
    end_ts: int | None = None
    preset: str | None = None

    @property
    def is_time_based(self) -> bool:
        return self.mode != "count"

    def fetch_count(self, max_count: int) -> int:
        return max_count if self.is_time_based else int(self.count or max_count)


@dataclass(frozen=True)
class ParsedScope:
    scope: SummaryScope
    consumed_text_count: int = 0


def build_count_scope(count: int) -> SummaryScope:
    return SummaryScope(
        mode="count", label=f"最近 {count} 条消息", raw=str(count), count=count
    )


def build_preset_scope(
    preset: str,
    now: datetime | None = None,
) -> SummaryScope:
    tz = get_scope_timezone()
    current = now.astimezone(tz) if now else datetime.now(tz)
    b_hour, b_minute = summary_config.get_day_boundary()

    today_boundary = current.replace(
        hour=b_hour,
        minute=b_minute,
        second=0,
        microsecond=0,
    )
    if current < today_boundary:
        today_boundary -= timedelta(days=1)

    if preset == "today":
        start_dt = today_boundary
        end_dt = current
        label = "今日"
    else:
        end_dt = today_boundary
        start_dt = today_boundary - timedelta(days=1)
        label = "昨日"

    return SummaryScope(
        mode="time",
        label=label,
        raw=label,
        start_ts=int(start_dt.timestamp()),
        end_ts=int(end_dt.timestamp()),
        preset=preset,
    )


def build_relative_scope(expr: str, now: datetime | None = None) -> SummaryScope:
    match = RELATIVE_RE.fullmatch(expr.strip())
    if not match:
        raise ValueError("相对时间格式无效，请使用如 2h、1h30m、2d")

    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    if days == 0 and hours == 0 and minutes == 0:
        raise ValueError("相对时间范围不能为空")

    tz = get_scope_timezone()
    current = now.astimezone(tz) if now else datetime.now(tz)
    start_dt = current - timedelta(days=days, hours=hours, minutes=minutes)

    return SummaryScope(
        mode="time",
        label=f"最近 {expr}",
        raw=expr,
        start_ts=int(start_dt.timestamp()),
        end_ts=int(current.timestamp()),
    )


def build_absolute_scope(
    range_part: str,
    date_part: str | None = None,
    now: datetime | None = None,
) -> SummaryScope:
    if "~" not in range_part:
        raise ValueError("时间段格式无效，请使用 7:00~8:00 或 0700~0800")

    start_str, end_str = [part.strip() for part in range_part.split("~", maxsplit=1)]
    if not start_str or not end_str:
        raise ValueError("时间段格式无效，请使用 7:00~8:00 或 0700~0800")

    start_hour, start_minute = parse_clock_time(start_str)
    end_hour, end_minute = parse_clock_time(end_str)

    tz = get_scope_timezone()
    current = now.astimezone(tz) if now else datetime.now(tz)

    if date_part:
        date_match = DATE_RE.fullmatch(date_part.strip())
        if not date_match:
            raise ValueError("日期格式无效，请使用 YYYY-MM-DD 或 MM-DD")
        year = int(date_match.group("year") or current.year)
        month = int(date_match.group("month"))
        day = int(date_match.group("day"))
        start_dt = datetime(year, month, day, start_hour, start_minute, tzinfo=tz)
    else:
        start_dt = current.replace(
            hour=start_hour,
            minute=start_minute,
            second=0,
            microsecond=0,
        )

    end_dt = start_dt.replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
    )
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    label_prefix = f"{date_part} " if date_part else ""
    label = (
        f"{label_prefix}{start_hour:02d}:{start_minute:02d}"
        f"~{end_hour:02d}:{end_minute:02d}"
    )

    return SummaryScope(
        mode="time",
        label=label,
        raw=f"{date_part} {range_part}".strip(),
        start_ts=int(start_dt.timestamp()),
        end_ts=int(end_dt.timestamp()),
    )


def parse_summary_scope(
    scope_token: str, trailing_texts: list[str] | None = None
) -> ParsedScope:
    scope_token = scope_token.strip()
    trailing_texts = trailing_texts or []

    if not scope_token:
        raise ValueError("请提供要总结/导出的数量或时间范围")

    lowered = scope_token.lower()
    if scope_token in {"今日", "today"} or lowered == "today":
        return ParsedScope(build_preset_scope("today"))
    if scope_token in {"昨日", "yesterday"} or lowered == "yesterday":
        return ParsedScope(build_preset_scope("yesterday"))

    if COUNT_RE.fullmatch(scope_token):
        return ParsedScope(build_count_scope(int(scope_token)))

    relative_match = RELATIVE_RE.fullmatch(scope_token)
    if relative_match and any(relative_match.groupdict().values()):
        return ParsedScope(build_relative_scope(scope_token))

    if "~" in scope_token:
        return ParsedScope(build_absolute_scope(scope_token))

    if DATE_RE.fullmatch(scope_token):
        if not trailing_texts:
            raise ValueError(
                "日期范围需要搭配时间段使用，例如：总结 2026-03-15 7:00~8:00"
            )
        range_part = trailing_texts[0].strip()
        if "~" not in range_part:
            raise ValueError(
                "日期范围需要搭配时间段使用，例如：总结 2026-03-15 7:00~8:00"
            )
        return ParsedScope(
            build_absolute_scope(range_part=range_part, date_part=scope_token),
            consumed_text_count=1,
        )

    raise ValueError(
        "无法识别的范围，请使用数量、今日/昨日、2h、1h30m、2d 或 7:00~8:00 这类格式"
    )


def build_partial_coverage_warning(scope: SummaryScope, max_count: int) -> str:
    if scope.preset in {"today", "yesterday"}:
        return (
            f"超过最大信息获取数量，仅基于最近 {max_count} 条记录"
            f"生成{scope.label}结果，"
            "时间范围可能不完整。"
        )
    return (
        f"超过最大信息获取数量，请求的 {scope.label} 仅部分覆盖，"
        f"以下结果仅基于最近 {max_count} 条记录。"
    )
