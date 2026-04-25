from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from zhenxun.services import external_bot_bridge as bridge_module


async def _noop_start_background_tasks() -> None:
    return None


# 模块导入时会把单例 bridge 绑定到 startup hook；
# 这里提前替换成 no-op，避免测试启动流程去连真实 sidecar。
bridge_module.external_bot_bridge.start_background_tasks = _noop_start_background_tasks


def _drop_bridge_vendor_modules() -> None:
    module_prefix = f"{bridge_module._BRIDGE_VENDOR_PACKAGE}."
    for module_name in list(sys.modules):
        if module_name == bridge_module._BRIDGE_VENDOR_PACKAGE:
            sys.modules.pop(module_name, None)
        elif module_name.startswith(module_prefix):
            sys.modules.pop(module_name, None)


def test_load_bridge_library_does_not_execute_genshinuid_package_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    vendor_src = tmp_path / "src"
    package_dir = vendor_src / "GenshinUID"
    (package_dir / "sayu_protocol").mkdir(parents=True)
    (package_dir / "protocols").mkdir()

    (package_dir / "__init__.py").write_text(
        "raise RuntimeError('GenshinUID package root was executed')\n"
    )
    (package_dir / "utils.py").write_text("command_start = set()\n")
    (package_dir / "sayu_protocol" / "__init__.py").write_text(
        "from .gs_client import GsClient\n"
        "from .pack import Message, MessageReceive, MessageSend\n"
    )
    (package_dir / "sayu_protocol" / "gs_client.py").write_text(
        "background_tasks = set()\n\n"
        "class GsClient:\n"
        "    pass\n"
    )
    (package_dir / "sayu_protocol" / "pack.py").write_text(
        "class Message:\n"
        "    pass\n"
        "class MessageReceive:\n"
        "    pass\n"
        "class MessageSend:\n"
        "    pass\n"
    )
    (package_dir / "protocols" / "__init__.py").write_text(
        "class AbstractProtocol:\n"
        "    pass\n"
    )
    (package_dir / "protocols" / "onebot_v11.py").write_text(
        "from . import AbstractProtocol\n"
        "from ..sayu_protocol import Message, MessageReceive\n"
        "from ..utils import command_start\n\n"
        "class OneBotV11Protocol(AbstractProtocol):\n"
        "    pass\n"
    )

    monkeypatch.setattr(bridge_module, "_LIBRARY_CACHE", None)
    monkeypatch.setattr(bridge_module, "_get_vendor_src_paths", lambda: [vendor_src])
    _drop_bridge_vendor_modules()
    for module_name in list(sys.modules):
        if module_name == "GenshinUID" or module_name.startswith("GenshinUID."):
            sys.modules.pop(module_name, None)

    library = bridge_module._load_bridge_library()

    assert library.gs_client_cls.__name__ == "GsClient"
    assert library.message_cls.__name__ == "Message"
    assert library.message_receive_cls.__name__ == "MessageReceive"
    assert library.message_send_cls.__name__ == "MessageSend"
    assert library.protocol_cls.__name__ == "OneBotV11Protocol"
    assert "GenshinUID" not in sys.modules


