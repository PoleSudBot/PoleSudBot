from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
import re
import time
from typing import Any

from .config import DEFAULT_B30_CONSTANTS_URL, get_settings
from .constants import MODULE_NAME, STATE_DIR, resource_data_path
from .runtime import AsyncHttpx, logger
from .storage import JsonStateStore

_CACHE_PATH = resource_data_path("charts", "b30_constants.csv")
_STATE_STORE = JsonStateStore(STATE_DIR / "b30_constants_state.json")
_COLUMN_ALIASES = {
    "song": "song",
    "歌曲": "song",
    "jpname": "jp name",
    "日文名": "jp name",
    "constant": "constant",
    "定数": "constant",
    "level": "level",
    "原始等级": "level",
    "原始難易度": "level",
    "等级": "level",
    "notecount": "note count",
    "note": "note count",
    "物量": "note count",
    "difficulty": "difficulty",
    "难度": "difficulty",
    "難易度": "difficulty",
    "难易度": "difficulty",
    "songid": "song id",
    "musicid": "song id",
    "id": "song id",
    "歌曲id": "song id",
    "乐曲id": "song id",
    "notes": "notes",
    "备注": "notes",
}


@dataclass(frozen=True)
class ChartConstant:
    music_id: int
    difficulty: str
    constant: float
    level: int = 0
    level_label: str = ""
    note_count: int = 0
    title: str = ""
    jp_title: str = ""
    notes: str = ""


