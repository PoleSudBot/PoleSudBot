from __future__ import annotations

from dataclasses import dataclass, field
import re
import shlex

from nonebot.adapters.onebot.v11 import MessageEvent

from .constants import (
    ALL_SERVERS_KEYWORD,
    SERVER_SET,
    normalize_deck_difficulty,
    normalize_live_type,
)


@dataclass
class ParsedCommand:
    action: str
    raw_text: str
    server: str | None = None
    game_id: str | None = None
    target_user_id: str | None = None
    event_id: int | None = None
    music_id: int | None = None
    difficulty: str | None = None
    live_type: str | None = None
    admin_subaction: str | None = None
    admin_target_type: str | None = None
    admin_value: str | None = None
    query_text: str | None = None
    alias: str | None = None
    all_servers: bool = False
    global_scope: bool = False
    card_ids: list[int] = field(default_factory=list)
    live_id: int | None = None
    manga_id: int | None = None
    multiplier_values: list[int] = field(default_factory=list)
    force_refresh: bool = False
    error: str | None = None


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_at_targets(event: MessageEvent) -> list[str]:
    targets: list[str] = []
    for segment in event.get_message():
        if segment.type != "at":
            continue
        qq = segment.data.get("qq")
        if qq and qq != "all":
            targets.append(str(qq))
    return targets


def _match_command(
    text: str, names: tuple[str, ...]
) -> tuple[str | None, str] | None:
    for name in names:
        matched = re.fullmatch(
            rf"(?:(?P<prefix>cn|jp|tw)\s*)?{re.escape(name)}(?:\s+(?P<rest>.*))?",
            text,
        )
        if matched:
            return matched.group("prefix"), matched.group("rest") or ""
    return None


def _consume_server(prefix: str | None, rest: str) -> tuple[str | None, str]:
    if prefix:
        return prefix, rest.strip()
    if not rest:
        return None, ""
    parts = rest.split(" ", 1)
    if parts[0] in SERVER_SET:
        return parts[0], parts[1].strip() if len(parts) > 1 else ""
    return None, rest.strip()


def _normalize_qq(value: str | None, at_targets: list[str]) -> str | None:
    if value:
        return value.lstrip("@")
    if at_targets:
        return at_targets[0]
    return None


def _parse_optional_event_command(
    text: str,
    names: tuple[str, ...],
    *,
    action: str,
    error_message: str,
) -> ParsedCommand | None:
    matched = _match_command(text, names)
    if not matched:
        return None
    prefix, rest = matched
    server, rest = _consume_server(prefix, rest)
    rest = rest.strip()
    if not rest:
        return ParsedCommand(action, text, server=server)
    parts = rest.split()
    if len(parts) != 1 or not parts[0].isdigit():
        return ParsedCommand(action, text, server=server, error=error_message)
    return ParsedCommand(action, text, server=server, event_id=int(parts[0]))


def _parse_toggle_command(text: str, names: tuple[str, ...], *, action: str) -> ParsedCommand | None:
    matched = _match_command(text, names)
    if not matched:
        return None
    prefix, rest = matched
    parts = rest.split()
    if not parts:
        return ParsedCommand(action, text, error=f"用法: {names[0]} <开启|关闭|状态> [区服]")
    action_map = {"开启": "enable", "关闭": "disable", "状态": "status"}
    if parts[0] not in action_map:
        return ParsedCommand(action, text, error=f"用法: {names[0]} <开启|关闭|状态> [区服]")
    server, extra = _consume_server(prefix, " ".join(parts[1:]))
    if extra:
        return ParsedCommand(action, text, server=server, error=f"用法: {names[0]} <开启|关闭|状态> [区服]")
    return ParsedCommand(action, text, server=server, admin_subaction=action_map[parts[0]])


def _parse_live_subscription(text: str) -> ParsedCommand | None:
    matched = _match_command(text, ("订阅live提醒",))
    if matched:
        prefix, rest = matched
        server, extra = _consume_server(prefix, rest)
        if extra:
            return ParsedCommand("live_subscribe", text, server=server, error="用法: 订阅live提醒 [区服]")
        return ParsedCommand("live_subscribe", text, server=server, admin_subaction="subscribe")
    matched = _match_command(text, ("取消订阅live提醒",))
    if matched:
        prefix, rest = matched
        server, extra = _consume_server(prefix, rest)
        if extra:
            return ParsedCommand("live_subscribe", text, server=server, error="用法: 取消订阅live提醒 [区服]")
        return ParsedCommand("live_subscribe", text, server=server, admin_subaction="unsubscribe")
    return None


