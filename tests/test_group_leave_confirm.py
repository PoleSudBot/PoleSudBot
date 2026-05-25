from __future__ import annotations

from datetime import datetime
import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
import pytest

nonebot.init()

group_leave = importlib.import_module("zhenxun.services.group_leave")
_PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "zhenxun"
    / "builtin_plugins"
    / "admin"
    / "group_leave_confirm"
    / "__init__.py"
)
_PLUGIN_SPEC = importlib.util.spec_from_file_location(
    "_test_group_leave_confirm_plugin",
    _PLUGIN_PATH,
)
assert _PLUGIN_SPEC
assert _PLUGIN_SPEC.loader
plugin = importlib.util.module_from_spec(_PLUGIN_SPEC)
sys.modules[_PLUGIN_SPEC.name] = plugin
_PLUGIN_SPEC.loader.exec_module(plugin)


class _Finished(Exception):
    pass


class _FakeMessageBuilder:
    def __init__(
        self, content: object, messages: list[tuple[str, object, bool]]
    ) -> None:
        self.content = content
        self.messages = messages

    async def finish(self, *, reply_to: bool = False):
        self.messages.append(("finish", self.content, reply_to))
        raise _Finished

    async def send(self, *, reply_to: bool = False):
        self.messages.append(("send", self.content, reply_to))


def _patch_message_utils(monkeypatch: pytest.MonkeyPatch):
    messages: list[tuple[str, object, bool]] = []

    monkeypatch.setattr(
        plugin.MessageUtils,
        "build_message",
        lambda content: _FakeMessageBuilder(content, messages),
    )
    return messages


class _FakeRuntimeEvent:
    def __init__(
        self,
        *,
        original_message: Message,
        plain_text: str,
        role: str = "admin",
        group_id: int = 30001,
        user_id: int = 20001,
    ) -> None:
        self.original_message = original_message
        self.message = Message([MessageSegment.text(plain_text)])
        self.group_id = group_id
        self.user_id = user_id
        self.sender = SimpleNamespace(role=role)

    def get_plaintext(self) -> str:
        return self.message.extract_plain_text()

    def get_user_id(self) -> str:
        return str(self.user_id)


def test_leave_command_requires_original_at_bot_after_onebot_strip():
    original_message = Message(
        [MessageSegment.at("10001"), MessageSegment.text(" 退群")]
    )
    event = _FakeRuntimeEvent(original_message=original_message, plain_text="退群")

    assert plugin.is_group_leave_request(
        event.original_message, "10001", event.get_plaintext()
    )


@pytest.mark.asyncio
async def test_matcher_uses_original_message_for_at_bot():
    event = _FakeRuntimeEvent(
        original_message=Message(
            [MessageSegment.at("10001"), MessageSegment.text(" 退群")]
        ),
        plain_text="退群",
    )

    assert await plugin._match_group_leave_command(_FakeBot(), event)


@pytest.mark.asyncio
async def test_matcher_accepts_legacy_bot_leave_commands():
    bot = _FakeBot()
    legacy_cn_event = _FakeRuntimeEvent(
        original_message=Message(
            [MessageSegment.at("10001"), MessageSegment.text(" bot退群")]
        ),
        plain_text="bot退群",
    )
    legacy_en_event = _FakeRuntimeEvent(
        original_message=Message(
            [MessageSegment.at("10001"), MessageSegment.text(" bot leave")]
        ),
        plain_text="bot leave",
    )

    assert await plugin._match_group_leave_command(bot, legacy_cn_event)
    assert await plugin._match_group_leave_command(bot, legacy_en_event)


@pytest.mark.asyncio
async def test_matcher_accepts_compact_confirm_code_after_at():
    event = _FakeRuntimeEvent(
        original_message=Message(
            [MessageSegment.at("10001"), MessageSegment.text(" 退群1234")]
        ),
        plain_text="退群1234",
    )

    assert await plugin._match_group_leave_command(_FakeBot(), event)


@pytest.mark.asyncio
async def test_matcher_rejects_naked_and_nickname_leave_commands():
    bot = _FakeBot()
    naked_event = _FakeRuntimeEvent(
        original_message=Message([MessageSegment.text("退群")]),
        plain_text="退群",
    )
    nickname_event = _FakeRuntimeEvent(
        original_message=Message([MessageSegment.text("真寻 退群")]),
        plain_text="退群",
    )
    legacy_without_at_event = _FakeRuntimeEvent(
        original_message=Message([MessageSegment.text("bot退群")]),
        plain_text="bot退群",
    )

    assert not await plugin._match_group_leave_command(bot, naked_event)
    assert not await plugin._match_group_leave_command(bot, nickname_event)
    assert not await plugin._match_group_leave_command(bot, legacy_without_at_event)


