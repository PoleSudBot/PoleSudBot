from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import ClassVar

import pytest

from zhenxun.utils.manager import message_manager as message_manager_module
from zhenxun.utils.manager.message_manager import MessageManager

ROOT = Path(__file__).resolve().parents[1]


class _LoggerStub:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _ConfigStub:
    @staticmethod
    def get_config(*_args, **_kwargs):
        return False


class _BotMessageStoreStub:
    @staticmethod
    async def create(**_kwargs):
        return None


class _WithdrawManagerStub:
    _data: ClassVar[dict[int, tuple[object, int, int]]] = {}

    @classmethod
    async def withdraw_message(cls, *_args, **_kwargs):
        return None

    @classmethod
    def remove(cls, index: int):
        cls._data.pop(index, None)


class _BotStub:
    _called_api_hook: ClassVar[list] = []

    def __init__(self, self_id: str = "bot-1") -> None:
        self.self_id = self_id
        self.deleted = []

    @classmethod
    def on_called_api(cls, func):
        cls._called_api_hook.append(func)
        return func

    async def call_api(self, api: str, **data):
        result = data.pop("_result", {"message_id": 2001})

        async def _run_hook(hook):
            await hook(self, None, api, data, result)

        await asyncio.gather(
            *(asyncio.create_task(_run_hook(hook)) for hook in self._called_api_hook)
        )
        return result

    async def delete_msg(self, *, message_id: int):
        self.deleted.append(message_id)


def _identity_decorator(func):
    return func


class _MessageStub(str):
    def extract_plain_text(self) -> str:
        return str(self)