def _parse_story(text: str) -> ParsedCommand | None:
    matched = re.fullmatch(r"活动剧情(?:\s+(?P<rest>.*))?", text)
    if not matched:
        return None
    rest = (matched.group("rest") or "").strip()
    if not rest:
        return ParsedCommand("story", text, error="用法: 活动剧情 <活动ID> [强制刷新]")
    parts = rest.split()
    force_refresh_indexes = [
        index for index, part in enumerate(parts) if part == "强制刷新"
    ]
    if len(force_refresh_indexes) > 1:
        return ParsedCommand(
            "story",
            text,
            error="用法: 活动剧情 <活动ID> [强制刷新]",
        )
    force_refresh = bool(force_refresh_indexes)
    if force_refresh:
        parts.pop(force_refresh_indexes[0])
    if len(parts) != 1 or not parts[0].isdigit():
        return ParsedCommand(
            "story",
            text,
            error="用法: 活动剧情 <活动ID> [强制刷新]",
        )
    return ParsedCommand(
        "story",
        text,
        event_id=int(parts[0]),
        force_refresh=force_refresh,
    )


def _parse_manga_by_id(text: str) -> ParsedCommand | None:
    matched = re.fullmatch(r"四格\s+(?P<manga_id>\d+)", text)
    if not matched:
        return None
    return ParsedCommand("manga_by_id", text, manga_id=int(matched.group("manga_id")))


def _parse_multiplier(text: str) -> ParsedCommand | None:
    matched = re.fullmatch(r"倍率(?:\s+(?P<rest>.*))?", text)
    if not matched:
        return None
    rest = (matched.group("rest") or "").strip()
    if not rest:
        return ParsedCommand(
            "multiplier",
            text,
            error="用法: 倍率 <a> <b> <c> <d> <e>",
        )
    parts = rest.split()
    if len(parts) != 5 or any(not part.isdigit() for part in parts):
        return ParsedCommand(
            "multiplier",
            text,
            error="用法: 倍率 <a> <b> <c> <d> <e>",
        )
    return ParsedCommand(
        "multiplier",
        text,
        multiplier_values=[int(part) for part in parts],
    )


def _parse_character(text: str) -> ParsedCommand | None:
    matched = re.fullmatch(r"查角色(?:\s+(?P<query>.*))?", text)
    if not matched:
        return None
    query = (matched.group("query") or "").strip()
    if not query:
        return ParsedCommand("character", text, error="用法: 查角色 <角色ID|名称|别名>")
    parts = query.split()
    force_refresh_indexes = [
        index for index, part in enumerate(parts) if part == "强制刷新"
    ]
    if len(force_refresh_indexes) > 1:
        return ParsedCommand(
            "character",
            text,
            error="用法: 查角色 <角色ID|名称|别名> [强制刷新]",
        )
    force_refresh = bool(force_refresh_indexes)
    if force_refresh:
        parts.pop(force_refresh_indexes[0])
    if not parts:
        return ParsedCommand(
            "character",
            text,
            error="用法: 查角色 <角色ID|名称|别名> [强制刷新]",
        )
    return ParsedCommand(
        "character",
        text,
        query_text=" ".join(parts),
        force_refresh=force_refresh,
    )


def _parse_alias(text: str) -> ParsedCommand | None:
    for name, target_type in (("角色别名", "character"), ("歌曲别名", "music")):
        matched = re.fullmatch(rf"{re.escape(name)}(?:\s+(?P<rest>.*))?", text)
        if not matched:
            continue
        rest = (matched.group("rest") or "").strip()
        if not rest:
            return ParsedCommand(
                "alias",
                text,
                error=f"用法: {name} <关键词> 或 {name} <查询|添加|删除> ...",
            )
        try:
            tokens = shlex.split(rest)
        except ValueError as exc:
            return ParsedCommand("alias", text, error=f"参数解析失败: {exc}")
        action_map = {"查询": "query", "添加": "add", "删除": "remove"}
        if tokens[0] not in action_map:
            return ParsedCommand(
                "alias",
                text,
                admin_target_type=target_type,
                admin_subaction="query",
                query_text=rest,
            )
        operation = action_map[tokens[0]]
        remaining = tokens[1:]
        global_scope = False
        if remaining and remaining[0] in {"全局", "global"}:
            global_scope = True
            remaining = remaining[1:]
        if operation == "query":
            if not remaining:
                return ParsedCommand("alias", text, error=f"用法: {name} 查询 <关键词>")
            return ParsedCommand(
                "alias",
                text,
                admin_target_type=target_type,
                admin_subaction=operation,
                query_text=" ".join(remaining),
                global_scope=global_scope,
            )
        if operation == "add":
            if len(remaining) < 2:
                return ParsedCommand("alias", text, error=f"用法: {name} 添加 [全局] <目标> <别名>")
            return ParsedCommand(
                "alias",
                text,
                admin_target_type=target_type,
                admin_subaction=operation,
                query_text=remaining[0],
                alias=" ".join(remaining[1:]),
                global_scope=global_scope,
            )
        if not remaining:
            return ParsedCommand("alias", text, error=f"用法: {name} 删除 [全局] <别名>")
        return ParsedCommand(
            "alias",
            text,
            admin_target_type=target_type,
            admin_subaction=operation,
            alias=" ".join(remaining),
            global_scope=global_scope,
        )
    return None


