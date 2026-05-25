from __future__ import annotations

from nonebot_plugin_alconna import At, Text

from zhenxun.plugins.mc_server.command_schema import (
    mcbind_command,
    mcchart_command,
    mcinfo_command,
    mcrcon_command,
    mcsend_command,
    mctime_command,
    mctoggle_command,
    mcwhitelist_command,
    resolve_mctime_query,
)


def _texts(parts: tuple[Text | At, ...]) -> list[str]:
    return [part.text for part in parts if isinstance(part, Text)]


def test_mc_commands_accept_common_short_aliases():
    assert mcsend_command().parse("mcsend hello").matched
    assert mcrcon_command().parse("mcrcon list").matched
    assert mcwhitelist_command().parse("mcwhitelist Steve").matched

    assert mcsend_command().parse("mcs hello").matched
    assert mcrcon_command().parse("mcr list").matched
    assert mcwhitelist_command().parse("mcw Steve").matched
    assert mcinfo_command().parse("mci").matched
    assert mctime_command().parse("mct 今日").matched
    assert mcchart_command().parse("mcc 本周").matched
    assert mctoggle_command().parse("mctg all on").matched


def test_mcbind_keeps_empty_and_address_modes():
    empty = mcbind_command().parse("mcbind")
    address = mcbind_command().parse("mcbind mc.example.com:25565")

    assert empty.matched
    assert empty.query("parts") == ()
    assert address.matched
    assert address.query("parts") == ("mc.example.com:25565",)


def test_mcrcon_keeps_rcon_command_body_flat():
    parsed = mcrcon_command().parse("mcrcon time set day")
    alias_parsed = mcrcon_command().parse("mcr time set day")

    assert parsed.matched
    assert parsed.query("parts") == ("time", "set", "day")
    assert alias_parsed.matched
    assert alias_parsed.query("parts") == ("time", "set", "day")


def test_mcrcon_password_allows_spaces():
    parsed = mcrcon_command().parse("mcrcon passwd 123456 my secret")

    assert parsed.matched
    assert parsed.query("parts") == ("passwd", "123456", "my", "secret")


def test_mcsend_keeps_message_words():
    parsed = mcsend_command().parse("mcsend hello minecraft")

    assert parsed.matched
    assert parsed.query("message_parts") == ("hello", "minecraft")


def test_mctoggle_accepts_all_switch_words():
    parsed = mctoggle_command().parse("mctoggle all on")

    assert parsed.matched
    assert parsed.query("parts") == ("all", "on")


def test_mcwhitelist_parses_text_parts():
    parsed = mcwhitelist_command().parse("mcwhitelist Steve")

    assert parsed.matched
    assert _texts(parsed.query("parts")) == ["Steve"]


def test_mctime_target_parser_keeps_ranking_range_without_target():
    parsed = mctime_command().parse("mctime 今日")

    assert parsed.matched
    assert resolve_mctime_query(parsed.query("parts"), self_qq_id="10000") == (
        None,
        "今日",
    )


def test_mctime_target_parser_uses_ranking_default_without_args():
    parsed = mctime_command().parse("mctime")

    assert parsed.matched
    assert resolve_mctime_query(parsed.query("parts"), self_qq_id="10000") == (
        None,
        None,
    )


def test_mctime_target_parser_supports_self_keywords():
    for keyword in ["me", "我", "自己"]:
        parsed = mctime_command().parse(f"mctime {keyword} 本周")

        assert parsed.matched
        assert resolve_mctime_query(parsed.query("parts"), self_qq_id="10000") == (
            "10000",
            "本周",
        )
