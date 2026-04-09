from __future__ import annotations

import re
import shlex
from typing import Sequence

from ..constants import normalize_deck_difficulty, normalize_live_type
from .models import (
    CustomBonusSpec,
    CustomCharacterQuery,
    DeckCommandRequest,
    DeckMode,
    StrongestTarget,
    deck_mode_label,
)

ATTR_ALIASES = {
    "绿": "pure",
    "纯": "pure",
    "pure": "pure",
    "蓝": "cool",
    "cool": "cool",
    "粉": "cute",
    "cute": "cute",
    "黄": "happy",
    "happy": "happy",
    "紫": "mysterious",
    "mysterious": "mysterious",
}

UNIT_ALIASES = {
    "ln": "leo_need",
    "leo_need": "leo_need",
    "mmj": "more_more_jump",
    "more_more_jump": "more_more_jump",
    "vbs": "vivid_bad_squad",
    "vivid_bad_squad": "vivid_bad_squad",
    "wxs": "wonderlands_showtime",
    "ws": "wonderlands_showtime",
    "wonderlands_showtime": "wonderlands_showtime",
    "25": "nightcord_at_25",
    "25h": "nightcord_at_25",
    "25ji": "nightcord_at_25",
    "n25": "nightcord_at_25",
    "nightcord_at_25": "nightcord_at_25",
    "vs": "piapro",
    "piapro": "piapro",
}

