from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import re
from typing import TypeVar
from zoneinfo import ZoneInfo

from .constants import DEFAULT_MC_PORT
from .types import ParsedAddress, TimeRange

MC_TIMEZONE = ZoneInfo("Asia/Shanghai")
BUSINESS_DAY_START_HOUR = 6
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_IPV6_PATTERN = re.compile(r"^\[([0-9A-Fa-f:.]+)](?::(\d+))?$")
_DATE_RANGE_PATTERN = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2})(?:\.\.(?P<end>\d{4}-\d{2}-\d{2}))?$"
)
_RCON_LIST_COUNT_PATTERN = re.compile(
    r"There are (?P<online>\d+) of a max of (?P<max>\d+) players online",
    re.IGNORECASE,
)
_TELLRAW_NO_RECIPIENT_PATTERNS = (
    "no player was found",
    "no entity was found",
    "找不到玩家",
    "没有找到玩家",
    "没有找到实体",
)
_TELLRAW_FAILURE_PATTERNS = (
    "unknown command",
    "incorrect argument",
    "invalid json",
    "malformed json",
    "expected",
    "未知的命令",
    "无效的json",
    "无效的 json",
    "格式错误",
)
EXIT_BIND_FLOW_WORDS = {"q", "quit", "退出", "取消"}
SKIP_BIND_FLOW_WORDS = {"skip", "跳过"}
DONE_BIND_FLOW_WORDS = {"done", "完成"}
TIME_RANGE_WORDS = {
    "今日",
    "今天",
    "本日",
    "day",
    "today",
    "昨日",
    "昨天",
    "yesterday",
    "本周",
    "这周",
    "周",
    "week",
    "thisweek",
    "this_week",
    "上周",
    "上星期",
    "上个星期",
    "lastweek",
    "last_week",
    "本月",
    "这个月",
    "月",
    "month",
    "thismonth",
    "this_month",
    "上月",
    "上个月",
    "lastmonth",
    "last_month",
    "本周目",
    "周目",
    "season",
}
T = TypeVar("T")


def parse_bind_private_setup(raw: str) -> tuple[str, str | None]:
    lines = [line.strip() for line in raw.splitlines()]
    password = lines[0] if lines else ""
    if not password:
        raise ValueError("RCON密码不能为空")
    log_path = lines[1] if len(lines) > 1 else ""
    if not log_path or is_bind_flow_skip(log_path):
        return password, None
    return password, log_path


def parse_server_address(
    raw: str,
    default_port: int = DEFAULT_MC_PORT,
) -> ParsedAddress:
    text = raw.strip()
    if not text:
        raise ValueError("服务器地址不能为空")

    ipv6_match = _IPV6_PATTERN.match(text)
    if ipv6_match:
        host = ipv6_match.group(1)
        port_text = ipv6_match.group(2)
        return ParsedAddress(host=host, port=_parse_port(port_text, default_port))

    if text.count(":") > 1:
        raise ValueError("IPv6 地址请使用 [::1]:25565 格式")

    host, _, port_text = text.partition(":")
    if not host or not _HOST_PATTERN.match(host):
        raise ValueError("服务器地址只能包含字母、数字、点、横线和下划线")
    return ParsedAddress(host=host, port=_parse_port(port_text or None, default_port))


def parse_port(raw: str, label: str = "端口") -> int:
    try:
        return _parse_port(raw.strip(), DEFAULT_MC_PORT)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("端口"):
            message = message.removeprefix("端口")
        raise ValueError(f"{label}{message}") from exc


