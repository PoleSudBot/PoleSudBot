from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from zhenxun.services import onebot_transport


class _FakeAdapter:
    def __init__(self, *, connections=None, api_roots=None):
        self.connections = connections or {}
        self.onebot_config = SimpleNamespace(onebot_api_roots=api_roots or {})

    def get_name(self) -> str:
        return "OneBot V11"


def _make_bot(*, connections=None, api_roots=None):
    return SimpleNamespace(
        self_id="bot_1",
        adapter=_FakeAdapter(connections=connections, api_roots=api_roots),
    )


@pytest.fixture(scope="session", autouse=True)
async def nonebug_init(_nonebot_init: None, after_nonebot_init: None):
    return None


def test_transport_available_when_ws_registered():
    onebot_transport.reset_state()
    bot = _make_bot(connections={"bot_1": object()})

    assert onebot_transport.is_available(bot) is True
    assert onebot_transport.get_status(bot).reason == "ws"


def test_transport_available_when_http_api_root_configured():
    onebot_transport.reset_state()
    bot = _make_bot(api_roots={"bot_1": "http://127.0.0.1:3000"})

    assert onebot_transport.is_available(bot) is True
    assert onebot_transport.get_status(bot).reason == "http"


def test_transport_unavailable_without_ws_or_http_root():
    onebot_transport.reset_state()
    bot = _make_bot()

    assert onebot_transport.is_available(bot) is False
    assert onebot_transport.get_status(bot).reason == "missing_control_plane"


def test_transport_unavailable_when_http_api_root_missing_scheme():
    onebot_transport.reset_state()
    bot = _make_bot(api_roots={"bot_1": "127.0.0.1:3000"})

    assert onebot_transport.is_available(bot) is False
    assert onebot_transport.get_status(bot).reason == "missing_control_plane"


def test_transport_cooldown_marks_bot_temporarily_unavailable(monkeypatch):
    onebot_transport.reset_state()
    now = {"value": 100.0}

    monkeypatch.setattr(
        onebot_transport.time, "monotonic", lambda: now["value"]
    )

    bot = _make_bot(connections={"bot_1": object()})
    onebot_transport.mark_disconnected(bot)

    assert onebot_transport.should_drop(bot) is True

    now["value"] = 106.0

    assert onebot_transport.should_drop(bot) is False


def test_note_unavailable_is_sampled(monkeypatch):
    onebot_transport.reset_state()
    now = {"value": 100.0}
    warnings: list[tuple[tuple, dict]] = []

    monkeypatch.setattr(
        onebot_transport.time, "monotonic", lambda: now["value"]
    )
    monkeypatch.setattr(
        onebot_transport.logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    bot = _make_bot()

    assert onebot_transport.note_unavailable(bot, "丢弃新消息") is True

    now["value"] = 105.0
    assert onebot_transport.note_unavailable(bot, "丢弃新消息") is False

    now["value"] = 111.0
    assert onebot_transport.note_unavailable(bot, "丢弃新消息") is True
    assert len(warnings) == 2