VS_SUPPORT_UNIT_PREFIXES = tuple(
    sorted(
        (
            (alias, unit)
            for alias, unit in UNIT_ALIASES.items()
            if unit != "piapro"
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

STRONGEST_SKILL_KEYWORDS = {"实效", "倍率", "skill", "时效"}
STRONGEST_POWER_KEYWORDS = {"综合力", "综合", "power"}


def parse_deck_command_request(
    *,
    raw_text: str,
    mode: DeckMode,
    server: str | None,
    rest: str,
    at_targets: Sequence[str],
) -> tuple[DeckCommandRequest | None, str | None]:
    label = deck_mode_label(mode)
    if len(at_targets) > 1:
        return None, f"{label}最多只能指定一个 @ 用户"
    try:
        return _MODE_PARSERS[mode](
            raw_text=raw_text,
            mode=mode,
            server=server,
            rest=rest.strip(),
            target_user_id=at_targets[0] if at_targets else None,
        )
    except ValueError as exc:
        return None, f"参数解析失败: {exc}"


def _tokenize(text: str) -> list[str]:
    if not text.strip():
        return []
    return shlex.split(text)


def _consume_common_tokens(
    tokens: list[str],
    *,
    label: str,
    allow_live_type: bool,
) -> tuple[list[str], str | None, bool, str | None, bool, str | None]:
    difficulty: str | None = None
    live_type: str | None = None
    explicit_difficulty = False
    explicit_live_type = False
    remaining: list[str] = []
    for token in tokens:
        normalized_difficulty = normalize_deck_difficulty(token)
        if normalized_difficulty:
            if explicit_difficulty:
                return [], None, False, None, False, "难度参数重复"
            difficulty = normalized_difficulty
            explicit_difficulty = True
            continue
        if allow_live_type:
            normalized_live_type = normalize_live_type(token)
            if normalized_live_type:
                if explicit_live_type:
                    return [], None, False, None, False, "模式参数重复"
                live_type = normalized_live_type
                explicit_live_type = True
                continue
        remaining.append(token)
    return remaining, difficulty, explicit_difficulty, live_type, explicit_live_type, None


def _parse_activity(
    *,
    raw_text: str,
    mode: DeckMode,
    server: str | None,
    rest: str,
    target_user_id: str | None,
) -> tuple[DeckCommandRequest, str | None]:
    label = deck_mode_label(mode)
    tokens = _tokenize(rest)
    event_id: int | None = None
    music_query: str | None = None
    music_tokens: list[str] = []
    difficulty: str | None = None
    live_type: str | None = None
    explicit_music = False
    explicit_difficulty = False
    explicit_live_type = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--music", "--music-id"}:
            if index + 1 >= len(tokens) or not tokens[index + 1].isdigit():
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "--music 需要一个数字参数",
                )
            if explicit_music:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "歌曲ID参数重复",
                )
            music_query = tokens[index + 1]
            explicit_music = True
            index += 2
            continue
        if token.startswith("--music="):
            value = token.partition("=")[2]
            if not value.isdigit():
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "--music 需要一个数字参数",
                )
            if explicit_music:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "歌曲ID参数重复",
                )
            music_query = value
            explicit_music = True
            index += 1
            continue
        if token == "--difficulty":
            if index + 1 >= len(tokens):
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "--difficulty 需要一个参数",
                )
            if explicit_difficulty:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "难度参数重复",
                )
            difficulty = normalize_deck_difficulty(tokens[index + 1])
            if not difficulty:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "难度仅支持 easy/normal/hard/expert/master/append 及其缩写",
                )
            explicit_difficulty = True
            index += 2
            continue
        if token.startswith("--difficulty="):
            if explicit_difficulty:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "难度参数重复",
                )
            difficulty = normalize_deck_difficulty(token.partition("=")[2])
            if not difficulty:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "难度仅支持 easy/normal/hard/expert/master/append 及其缩写",
                )
            explicit_difficulty = True
            index += 1
            continue
        if token in {"--live-type", "--live_type"}:
            if index + 1 >= len(tokens):
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "--live-type 需要一个参数",
                )
            if explicit_live_type:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "模式参数重复",
                )
            live_type = normalize_live_type(tokens[index + 1])
            if not live_type:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "模式仅支持 multi/solo/auto/cheerful 及中文别名",
                )
            explicit_live_type = True
            index += 2
            continue
        if token.startswith("--live-type=") or token.startswith("--live_type="):
            if explicit_live_type:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "模式参数重复",
                )
            live_type = normalize_live_type(token.partition("=")[2])
            if not live_type:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "模式仅支持 multi/solo/auto/cheerful 及中文别名",
                )
            explicit_live_type = True
            index += 1
            continue
        if token.isdigit():
            if event_id is None:
                event_id = int(token)
                index += 1
                continue
            if not explicit_music:
                music_query = token
                explicit_music = True
                index += 1
                continue
            music_tokens.append(token)
            index += 1
            continue
        normalized_difficulty = normalize_deck_difficulty(token)
        if normalized_difficulty:
            if explicit_difficulty:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "难度参数重复",
                )
            difficulty = normalized_difficulty
            explicit_difficulty = True
            index += 1
            continue
        normalized_live_type = normalize_live_type(token)
        if normalized_live_type:
            if explicit_live_type:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "模式参数重复",
                )
            live_type = normalized_live_type
            explicit_live_type = True
            index += 1
            continue
        music_tokens.append(token)
        index += 1

    if music_tokens:
        if explicit_music:
            return (
                DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                "歌曲参数重复",
            )
        music_query = " ".join(music_tokens)
        explicit_music = True

    return (
        DeckCommandRequest(
            mode=mode,
            server=server,
            target_user_id=target_user_id,
            event_id=event_id,
            music_query=music_query,
            difficulty=difficulty,
            live_type=live_type,
            explicit_event=event_id is not None,
            explicit_music=explicit_music,
            explicit_difficulty=explicit_difficulty,
            explicit_live_type=explicit_live_type,
        ),
        None,
    )


