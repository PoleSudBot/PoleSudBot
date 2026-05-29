from __future__ import annotations

from collections.abc import Callable
import csv
from dataclasses import dataclass, field
from io import StringIO
import time
from typing import Any

from .adapters.runtime import AsyncHttpx
from .config import get_settings

B30_LIMIT = 30
DEFAULT_B30_CONSTANTS_URL = "https://moe.exmeaning.com/data/pjskb30/merged_chart.csv"
DIFFICULTY_ORDER = {
    "easy": 1,
    "normal": 2,
    "hard": 3,
    "expert": 4,
    "master": 5,
    "append": 6,
}
PLAY_RESULT_ORDER = {"": 0, "C": 1, "FC": 2, "AP": 3}


@dataclass(frozen=True)
class ChartConstant:
    music_id: int
    difficulty: str
    constant: float
    level: int = 0
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


@dataclass(frozen=True)
class UserMusicResult:
    music_id: int
    difficulty: str
    play_result: str = ""
    full_combo: bool = False
    full_perfect: bool = False


@dataclass(frozen=True)
class MusicMeta:
    music_id: int
    title: str = ""
    assetbundle_name: str = ""
    jacket_uri: str = ""
    published_at: int = 0


@dataclass
class B30Entry:
    rank: int
    music_id: int
    title: str
    difficulty: str
    difficulty_label: str
    level: int
    constant: float
    user_rating: float
    play_result: str
    note_count: int
    assetbundle_name: str = ""
    jacket_uri: str = ""
    published_at: int = 0


@dataclass
class B30Result:
    entries: list[B30Entry]
    average: float
    candidate_count: int
    ap_count: int
    fc_count: int
    missing_constants_count: int
    total_result_count: int


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


def difficulty_label(value: str) -> str:
    return {
        "easy": "EASY",
        "normal": "NORMAL",
        "hard": "HARD",
        "expert": "EXPERT",
        "master": "MASTER",
        "append": "APPEND",
    }.get(normalize_difficulty(value), str(value or "").upper())


def normalize_play_result(result: UserMusicResult) -> str:
    if result.full_perfect:
        return "AP"
    play_result = str(result.play_result or "").strip().lower()
    if play_result in {
        "all_perfect",
        "full_perfect",
        "ap",
        "allperfect",
        "fullperfect",
    }:
        return "AP"
    if result.full_combo:
        return "FC"
    if play_result in {"full_combo", "fc", "fullcombo"}:
        return "FC"
    if play_result in {"clear", "c", "live_clear", "success"}:
        return "C"
    return ""


def user_rating(constant: float, play_result: str) -> float:
    if play_result == "AP":
        return constant
    if play_result == "FC":
        return constant - 1 if constant >= 33 else constant - 1.5
    return 0


def _best_user_music_results(
    results: list[UserMusicResult],
) -> dict[str, UserMusicResult]:
    # 同一首同一难度可能有多条 playType 记录，先保留最佳完成状态再参与 B30。
    best: dict[str, UserMusicResult] = {}
    for result in results:
        difficulty = normalize_difficulty(result.difficulty)
        if result.music_id <= 0 or not difficulty:
            continue
        key = f"{result.music_id}:{difficulty}"
        current_priority = PLAY_RESULT_ORDER[normalize_play_result(result)]
        previous = best.get(key)
        previous_priority = (
            PLAY_RESULT_ORDER[normalize_play_result(previous)] if previous else -1
        )
        if previous is None or current_priority > previous_priority:
            best[key] = UserMusicResult(
                music_id=result.music_id,
                difficulty=difficulty,
                play_result=result.play_result,
                full_combo=result.full_combo,
                full_perfect=result.full_perfect,
            )
    return best


