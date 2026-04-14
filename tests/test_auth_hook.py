from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace

import nonebot
from nonebot.exception import IgnoredException
from nonebot_plugin_session import EventSession
import pytest

nonebot.init()

auth_hook = importlib.import_module("zhenxun.builtin_plugins.hooks.auth_hook")
init_plugin = importlib.import_module("zhenxun.builtin_plugins.init")


class _FakeEvent:
    def __init__(
        self,
        *,
        event_type: str = "message",
        user_id: int | None = 123,
        group_id: int | None = 456,
        channel_id: int | None = None,
        self_id: int = 3072536533,
        event_time: int = 1_776_182_486,
        plaintext: str = "测试命令",
    ) -> None:
        self.user_id = user_id
        self.group_id = group_id
        self.channel_id = channel_id
        self.self_id = self_id
        self.time = event_time
        self._event_type = event_type
        self._plaintext = plaintext

    def get_type(self) -> str:
        return self._event_type

    def get_plaintext(self) -> str:
        return self._plaintext


class _FakeAdapter:
    def __init__(self):
        self.connections = {"3072536533": object()}
        self.onebot_config = SimpleNamespace(onebot_api_roots={})

    def get_name(self) -> str:
        return "OneBot V11"


def _make_bot():
    return SimpleNamespace(
        self_id="3072536533",
        adapter=_FakeAdapter(),
        config=SimpleNamespace(superusers={"1163272259"}),
    )


def _make_session():
    return SimpleNamespace(
        id1="123",
        id2="456",
        id3=None,
        platform="qq",
        bot_type="OneBot V11",
    )


def _make_matcher():
    return SimpleNamespace(
        plugin=SimpleNamespace(name="demo", module_name="demo.module"),
        plugin_name="demo",
        state={},
        type="message",
    )


@pytest.fixture(scope="session", autouse=True)
async def nonebug_init(_nonebot_init: None, after_nonebot_init: None):
    return None


def test_auth_hooks_depend_on_event_session():
    preprocessor_sig = inspect.signature(auth_hook._auth_preprocessor)
    postprocessor_sig = inspect.signature(auth_hook._unblock_after_matcher)

    assert preprocessor_sig.parameters["session"].annotation is EventSession
    assert postprocessor_sig.parameters["session"].annotation is EventSession


@pytest.mark.asyncio
async def test_drop_message_before_uninfo_lookup_when_transport_unavailable(
    monkeypatch,
):
    noted: list[str] = []
    bot = _make_bot()
    event = _FakeEvent()

    monkeypatch.setattr(auth_hook, "is_cache_ready", lambda: True)
    monkeypatch.setattr(auth_hook, "_get_event_bot", lambda _event: bot)
    monkeypatch.setattr(auth_hook.onebot_transport, "should_drop", lambda _bot: True)
    monkeypatch.setattr(
        auth_hook.onebot_transport,
        "note_unavailable",
        lambda _bot, action, extra=None: noted.append(f"{action}|{extra}") or True,
    )

    with pytest.raises(IgnoredException):
        await auth_hook._drop_message_before_cache_ready(event)

    assert noted


@pytest.mark.asyncio
async def test_auth_preprocessor_builds_local_light_session(monkeypatch):
    captured: dict[str, object] = {}
    bot = _make_bot()
    matcher = _make_matcher()
    event = _FakeEvent()
    session = _make_session()
    state: dict[str, object] = {}

    async def _fake_route_context(_text, _event_cache):
        return {"demo"}

    async def _fake_route_precheck(*_args, **_kwargs):
        return False

    async def _fake_auth(_matcher, _event, _bot, light_session, *_args, **_kwargs):
        captured["session"] = light_session

    monkeypatch.setattr(auth_hook, "is_cache_ready", lambda: True)
    monkeypatch.setattr(auth_hook, "_get_route_context", _fake_route_context)
    monkeypatch.setattr(auth_hook, "route_precheck", _fake_route_precheck)
    monkeypatch.setattr(auth_hook, "auth", _fake_auth)

    await auth_hook._auth_preprocessor(
        matcher=matcher,
        event=event,
        bot=bot,
        session=session,
        state=state,
        message=None,
    )

    light_session = state["_zx_light_session"]
    entity = state["_zx_entity"]

    assert light_session.user.id == "123"
    assert entity.user_id == "123"
    assert entity.group_id == "456"
    assert captured["session"].user.id == "123"


@pytest.mark.asyncio
async def test_unblock_after_matcher_uses_local_light_session(monkeypatch):
    calls: list[tuple[str, str, str | None, str | None]] = []

    monkeypatch.setattr(
        auth_hook.LimitManager,
        "unblock",
        lambda module, user_id, group_id, channel_id: calls.append(
            (module, user_id, group_id, channel_id)
        ),
    )

    await auth_hook._unblock_after_matcher(
        matcher=_make_matcher(),
        bot=_make_bot(),
        session=_make_session(),
        event=_FakeEvent(),
    )

    assert calls == [("demo", "123", "456", None)]


@pytest.mark.asyncio
async def test_init_hook_skips_group_sync_until_transport_ready(monkeypatch):
    bot = _make_bot()
    noted: list[str] = []

    async def _fake_wait_until_ready(*_args, **_kwargs):
        return False

    async def _unexpected_group_list(*_args, **_kwargs):
        raise AssertionError("transport 未就绪时不应拉取群列表")

    monkeypatch.setattr(init_plugin.PlatformUtils, "get_platform", lambda _bot: "qq")
    monkeypatch.setattr(
        init_plugin.onebot_transport, "wait_until_ready", _fake_wait_until_ready
    )
    monkeypatch.setattr(
        init_plugin.onebot_transport,
        "note_unavailable",
        lambda _bot, action, extra=None: noted.append(f"{action}|{extra}") or True,
    )
    monkeypatch.setattr(
        init_plugin.PlatformUtils, "get_group_list", _unexpected_group_list
    )

    await init_plugin._sync_group_auth_on_bot_connect(bot)

    assert noted


@pytest.mark.asyncio
async def test_init_hook_syncs_groups_after_transport_recovers(monkeypatch):
    bot = _make_bot()
    group = SimpleNamespace(group_id="456", group_name="测试群", group_flag=0)
    bulk_create_calls: list[list[object]] = []

    class _FakeQuery:
        async def values_list(self, *_args, **_kwargs):
            return []

    class _FakeFilter:
        async def update(self, **_kwargs):
            return 0

    async def _fake_wait_until_ready(*_args, **_kwargs):
        return True

    async def _fake_get_group_list(*_args, **_kwargs):
        return [group], "qq"

    async def _fake_bulk_create(groups, *_args, **_kwargs):
        bulk_create_calls.append(groups)

    monkeypatch.setattr(init_plugin.PlatformUtils, "get_platform", lambda _bot: "qq")
    monkeypatch.setattr(
        init_plugin.onebot_transport, "wait_until_ready", _fake_wait_until_ready
    )
    monkeypatch.setattr(
        init_plugin.PlatformUtils, "get_group_list", _fake_get_group_list
    )
    monkeypatch.setattr(init_plugin.GroupConsole, "all", lambda: _FakeQuery())
    monkeypatch.setattr(init_plugin.GroupConsole, "bulk_create", _fake_bulk_create)
    monkeypatch.setattr(
        init_plugin.GroupConsole, "filter", lambda **_kwargs: _FakeFilter()
    )

    await init_plugin._sync_group_auth_on_bot_connect(bot)

    assert group.group_flag == 1
    assert bulk_create_calls == [[group]]