def _parse_activity_deck(
    text: str, at_targets: list[str]
) -> ParsedCommand | None:
    matched = _match_command(text, ("活动组卡",))
    if not matched:
        return None
    prefix, rest = matched
    server, rest = _consume_server(prefix, rest)
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        return ParsedCommand(
            action="activity_deck",
            raw_text=text,
            server=server,
            error=f"参数解析失败: {exc}",
        )

    event_id = None
    music_id = None
    difficulty = None
    live_type = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--music", "--music-id"}:
            if index + 1 >= len(tokens) or not tokens[index + 1].isdigit():
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="--music 需要一个数字参数",
                )
            if music_id is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="歌曲ID参数重复",
                )
            music_id = int(tokens[index + 1])
            index += 2
            continue
        if token.startswith("--music="):
            value = token.partition("=")[2]
            if not value.isdigit():
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="--music 需要一个数字参数",
                )
            if music_id is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="歌曲ID参数重复",
                )
            music_id = int(value)
            index += 1
            continue
        if token == "--difficulty":
            if index + 1 >= len(tokens):
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="--difficulty 需要一个参数",
                )
            if difficulty is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="难度参数重复",
                )
            difficulty = normalize_deck_difficulty(tokens[index + 1])
            if not difficulty:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="难度仅支持 easy/normal/hard/expert/master/append 及其缩写",
                )
            index += 2
            continue
        if token.startswith("--difficulty="):
            if difficulty is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="难度参数重复",
                )
            difficulty = normalize_deck_difficulty(token.partition("=")[2])
            if not difficulty:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="难度仅支持 easy/normal/hard/expert/master/append 及其缩写",
                )
            index += 1
            continue
        if token in {"--live-type", "--live_type"}:
            if index + 1 >= len(tokens):
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="--live-type 需要一个参数",
                )
            if live_type is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="模式参数重复",
                )
            live_type = normalize_live_type(tokens[index + 1])
            if not live_type:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="模式仅支持 multi/solo/auto/cheerful 及中文别名",
                )
            index += 2
            continue
        if token.startswith("--live-type=") or token.startswith("--live_type="):
            if live_type is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="模式参数重复",
                )
            live_type = normalize_live_type(token.partition("=")[2])
            if not live_type:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="模式仅支持 multi/solo/auto/cheerful 及中文别名",
                )
            index += 1
            continue
        if token.isdigit():
            if event_id is None:
                event_id = int(token)
                index += 1
                continue
            if music_id is None:
                music_id = int(token)
                index += 1
                continue
            return ParsedCommand(
                action="activity_deck",
                raw_text=text,
                server=server,
                error="活动组卡最多只能提供一个活动ID和一个歌曲ID",
            )
        normalized_difficulty = normalize_deck_difficulty(token)
        if normalized_difficulty:
            if difficulty is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="难度参数重复",
                )
            difficulty = normalized_difficulty
            index += 1
            continue
        normalized_live_type = normalize_live_type(token)
        if normalized_live_type:
            if live_type is not None:
                return ParsedCommand(
                    action="activity_deck",
                    raw_text=text,
                    server=server,
                    error="模式参数重复",
                )
            live_type = normalized_live_type
            index += 1
            continue
        return ParsedCommand(
            action="activity_deck",
            raw_text=text,
            server=server,
            error=f"无法识别的参数: {token}",
        )

    if len(at_targets) > 1:
        return ParsedCommand(
            action="activity_deck",
            raw_text=text,
            server=server,
            error="活动组卡最多只能指定一个 @ 用户",
        )

    return ParsedCommand(
        action="activity_deck",
        raw_text=text,
        server=server,
        target_user_id=at_targets[0] if at_targets else None,
        event_id=event_id,
        music_id=music_id,
        difficulty=difficulty,
        live_type=live_type,
    )