def _install_import_stubs():
    """隔离重型服务依赖，避免定向单测触发完整 NoneBot startup。"""
    nonebot_module = types.ModuleType("nonebot")

    class _MatcherCollector:
        def __init__(self):
            self.handlers = []

        def handle(self):
            def _decorator(func):
                self.handlers.append(func)
                return func

            return _decorator

    nonebot_module.on_notice = lambda *_args, **_kwargs: _MatcherCollector()
    adapters_module = types.ModuleType("nonebot.adapters")
    adapters_module.Bot = _BotStub
    adapters_module.Event = object
    adapters_module.Message = _MessageStub
    exception_module = types.ModuleType("nonebot.exception")
    exception_module.ActionFailed = RuntimeError
    exception_module.NetworkError = OSError
    onebot_module = types.ModuleType("nonebot.adapters.onebot")
    onebot_v11_module = types.ModuleType("nonebot.adapters.onebot.v11")

    class _RecallNoticeEvent:
        def __init__(self, user_id="9001", message_id=1001):
            self.user_id = user_id
            self.message_id = message_id

    class _GroupRecallNoticeEvent(_RecallNoticeEvent):
        pass

    class _FriendRecallNoticeEvent(_RecallNoticeEvent):
        pass

    onebot_v11_module.GroupRecallNoticeEvent = _GroupRecallNoticeEvent
    onebot_v11_module.FriendRecallNoticeEvent = _FriendRecallNoticeEvent
    current_event = ContextVar("current_event")

    class _MatcherStub:
        @contextmanager
        def ensure_context(self, _bot, event):
            token = current_event.set(event)
            try:
                yield
            finally:
                current_event.reset(token)

    class _AlconnaMatcherStub(_MatcherStub):
        @contextmanager
        def ensure_context(self, _bot, event):
            token = current_event.set(event)
            try:
                yield
            finally:
                current_event.reset(token)

    matcher_module = types.ModuleType("nonebot.matcher")
    matcher_module.Matcher = _MatcherStub
    matcher_module.current_event = current_event
    alconna_module = types.ModuleType("nonebot_plugin_alconna")
    alconna_module.__path__ = []
    alconna_module.Alconna = type(
        "Alconna",
        (),
        {"__init__": lambda self, *_args, **_kwargs: None},
    )
    alconna_module.Arparma = type("Arparma", (), {})
    alconna_module.on_alconna = lambda *_args, **_kwargs: _MatcherCollector()
    alconna_matcher_module = types.ModuleType("nonebot_plugin_alconna.matcher")
    alconna_matcher_module.AlconnaMatcher = _AlconnaMatcherStub
    alconna_uniseg_module = types.ModuleType("nonebot_plugin_alconna.uniseg")
    alconna_uniseg_module.__path__ = []
    alconna_tools_module = types.ModuleType("nonebot_plugin_alconna.uniseg.tools")

    async def _reply_fetch(*_args, **_kwargs):
        return None

    alconna_tools_module.reply_fetch = _reply_fetch
    uninfo_module = types.ModuleType("nonebot_plugin_uninfo")
    uninfo_module.Uninfo = type("Uninfo", (), {})
    plugin_module = types.ModuleType("nonebot.plugin")
    plugin_module.PluginMetadata = type(
        "PluginMetadata", (), {"__init__": lambda self, **_kwargs: None}
    )
    rule_module = types.ModuleType("nonebot.rule")
    rule_module.Rule = type("Rule", (), {"__init__": lambda self, *_args: None})
    message_runtime_module = types.ModuleType("nonebot.message")
    message_runtime_module.run_preprocessor = _identity_decorator
    message_runtime_module.run_postprocessor = _identity_decorator
    configs_utils_module = types.ModuleType("zhenxun.configs.utils")
    configs_utils_module.Command = type(
        "Command", (), {"__init__": lambda self, **_kwargs: None}
    )
    configs_utils_module.PluginExtraData = type(
        "PluginExtraData",
        (),
        {
            "__init__": lambda self, **_kwargs: None,
            "to_dict": lambda self: {},
        },
    )
    config_module = types.ModuleType("zhenxun.configs.config")
    config_module.Config = _ConfigStub
    store_module = types.ModuleType("zhenxun.models.bot_message_store")
    store_module.BotMessageStore = _BotMessageStoreStub
    log_module = types.ModuleType("zhenxun.services.log")
    log_module.logger = _LoggerStub()
    sanitizer_module = types.ModuleType("zhenxun.utils.log_sanitizer")
    sanitizer_module.sanitize_for_logging = lambda value, **_kwargs: value
    platform_module = types.ModuleType("zhenxun.utils.platform")
    platform_module.PlatformUtils = SimpleNamespace(
        get_platform=lambda _target: "qq"
    )
    message_module = types.ModuleType("zhenxun.utils.message")
    message_module.MessageUtils = SimpleNamespace(
        build_message=lambda _message: SimpleNamespace(send=_noop)
    )
    withdraw_manage_module = types.ModuleType("zhenxun.utils.withdraw_manage")
    withdraw_manage_module.WithdrawManager = _WithdrawManagerStub

    return {
        "nonebot": nonebot_module,
        "nonebot.adapters": adapters_module,
        "nonebot.adapters.onebot": onebot_module,
        "nonebot.adapters.onebot.v11": onebot_v11_module,
        "nonebot.exception": exception_module,
        "nonebot.matcher": matcher_module,
        "nonebot.plugin": plugin_module,
        "nonebot.rule": rule_module,
        "nonebot_plugin_alconna": alconna_module,
        "nonebot_plugin_alconna.matcher": alconna_matcher_module,
        "nonebot_plugin_alconna.uniseg": alconna_uniseg_module,
        "nonebot_plugin_alconna.uniseg.tools": alconna_tools_module,
        "nonebot_plugin_uninfo": uninfo_module,
        "nonebot.message": message_runtime_module,
        "zhenxun.configs.config": config_module,
        "zhenxun.configs.utils": configs_utils_module,
        "zhenxun.models.bot_message_store": store_module,
        "zhenxun.services": types.ModuleType("zhenxun.services"),
        "zhenxun.services.log": log_module,
        "zhenxun.utils.log_sanitizer": sanitizer_module,
        "zhenxun.utils.platform": platform_module,
        "zhenxun.utils.message": message_module,
        "zhenxun.utils.withdraw_manage": withdraw_manage_module,
    }


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_MISSING = object()
_STUBS = _install_import_stubs()
_PREVIOUS_MODULES = {name: sys.modules.get(name, _MISSING) for name in _STUBS}
try:
    sys.modules.update(_STUBS)
    call_hook = _load_module(
        "_test_auto_withdraw_call_hook",
        ROOT / "zhenxun" / "builtin_plugins" / "hooks" / "call_hook.py",
    )
    auto_withdraw = _load_module(
        "_test_auto_withdraw_helper",
        ROOT / "zhenxun" / "utils" / "auto_withdraw.py",
    )
    withdraw_plugin = _load_module(
        "_test_auto_withdraw_plugin",
        ROOT / "zhenxun" / "builtin_plugins" / "withdraw.py",
    )
    withdraw_hook = _load_module(
        "_test_auto_withdraw_hook",
        ROOT / "zhenxun" / "builtin_plugins" / "hooks" / "withdraw_hook.py",
    )