@pytest.mark.asyncio
async def test_bridge_gs_client_preserves_source_plugin_without_vendor_model_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    vendor_src = tmp_path / "src"
    package_dir = vendor_src / "GenshinUID"
    (package_dir / "sayu_protocol").mkdir(parents=True)
    (package_dir / "protocols").mkdir()

    (package_dir / "__init__.py").write_text(
        "raise RuntimeError('GenshinUID package root was executed')\n"
    )
    (package_dir / "utils.py").write_text("command_start = set()\n")
    (package_dir / "sayu_protocol" / "__init__.py").write_text(
        "from .gs_client import GsClient\n"
        "from .pack import Message, MessageReceive, MessageSend\n"
    )
    (package_dir / "sayu_protocol" / "gs_client.py").write_text(
        "background_tasks = set()\n\n"
        "class GsClient:\n"
        "    def __init__(self, host, port, bot_id, callback, is_retry=True):\n"
        "        self.host = host\n"
        "        self.port = port\n"
        "        self.bot_id = bot_id\n"
        "        self.callback = callback\n"
        "        self.is_retry = is_retry\n"
        "        self._client = None\n"
    )
    (package_dir / "sayu_protocol" / "pack.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class Message(BaseModel):\n"
        "    type: str | None = None\n"
        "    data: object | None = None\n\n"
        "class MessageReceive(BaseModel):\n"
        "    bot_self_id: str = ''\n"
        "    msg_id: str = ''\n\n"
        "class MessageSend(BaseModel):\n"
        "    bot_self_id: str = ''\n"
        "    msg_id: str = ''\n"
        "    target_type: str | None = None\n"
        "    target_id: str | None = None\n"
        "    content: list[Message] | None = None\n"
    )
    (package_dir / "protocols" / "__init__.py").write_text(
        "class AbstractProtocol:\n"
        "    pass\n"
    )
    (package_dir / "protocols" / "onebot_v11.py").write_text(
        "from . import AbstractProtocol\n\n"
        "class OneBotV11Protocol(AbstractProtocol):\n"
        "    pass\n"
    )

    monkeypatch.setattr(bridge_module, "_LIBRARY_CACHE", None)
    monkeypatch.setattr(bridge_module, "_get_vendor_src_paths", lambda: [vendor_src])
    _drop_bridge_vendor_modules()

    received_messages = []

    async def callback(message):
        received_messages.append(message)

    class _FakeWebSocket:
        open = True

        async def recv(self):
            self.open = False
            return json.dumps(
                {
                    "bot_self_id": "",
                    "msg_id": "",
                    "target_type": "group",
                    "target_id": "178732453",
                    "content": [{"type": "text", "data": "hello"}],
                    "source_plugin": "RocomUID",
                }
            )

    library = bridge_module._load_bridge_library()
    client = library.gs_client_cls("127.0.0.1", 8765, "NoneBot2", callback)
    client._client = _FakeWebSocket()

    await client._recv()
    await bridge_module.asyncio.sleep(0)

    assert "source_plugin" not in library.message_send_cls.model_fields
    assert getattr(received_messages[0], "source_plugin") == "RocomUID"


@pytest.mark.asyncio
async def test_start_background_tasks_starts_reconcile_when_connect_on_startup_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    created_task_names: list[str | None] = []

    class _FakeTask:
        def __init__(self, name: str | None):
            self._name = name

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    def fake_create_task(coro, *, name=None):
        coro.close()
        created_task_names.append(name)
        return _FakeTask(name)

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(
            connect_on_startup=True,
            enable_proactive_push=False,
            push_queue_size=200,
            push_workers=1,
            push_reconcile_interval=5.0,
        ),
    )
    monkeypatch.setattr(bridge_module.asyncio, "create_task", fake_create_task)

    bridge = bridge_module.ExternalBotBridge()

    await bridge.start_background_tasks()

    assert bridge._reconcile_task is not None
    assert created_task_names == [
        "external-bot-bridge-outbound-0",
        "external-bot-bridge-reconcile",
    ]


@pytest.mark.asyncio
async def test_probe_ready_sets_event_when_health_reports_healthy(
    monkeypatch: pytest.MonkeyPatch,
):
    requested_urls: list[str] = []
    requested_timeouts: list[float] = []

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"status": 0, "data": {"status": "healthy"}}

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float):
            requested_timeouts.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            requested_urls.append(url)
            return _FakeResponse()

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(host="127.0.0.1", port=8765),
    )
    monkeypatch.setattr(bridge_module.httpx, "AsyncClient", _FakeAsyncClient)

    bridge = bridge_module.ExternalBotBridge()
    monkeypatch.setattr(bridge, "_is_connected", lambda: True)
    monkeypatch.setattr(bridge, "_current_ws_id", lambda: 42)

    assert await bridge._probe_ready() is True
    assert bridge._ready_event.is_set() is True
    assert bridge._ready_ws_id == 42
    assert requested_urls == ["http://127.0.0.1:8765/api/system/health"]
    assert requested_timeouts == [bridge_module.READY_PROBE_TIMEOUT_SECONDS]


@pytest.mark.asyncio
async def test_probe_ready_treats_malformed_payload_as_not_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return ["not", "a", "dict"]

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url: str):
            return _FakeResponse()

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(host="127.0.0.1", port=8765),
    )
    monkeypatch.setattr(bridge_module.httpx, "AsyncClient", _FakeAsyncClient)

    bridge = bridge_module.ExternalBotBridge()
    monkeypatch.setattr(bridge, "_is_connected", lambda: True)

    assert await bridge._probe_ready() is False
    assert bridge._ready_event.is_set() is False


