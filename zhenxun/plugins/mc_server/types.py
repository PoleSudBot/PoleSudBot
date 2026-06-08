from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class RenderedMessage:
    image: bytes | None
    fallback_text: str
    lead_text: str = ""

    def to_message_parts(self) -> list[object]:
        if self.image:
            if self.lead_text:
                return [self.lead_text, "\n", self.image]
            return [self.image]
        return [self.fallback_text]


@dataclass(frozen=True)
class ParsedAddress:
    host: str
    port: int

    @property
    def display(self) -> str:
        if ":" in self.host and not self.host.startswith("["):
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class TimeRange:
    label: str
    start: datetime
    end: datetime
    end_is_current: bool = False


@dataclass(frozen=True)
class LogEvent:
    type: str
    player_name: str = ""
    message: str = ""
    raw: str = ""
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class BlueMapPlayer:
    uuid: str
    name: str
    map_id: str
    x: float
    y: float
    z: float

    @property
    def position_text(self) -> str:
        return f"{self.map_id} ({self.x:.1f}, {self.y:.1f}, {self.z:.1f})"


@dataclass(frozen=True)
class PlayerStatus:
    name: str
    uuid: str = ""
    online_seconds: int = 0
    total_seconds: int = 0
    position: str = ""


@dataclass(frozen=True)
class ServerStatus:
    name: str
    address: str
    online: bool
    latency_ms: float | None = None
    version: str = ""
    online_players: int = 0
    max_players: int = 0
    players: list[PlayerStatus] = field(default_factory=list)
    player_list_complete: bool = False
    weather: str = "未知/未配置数据源"
    error: str = ""


@dataclass(frozen=True)
class PlaytimeEntry:
    player_name: str
    seconds: int
    qq_id: str = ""
    average_seconds: int = 0
    today_seconds: int = 0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class PlaytimeRow:
    player_name: str
    qq_id: str
    seconds: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class SamplePoint:
    captured_at: datetime
    online_count: int


@dataclass(frozen=True)
class OnlineDurationPoint:
    label: str
    seconds: int


@dataclass(frozen=True)
class PersonalOnlineSegment:
    started_at: datetime
    ended_at: datetime
    seconds: int


@dataclass(frozen=True)
class PersonalOnlineData:
    title: str
    range_label: str
    range_start: datetime
    range_end: datetime
    qq_id: str
    range_end_is_current: bool = False
    player_names: list[str] = field(default_factory=list)
    segments: list[PersonalOnlineSegment] = field(default_factory=list)
    daily_points: list[OnlineDurationPoint] = field(default_factory=list)
    chart_points: list[OnlineDurationPoint] = field(default_factory=list)
    chart_granularity: Literal["hourly", "daily"] = "daily"
    total_seconds: int = 0
    average_seconds: int = 0
    today_seconds: int = 0
    show_today_online: bool = False


@dataclass(frozen=True)
class ChartData:
    title: str
    range_label: str
    points: list[SamplePoint]
    range_start: datetime | None = None
    range_end: datetime | None = None
    range_end_is_current: bool = False
    playtime_entries: list[PlaytimeEntry] = field(default_factory=list)