def format_server_address(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def is_bind_flow_exit(raw: str) -> bool:
    return raw.strip().lower() in EXIT_BIND_FLOW_WORDS


def is_bind_flow_skip(raw: str) -> bool:
    return raw.strip().lower() in SKIP_BIND_FLOW_WORDS


def is_bind_flow_done(raw: str) -> bool:
    return raw.strip().lower() in DONE_BIND_FLOW_WORDS


def resolve_latest_log_path(path: str) -> Path | None:
    raw_path = Path(path).expanduser()
    # 允许用户直接填 logs 目录，向导与单步命令统一落到 latest.log 文件。
    log_path = raw_path / "latest.log" if raw_path.is_dir() else raw_path
    if not log_path.exists() or not log_path.is_file():
        return None
    try:
        with log_path.open("rb"):
            pass
    except OSError:
        return None
    return log_path


def _parse_port(raw: str | None, default_port: int) -> int:
    if not raw:
        return default_port
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("端口必须是数字") from exc
    if not 1 <= port <= 65535:
        raise ValueError("端口范围必须是 1-65535")
    return port


def now_local() -> datetime:
    return datetime.now(MC_TIMEZONE)


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=MC_TIMEZONE)
    return value.astimezone(MC_TIMEZONE)


def combine_local(target_date: date, target_time: time) -> datetime:
    return datetime.combine(target_date, target_time, tzinfo=MC_TIMEZONE)


def combine_business_start(target_date: date) -> datetime:
    return combine_local(target_date, time(BUSINESS_DAY_START_HOUR))


def business_day_start(value: datetime | None = None) -> datetime:
    current = normalize_datetime(value) or now_local()
    boundary = combine_local(current.date(), time(BUSINESS_DAY_START_HOUR))
    if current < boundary:
        boundary -= timedelta(days=1)
    return boundary


def business_today_range(value: datetime | None = None) -> TimeRange:
    start = business_day_start(value)
    end = normalize_datetime(value) or now_local()
    return TimeRange("今日", start, end, end_is_current=True)


def business_day_count(range_start: datetime, range_end: datetime) -> int:
    start = business_day_start(range_start)
    end = normalize_datetime(range_end) or now_local()
    if end <= start:
        return 1
    last_day_start = business_day_start(end - timedelta(microseconds=1))
    return max(1, (last_day_start.date() - start.date()).days + 1)


def iter_business_days(range_start: datetime, range_end: datetime) -> list[TimeRange]:
    start = business_day_start(range_start)
    end = normalize_datetime(range_end) or now_local()
    days = []
    cursor = start
    while cursor < end:
        next_cursor = cursor + timedelta(days=1)
        clipped_start = max(cursor, normalize_datetime(range_start) or cursor)
        clipped_end = min(next_cursor, end)
        if clipped_end > clipped_start:
            days.append(
                TimeRange(
                    cursor.strftime("%m-%d"),
                    clipped_start,
                    clipped_end,
                )
            )
        cursor = next_cursor
    return days or [TimeRange(start.strftime("%m-%d"), start, end)]


def format_time_range_hint(time_range: TimeRange) -> str:
    start = normalize_datetime(time_range.start) or time_range.start
    end = normalize_datetime(time_range.end) or time_range.end
    end_text = "" if time_range.end_is_current else end.strftime("%y-%m-%d %H:%M")
    return f"{start.strftime('%y-%m-%d %H:%M')} ~ {end_text}"


def should_show_today_online(
    time_range: TimeRange,
    *,
    now: datetime | None = None,
) -> bool:
    current = (
        normalize_datetime(now) or normalize_datetime(time_range.end) or now_local()
    )
    start = normalize_datetime(time_range.start) or time_range.start
    end = normalize_datetime(time_range.end) or time_range.end
    today_start = business_day_start(current)
    if not time_range.end_is_current:
        return False
    if business_day_count(start, end) <= 1:
        return False

    # “本日在线”只服务当前业务日相关的多日查询，历史范围即使超过一天也不显示。
    return start < today_start and end > today_start


def is_time_range_word(raw: str) -> bool:
    text = raw.strip()
    normalized = text.lower()
    return normalized in TIME_RANGE_WORDS or bool(_DATE_RANGE_PATTERN.match(normalized))


