from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class MultiplierResult:
    leader: int
    internal: int
    effective_percent: Decimal
    multiplier: Decimal


def calculate_multiplier(values: list[int]) -> MultiplierResult:
    if len(values) != 5:
        raise ValueError("倍率必须提供 5 个数值")
    leader = values[0]
    internal = sum(values)
    effective_percent = Decimal(leader) + Decimal(sum(values[1:])) * Decimal("0.2")
    multiplier = (Decimal("100") + effective_percent) / Decimal("100")
    return MultiplierResult(
        leader=leader,
        internal=internal,
        effective_percent=effective_percent.quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ),
        multiplier=multiplier.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )
