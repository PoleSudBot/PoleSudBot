from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
import pytest

nonebot.init()

from zhenxun.services.external_onebot_gateway import (
    AttributionCache,
    ExternalOneBotAppSpec,
    ExternalOneBotSession,
    QueuedOneBotEvent,
    apply_auto_slash,
    apply_extra_prefix_policy,
    apply_token_rewrites,
    build_action_response,
    content_filter_allows,
    extract_reply_id,
    normalize_action_message,
)
from zhenxun.services.external_onebot_gateway_config import (
    ContentFilterSettings,
    ExternalOneBotAppSettings,
    IdFilterSettings,
)


def _settings(
    *,
    action_allowlist: set[str] | None = None,
) -> ExternalOneBotAppSettings:
    return ExternalOneBotAppSettings(
        ws_url="ws://127.0.0.1:1/ws",
        access_token="",
        virtual_self_id="999",
        route_bot_self_id="",
        attribution_ttl_seconds=30,
        attribution_max_size=10000,
        attribution_sweep_interval_seconds=15,
        event_queue_max_size=1000,
        enable_auto_slash=True,
        heartbeat_interval_seconds=5,
        action_allowlist=action_allowlist or {"get_status"},
        user_filter=IdFilterSettings(mode="blacklist", ids=set()),
        group_filter=IdFilterSettings(mode="blacklist", ids=set()),
        content_filter=ContentFilterSettings(mode="on", patterns=()),
        extra_prefixes=(),
        prefix_replace="",
        token_rewrites={},
    )


def _session(settings: ExternalOneBotAppSettings) -> ExternalOneBotSession:
    return ExternalOneBotSession(
        ExternalOneBotAppSpec(
            name="pjsk",
            display_name="PJSK",
            plugin_module="pjsk",
            settings_factory=lambda: settings,
        )
    )


def test_auto_slash_rewrites_first_text_segment_after_at():
    segments = [
        {"type": "at", "data": {"qq": "123"}},
        {"type": "text", "data": {"text": " 查卡 957"}},
    ]

    assert apply_auto_slash(segments, enabled=True) is True
    assert segments[1]["data"]["text"] == " /查卡 957"


def test_auto_slash_keeps_explicit_slash():
    segments = [{"type": "text", "data": {"text": " /查卡 957"}}]

    assert apply_auto_slash(segments, enabled=True) is False
    assert segments[0]["data"]["text"] == " /查卡 957"


def test_extra_prefix_replaces_without_touching_raw_message():
    segments = [{"type": "text", "data": {"text": " b1查卡 957"}}]

    assert (
        apply_extra_prefix_policy(
            segments,
            extra_prefixes=("b1",),
            prefix_replace="/",
        )
        is True
    )
    assert segments[0]["data"]["text"] == " /查卡 957"


def test_token_rewrite_replaces_first_token_only():
    segments = [{"type": "text", "data": {"text": " /hyw 123"}}]

    assert apply_token_rewrites(segments, {"/hyw": "/q"}) is True
    assert segments[0]["data"]["text"] == " /q 123"


def test_content_filter_modes():
    blacklist = ContentFilterSettings(mode="blacklist", patterns=("哈哈",))
    whitelist = ContentFilterSettings(mode="whitelist", patterns=("查卡",))

    assert content_filter_allows(blacklist, "查卡 957") is True
    assert content_filter_allows(blacklist, "哈哈") is False
    assert content_filter_allows(whitelist, "查卡 957") is True
    assert content_filter_allows(whitelist, "你好") is False
    assert content_filter_allows(whitelist, "你好", bypass=True) is True


def test_extract_reply_id_from_message_array_and_cq_string():
    assert (
        extract_reply_id(
            [
                {"type": "reply", "data": {"id": "1001"}},
                {"type": "text", "data": {"text": "ok"}},
            ]
        )
        == "1001"
    )
    assert extract_reply_id("[CQ:reply,id=1002][CQ:image,file=a.png]") == "1002"


def test_action_response_preserves_echo_on_failure():
    response = build_action_response(
        echo="abc",
        ok=False,
        message="blocked",
    )

    assert response["status"] == "failed"
    assert response["retcode"] == 100
    assert response["echo"] == "abc"


def test_normalize_action_message_converts_dict_segments_to_message():
    message = normalize_action_message(
        [
            {"type": "reply", "data": {"id": "1001"}},
            {"type": "image", "data": {"file": "a.jpg"}},
        ]
    )

    assert isinstance(message, Message)
    assert Message(message).extract_plain_text() == ""
    first_segment = next(iter(message))
    assert first_segment.type == "reply"
    assert first_segment.data["id"] == "1001"


def test_attribution_cache_sweeps_and_evicts_fifo(monkeypatch: pytest.MonkeyPatch):
    warnings: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        "zhenxun.services.external_onebot_gateway.logger.warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    cache = AttributionCache(ttl_seconds=10, max_size=2)

    cache.put(
        "1",
        user_id="u1",
        group_id="g",
        source_bot_self_id="bot",
        plugin_module="pjsk",
        app_name="pjsk",
        now=100,
    )
    cache.put(
        "2",
        user_id="u2",
        group_id="g",
        source_bot_self_id="bot",
        plugin_module="pjsk",
        app_name="pjsk",
        now=101,
    )
    cache.put(
        "3",
        user_id="u3",
        group_id="g",
        source_bot_self_id="bot",
        plugin_module="pjsk",
        app_name="pjsk",
        now=102,
    )

    assert cache.get("1", now=103) is None
    assert cache.get("2", now=103) is not None
    assert cache.sweep(now=112) == 2
    assert len(cache) == 0
    assert warnings


def test_attribution_cache_duplicate_message_id_keeps_latest_record():
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    base = {
        "group_id": "g",
        "source_bot_self_id": "bot",
        "plugin_module": "pjsk",
        "app_name": "pjsk",
    }

    cache.put("1", user_id="old", now=100, **base)
    cache.put("1", user_id="new", now=105, **base)

    assert cache.sweep(now=111) == 0
    record = cache.get("1", now=111)
    assert record is not None
    assert record.user_id == "new"


def test_attribution_cache_ambiguous_message_id_returns_none():
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    base = {
        "group_id": "g",
        "plugin_module": "pjsk",
        "app_name": "pjsk",
    }

    cache.put("1", user_id="u1", source_bot_self_id="bot1", now=100, **base)
    cache.put("1", user_id="u2", source_bot_self_id="bot2", now=100, **base)

    assert cache.get("1", group_id="g", now=101) is None
    record = cache.get("1", group_id="g", source_bot_self_id="bot1", now=101)
    assert record is not None
    assert record.user_id == "u1"


def test_build_event_payload_rewrites_segments_and_raw_message():
    event = GroupMessageEvent(
        time=1,
        self_id=111,
        post_type="message",
        sub_type="normal",
        user_id=222,
        message_type="group",
        message_id=333,
        message=Message([MessageSegment.at(123), MessageSegment.text(" 查卡")]),
        original_message=Message(
            [MessageSegment.at(123), MessageSegment.text(" 查卡")]
        ),
        raw_message="[CQ:at,qq=123] 查卡",
        font=0,
        sender={
            "user_id": 222,
            "nickname": "tester",
            "card": "",
            "sex": "unknown",
            "age": 0,
            "area": "",
            "level": "",
            "role": "member",
            "title": "",
        },
        group_id=444,
    )
    session = _session(_settings())

    payload = session._build_event_payload(QueuedOneBotEvent("111", event), _settings())

    assert payload is not None
    assert payload["self_id"] == 999
    assert payload["message"][1]["data"]["text"] == " /查卡"
    assert payload["raw_message"] == "[CQ:at,qq=123] /查卡"
    assert "original_message" not in payload


@pytest.mark.asyncio
async def test_handle_action_denies_unknown_action_with_echo(monkeypatch):
    sent: list[dict] = []
    session = _session(_settings(action_allowlist={"get_status"}))

    async def fake_send(payload: dict):
        sent.append(payload)

    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {"action": "delete_msg", "params": {"message_id": 1}, "echo": "e1"}
    )

    assert sent == [
        {
            "status": "failed",
            "retcode": 100,
            "data": None,
            "msg": "action delete_msg is not allowed",
            "wording": "action delete_msg is not allowed",
            "echo": "e1",
        }
    ]


@pytest.mark.asyncio
async def test_handle_action_normalizes_message_before_call_api(monkeypatch):
    sent: list[dict] = []
    calls: list[tuple[str, dict]] = []
    session = _session(_settings(action_allowlist={"send_group_msg"}))

    class FakeBot:
        self_id = "999"

        async def call_api(self, action: str, **params):
            calls.append((action, params))
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params):
        return True

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [
                    {"type": "reply", "data": {"id": "1001"}},
                    {"type": "image", "data": {"file": "a.jpg"}},
                ],
            },
            "echo": "e2",
        }
    )

    assert calls[0][0] == "send_group_msg"
    assert isinstance(calls[0][1]["message"], Message)
    assert Message(calls[0][1]["message"])
    assert sent[0]["status"] == "ok"
    assert sent[0]["echo"] == "e2"


@pytest.mark.asyncio
async def test_handle_action_blocks_send_when_plugin_disabled(monkeypatch):
    sent: list[dict] = []
    calls: list[tuple[str, dict]] = []
    session = _session(_settings(action_allowlist={"send_group_msg"}))

    class FakeBot:
        self_id = "999"

        async def call_api(self, action: str, **params):
            calls.append((action, params))
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params):
        return False

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {"group_id": 444, "message": "ok"},
            "echo": "e3",
        }
    )

    assert calls == []
    assert sent[0]["status"] == "failed"
    assert sent[0]["echo"] == "e3"