def _month_start(target_date: date) -> date:
    return target_date.replace(day=1)


def _previous_month_start(target_date: date) -> date:
    return (target_date.replace(day=1) - timedelta(days=1)).replace(day=1)


def parse_time_range(
    raw: str | None,
    season_start: datetime | None = None,
    *,
    now: datetime | None = None,
) -> TimeRange:
    current = normalize_datetime(now) or now_local()
    text = (raw or "").strip().lower()
    business_anchor = business_day_start(current)
    business_date = business_anchor.date()
    week_start_date = business_date - timedelta(days=business_date.weekday())
    month_start_date = _month_start(business_date)

    # 所有预设统计范围都按 06:00 业务边界切分，避免 mct/mcc 对“今日”等词各算各的。
    if text in {"", "本周目", "周目", "season"}:
        start = normalize_datetime(season_start) or business_day_start(current)
        return TimeRange("本周目", start, current, end_is_current=True)
    if text in {"今日", "今天", "本日", "day", "today"}:
        return TimeRange("今日", business_anchor, current, end_is_current=True)
    if text in {"昨日", "昨天", "yesterday"}:
        return TimeRange("昨日", business_anchor - timedelta(days=1), business_anchor)
    if text in {"本周", "这周", "周", "week", "thisweek", "this_week"}:
        return TimeRange(
            "本周",
            combine_business_start(week_start_date),
            current,
            end_is_current=True,
        )
    if text in {"上周", "上星期", "上个星期", "lastweek", "last_week"}:
        end = combine_business_start(week_start_date)
        return TimeRange("上周", end - timedelta(days=7), end)
    if text in {"本月", "这个月", "月", "month", "thismonth", "this_month"}:
        return TimeRange(
            "本月",
            combine_business_start(month_start_date),
            current,
            end_is_current=True,
        )
    if text in {"上月", "上个月", "lastmonth", "last_month"}:
        end = combine_business_start(month_start_date)
        start = combine_business_start(_previous_month_start(business_date))
        return TimeRange("上月", start, end)

    match = _DATE_RANGE_PATTERN.match(text)
    if not match:
        raise ValueError(
            "时间范围支持 今日/昨日/本周/上周/本月/上月/本周目/"
            "YYYY-MM-DD/YYYY-MM-DD..YYYY-MM-DD"
        )

    start_date = date.fromisoformat(match.group("start"))
    end_date = date.fromisoformat(match.group("end") or match.group("start"))
    if end_date < start_date:
        raise ValueError("结束日期不能早于开始日期")
    start = combine_business_start(start_date)
    end = combine_business_start(end_date + timedelta(days=1))
    label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()}..{end_date.isoformat()}"
    )
    range_end = min(end, current)
    return TimeRange(label, start, range_end, end_is_current=end > current)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02}分"
    return f"{minutes}分钟"


def build_tellraw_command(sender: str, message: str, template: str) -> str:
    rendered = template.format(sender=sender.strip() or "QQ", message=message.strip())
    payload = json.dumps({"text": rendered}, ensure_ascii=False)
    return f"tellraw @a {payload}"


def parse_rcon_list_online_count(response: str) -> int | None:
    match = _RCON_LIST_COUNT_PATTERN.search(response or "")
    if not match:
        return None
    return int(match.group("online"))


def classify_tellraw_response(response: str) -> str | None:
    text = (response or "").strip()
    if not text:
        return None
    normalized = text.lower()
    if any(pattern in normalized for pattern in _TELLRAW_NO_RECIPIENT_PATTERNS):
        return None
    if any(pattern in normalized for pattern in _TELLRAW_FAILURE_PATTERNS):
        return text
    return None


def downsample_points(items: list[T], limit: int) -> list[T]:
    if limit <= 0 or len(items) <= limit:
        return items
    if limit == 1:
        return [items[-1]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(index * step)] for index in range(limit)]
