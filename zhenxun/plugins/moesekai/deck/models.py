from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DeckMode = Literal["event", "custom", "mysekai", "strongest", "challenge", "wl3"]
StrongestTarget = Literal["power", "skill"]


@dataclass(frozen=True)
class DeckModeSpec:
    mode: DeckMode
    label: str
    command_names: tuple[str, ...]


DECK_MODE_SPECS: tuple[DeckModeSpec, ...] = (
    DeckModeSpec(mode="event", label="活动组卡", command_names=("活动组卡",)),
    DeckModeSpec(mode="custom", label="组卡", command_names=("组卡",)),
    DeckModeSpec(mode="mysekai", label="烤森组卡", command_names=("烤森组卡",)),
    DeckModeSpec(mode="strongest", label="最强组卡", command_names=("最强组卡",)),
    DeckModeSpec(mode="challenge", label="挑战组卡", command_names=("挑战组卡",)),
)

# 组卡模式实现仅保留内部能力，对外命令入口全部关闭。
DECK_COMMAND_SPECS: tuple[DeckModeSpec, ...] = ()

DECK_MODE_SPEC_MAP = {spec.mode: spec for spec in DECK_MODE_SPECS}


def deck_mode_label(mode: DeckMode) -> str:
    return DECK_MODE_SPEC_MAP.get(mode, DeckModeSpec(mode=mode, label=mode, command_names=(mode,))).label


@dataclass(frozen=True)
class CustomCharacterQuery:
    query: str
    support_unit: str | None = None


@dataclass(frozen=True)
class CustomBonusSpec:
    kind: Literal["unit", "mixed"]
    attr: str
    unit: str | None = None
    characters: tuple[CustomCharacterQuery, ...] = ()


@dataclass(frozen=True)
class DeckCommandRequest:
    mode: DeckMode
    server: str | None
    target_user_id: str | None = None
    event_id: int | None = None
    music_query: str | None = None
    character_query: str | None = None
    free_text_query: str | None = None
    custom_bonus: CustomBonusSpec | None = None
    difficulty: str | None = None
    live_type: str | None = None
    strongest_target: StrongestTarget | None = None
    explicit_event: bool = False
    explicit_music: bool = False
    explicit_difficulty: bool = False
    explicit_live_type: bool = False


@dataclass(frozen=True)
class DeckResolvedRequest:
    mode: DeckMode
    kind_label: str
    server: str
    game_id: str
    event_id: int | None = None
    music_id: int | None = None
    character_id: int | None = None
    difficulty: str | None = None
    live_type: str | None = None
    custom_attr: str | None = None
    custom_unit: str | None = None
    custom_character_ids: tuple[int, ...] = ()
    custom_character_units: dict[int, str] = field(default_factory=dict)
    strongest_target: StrongestTarget | None = None
    leader_character_id: int | None = None
    support_character_id: int | None = None
    wl3_group_id: int | None = None


@dataclass(frozen=True)
class DeckBackendRequest:
    mode: DeckMode
    kind_label: str
    server: str
    game_id: str
    event_id: int | None = None
    music_id: int | None = None
    character_id: int | None = None
    difficulty: str | None = None
    live_type: str | None = None
    custom_attr: str | None = None
    custom_unit: str | None = None
    custom_character_ids: tuple[int, ...] = ()
    custom_character_units: dict[int, str] = field(default_factory=dict)
    strongest_target: StrongestTarget | None = None
    leader_character_id: int | None = None
    support_character_id: int | None = None
    wl3_group_id: int | None = None


def to_backend_request(request: DeckResolvedRequest) -> DeckBackendRequest:
    return DeckBackendRequest(
        mode=request.mode,
        kind_label=request.kind_label,
        server=request.server,
        game_id=request.game_id,
        event_id=request.event_id,
        music_id=request.music_id,
        character_id=request.character_id,
        difficulty=request.difficulty,
        live_type=request.live_type,
        custom_attr=request.custom_attr,
        custom_unit=request.custom_unit,
        custom_character_ids=tuple(request.custom_character_ids),
        custom_character_units=dict(request.custom_character_units),
        strongest_target=request.strongest_target,
        leader_character_id=request.leader_character_id,
        support_character_id=request.support_character_id,
        wl3_group_id=request.wl3_group_id,
    )
