from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cmp_to_key
import math


@dataclass(slots=True)
class PigKingEntry:
    user_id: str
    display_name: str
    collection_count: int
    reached_at: float | None = None


def normalize_user_sort_key(user_id: str) -> tuple[int, int | str]:
    return (0, int(user_id)) if str(user_id).isdigit() else (1, str(user_id))


def resolve_collection_reached_at(
    progress: Mapping[str, object] | None,
    collection_count: int,
) -> float | None:
    if not isinstance(progress, Mapping):
        return None

    try:
        progress_count = int(progress.get("count", -1))
    except (TypeError, ValueError):
        return None

    if progress_count != collection_count:
        return None

    raw_reached_at = progress.get("reached_at")
    if raw_reached_at is None:
        return None

    try:
        reached_at = float(raw_reached_at)
    except (TypeError, ValueError):
        return None

    return reached_at if math.isfinite(reached_at) else None


def _compare_pig_king_entries(left: PigKingEntry, right: PigKingEntry) -> int:
    if left.collection_count != right.collection_count:
        return -1 if left.collection_count > right.collection_count else 1

    # 只有双方都确认记录了“达到当前图鉴数”的时间时，才按先后比较；
    # 旧账本缺少时间时必须回退到既有 user_id 顺序，避免伪造历史先后。
    if left.reached_at is not None and right.reached_at is not None:
        if left.reached_at != right.reached_at:
            return -1 if left.reached_at < right.reached_at else 1

    left_sort_key = normalize_user_sort_key(left.user_id)
    right_sort_key = normalize_user_sort_key(right.user_id)
    if left_sort_key < right_sort_key:
        return -1
    if left_sort_key > right_sort_key:
        return 1
    return 0


def sort_pig_king_rankings(rankings: list[PigKingEntry]) -> list[PigKingEntry]:
    return sorted(rankings, key=cmp_to_key(_compare_pig_king_entries))