def _parse_admin(text: str, at_targets: list[str]) -> ParsedCommand | None:
    update_matched = re.fullmatch(
        rf"(?:(?P<prefix>cn|jp|tw)\s*)?pjsk\s+update(?:\s+(?P<rest>.*))?",
        text,
    )
    if update_matched:
        prefix = update_matched.group("prefix")
        rest = (update_matched.group("rest") or "").strip()
        if prefix:
            if not rest:
                return ParsedCommand("update", text, server=prefix)
            return ParsedCommand("update", text, server=prefix, error="pjsk update 只能指定一个区服或 all")
        if not rest:
            return ParsedCommand("update", text)
        if rest in SERVER_SET:
            return ParsedCommand("update", text, server=rest)
        if rest == ALL_SERVERS_KEYWORD:
            return ParsedCommand("update", text, all_servers=True)
        return ParsedCommand("update", text, error="用法: pjsk update [区服|all]")

    test_matched = re.fullmatch(
        rf"(?:(?P<prefix>cn|jp|tw)\s*)?pjsk\s+test\s+(?P<kind>live提醒|新卡上线提醒|新卡提醒)(?:\s+(?P<rest>.*))?",
        text,
    )
    if test_matched:
        prefix = test_matched.group("prefix")
        kind = test_matched.group("kind")
        server, rest = _consume_server(prefix, (test_matched.group("rest") or "").strip())
        tokens = rest.split() if rest else []
        if kind == "live提醒":
            if len(tokens) > 1 or (tokens and not tokens[0].isdigit()):
                return ParsedCommand("test_live", text, server=server, error="用法: pjsk test live提醒 [区服] [live_id]")
            return ParsedCommand(
                "test_live",
                text,
                server=server,
                live_id=int(tokens[0]) if tokens else None,
            )
        if any(not token.isdigit() for token in tokens):
            return ParsedCommand(
                "test_new_card",
                text,
                server=server,
                error="用法: pjsk test 新卡上线提醒 [区服] [card_id...]",
            )
        return ParsedCommand(
            "test_new_card",
            text,
            server=server,
            card_ids=[int(token) for token in tokens],
        )

    if not text.startswith("pjsk"):
        return None
    tokens = text.split()
    if not tokens:
        return None

    if len(tokens) >= 3 and tokens[1] == "blacklist":
        if len(tokens) < 4:
            return ParsedCommand("admin_blacklist", text, error="用法: pjsk blacklist <add|remove|check> <uid|qq> ...")
        subaction = tokens[2]
        target_type = tokens[3]
        if target_type == "uid":
            if len(tokens) < 6:
                return ParsedCommand("admin_blacklist", text, error="用法: pjsk blacklist <add|remove|check> uid <区服> <uid>")
            server = tokens[4]
            value = tokens[5]
            if server not in SERVER_SET:
                return ParsedCommand("admin_blacklist", text, error="UID 黑名单需要指定 cn/jp/tw 区服")
            return ParsedCommand(
                "admin_blacklist",
                text,
                server=server,
                admin_subaction=subaction,
                admin_target_type=target_type,
                admin_value=value,
            )
        if target_type == "qq":
            value = tokens[4] if len(tokens) >= 5 else None
            value = _normalize_qq(value, at_targets)
            if not value:
                return ParsedCommand("admin_blacklist", text, error="QQ 黑名单需要提供 QQ 或 @用户")
            return ParsedCommand(
                "admin_blacklist",
                text,
                admin_subaction=subaction,
                admin_target_type=target_type,
                admin_value=value,
            )
        return ParsedCommand("admin_blacklist", text, error="黑名单只支持 uid 或 qq")

    if len(tokens) >= 3 and (tokens[1] == "查询绑定" or (tokens[1] == "bind" and tokens[2] == "query")):
        offset = 2 if tokens[1] == "查询绑定" else 3
        if len(tokens) <= offset:
            return ParsedCommand("admin_query_binding", text, error="用法: pjsk 查询绑定 <uid|qq> ...")
        target_type = tokens[offset]
        if target_type == "uid":
            remaining = tokens[offset + 1 :]
            server = None
            value = None
            if remaining:
                if remaining[0] in SERVER_SET:
                    server = remaining[0]
                    remaining = remaining[1:]
                if remaining:
                    value = remaining[0]
            if not value:
                return ParsedCommand("admin_query_binding", text, error="用法: pjsk 查询绑定 uid [区服] <uid>")
            return ParsedCommand(
                "admin_query_binding",
                text,
                server=server,
                admin_target_type=target_type,
                admin_value=value,
            )
        if target_type == "qq":
            value = tokens[offset + 1] if len(tokens) > offset + 1 else None
            value = _normalize_qq(value, at_targets)
            if not value:
                return ParsedCommand("admin_query_binding", text, error="用法: pjsk 查询绑定 qq <qq|@用户>")
            return ParsedCommand(
                "admin_query_binding",
                text,
                admin_target_type=target_type,
                admin_value=value,
            )
        return ParsedCommand("admin_query_binding", text, error="查询绑定只支持 uid 或 qq")

    return None