def _parse_custom_mixed_spec(text: str) -> tuple[CustomBonusSpec | None, str | None, str | None]:
    percent_index = text.find("%")
    at_index = text.find("@", percent_index + 1) if percent_index != -1 else -1
    if percent_index == -1 and at_index == -1:
        return None, text, None
    if percent_index == -1 or at_index == -1 or at_index <= percent_index:
        return None, None, "混活格式请使用 %角色列表 @属性"

    prefix = text[:percent_index].strip()
    character_text = text[percent_index + 1 : at_index].strip()
    tail = text[at_index + 1 :].strip()
    if not character_text:
        return None, None, "请在 % 后指定至少一个角色"

    tail_tokens = _tokenize(tail)
    if not tail_tokens:
        return None, None, "请在 @ 后指定一个有效属性"

    attr = ATTR_ALIASES.get(tail_tokens[0])
    if not attr:
        return None, None, "请在 @ 后指定一个有效属性"

    character_tokens: list[str] = []
    for token in _tokenize(character_text):
        character_tokens.extend(
            part.strip() for part in re.split(r"[，,、/|]+", token) if part.strip()
        )
    if not character_tokens:
        return None, None, "未识别到任何有效角色，请检查 % 后的角色名"
    if len(character_tokens) > 5:
        return None, None, "自定义混活最多指定5名角色"

    characters: list[CustomCharacterQuery] = []
    for token in character_tokens:
        query = token
        support_unit: str | None = None
        for prefix_alias, unit in VS_SUPPORT_UNIT_PREFIXES:
            if token.startswith(prefix_alias) and len(token) > len(prefix_alias):
                query = token[len(prefix_alias) :]
                support_unit = unit
                break
        if not query:
            return None, None, "未识别到任何有效角色，请检查 % 后的角色名"
        characters.append(CustomCharacterQuery(query=query, support_unit=support_unit))

    remaining_segments = [segment for segment in (prefix, " ".join(tail_tokens[1:])) if segment]
    return (
        CustomBonusSpec(kind="mixed", attr=attr, characters=tuple(characters)),
        " ".join(remaining_segments).strip(),
        None,
    )


def _parse_custom(
    *,
    raw_text: str,
    mode: DeckMode,
    server: str | None,
    rest: str,
    target_user_id: str | None,
) -> tuple[DeckCommandRequest, str | None]:
    label = deck_mode_label(mode)
    if not rest:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            f"用法: {label} <颜色 团体 | %角色列表 @颜色> [歌曲] [难度] [模式]",
        )

    mixed_spec, remaining_text, mixed_error = _parse_custom_mixed_spec(rest)
    if mixed_error:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            mixed_error,
        )
    if mixed_spec is not None:
        remaining_tokens, difficulty, explicit_difficulty, live_type, explicit_live_type, error = _consume_common_tokens(
            _tokenize(remaining_text or ""),
            label=label,
            allow_live_type=True,
        )
        if error:
            return (
                DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                "模式参数重复" if error == "模式参数重复" else error,
            )
        return (
            DeckCommandRequest(
                mode=mode,
                server=server,
                target_user_id=target_user_id,
                music_query=" ".join(remaining_tokens) or None,
                custom_bonus=mixed_spec,
                difficulty=difficulty,
                live_type=live_type,
                explicit_music=bool(remaining_tokens),
                explicit_difficulty=explicit_difficulty,
                explicit_live_type=explicit_live_type,
            ),
            None,
        )

    tokens = _tokenize(rest)
    remaining_tokens, difficulty, explicit_difficulty, live_type, explicit_live_type, error = _consume_common_tokens(
        tokens,
        label=label,
        allow_live_type=True,
    )
    if error:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            error,
        )
    attr: str | None = None
    unit: str | None = None
    music_tokens: list[str] = []
    for token in remaining_tokens:
        if token.isdigit():
            music_tokens.append(token)
            continue
        normalized_attr = ATTR_ALIASES.get(token)
        if normalized_attr:
            if attr is not None:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "加成属性参数重复",
                )
            attr = normalized_attr
            continue
        normalized_unit = UNIT_ALIASES.get(token)
        if normalized_unit:
            if unit is not None:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "加成团体参数重复",
                )
            unit = normalized_unit
            continue
        music_tokens.append(token)

    if attr is None and unit is None:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            f"用法: {label} <颜色 团体 | %角色列表 @颜色> [歌曲] [难度] [模式]",
        )
    if attr is None or unit is None:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            "箱活组卡需要同时提供颜色和团体，例如：组卡 绿 vbs",
        )
    return (
        DeckCommandRequest(
            mode=mode,
            server=server,
            target_user_id=target_user_id,
            music_query=" ".join(music_tokens) or None,
            custom_bonus=CustomBonusSpec(kind="unit", attr=attr, unit=unit),
            difficulty=difficulty,
            live_type=live_type,
            explicit_music=bool(music_tokens),
            explicit_difficulty=explicit_difficulty,
            explicit_live_type=explicit_live_type,
        ),
        None,
    )


