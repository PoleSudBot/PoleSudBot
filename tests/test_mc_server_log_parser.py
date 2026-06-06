from __future__ import annotations

from datetime import datetime

from zhenxun.plugins.mc_server.log_parser import parse_paper_log_line


def test_parse_paper_log_line_detects_join_leave_and_chat():
    now = datetime(2026, 5, 16, 20, 0, 0)

    joined = parse_paper_log_line(
        "[20:01:02] [Server thread/INFO]: Steve joined the game",
        now,
    )
    left = parse_paper_log_line(
        "[20:03:04] [Server thread/INFO]: Steve left the game",
        now,
    )
    chat = parse_paper_log_line(
        "[20:05:06] [Async Chat Thread - #1/INFO]: <Steve> hello",
        now,
    )

    assert joined is not None
    assert joined.type == "join"
    assert joined.player_name == "Steve"
    assert joined.occurred_at == datetime(2026, 5, 16, 20, 1, 2)
    assert left is not None
    assert left.type == "leave"
    assert chat is not None
    assert chat.type == "chat"
    assert chat.message == "hello"


def test_parse_paper_log_line_ignores_login_and_detects_not_secure_chat():
    now = datetime(2026, 5, 16, 20, 0, 0)

    login = parse_paper_log_line(
        "[20:01:02] [Server thread/INFO]: Alex[/127.0.0.1:12345] "
        "logged in with entity id 1 at ([world]0.0, 64.0, 0.0)",
        now,
    )
    chat = parse_paper_log_line(
        "[20:05:06] [Async Chat Thread - #1/INFO]: [Not Secure] <Alex> ping",
        now,
    )

    assert login is None
    assert chat is not None
    assert chat.type == "chat"
    assert chat.player_name == "Alex"
    assert chat.message == "ping"


def test_parse_paper_log_line_ignores_authme_login():
    line = "[21:40:00] [Server thread/INFO]: [AuthMe] Alex logged in 127.0.0.1"

    assert parse_paper_log_line(line) is None


def test_parse_paper_log_line_ignores_unrelated_lines():
    assert parse_paper_log_line("[20:00:00] [Server thread/INFO]: Done") is None