@pytest.mark.asyncio
async def test_wait_until_ready_raises_warming_up_when_probe_times_out(
    monkeypatch: pytest.MonkeyPatch,
):
    bridge = bridge_module.ExternalBotBridge()

    async def fake_probe_ready() -> bool:
        return False

    monkeypatch.setattr(bridge, "_probe_ready", fake_probe_ready)

    with pytest.raises(bridge_module.BridgeServiceWarmingUp):
        await bridge.wait_until_ready(0.01)


@pytest.mark.asyncio
async def test_wait_until_ready_rechecks_when_websocket_generation_changes(
    monkeypatch: pytest.MonkeyPatch,
):
    bridge = bridge_module.ExternalBotBridge()
    bridge._ready_event.set()
    bridge._ready_ws_id = 1

    monkeypatch.setattr(bridge, "_is_connected", lambda: True)
    monkeypatch.setattr(bridge, "_current_ws_id", lambda: 2)

    probe_calls = 0

    async def fake_probe_ready() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        bridge._ready_ws_id = 2
        bridge._ready_event.set()
        return True

    monkeypatch.setattr(bridge, "_probe_ready", fake_probe_ready)

    await bridge.wait_until_ready(0.1)

    assert probe_calls == 1


@pytest.mark.asyncio
async def test_send_request_does_not_send_before_bridge_is_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    sent_messages: list[object] = []

    class _FakeProtocol:
        def __init__(self, _bot):
            return None

        @staticmethod
        async def handle_message(_event):
            return SimpleNamespace(bot_self_id="bot_1", msg_id="msg_1")

    class _FakeClient:
        async def send(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(
        bridge_module,
        "_load_bridge_library",
        lambda: SimpleNamespace(protocol_cls=_FakeProtocol),
    )
    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(request_timeout=20.0, reply_idle_seconds=0.6),
    )

    bridge = bridge_module.ExternalBotBridge()
    bridge._client = _FakeClient()
    monkeypatch.setattr(bridge, "_is_supported", lambda _bot, _event: True)

    async def fake_ensure_connected(_bot_id: str) -> None:
        return None

    async def fake_wait_until_ready(_timeout: float) -> None:
        raise bridge_module.BridgeServiceWarmingUp("warming up")

    monkeypatch.setattr(bridge, "ensure_connected", fake_ensure_connected)
    monkeypatch.setattr(bridge, "wait_until_ready", fake_wait_until_ready)

    with pytest.raises(bridge_module.BridgeServiceWarmingUp):
        await bridge.send_request(SimpleNamespace(self_id="bot_1"), object())

    assert sent_messages == []


@pytest.mark.asyncio
async def test_stream_request_replays_tail_response_after_legacy_idle(
    monkeypatch: pytest.MonkeyPatch,
):
    sent_responses: list[object] = []

    class _FakeProtocol:
        def __init__(self, _bot):
            return None

        @staticmethod
        async def handle_message(_event):
            return SimpleNamespace(bot_self_id="bot_1", msg_id="msg_1")

    monkeypatch.setattr(
        bridge_module,
        "_load_bridge_library",
        lambda: SimpleNamespace(protocol_cls=_FakeProtocol),
    )
    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(
            request_timeout=0.2,
            reply_idle_seconds=0.01,
            response_tail_idle_seconds=0.05,
            push_queue_size=10,
            push_workers=1,
        ),
    )

    bridge = bridge_module.ExternalBotBridge()
    emit_tasks: list[asyncio.Task[None]] = []
    first_response = SimpleNamespace(
        bot_self_id="bot_1",
        msg_id="msg_1",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="text", data="first")],
    )
    second_response = SimpleNamespace(
        bot_self_id="bot_1",
        msg_id="msg_1",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="text", data="second")],
    )

    class _FakeClient:
        async def send(self, _message):
            async def emit_responses():
                await bridge._handle_response(first_response)
                await asyncio.sleep(0.02)
                await bridge._handle_response(second_response)

            emit_tasks.append(asyncio.create_task(emit_responses()))

        async def close(self):
            return None

    async def fake_send_response(_bot, response):
        sent_responses.append(response)
        return True

    bridge._client = _FakeClient()
    monkeypatch.setattr(bridge, "_is_supported", lambda _bot, _event: True)
    monkeypatch.setattr(bridge, "ensure_connected", lambda _bot_id: asyncio.sleep(0))
    monkeypatch.setattr(bridge, "wait_until_ready", lambda _timeout: asyncio.sleep(0))
    monkeypatch.setattr(bridge, "_send_response_via_bot", fake_send_response)

    sent_count = await bridge.stream_request(
        SimpleNamespace(self_id="bot_1"),
        object(),
    )
    await asyncio.gather(*emit_tasks)

    assert sent_count == 2
    assert sent_responses == [first_response, second_response]


