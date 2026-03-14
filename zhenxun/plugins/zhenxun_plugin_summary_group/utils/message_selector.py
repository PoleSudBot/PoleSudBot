from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re

import pytz

from ..config import summary_config

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")

_RELATIVE_TIME_PATTERN = re.compile(r"^(?:最近)?\s*(\d+)\s*([mhd])$", re.IGNORECASE)
_TIME_RANGE_SEPARATORS = ("~", "～", "到")
_TIME_ONLY_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}$")


@dataclass(slots=True)
class MessageSelector:
    mode: str
    label: str
    count: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    raw_expression: str | None = None

    @property
    def is_time_based(self) -> bool:
        return self.mode in {"today", "yesterday", "duration", "time_range"}

    @property
    def start_timestamp(self) -> float | None:
        return self.start_time.timestamp() if self.start_time else None

    @property
    def end_timestamp(self) -> float | None:
        return self.end_time.timestamp() if self.end_time else None


def _ensure_timezone(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return SHANGHAI_TZ.localize(dt)
    return dt.astimezone(SHANGHAI_TZ)


def get_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI_TZ)
    return _ensure_timezone(now)


def build_count_selector(count: int) -> MessageSelector:
    return MessageSelector(mode="count", count=count, label=f"最近 {count} 条")


def _get_day_boundaries(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = get_now(now)
    b_hour, b_minute = summary_config.get_day_boundary()
    today_boundary = current.replace(
        hour=b_hour, minute=b_minute, second=0, microsecond=0
    )
    if current < today_boundary:
        today_boundary -= timedelta(days=1)
    yesterday_boundary = today_boundary - timedelta(days=1)
    return yesterday_boundary, today_boundary


def build_today_selector(now: datetime | None = None) -> MessageSelector:
    current = get_now(now)
    _, today_boundary = _get_day_boundaries(current)
    return MessageSelector(
        mode="today",
        label="今日",
        start_time=today_boundary,
        end_time=current,
    )


def build_yesterday_selector(now: datetime | None = None) -> MessageSelector:
    current = get_now(now)
    yesterday_boundary, today_boundary = _get_day_boundaries(current)
    return MessageSelector(
        mode="yesterday",
        label="昨日",
        start_time=yesterday_boundary,
        end_time=today_boundary,
    )


def _parse_datetime_point(text: str, current: datetime) -> datetime:
    value = text.strip()
    if _DATE_TIME_PATTERN.fullmatch(value):
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
        return SHANGHAI_TZ.localize(parsed)
    if _TIME_ONLY_PATTERN.fullmatch(value):
        parsed_time = datetime.strptime(value, "%H:%M")
        return current.replace(
            hour=parsed_time.hour,
            minute=parsed_time.minute,
            second=0,
            microsecond=0,
        )
    raise ValueError(
        "时间表达式无效，请使用 2h/30m/3d、09:00~12:00 或 YYYY-MM-DD HH:MM~YYYY-MM-DD HH:MM"
    )


def _split_time_range(expression: str) -> tuple[str, str]:
    for separator in _TIME_RANGE_SEPARATORS:
        if separator in expression:
            start_text, end_text = expression.split(separator, 1)
            if start_text.strip() and end_text.strip():
                return start_text.strip(), end_text.strip()
    raise ValueError(
        "时间区间格式无效，请使用 09:00~12:00 或 YYYY-MM-DD HH:MM~YYYY-MM-DD HH:MM"
    )


def parse_time_expression(
    expression: str,
    now: datetime | None = None,
) -> MessageSelector:
    current = get_now(now)
    value = expression.strip()
    if not value:
        raise ValueError("时间表达式不能为空")

    relative_match = _RELATIVE_TIME_PATTERN.fullmatch(value)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2).lower()
        delta_map = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }
        label_suffix = {"m": "分钟", "h": "小时", "d": "天"}[unit]
        return MessageSelector(
            mode="duration",
            label=f"最近 {amount}{label_suffix}",
            start_time=current - delta_map[unit],
            end_time=current,
            raw_expression=value,
        )

    start_text, end_text = _split_time_range(value)
    start_time = _parse_datetime_point(start_text, current)
    end_time = _parse_datetime_point(end_text, current)

    if _TIME_ONLY_PATTERN.fullmatch(start_text) and _TIME_ONLY_PATTERN.fullmatch(end_text):
        if end_time <= start_time:
            end_time += timedelta(days=1)
    elif end_time <= start_time:
        raise ValueError("结束时间必须晚于开始时间")

    return MessageSelector(
        mode="time_range",
        label=f"{start_text} ~ {end_text}",
        start_time=start_time,
        end_time=end_time,
        raw_expression=value,
    )


def build_selector_from_time_range_type(
    time_range_type: str,
    now: datetime | None = None,
) -> MessageSelector:
    if time_range_type == "today":
        return build_today_selector(now)
    if time_range_type == "yesterday":
        return build_yesterday_selector(now)
    raise ValueError(f"未知的时间范围类型: {time_range_type}")
