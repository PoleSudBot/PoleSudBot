from __future__ import annotations

from nonebot_plugin_alconna import Alconna, Args, At, CommandMeta, MultiVar, Text

from .constants import SERVER_SET


def _command_meta() -> CommandMeta:
    return CommandMeta(compact=True, strict=False)


def best30_command() -> Alconna:
    command = Alconna(
        "b30",
        Args["parts", MultiVar(Text | At, "*")],
        meta=_command_meta(),
    )
    # 区服短前缀只作为快捷方式，主入口仍保持 b30 / pjskb30。
    for server in sorted(SERVER_SET):
        command.shortcut(f"{server}b30", {"args": [server]})
    return command


def text_words_from_parts(parts: tuple[Text | At, ...]) -> list[str]:
    words: list[str] = []
    for part in parts:
        if isinstance(part, Text):
            words.extend(part.text.split())
        elif isinstance(part, str):
            words.extend(part.split())
    return words


def first_at_from_parts(
    parts: tuple[Text | At, ...],
    *,
    self_id: str = "",
) -> str | None:
    for part in parts:
        if isinstance(part, At):
            target = part.target if part.flag == "user" else None
        else:
            target = getattr(part, "target", None)
        if target and str(target) != "all" and str(target) != str(self_id):
            return str(target)
    return None


def resolve_best30_query(
    parts: tuple[Text | At, ...],
    *,
    self_id: str = "",
) -> tuple[str | None, str | None, str | None, str | None]:
    words = text_words_from_parts(parts)
    server = words[0].lower() if words and words[0].lower() in SERVER_SET else None
    if server:
        words = words[1:]
    at_target = first_at_from_parts(parts, self_id=self_id)
    game_id = words[0] if words else None
    if len(words) > 1:
        return server, game_id, at_target, "用法: b30 [区服] [游戏ID|@用户]"
    if game_id and at_target:
        return server, game_id, at_target, "B30 查询不能同时指定游戏ID和 @用户"
    return server, game_id, at_target, None