@pytest.mark.asyncio
async def test_stream_request_wraps_unsupported_response_error(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeProtocol:
        def __init__(self, _bot):
            return None

        @staticmethod
        async def handle_message(_event):
            return SimpleNamespace(bot_self_id="bot_1", msg_id="msg_1")

    monkeypatch.setattr(
        bridge_module,
        "_load_bridge_library",
        lambda: SimpleNamespace(protocol_cls=_FakeProtocol),
    )
    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(
            request_timeout=0.2,
            response_tail_idle_seconds=0.01,
        ),
    )

    bridge = bridge_module.ExternalBotBridge()
    response = SimpleNamespace(
        bot_self_id="bot_1",
        msg_id="msg_1",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="broken", data=None)],
    )

    class _FakeClient:
        async def send(self, _message):
            await bridge._handle_response(response)

    async def fake_send_response(_bot, _response):
        raise bridge_module.BridgeUnsupportedEvent("unsupported")

    bridge._client = _FakeClient()
    monkeypatch.setattr(bridge, "_is_supported", lambda _bot, _event: True)
    monkeypatch.setattr(bridge, "ensure_connected", lambda _bot_id: asyncio.sleep(0))
    monkeypatch.setattr(bridge, "wait_until_ready", lambda _timeout: asyncio.sleep(0))
    monkeypatch.setattr(bridge, "_send_response_via_bot", fake_send_response)

    with pytest.raises(bridge_module.BridgeUnsupportedResponse):
        await bridge.stream_request(SimpleNamespace(self_id="bot_1"), object())


@pytest.mark.asyncio
async def test_late_response_after_pending_finishes_uses_outbound_dispatch(
    monkeypatch: pytest.MonkeyPatch,
):
    sent_responses: list[object] = []
    late_sent = asyncio.Event()
    bot = SimpleNamespace(self_id="bot_1")

    class _FakeProtocol:
        def __init__(self, _bot):
            return None

        @staticmethod
        async def handle_message(_event):
            return SimpleNamespace(bot_self_id="bot_1", msg_id="msg_1")

    monkeypatch.setattr(
        bridge_module,
        "_load_bridge_library",
        lambda: SimpleNamespace(protocol_cls=_FakeProtocol),
    )
    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(
            request_timeout=0.2,
            reply_idle_seconds=0.01,
            response_tail_idle_seconds=0.01,
            push_queue_size=10,
            push_workers=1,
        ),
    )

    bridge = bridge_module.ExternalBotBridge()
    first_response = SimpleNamespace(
        bot_self_id="bot_1",
        msg_id="msg_1",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="text", data="first")],
    )
    late_response = SimpleNamespace(
        bot_self_id="bot_1",
        msg_id="msg_1",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="image", data="file:///tmp/a.png")],
    )

    class _FakeClient:
        async def send(self, _message):
            await bridge._handle_response(first_response)

        async def close(self):
            return None

    async def fake_send_response(_bot, response):
        sent_responses.append(response)
        if response is late_response:
            late_sent.set()
        return True

    bridge._client = _FakeClient()
    monkeypatch.setattr(bridge, "_is_supported", lambda _bot, _event: True)
    monkeypatch.setattr(bridge, "ensure_connected", lambda _bot_id: asyncio.sleep(0))
    monkeypatch.setattr(bridge, "wait_until_ready", lambda _timeout: asyncio.sleep(0))
    monkeypatch.setattr(bridge, "_send_response_via_bot", fake_send_response)
    monkeypatch.setattr(
        bridge,
        "_resolve_outbound_bots",
        lambda _message: ([bot], False),
    )

    try:
        assert await bridge.stream_request(bot, object()) == 1
        await bridge._handle_response(late_response)
        await asyncio.wait_for(late_sent.wait(), timeout=0.2)
    finally:
        await bridge.close()

    assert sent_responses == [first_response, late_response]


