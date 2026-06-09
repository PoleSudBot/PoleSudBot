from __future__ import annotations

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai.command_schema import (
    best30_command,
    resolve_best30_query,
)
from nonebot_plugin_alconna import At


def test_best30_command_accepts_main_alias_and_compact_server_shortcuts():
    assert best30_command().parse("b30").matched
    assert best30_command().parse("b30 jp 1234567890123").matched
    assert best30_command().parse("jpb30 1234567890123").matched
    assert best30_command().parse("cnb30").matched


def test_resolve_best30_query_keeps_server_and_game_id():
    parsed = best30_command().parse("b30 jp 1234567890123")

    assert parsed.matched
    assert resolve_best30_query(parsed.query("parts")) == (
        "jp",
        "1234567890123",
        None,
        None,
    )


def test_resolve_best30_query_rejects_extra_words():
    parsed = best30_command().parse("b30 jp 123 456")

    assert parsed.matched
    assert resolve_best30_query(parsed.query("parts")) == (
        "jp",
        "123",
        None,
        "用法: b30 [区服] [游戏ID]",
    )


def test_resolve_best30_query_rejects_at_target():
    assert resolve_best30_query((At("user", "2233"),)) == (
        None,
        None,
        "2233",
        "B30 不支持 @用户查询，请使用游戏ID或查询自己的绑定账号",
    )