def _parse_mysekai(
    *,
    raw_text: str,
    mode: DeckMode,
    server: str | None,
    rest: str,
    target_user_id: str | None,
) -> tuple[DeckCommandRequest, str | None]:
    label = deck_mode_label(mode)
    tokens = _tokenize(rest)
    if not tokens:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            None,
        )
    if len(tokens) == 1 and tokens[0].isdigit():
        return (
            DeckCommandRequest(
                mode=mode,
                server=server,
                target_user_id=target_user_id,
                event_id=int(tokens[0]),
                explicit_event=True,
            ),
            None,
        )
    return (
        DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
        f"用法: {label} [活动ID]",
    )


def _parse_strongest(
    *,
    raw_text: str,
    mode: DeckMode,
    server: str | None,
    rest: str,
    target_user_id: str | None,
) -> tuple[DeckCommandRequest, str | None]:
    tokens = _tokenize(rest)
    remaining_tokens: list[str] = []
    difficulty: str | None = None
    live_type: str | None = None
    strongest_target: StrongestTarget | None = None
    explicit_difficulty = False
    explicit_live_type = False
    for token in tokens:
        normalized_difficulty = normalize_deck_difficulty(token)
        if normalized_difficulty:
            if explicit_difficulty:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "难度参数重复",
                )
            difficulty = normalized_difficulty
            explicit_difficulty = True
            continue
        normalized_live_type = normalize_live_type(token)
        if normalized_live_type:
            if explicit_live_type:
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "模式参数重复",
                )
            live_type = normalized_live_type
            explicit_live_type = True
            continue
        if token in STRONGEST_SKILL_KEYWORDS:
            if strongest_target and strongest_target != "skill":
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "目标参数重复",
                )
            strongest_target = "skill"
            continue
        if token in STRONGEST_POWER_KEYWORDS:
            if strongest_target and strongest_target != "power":
                return (
                    DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
                    "目标参数重复",
                )
            strongest_target = "power"
            continue
        remaining_tokens.append(token)

    return (
        DeckCommandRequest(
            mode=mode,
            server=server,
            target_user_id=target_user_id,
            music_query=" ".join(remaining_tokens) or None,
            difficulty=difficulty,
            live_type=live_type,
            strongest_target=strongest_target,
            explicit_music=bool(remaining_tokens),
            explicit_difficulty=explicit_difficulty,
            explicit_live_type=explicit_live_type,
        ),
        None,
    )


def _parse_challenge(
    *,
    raw_text: str,
    mode: DeckMode,
    server: str | None,
    rest: str,
    target_user_id: str | None,
) -> tuple[DeckCommandRequest, str | None]:
    label = deck_mode_label(mode)
    tokens = _tokenize(rest)
    if not tokens:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            f"用法: {label} <角色> [歌曲] [难度]",
        )
    remaining_tokens, difficulty, explicit_difficulty, _, _, error = _consume_common_tokens(
        tokens,
        label=label,
        allow_live_type=False,
    )
    if error:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            error,
        )
    if not remaining_tokens:
        return (
            DeckCommandRequest(mode=mode, server=server, target_user_id=target_user_id),
            f"用法: {label} <角色> [歌曲] [难度]",
        )
    return (
        DeckCommandRequest(
            mode=mode,
            server=server,
            target_user_id=target_user_id,
            free_text_query=" ".join(remaining_tokens),
            difficulty=difficulty,
            explicit_difficulty=explicit_difficulty,
        ),
        None,
    )


_MODE_PARSERS = {
    "event": _parse_activity,
    "custom": _parse_custom,
    "mysekai": _parse_mysekai,
    "strongest": _parse_strongest,
    "challenge": _parse_challenge,
}