def parse_command(event: MessageEvent) -> ParsedCommand | None:
    text = normalize_text(event.get_plaintext()).lower()
    at_targets = extract_at_targets(event)

    admin = _parse_admin(text, at_targets)
    if admin:
        return admin

    matched = _match_command(text, ("个人档案",))
    if matched:
        prefix, rest = matched
        server, rest = _consume_server(prefix, rest)
        if rest:
            return ParsedCommand("personal_archive", text, server=server, error="个人档案不需要额外参数")
        return ParsedCommand("personal_archive", text, server=server)

    matched = _match_command(text, ("查询档案", "档案查询"))
    if matched:
        prefix, rest = matched
        server, rest = _consume_server(prefix, rest)
        if len(at_targets) > 1:
            return ParsedCommand("query_archive", text, server=server, error="查询档案最多只能指定一个 @ 用户")
        rest = rest.strip()
        if at_targets and rest:
            return ParsedCommand("query_archive", text, server=server, error="查询档案不能同时指定游戏ID和 @用户")
        if not at_targets and not rest:
            return ParsedCommand("query_archive", text, server=server, error="请提供游戏ID或 @用户")
        if at_targets:
            return ParsedCommand("query_archive", text, server=server, target_user_id=at_targets[0])
        return ParsedCommand("query_archive", text, server=server, game_id=rest)

    matched = _match_command(text, ("绑定",))
    if matched:
        prefix, rest = matched
        server, rest = _consume_server(prefix, rest)
        game_id = rest.strip()
        if not game_id:
            return ParsedCommand("bind", text, server=server, error="用法: 绑定 [区服] <游戏ID>")
        return ParsedCommand("bind", text, server=server, game_id=game_id)

    matched = _match_command(text, ("解绑",))
    if matched:
        prefix, rest = matched
        server, rest = _consume_server(prefix, rest)
        if rest:
            return ParsedCommand("unbind", text, server=server, error="用法: 解绑 [区服]")
        return ParsedCommand("unbind", text, server=server)

    matched = _match_command(text, ("默认区服",))
    if matched:
        prefix, rest = matched
        if prefix:
            return ParsedCommand("default_server", text, error="默认区服命令不支持区服前缀")
        rest = rest.strip()
        if rest and rest not in SERVER_SET:
            return ParsedCommand("default_server", text, error="默认区服只支持 cn / jp / tw")
        return ParsedCommand("default_server", text, server=rest or None)

    if text == "给看":
        return ParsedCommand("visibility", text, admin_value="allow")
    if text == "不给看":
        return ParsedCommand("visibility", text, admin_value="deny")

    if text == "随机四格":
        return ParsedCommand("random_manga", text)

    if manga := _parse_manga_by_id(text):
        return manga
    if story := _parse_story(text):
        return story
    if multiplier := _parse_multiplier(text):
        return multiplier
    if alias := _parse_alias(text):
        return alias
    if live_toggle := _parse_toggle_command(text, ("live提醒",), action="live_toggle"):
        return live_toggle
    if new_card_toggle := _parse_toggle_command(
        text,
        ("新卡上线提醒", "新卡提醒"),
        action="new_card_toggle",
    ):
        return new_card_toggle
    if subscription := _parse_live_subscription(text):
        return subscription

    prediction = _parse_optional_event_command(
        text,
        ("sk预测",),
        action="prediction",
        error_message="sk预测只支持一个可选活动ID",
    )
    if prediction:
        return prediction

    ycx = _parse_optional_event_command(
        text,
        ("ycx",),
        action="ycx",
        error_message="ycx只支持一个可选活动ID",
    )
    if ycx:
        return ycx

    return _parse_activity_deck(text, at_targets)