finally:
    for _name, _previous in _PREVIOUS_MODULES.items():
        if _previous is _MISSING:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _previous


class _FakeMessageEvent:
    def __init__(
        self, message_id: int | None = 1001, user_id: int | None = 9001
    ) -> None:
        self.message_id = message_id
        self.user_id = user_id

    def get_type(self) -> str:
        return "message"


class _FakeNoticeEvent:
    def get_type(self) -> str:
        return "notice"


class _FakeBot(SimpleNamespace):
    async def delete_msg(self, *, message_id: int):
        self.deleted.append(message_id)


@pytest.fixture(autouse=True)
def clear_message_manager():
    MessageManager.data.clear()
    MessageManager.triggered_data.clear()
    MessageManager.triggered_reply_index.clear()
    MessageManager.triggered_order.clear()
    MessageManager.recalled_trigger_sources.clear()
    MessageManager.recalled_trigger_order.clear()
    call_hook._current_source_context.set(None)
    call_hook._api_source_context.set(None)
    call_hook._queued_source_context.set(None)
    call_hook._matcher_task_context.set(None)
    withdraw_hook.WithdrawManager._data.clear()
    withdraw_hook._WITHDRAW_TASKS.clear()


def test_message_manager_records_and_pops_triggered_messages():
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)
    MessageManager.add_triggered("bot-1", 1001, 2002)
    MessageManager.add_triggered("bot-2", 1001, 3001)

    assert MessageManager.pop_triggered("bot-1", "1001") == ["2001", "2002"]
    assert MessageManager.pop_triggered("bot-1", "1001") == []
    assert MessageManager.pop_triggered("bot-2", "1001") == ["3001"]
    assert MessageManager.triggered_reply_index == {}


def test_message_manager_removes_single_triggered_reply_by_index():
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)

    assert MessageManager.remove_triggered_reply("bot-1", 2001) is True
    assert MessageManager.remove_triggered_reply("bot-1", 2001) is False
    assert MessageManager.pop_triggered("bot-1", 1001) == ["2002"]
    assert MessageManager.triggered_reply_index == {}


def test_message_manager_triggered_eviction_cleans_reply_index():
    old_limit = MessageManager._max_triggered_source_messages
    MessageManager._max_triggered_source_messages = 1
    try:
        MessageManager.add_triggered("bot-1", 1001, 2001)
        MessageManager.add_triggered("bot-1", 1002, 2002)

        assert MessageManager.pop_triggered("bot-1", 1001) == []
        assert MessageManager.triggered_reply_index == {("bot-1", "2002"): "1002"}
    finally:
        MessageManager._max_triggered_source_messages = old_limit


def test_message_manager_marks_recalled_source_and_pops_triggered_messages():
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)

    assert MessageManager.mark_trigger_source_recalled("bot-1", 1001) == [
        "2001",
        "2002",
    ]
    assert MessageManager.is_trigger_source_recalled("bot-1", 1001)
    assert MessageManager.pop_triggered("bot-1", 1001) == []