@dataclass
class ConstantsTable:
    entries: list[ChartConstant]
    by_music: dict[int, dict[str, ChartConstant]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_music = {}
        normalized_entries: list[ChartConstant] = []
        for entry in self.entries:
            difficulty = normalize_difficulty(entry.difficulty)
            if entry.music_id <= 0 or not difficulty or entry.constant <= 0:
                continue
            normalized = ChartConstant(
                music_id=entry.music_id,
                difficulty=difficulty,
                constant=entry.constant,
                level=entry.level,
                level_label=entry.level_label,
                note_count=entry.note_count,
                title=entry.title,
                jp_title=entry.jp_title,
                notes=entry.notes,
            )
            self.by_music.setdefault(normalized.music_id, {})[difficulty] = normalized
            normalized_entries.append(normalized)
        self.entries = normalized_entries

    def get(self, music_id: int, difficulty: str) -> ChartConstant | None:
        return self.by_music.get(music_id, {}).get(normalize_difficulty(difficulty))


def normalize_difficulty(value: str) -> str:
    match str(value or "").strip().lower():
        case "easy" | "ez" | "eas" | "简单":
            return "easy"
        case "normal" | "nm" | "nor" | "普通":
            return "normal"
        case "hard" | "hd" | "hrd" | "困难":
            return "hard"
        case "expert" | "ex" | "exp" | "专家":
            return "expert"
        case "master" | "mas" | "ma" | "大师":
            return "master"
        case "append" | "apd" | "app" | "追加":
            return "append"
        case other:
            return other


def parse_constants_csv(content: str) -> list[ChartConstant]:
    reader = csv.reader(StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        return []
    columns = _normalize_columns(header)
    required = {"constant", "difficulty", "song id"}
    if missing := sorted(required.difference(columns)):
        raise ValueError(f"B30 定数 CSV 缺少列: {', '.join(missing)}")

    entries: list[ChartConstant] = []
    for row in reader:
        music_id = _parse_int(_csv_column(row, columns, "song id"))
        constant = _parse_float(_csv_column(row, columns, "constant"))
        difficulty = normalize_difficulty(_csv_column(row, columns, "difficulty"))
        if music_id <= 0 or constant <= 0 or not difficulty:
            continue
        level = _parse_level_number(_csv_column(row, columns, "level"))
        entries.append(
            ChartConstant(
                music_id=music_id,
                difficulty=difficulty,
                constant=constant,
                level=level,
                level_label=str(level) if level > 0 else "",
                note_count=_first_positive(
                    _parse_int(_csv_column(row, columns, "note count")),
                    _parse_int(_csv_column(row, columns, "notes")),
                ),
                title=_csv_column(row, columns, "song"),
                jp_title=_csv_column(row, columns, "jp name"),
                notes=_csv_column(row, columns, "notes"),
            )
        )
    return entries


class B30ConstantsProvider:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: ConstantsTable | None = None
        self._cache_mtime = 0.0
        self._source_url = ""

    async def get_table(self) -> ConstantsTable:
        settings = get_settings()
        source_url = settings.b30_constants_url or DEFAULT_B30_CONSTANTS_URL
        interval = settings.b30_constants_refresh_interval_seconds
        async with self._lock:
            state = self._load_state()
            cached_content = self._read_cached_content()
            if (
                cached_content
                and self._cache is not None
                and self._source_url == source_url
                and self._cache_mtime == _CACHE_PATH.stat().st_mtime
                and not self._needs_refresh(state, interval, source_url)
            ):
                return self._cache

            if cached_content and not self._needs_refresh(
                state,
                interval,
                source_url,
            ):
                return self._table_from_content(cached_content, source_url)
            return await self._refresh_locked(
                source_url,
                cached_content=cached_content,
            )

    async def refresh_if_needed(self, *, force: bool = False) -> bool:
        settings = get_settings()
        source_url = settings.b30_constants_url or DEFAULT_B30_CONSTANTS_URL
        interval = settings.b30_constants_refresh_interval_seconds
        async with self._lock:
            state = self._load_state()
            if not force and not self._needs_refresh(state, interval, source_url):
                return False
            cached_content = self._read_cached_content()
            before = cached_content or ""
            await self._refresh_locked(source_url, cached_content=cached_content)
            after = self._read_cached_content() or ""
            return before != after

    def source_url(self) -> str:
        return self._source_url or get_settings().b30_constants_url

    def _load_state(self) -> dict[str, Any]:
        payload = _STATE_STORE.load({})
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _read_cached_content() -> str:
        if not _CACHE_PATH.exists():
            return ""
        try:
            return _CACHE_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("SekaiResource B30 定数缓存读取失败", MODULE_NAME, e=exc)
            return ""

    @staticmethod
    def _needs_refresh(
        state: dict[str, Any],
        interval: int,
        source_url: str,
    ) -> bool:
        if not _CACHE_PATH.exists():
            return True
        if str(state.get("source_url") or "") != source_url:
            return True
        checked_at = _parse_float(state.get("checked_at"))
        return time.time() - checked_at >= interval

    async def _refresh_locked(
        self,
        source_url: str,
        *,
        cached_content: str,
    ) -> ConstantsTable:
        now = time.time()
        try:
            response = await AsyncHttpx.get(
                source_url,
                timeout=get_settings().b30_constants_timeout_seconds,
            )
            table = self._table_from_content(response.text, source_url)
            self._save_cache(response.text)
            self._save_state(
                source_url=source_url,
                checked_at=now,
                updated_at=now,
                error=None,
            )
            return table
        except Exception as exc:
            self._save_state(
                source_url=source_url,
                checked_at=now,
                updated_at=_parse_float(self._load_state().get("updated_at")),
                error=f"{type(exc).__name__}: {exc}",
            )
            if cached_content:
                # 网络或远端表结构偶发异常时保留旧定数，避免 B30 查询被外部源拖垮。
                logger.warning(
                    "SekaiResource B30 定数刷新失败，使用本地缓存",
                    MODULE_NAME,
                    e=exc,
                )
                return self._table_from_content(cached_content, source_url)
            raise ValueError("B30 定数 CSV 请求失败") from exc

    def _table_from_content(self, content: str, source_url: str) -> ConstantsTable:
        table = ConstantsTable(parse_constants_csv(content))
        if not table.entries:
            raise ValueError("B30 定数 CSV 没有可用数据")
        self._cache = table
        self._cache_mtime = _CACHE_PATH.stat().st_mtime if _CACHE_PATH.exists() else 0.0
        self._source_url = source_url
        return table

    @staticmethod
    def _save_cache(content: str) -> None:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # CSV 先写临时文件再替换，避免定时刷新中断留下半截定数表。
        tmp_path = _CACHE_PATH.with_suffix(".csv.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(_CACHE_PATH)

    @staticmethod
    def _save_state(
        *,
        source_url: str,
        checked_at: float,
        updated_at: float,
        error: str | None,
    ) -> None:
        _STATE_STORE.save(
            {
                "source_url": source_url,
                "checked_at": checked_at,
                "checked_time": datetime.fromtimestamp(checked_at).isoformat(
                    timespec="seconds"
                ),
                "updated_at": updated_at,
                "updated_time": (
                    datetime.fromtimestamp(updated_at).isoformat(timespec="seconds")
                    if updated_at > 0
                    else None
                ),
                "error": error,
            }
        )


def _normalize_columns(header: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, name in enumerate(header):
        normalized = str(name or "").strip().removeprefix("\ufeff")
        key = re.sub(r"\s+", "", normalized).lower()
        canonical = _COLUMN_ALIASES.get(key)
        if canonical and canonical not in columns:
            columns[canonical] = index
    return columns


def _csv_column(row: list[str], columns: dict[str, int], name: str) -> str:
    index = columns.get(name)
    if index is None or index < 0 or index >= len(row):
        return ""
    return row[index].strip()


def _parse_int(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return 0


def _parse_level_number(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    match = re.search(r"\d+", text)
    return int(match.group()) if match else 0


def _parse_float(value: Any) -> float:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    try:
        return float(text)
    except ValueError:
        return 0


def _first_positive(*values: int) -> int:
    for value in values:
        if value > 0:
            return value
    return 0


b30_constants_provider = B30ConstantsProvider()

__all__ = [
    "DEFAULT_B30_CONSTANTS_URL",
    "B30ConstantsProvider",
    "ChartConstant",
    "ConstantsTable",
    "b30_constants_provider",
    "normalize_difficulty",
    "parse_constants_csv",
]
