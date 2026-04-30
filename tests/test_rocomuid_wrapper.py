from __future__ import annotations

import pytest

from zhenxun.plugins.rocomuid_helper import (
    QQ_LOGIN_CASE_HINT,
    SENSITIVE_FEATURE_DISCLAIMER,
    extract_rocomuid_command_head,
    get_rocomuid_local_action,
    normalize_rocomuid_command_head,
    run_rocomuid_local_action,
    should_finish_with_login_case_hint,
    should_pre_send_sensitive_disclaimer,
)


@pytest.mark.parametrize(
    ("plain_text", "command_head", "needs_disclaimer"),
    [
        ("rcuid", "rcuid", False),
        ("rc我的信息", "rc我的信息", False),
        ("rc洛克档案", "rc洛克档案", False),
        ("rc绑定uid 12345", "rc绑定uid", True),
        ("rcQQ登录", "rcQQ登录", True),
        ("RcUID", "RcUID", False),
        ("RC绑定UID 12345", "RC绑定UID", True),
        ("rc帮助", "rc帮助", False),
        ("rc图鉴迪莫", "rc图鉴迪莫", False),
        ("   ", "", False),
    ],
)
def test_rocomuid_command_policy_detects_sensitive_commands(
    plain_text: str,
    command_head: str,
    needs_disclaimer: bool,
):
    assert extract_rocomuid_command_head(plain_text) == command_head
    assert normalize_rocomuid_command_head(command_head) == command_head.lower()
    assert should_pre_send_sensitive_disclaimer(command_head) is needs_disclaimer


@pytest.mark.parametrize("plain_text", ["rcqq登录", "RCqq登录", "RcQQ登录"])
def test_rocomuid_command_policy_detects_rcqq_login_typo(plain_text: str) -> None:
    action = get_rocomuid_local_action(plain_text)

    assert should_finish_with_login_case_hint(plain_text) is True
    assert action.send_disclaimer is False
    assert action.statistics_skip is True
    assert action.finish_message == (
        f"{SENSITIVE_FEATURE_DISCLAIMER}\n\n{QQ_LOGIN_CASE_HINT}"
    )


def test_rocomuid_command_policy_allows_canonical_rcQQ_login() -> None:
    action = get_rocomuid_local_action("rcQQ登录")

    assert should_finish_with_login_case_hint("rcQQ登录") is False
    assert action.send_disclaimer is True
    assert action.finish_message is None


@pytest.mark.asyncio
async def test_run_rocomuid_local_action_sends_disclaimer_before_continuing():
    sent_messages: list[str] = []
    finished_messages: list[str] = []
    call_order: list[str] = []
    matcher_state: dict[str, object] = {}

    async def fake_send_message(message: str) -> None:
        call_order.append("send")
        sent_messages.append(message)

    async def fake_finish_message(message: str) -> None:
        call_order.append("finish")
        finished_messages.append(message)

    async def fake_continue_handler() -> int:
        call_order.append("continue")
        return 1

    result = await run_rocomuid_local_action(
        "rcQQ登录",
        matcher_state,
        send_message=fake_send_message,
        finish_message=fake_finish_message,
        continue_handler=fake_continue_handler,
    )

    assert result == 1
    assert call_order == ["send", "continue"]
    assert sent_messages == [SENSITIVE_FEATURE_DISCLAIMER]
    assert finished_messages == []
    assert matcher_state == {}


@pytest.mark.asyncio
async def test_run_rocomuid_local_action_finishes_on_rcqq_login_typo():
    sent_messages: list[str] = []
    finished_messages: list[str] = []
    continue_called = False
    matcher_state: dict[str, object] = {}

    async def fake_send_message(message: str) -> None:
        sent_messages.append(message)

    async def fake_finish_message(message: str) -> None:
        finished_messages.append(message)

    async def fake_continue_handler() -> int:
        nonlocal continue_called
        continue_called = True
        return 1

    result = await run_rocomuid_local_action(
        "rcqq登录",
        matcher_state,
        send_message=fake_send_message,
        finish_message=fake_finish_message,
        continue_handler=fake_continue_handler,
    )

    assert result is None
    assert sent_messages == []
    assert finished_messages == [
        f"{SENSITIVE_FEATURE_DISCLAIMER}\n\n{QQ_LOGIN_CASE_HINT}"
    ]
    assert continue_called is False
    assert matcher_state["_statistics_skip"] is True