def test_message_manager_recalled_source_does_not_expire_by_time(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(message_manager_module.time, "monotonic", lambda: now)

    MessageManager.mark_trigger_source_recalled("bot-1", 1001)

    assert MessageManager.is_trigger_source_recalled("bot-1", 1001)
    now += 3600
    assert MessageManager.is_trigger_source_recalled("bot-1", 1001)
    assert MessageManager.recalled_trigger_order == [("bot-1", "1001")]


@pytest.mark.asyncio
async def test_call_hook_records_triggered_message_in_current_message_context():
    bot = SimpleNamespace(self_id="bot-1")
    call_hook._bind_send_source_context(_FakeMessageEvent(message_id=1001))

    await call_hook.handle_api_result(
        bot,
        None,
        "send_group_msg",
        {"group_id": 10, "message": "ok"},
        {"message_id": 2001},
    )
    await call_hook.handle_api_result(
        bot,
        None,
        "send_group_msg",
        {"group_id": 10, "message": "ok"},
        {"message_id": 2002},
    )

    assert MessageManager.pop_triggered("bot-1", "1001") == ["2001", "2002"]
    assert MessageManager.check("9001", "2001")
    assert MessageManager.check("9001", "2002")


@pytest.mark.asyncio
async def test_call_hook_ignores_missing_message_context_and_source_id():
    bot = SimpleNamespace(self_id="bot-1")

    await call_hook.handle_api_result(
        bot,
        None,
        "send_group_msg",
        {"group_id": 10, "message": "ok"},
        {"message_id": 2001},
    )
    call_hook._bind_send_source_context(_FakeNoticeEvent())
    await call_hook.handle_api_result(
        bot,
        None,
        "send_private_msg",
        {"user_id": 20, "message": "ok"},
        {"message_id": 2002},
    )
    call_hook._bind_send_source_context(_FakeMessageEvent(message_id=None))
    await call_hook.handle_api_result(
        bot,
        None,
        "send_msg",
        {"message_type": "group", "group_id": 10, "message": "ok"},
        {"message_id": 2003},
    )

    assert MessageManager.triggered_data == {}


@pytest.mark.asyncio
async def test_call_hook_records_manual_withdraw_for_direct_send_apis():
    bot = SimpleNamespace(self_id="bot-1")
    call_hook._bind_send_source_context(
        _FakeMessageEvent(message_id=1001, user_id=9001)
    )

    await call_hook.handle_api_result(
        bot,
        None,
        "send_group_msg",
        {"group_id": 10, "message": "ok"},
        {"message_id": 2001},
    )
    await call_hook.handle_api_result(
        bot,
        None,
        "send_private_msg",
        {"user_id": 20, "message": "ok"},
        {"message_id": 2002},
    )

    assert MessageManager.check("9001", "2001")
    assert MessageManager.check("20", "2002")


@pytest.mark.asyncio
async def test_call_hook_immediately_deletes_late_reply_after_source_recalled(
    monkeypatch,
):
    bot = _FakeBot(self_id="bot-1", deleted=[])
    call_hook._bind_send_source_context(_FakeMessageEvent(message_id=1001))
    MessageManager.mark_trigger_source_recalled("bot-1", 1001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    await call_hook.handle_api_result(
        bot,
        None,
        "send_group_msg",
        {"group_id": 10, "message": "ok"},
        {"message_id": 2001},
    )

    assert bot.deleted == [2001]
    assert MessageManager.pop_triggered("bot-1", 1001) == []


@pytest.mark.asyncio
async def test_call_hook_deletes_late_reply_after_old_source_recalled(monkeypatch):
    now = 1000.0
    bot = _FakeBot(self_id="bot-1", deleted=[])
    call_hook._bind_send_source_context(_FakeMessageEvent(message_id=1001))
    monkeypatch.setattr(message_manager_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())
    MessageManager.mark_trigger_source_recalled("bot-1", 1001)
    now += 3600

    await call_hook.handle_api_result(
        bot,
        None,
        "send_group_msg",
        {"group_id": 10, "message": "ok"},
        {"message_id": 2001},
    )

    assert bot.deleted == [2001]
    assert MessageManager.pop_triggered("bot-1", 1001) == []


@pytest.mark.asyncio
async def test_call_hook_ignores_background_task_inherited_source_context():
    bot = SimpleNamespace(self_id="bot-1")
    call_hook._bind_send_source_context(_FakeMessageEvent(message_id=1001))

    async def _send_in_background():
        await call_hook.handle_api_result(
            bot,
            None,
            "send_group_msg",
            {"group_id": 10, "message": "ok"},
            {"message_id": 2001},
        )

    await asyncio.create_task(_send_in_background())

    assert MessageManager.triggered_data == {}


@pytest.mark.asyncio
async def test_call_api_wrapper_ignores_awaited_child_task_from_gather():
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)

    async def _send_in_child_task():
        await bot.call_api(
            "send_group_msg",
            group_id=10,
            message="ok",
            _result={"message_id": 2001},
        )

    with call_hook.Matcher().ensure_context(bot, event):
        await asyncio.gather(_send_in_child_task())

    assert MessageManager.triggered_data == {}


@pytest.mark.asyncio
async def test_call_api_wrapper_keeps_source_context_for_called_api_child_task(
    monkeypatch,
):
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    with call_hook.Matcher().ensure_context(bot, event):
        await bot.call_api(
            "send_group_msg",
            group_id=10,
            message="ok",
            _result={"message_id": 2001},
        )

    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == ["2001"]
    assert bot.deleted == [2001]


@pytest.mark.asyncio
async def test_alconna_call_api_wrapper_records_multiple_response_messages(
    monkeypatch,
):
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    with call_hook.AlconnaMatcher().ensure_context(bot, event):
        await bot.call_api(
            "send_group_msg",
            group_id=10,
            message="searching",
            _result={"message_id": 2001},
        )
        await bot.call_api(
            "send_group_forward_msg",
            group_id=10,
            messages=[],
            _result={"message_id": 2002},
        )

    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == [
        "2001",
        "2002",
    ]
    assert bot.deleted == [2001, 2002]


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["send_group_forward_msg", "send_private_forward_msg"])
async def test_call_api_wrapper_records_forward_message_apis(api):
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)
    data = {"group_id": 10} if api == "send_group_forward_msg" else {"user_id": 20}

    with call_hook.Matcher().ensure_context(bot, event):
        await bot.call_api(
            api,
            messages=[],
            _result={"message_id": 2001},
            **data,
        )

    assert MessageManager.pop_triggered("bot-1", 1001) == ["2001"]