def test_leave_command_parses_alias_and_confirm_code():
    result = plugin.parse_leave_command_text("退群 1234")

    assert result.matched is True
    assert result.confirm_code == "1234"

    result = plugin.parse_leave_command_text("退群1234")

    assert result.matched is True
    assert result.confirm_code == "1234"

    result = plugin.parse_leave_command_text("bot退群 1234")

    assert result.matched is True
    assert result.confirm_code == "1234"

    result = plugin.parse_leave_command_text("bot leave 1234")

    assert result.matched is True
    assert result.confirm_code == "1234"


def test_leave_command_rejects_invalid_confirm_code():
    result = plugin.parse_leave_command_text("退群 12")

    assert result.matched is True
    assert result.invalid_reason == "confirm_code"

    result = plugin.parse_leave_command_text("退群12")

    assert result.matched is True
    assert result.invalid_reason == "confirm_code"

    result = plugin.parse_leave_command_text("退群１２３４")

    assert result.matched is True
    assert result.invalid_reason == "confirm_code"

    result = plugin.parse_leave_command_text("bot退群 12")

    assert result.matched is True
    assert result.invalid_reason == "confirm_code"


def test_confirmation_code_is_bound_to_operator_and_consumed_once():
    group_leave.clear_leave_confirmations()
    confirmation = group_leave.create_leave_confirmation(
        "bot", "group", "operator", 60, now=100
    )

    assert (
        group_leave.consume_leave_confirmation(
            "bot", "group", "other", confirmation.code, now=101
        )
        == "missing"
    )
    assert (
        group_leave.consume_leave_confirmation(
            "bot", "group", "operator", "0000", now=101
        )
        == "mismatch"
    )
    assert (
        group_leave.consume_leave_confirmation(
            "bot", "group", "operator", confirmation.code, now=101
        )
        == "ok"
    )
    assert (
        group_leave.consume_leave_confirmation(
            "bot", "group", "operator", confirmation.code, now=101
        )
        == "missing"
    )


def test_confirmation_code_expires():
    group_leave.clear_leave_confirmations()
    confirmation = group_leave.create_leave_confirmation(
        "bot", "group", "operator", 5, now=100
    )

    assert (
        group_leave.consume_leave_confirmation(
            "bot", "group", "operator", confirmation.code, now=106
        )
        == "missing"
    )


class _FakeBot:
    def __init__(self, *, member_role: str = "member") -> None:
        self.self_id = "10001"
        self.member_role = member_role
        self.config = SimpleNamespace(superusers=set())
        self.lookup_count = 0

    async def get_group_member_info(self, **_kwargs):
        self.lookup_count += 1
        return {"role": self.member_role}


class _FakeEvent:
    def __init__(self, *, role: str, user_id: int = 20001) -> None:
        self.group_id = 30001
        self.user_id = user_id
        self.sender = SimpleNamespace(role=role)

    def get_user_id(self) -> str:
        return str(self.user_id)


@pytest.mark.asyncio
async def test_first_leave_request_sends_confirmation_without_leaving(
    monkeypatch: pytest.MonkeyPatch,
):
    messages = _patch_message_utils(monkeypatch)
    leave_calls: list[object] = []
    monkeypatch.setattr(plugin.BotConfig, "self_nickname", "南极")

    async def _can_leave(_bot, _event):
        return True

    async def _joined(_bot, _group_id):
        return True

    async def _leave(*_args, **_kwargs):
        leave_calls.append("leave")

    monkeypatch.setattr(plugin, "_can_leave_group_operator", _can_leave)
    monkeypatch.setattr(plugin, "is_bot_joined_group", _joined)
    monkeypatch.setattr(plugin, "execute_group_leave", _leave)
    monkeypatch.setattr(plugin, "_get_config_int", lambda _key, default: default)
    monkeypatch.setattr(
        plugin,
        "create_leave_confirmation",
        lambda *_args, **_kwargs: SimpleNamespace(code="1234"),
    )

    event = _FakeRuntimeEvent(
        original_message=Message(
            [MessageSegment.at("10001"), MessageSegment.text(" 退群")]
        ),
        plain_text="退群",
    )

    with pytest.raises(_Finished):
        await plugin._(_FakeBot(), event)

    assert leave_calls == []
    assert len(messages) == 1
    message_type, content, reply_to = messages[0]
    assert message_type == "finish"
    assert isinstance(content, str)
    assert "@南极 退群 1234" in content
    assert reply_to is True


@pytest.mark.asyncio
async def test_confirmed_leave_consumes_code_and_leaves_once(
    monkeypatch: pytest.MonkeyPatch,
):
    messages = _patch_message_utils(monkeypatch)
    consume_calls: list[tuple[object, ...]] = []
    leave_calls: list[tuple[object, ...]] = []

    async def _can_leave(_bot, _event):
        return True

    async def _joined(_bot, _group_id):
        return True

    async def _leave(*args, **kwargs):
        leave_calls.append((*args, kwargs))

    def _consume(*args, **_kwargs):
        consume_calls.append(args)
        return "ok"

    monkeypatch.setattr(plugin, "_can_leave_group_operator", _can_leave)
    monkeypatch.setattr(plugin, "is_bot_joined_group", _joined)
    monkeypatch.setattr(plugin, "execute_group_leave", _leave)
    monkeypatch.setattr(plugin, "consume_leave_confirmation", _consume)
    monkeypatch.setattr(plugin, "_get_config_int", lambda _key, _default: 0)

    event = _FakeRuntimeEvent(
        original_message=Message(
            [MessageSegment.at("10001"), MessageSegment.text(" 退群 1234")]
        ),
        plain_text="退群 1234",
    )

    await plugin._(_FakeBot(), event)

    assert consume_calls == [("10001", 30001, 20001, "1234")]
    assert len(leave_calls) == 1
    assert len(messages) == 1
    message_type, content, reply_to = messages[0]
    assert message_type == "send"
    assert isinstance(content, str)
    assert content
    assert reply_to is True


