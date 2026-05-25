from __future__ import annotations

from nonebot_plugin_alconna import Alconna, Args, At, CommandMeta, MultiVar, Text

MCTIME_SELF_WORDS = {"me", "我", "自己"}


def _command_meta() -> CommandMeta:
    return CommandMeta(compact=True, strict=False)


def _with_shortcuts(command: Alconna, *aliases: str) -> Alconna:
    # 单测直接调用 Alconna.parse，不经过 NoneBot matcher，所以 schema 也注册快捷命令。
    for alias in aliases:
        command.shortcut(alias, command=command.command)
    return command


def text_words_from_parts(parts: tuple[Text | At, ...]) -> list[str]:
    words: list[str] = []
    for part in parts:
        if isinstance(part, Text):
            words.extend(part.text.split())
        elif isinstance(part, str):
            words.extend(part.split())
    return words


def first_at_from_parts(parts: tuple[Text | At, ...]) -> str | None:
    # Alconna 会把 OneBot at 转为统一 At 段；保留属性兜底避免适配器差异。
    for part in parts:
        if isinstance(part, At):
            target = part.target if part.flag == "user" else None
        else:
            target = getattr(part, "target", None)
        if target and str(target) != "all":
            return str(target)
    return None


def resolve_mctime_query(
    parts: tuple[Text | At, ...],
    *,
    self_qq_id: str,
) -> tuple[str | None, str | None]:
    qq_id = first_at_from_parts(parts)
    words = text_words_from_parts(parts)
    if not qq_id and words and words[0].lower() in MCTIME_SELF_WORDS:
        # me/我/自己 是个人图的目标词，不应继续传给时间范围解析。
        qq_id = self_qq_id
        words = words[1:]
    return qq_id, " ".join(item for item in words if item).strip() or None


def mcbind_command() -> Alconna:
    return Alconna("mcbind", Args["parts", MultiVar(str, "*")], meta=_command_meta())


def mclog_command() -> Alconna:
    return Alconna("mclog", Args["path", MultiVar(str, "*")], meta=_command_meta())


def mcbluemap_command() -> Alconna:
    return Alconna(
        "mcbluemap",
        Args["parts", MultiVar(str, "*")],
        meta=_command_meta(),
    )


def mclist_command() -> Alconna:
    return Alconna("mclist", meta=_command_meta())


def mcinfo_command() -> Alconna:
    return _with_shortcuts(Alconna("mcinfo", meta=_command_meta()), "mcstatus", "mci")


def mctoggle_command() -> Alconna:
    return _with_shortcuts(
        Alconna(
            "mctoggle",
            Args["parts", MultiVar(str, "*")],
            meta=_command_meta(),
        ),
        "mctg",
    )


def mctime_command() -> Alconna:
    return _with_shortcuts(
        Alconna(
            "mctime",
            Args["parts", MultiVar(Text | At, "*")],
            meta=_command_meta(),
        ),
        "mct",
    )


def mcchart_command() -> Alconna:
    return _with_shortcuts(
        Alconna(
            "mcchart",
            Args["range_parts", MultiVar(str, "*")],
            meta=_command_meta(),
        ),
        "mcc",
    )


def mcsend_command() -> Alconna:
    return _with_shortcuts(
        Alconna(
            "mcsend",
            Args["message_parts", MultiVar(str, "*")],
            meta=_command_meta(),
        ),
        "mcs",
    )


def mcrcon_command() -> Alconna:
    # RCON 正文可能包含 set/passwd 等词，保持扁平参数避免误吞服务端命令。
    return _with_shortcuts(
        Alconna("mcrcon", Args["parts", MultiVar(str, "*")], meta=_command_meta()),
        "mcr",
    )


def mcwhitelist_command() -> Alconna:
    return _with_shortcuts(
        Alconna(
            "mcwhitelist",
            Args["parts", MultiVar(Text | At, "*")],
            meta=_command_meta(),
        ),
        "mcwhite",
        "mcw",
    )