@pytest.mark.asyncio
async def test_alconna_call_api_immediately_deletes_late_forward_after_source_recalled(
    monkeypatch,
):
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)
    MessageManager.mark_trigger_source_recalled("bot-1", 1001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    with call_hook.AlconnaMatcher().ensure_context(bot, event):
        await bot.call_api(
            "send_group_forward_msg",
            group_id=10,
            messages=[],
            _result={"message_id": 2001},
        )

    assert bot.deleted == [2001]
    assert MessageManager.pop_triggered("bot-1", 1001) == []


@pytest.mark.asyncio
async def test_call_api_wrapper_ignores_background_task_inherited_matcher_context():
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)

    async def _send_in_background():
        await bot.call_api(
            "send_group_msg",
            group_id=10,
            message="ok",
            _result={"message_id": 2001},
        )

    with call_hook.Matcher().ensure_context(bot, event):
        await asyncio.create_task(_send_in_background())

    assert MessageManager.triggered_data == {}


@pytest.mark.asyncio
async def test_alconna_call_api_wrapper_ignores_background_task_context():
    bot = call_hook.Bot("bot-1")
    event = _FakeMessageEvent(message_id=1001, user_id=9001)

    async def _send_in_background():
        await bot.call_api(
            "send_group_msg",
            group_id=10,
            message="ok",
            _result={"message_id": 2001},
        )

    with call_hook.AlconnaMatcher().ensure_context(bot, event):
        await asyncio.create_task(_send_in_background())

    assert MessageManager.triggered_data == {}