@pytest.mark.asyncio
async def test_group_owner_can_request_leave(monkeypatch: pytest.MonkeyPatch):
    async def _not_superuser(_bot, _event):
        return False

    monkeypatch.setattr(plugin, "SUPERUSER", _not_superuser)

    assert await plugin._can_leave_group_operator(
        _FakeBot(), _FakeEvent(role="owner")
    )


@pytest.mark.asyncio
async def test_superuser_can_request_leave(monkeypatch: pytest.MonkeyPatch):
    async def _is_superuser(_bot, _event):
        return True

    monkeypatch.setattr(plugin, "SUPERUSER", _is_superuser)

    assert await plugin._can_leave_group_operator(
        _FakeBot(), _FakeEvent(role="member")
    )


@pytest.mark.asyncio
async def test_missing_role_uses_group_member_lookup(monkeypatch: pytest.MonkeyPatch):
    async def _not_superuser(_bot, _event):
        return False

    monkeypatch.setattr(plugin, "SUPERUSER", _not_superuser)
    bot = _FakeBot(member_role="admin")

    assert await plugin._can_leave_group_operator(bot, _FakeEvent(role=""))
    assert bot.lookup_count == 1


@pytest.mark.asyncio
async def test_normal_member_cannot_request_leave(monkeypatch: pytest.MonkeyPatch):
    async def _not_superuser(_bot, _event):
        return False

    monkeypatch.setattr(plugin, "SUPERUSER", _not_superuser)

    assert not await plugin._can_leave_group_operator(
        _FakeBot(member_role="member"), _FakeEvent(role="member")
    )


@pytest.mark.asyncio
async def test_execute_group_leave_calls_api_and_deletes_console(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[object] = []
    reports: list[str] = []

    class _LeaveBot:
        async def set_group_leave(self, *, group_id: int):
            calls.append(("leave", group_id))

    class _GroupRecord:
        group_name = "测试群"

        async def delete(self):
            calls.append("delete")

    async def _get_group_db(group_id: str):
        calls.append(("get_group_db", group_id))
        return _GroupRecord()

    async def _get_user(**kwargs):
        calls.append(("get_user", kwargs))
        return SimpleNamespace(user_name="操作者")

    async def _send_superuser(_bot, message):
        reports.append(message)
        calls.append(("send_superuser", message))
        return []

    monkeypatch.setattr(group_leave.GroupConsole, "get_group_db", _get_group_db)
    monkeypatch.setattr(group_leave.GroupInfoUser, "get_or_none", _get_user)
    monkeypatch.setattr(group_leave.PlatformUtils, "send_superuser", _send_superuser)

    await group_leave.execute_group_leave(
        _LeaveBot(),
        30001,
        delay_seconds=0,
        log_command="test",
        operator_id=20001,
    )

    assert calls[:4] == [
        ("get_user", {"user_id": "20001", "group_id": "30001"}),
        ("get_group_db", "30001"),
        ("leave", 30001),
        ("get_group_db", "30001"),
    ]
    assert calls[4] == "delete"
    assert calls[5][0] == "send_superuser"
    assert len(reports) == 1
    assert "操作者(20001)" in reports[0]
    assert "测试群(30001)" in reports[0]
    assert "日期：" in reports[0]


def test_build_group_leave_report_formats_context():
    report = group_leave.build_group_leave_report(
        group_leave.GroupLeaveNoticeContext(
            operator_id="20001",
            operator_name="操作者",
            group_id="30001",
            group_name="测试群",
        ),
        now=datetime(2026, 5, 10, 20, 11, 36),
    )

    assert "操作者(20001)" in report
    assert "测试群(30001)" in report
    assert "日期：2026-05-10 20:11:36" in report


@pytest.mark.asyncio
async def test_group_leave_notice_ignores_missing_superuser(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[object] = []

    async def _send_superuser(_bot, _message):
        calls.append("send_superuser")
        raise group_leave.NotFindSuperuser()

    monkeypatch.setattr(group_leave.PlatformUtils, "send_superuser", _send_superuser)

    await group_leave.send_group_leave_superuser_notice(
        object(),
        group_leave.GroupLeaveNoticeContext(
            operator_id="20001",
            operator_name="操作者",
            group_id="30001",
            group_name="测试群",
        ),
    )

    assert calls == ["send_superuser"]