def test_outbound_bot_resolution_honors_ambiguous_drop_policy(
    monkeypatch: pytest.MonkeyPatch,
):
    bridge = bridge_module.ExternalBotBridge()
    message_send = SimpleNamespace(bot_self_id="", target_type="group")
    bot_1 = SimpleNamespace(self_id="bot_1")
    bot_2 = SimpleNamespace(self_id="bot_2")

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(ambiguous_bot_policy="drop"),
    )
    monkeypatch.setattr(
        bridge,
        "_get_online_onebot_bots",
        lambda: {"bot_1": bot_1, "bot_2": bot_2},
    )

    assert bridge._resolve_outbound_bots(message_send) == ([], False)
    assert bridge._outbound_metrics["outbound_failed_ambiguous_bot"] == 1


def test_outbound_bot_resolution_defaults_to_random_order(
    monkeypatch: pytest.MonkeyPatch,
):
    bridge = bridge_module.ExternalBotBridge()
    message_send = SimpleNamespace(bot_self_id="", target_type="group")
    bot_1 = SimpleNamespace(self_id="bot_1")
    bot_2 = SimpleNamespace(self_id="bot_2")

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(ambiguous_bot_policy="random_order"),
    )
    monkeypatch.setattr(
        bridge,
        "_get_online_onebot_bots",
        lambda: {"bot_1": bot_1, "bot_2": bot_2},
    )

    bots, routed_randomly = bridge._resolve_outbound_bots(message_send)

    assert {bot.self_id for bot in bots} == {"bot_1", "bot_2"}
    assert routed_randomly is True


@pytest.mark.asyncio
async def test_unsolicited_message_without_source_plugin_is_forwarded(
    monkeypatch: pytest.MonkeyPatch,
):
    delivered = asyncio.Event()
    bot = SimpleNamespace(self_id="bot_1")
    message_send = SimpleNamespace(
        bot_self_id="",
        msg_id="",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="text", data="notice")],
    )

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(push_queue_size=10, push_workers=1),
    )

    bridge = bridge_module.ExternalBotBridge()

    async def fake_send_response(_bot, response):
        assert response is message_send
        delivered.set()
        return True

    monkeypatch.setattr(bridge, "_get_online_onebot_bots", lambda: {"bot_1": bot})
    monkeypatch.setattr(bridge, "_send_response_via_bot", fake_send_response)

    try:
        await bridge._handle_response(message_send)
        await asyncio.wait_for(delivered.wait(), timeout=0.2)
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_ensure_connected_restarts_when_existing_tasks_are_done(
    monkeypatch: pytest.MonkeyPatch,
):
    connect_calls = 0

    class _DoneTask:
        @staticmethod
        def done():
            return True

    class _FakeClient:
        def __init__(self):
            self._tasks = [_DoneTask()]

        async def connect(self):
            nonlocal connect_calls
            connect_calls += 1

    monkeypatch.setattr(
        bridge_module,
        "_load_bridge_library",
        lambda: SimpleNamespace(gs_client_cls=object),
    )
    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(
            client_id="",
            host="127.0.0.1",
            port=8765,
            retry=False,
            request_timeout=20.0,
        ),
    )

    bridge = bridge_module.ExternalBotBridge()
    bridge._client = _FakeClient()
    bridge._client_id = "bot_1"
    monkeypatch.setattr(bridge, "_is_connected", lambda: False)
    monkeypatch.setattr(
        bridge,
        "_wait_until_connected",
        lambda _timeout: asyncio.sleep(0),
    )

    await bridge.ensure_connected("bot_1")

    assert connect_calls == 1
    assert bridge._client._tasks == []


@pytest.mark.asyncio
async def test_source_plugin_message_is_forwarded_when_proactive_push_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    delivered = asyncio.Event()
    bot = SimpleNamespace(self_id="bot_1")
    message_send = SimpleNamespace(
        bot_self_id="",
        msg_id="",
        target_type="group",
        target_id="10000",
        content=[SimpleNamespace(type="text", data="notice")],
        source_plugin="RocomUID",
    )

    monkeypatch.setattr(
        bridge_module,
        "get_external_bot_bridge_settings",
        lambda: SimpleNamespace(
            enable_proactive_push=False,
            push_queue_size=10,
            push_workers=1,
        ),
    )

    bridge = bridge_module.ExternalBotBridge()

    async def fake_send_response(_bot, response):
        assert response is message_send
        delivered.set()
        return True

    monkeypatch.setattr(bridge, "_get_online_onebot_bots", lambda: {"bot_1": bot})
    monkeypatch.setattr(bridge, "_send_response_via_bot", fake_send_response)

    try:
        await bridge._handle_response(message_send)
        await asyncio.wait_for(delivered.wait(), timeout=0.2)
    finally:
        await bridge.close()