@pytest.mark.asyncio
async def test_auto_withdraw_deletes_triggered_messages_once(monkeypatch):
    bot = _FakeBot(self_id="bot-1", deleted=[])
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == [
        "2001",
        "2002",
    ]
    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == []
    assert bot.deleted == [2001, 2002]


@pytest.mark.asyncio
async def test_auto_withdraw_skips_bot_reply_removed_by_self_recall(monkeypatch):
    bot = _FakeBot(self_id="bot-1", deleted=[])
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)
    MessageManager.remove_triggered_reply("bot-1", 2001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == ["2002"]
    assert bot.deleted == [2002]


@pytest.mark.asyncio
async def test_withdraw_notice_for_bot_self_recall_only_unlinks_reply(monkeypatch):
    bot = _FakeBot(self_id="bot-1", deleted=[])
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)
    event = withdraw_plugin.GroupRecallNoticeEvent(user_id="bot-1", message_id=2001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    await withdraw_plugin._auto_withdraw_matcher.handlers[0](bot, event)

    assert not MessageManager.is_trigger_source_recalled("bot-1", 2001)
    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == ["2002"]
    assert bot.deleted == [2002]


@pytest.mark.asyncio
async def test_auto_withdraw_retries_failed_message_once(monkeypatch):
    class _RetryBot(SimpleNamespace):
        async def delete_msg(self, *, message_id: int):
            self.calls.append(message_id)
            if len(self.calls) == 1:
                raise ValueError("temporary failed")

    bot = _RetryBot(self_id="bot-1", calls=[])
    MessageManager.add_triggered("bot-1", 1001, 2001)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == ["2001"]
    assert bot.calls == [2001, 2001]


@pytest.mark.asyncio
async def test_auto_withdraw_failed_message_does_not_stop_later_messages(monkeypatch):
    class _PartiallyFailedBot(SimpleNamespace):
        async def delete_msg(self, *, message_id: int):
            self.calls.append(message_id)
            if message_id == 2001:
                raise ValueError("always failed")
            self.deleted.append(message_id)

    bot = _PartiallyFailedBot(self_id="bot-1", calls=[], deleted=[])
    MessageManager.add_triggered("bot-1", 1001, 2001)
    MessageManager.add_triggered("bot-1", 1001, 2002)
    monkeypatch.setattr(auto_withdraw.asyncio, "sleep", lambda _delay: _noop())

    assert await auto_withdraw.auto_withdraw_triggered_messages(bot, 1001) == [
        "2001",
        "2002",
    ]
    assert bot.calls == [2001, 2001, 2002]
    assert bot.deleted == [2002]


@pytest.mark.asyncio
async def test_withdraw_hook_schedules_delayed_withdraw_without_blocking(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def _withdraw_message(bot, message_id, time):
        calls.append((bot, message_id, time))
        started.set()
        await release.wait()

    monkeypatch.setattr(
        withdraw_hook.WithdrawManager,
        "withdraw_message",
        _withdraw_message,
    )
    bot = SimpleNamespace(self_id="bot-1")
    withdraw_hook.WithdrawManager._data[0] = (bot, 2001, 30)

    await asyncio.wait_for(withdraw_hook._(None, None, bot), timeout=0.1)
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert calls == [(bot, 2001, 30)]
    assert withdraw_hook.WithdrawManager._data == {}

    release.set()
    await asyncio.gather(*withdraw_hook._WITHDRAW_TASKS)


async def _noop():
    """为自动撤回测试跳过真实等待。"""
    return None
