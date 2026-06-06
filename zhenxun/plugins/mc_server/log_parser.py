from __future__ import annotations

from datetime import datetime
import re

from .types import LogEvent

_TIME_PREFIX = re.compile(r"^\[(?P<hms>\d{2}:\d{2}:\d{2})]\s+\[[^]]+]:\s+(?P<body>.*)$")
_JOIN_PATTERN = re.compile(r"^(?P<player>[A-Za-z0-9_]{3,16}) joined the game$")
_LEAVE_PATTERN = re.compile(r"^(?P<player>[A-Za-z0-9_]{3,16}) left the game$")
_CHAT_PATTERNS = (
    re.compile(r"^<(?P<player>[A-Za-z0-9_]{3,16})> (?P<message>.*)$"),
    re.compile(r"^\[Not Secure] <(?P<player>[A-Za-z0-9_]{3,16})> (?P<message>.*)$"),
)


def parse_paper_log_line(line: str, now: datetime | None = None) -> LogEvent | None:
    raw = line.rstrip("\n")
    match = _TIME_PREFIX.match(raw)
    body = match.group("body") if match else raw
    occurred_at = _parse_log_time(match.group("hms"), now) if match else now

    if joined := _JOIN_PATTERN.match(body):
        return LogEvent(
            type="join",
            player_name=joined.group("player"),
            raw=raw,
            occurred_at=occurred_at,
        )

    if left := _LEAVE_PATTERN.match(body):
        return LogEvent(
            type="leave",
            player_name=left.group("player"),
            raw=raw,
            occurred_at=occurred_at,
        )

    for pattern in _CHAT_PATTERNS:
        if chat := pattern.match(body):
            return LogEvent(
                type="chat",
                player_name=chat.group("player"),
                message=chat.group("message"),
                raw=raw,
                occurred_at=occurred_at,
            )
    return None


def _parse_log_time(hms: str, now: datetime | None) -> datetime | None:
    base = now or datetime.now()
    hour, minute, second = (int(part) for part in hms.split(":"))
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)