def calculate_best30(
    results: list[UserMusicResult],
    table: ConstantsTable,
    resolve_meta: Callable[[int, str, ChartConstant], MusicMeta] | None = None,
) -> B30Result:
    best = _best_user_music_results(results)
    entries: list[B30Entry] = []
    missing_constants = 0
    ap_count = 0
    fc_count = 0

    for result in best.values():
        play_result = normalize_play_result(result)
        if play_result not in {"AP", "FC"}:
            continue
        constant = table.get(result.music_id, result.difficulty)
        if constant is None:
            missing_constants += 1
            continue
        if play_result == "AP":
            ap_count += 1
        else:
            fc_count += 1
        meta = (
            resolve_meta(result.music_id, result.difficulty, constant)
            if resolve_meta
            else MusicMeta(result.music_id)
        )
        title = (
            meta.title
            or constant.title
            or constant.jp_title
            or f"歌曲 #{result.music_id}"
        )
        entries.append(
            B30Entry(
                rank=0,
                music_id=result.music_id,
                title=title,
                difficulty=result.difficulty,
                difficulty_label=difficulty_label(result.difficulty),
                level=constant.level,
                constant=constant.constant,
                user_rating=user_rating(constant.constant, play_result),
                play_result=play_result,
                note_count=constant.note_count,
                assetbundle_name=meta.assetbundle_name,
                jacket_uri=meta.jacket_uri,
                published_at=meta.published_at,
            )
        )

    # 排序规则保持和 Moebot-NEXT 一致，避免同分情况下展示顺序漂移。
    entries.sort(
        key=lambda item: (
            -item.user_rating,
            -item.constant,
            -PLAY_RESULT_ORDER.get(item.play_result, 0),
            item.music_id,
            DIFFICULTY_ORDER.get(item.difficulty, 99),
        )
    )
    entries = entries[:B30_LIMIT]
    for index, entry in enumerate(entries, start=1):
        entry.rank = index
    average = (
        sum(entry.user_rating for entry in entries) / len(entries)
        if entries
        else 0
    )
    return B30Result(
        entries=entries,
        average=average,
        candidate_count=ap_count + fc_count,
        ap_count=ap_count,
        fc_count=fc_count,
        missing_constants_count=missing_constants,
        total_result_count=len(best),
    )


def parse_user_music_results(payload: Any) -> list[UserMusicResult]:
    results: list[UserMusicResult] = []
    if not isinstance(payload, list):
        return results
    for item in payload:
        if not isinstance(item, dict):
            continue
        music_id = _as_int(item.get("musicId"))
        difficulty = str(
            item.get("musicDifficultyType") or item.get("musicDifficulty") or ""
        )
        results.append(
            UserMusicResult(
                music_id=music_id,
                difficulty=difficulty,
                play_result=str(item.get("playResult") or ""),
                full_combo=bool(item.get("fullComboFlg")),
                full_perfect=bool(item.get("fullPerfectFlg")),
            )
        )
    return results


def parse_constants_csv(content: str) -> list[ChartConstant]:
    reader = csv.reader(StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        return []
    columns = {
        name.strip().removeprefix("\ufeff").lower(): index
        for index, name in enumerate(header)
    }
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
        entries.append(
            ChartConstant(
                music_id=music_id,
                difficulty=difficulty,
                constant=constant,
                level=_parse_int(_csv_column(row, columns, "level")),
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
        self._cache: ConstantsTable | None = None
        self._loaded_at = 0.0
        self._source_url = ""

    async def get_table(self) -> ConstantsTable:
        settings = get_settings()
        source_url = settings.b30_constants_url or DEFAULT_B30_CONSTANTS_URL
        interval = settings.b30_constants_refresh_interval_seconds
        now = time.time()
        if (
            self._cache is not None
            and self._source_url == source_url
            and now - self._loaded_at < interval
        ):
            return self._cache
        try:
            response = await AsyncHttpx.get(
                source_url,
                timeout=settings.b30_constants_timeout_seconds,
            )
        except Exception as exc:
            raise ValueError("B30 定数 CSV 请求失败") from exc
        table = ConstantsTable(parse_constants_csv(response.text))
        if not table.entries:
            raise ValueError("B30 定数 CSV 没有可用数据")
        self._cache = table
        self._loaded_at = now
        self._source_url = source_url
        return table

    def source_url(self) -> str:
        return (
            self._source_url
            or get_settings().b30_constants_url
            or DEFAULT_B30_CONSTANTS_URL
        )


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


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


b30_constants_provider = B30ConstantsProvider()
