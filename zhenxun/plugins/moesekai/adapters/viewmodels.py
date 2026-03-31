from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReminderCardViewModel:
    title: str
    subtitle: str | None
    lines: list[str]
    banner: bytes | None = None
    accent: str | None = None
