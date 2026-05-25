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
T = TypeVar("T")


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


def parse_time_range(
    raw: str | None,
    season_start: datetime | None = None,
) -> TimeRange:
    now = now_local()
    text = (raw or "").strip().lower()
    if text in {"", "本周目", "周目", "season"}:
        start = normalize_datetime(season_start) or combine_local(now.date(), time.min)
        return TimeRange("本周目", start, now)
    if text in {"今日", "今天", "day", "today"}:
        start = combine_local(now.date(), time.min)
        return TimeRange("今日", start, now)
    if text in {"本周", "周", "week"}:
        start_date = now.date() - timedelta(days=now.weekday())
        return TimeRange("本周", combine_local(start_date, time.min), now)
    if text in {"本月", "月", "month"}:
        start_date = now.date().replace(day=1)
        return TimeRange("本月", combine_local(start_date, time.min), now)

    match = _DATE_RANGE_PATTERN.match(text)
    if not match:
        raise ValueError(
            "时间范围支持 今日/本周/本月/本周目/YYYY-MM-DD/"
            "YYYY-MM-DD..YYYY-MM-DD"
        )

    start_date = date.fromisoformat(match.group("start"))
    end_date = date.fromisoformat(match.group("end") or match.group("start"))
    if end_date < start_date:
        raise ValueError("结束日期不能早于开始日期")
    start = combine_local(start_date, time.min)
    end = combine_local(end_date + timedelta(days=1), time.min)
    label = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()}..{end_date.isoformat()}"
    )
    return TimeRange(label, start, min(end, now))


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
