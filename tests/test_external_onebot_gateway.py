from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib
import time

import nonebot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
import pytest

nonebot.init()

from zhenxun.services import external_onebot_gateway as gateway_module
from zhenxun.services.external_onebot_gateway import (
    _VIRTUAL_SESSION_KEY,
    AttributionCache,
    AutoSlashFuse,
    EchoSourceTracker,
    ExternalOneBotAppRuntime,
    ExternalOneBotAppSpec,
    ExternalOneBotSession,
    PJSKTimingStats,
    QueuedOneBotEvent,
    _OneBotSelfIdUnavailable,
    apply_auto_slash,
    apply_extra_prefix_policy,
    apply_token_rewrites,
    build_action_response,
    content_filter_allows,
    extract_reply_id,
    normalize_action_message,
    should_apply_auto_slash,
)
from zhenxun.services.external_onebot_gateway_config import (
    DEFAULT_CONTENT_FILTER_REGEX,
    ContentFilterSettings,
    ExternalOneBotAppSettings,
    IdFilterSettings,
    parse_config_bool,
)
from zhenxun.utils.manager.message_manager import MessageManager


def _settings(
    *,
    action_allowlist: set[str] | None = None,
    auto_slash_enabled_group_ids: set[str] | None = None,
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
        auto_slash_enabled_group_ids=auto_slash_enabled_group_ids or set(),
        auto_slash_disabled_group_ids=set(),
        auto_slash_fuse_enabled=True,
        auto_slash_fuse_echo_source_seconds=3,
        auto_slash_fuse_window_seconds=15,
        auto_slash_fuse_max_replies=4,
        auto_slash_fuse_suspend_seconds=30,
        auto_slash_fuse_group_notice_enabled=True,
        auto_slash_fuse_superuser_notice_enabled=True,
        heartbeat_interval_seconds=5,
        timing_enabled=True,
        timing_slow_ms=300,
        timing_summary_interval_seconds=300,
        timing_recent_slow_limit=30,
        timing_text_limit=80,
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


def _clear_auto_withdraw_state() -> None:
    MessageManager.triggered_data.clear()
    MessageManager.triggered_reply_index.clear()
    MessageManager.triggered_order.clear()
    MessageManager.recalled_trigger_sources.clear()
    MessageManager.recalled_trigger_order.clear()


def _spec(settings: ExternalOneBotAppSettings) -> ExternalOneBotAppSpec:
    return ExternalOneBotAppSpec(
        name="pjsk",
        display_name="PJSK",
        plugin_module="pjsk",
        settings_factory=lambda: settings,
    )


def _pjsk_plugin():
    return importlib.import_module("zhenxun.plugins.pjsk")


class _FakeForwardBot:
    self_id = "111"

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_group_forward_msg(self, **kwargs):
        self.sent.append(("group", kwargs))

    async def send_private_forward_msg(self, **kwargs):
        self.sent.append(("private", kwargs))


def test_pjsk_help_message_upgrades_legacy_default_urls(monkeypatch, tmp_path):
    pjsk_plugin = _pjsk_plugin()

    # 旧环境里已落盘的默认 HELP_URLS 需要展示为新的教程链接清单。
    monkeypatch.setattr(
        pjsk_plugin.Config,
        "get",
        lambda module: {
            "HELP_IMAGE_PATH": str(tmp_path / "missing-help.png"),
            "HELP_URLS": pjsk_plugin.LEGACY_HELP_URLS,
        },
    )

    assert pjsk_plugin._build_help_message_items() == [pjsk_plugin.DEFAULT_HELP_URLS]


def test_pjsk_default_help_urls_are_grouped():
    pjsk_plugin = _pjsk_plugin()
    help_urls = pjsk_plugin.DEFAULT_HELP_URLS

    main_index = help_urls.index("【主要入口】")
    data_index = help_urls.index("【账号与数据教程】")
    device_index = help_urls.index("【代理与设备教程】")

    assert main_index < data_index < device_index
    assert "帮助文档: https://neo.haruki.seiunx.com/bot-help/" in help_urls
    assert "Haruki工具箱: https://haruki.seiunx.com" in help_urls
    assert (
        "快速验证: https://neo.haruki.seiunx.com/toolbox-tutorial/verify-guide"
        in help_urls
    )


def test_pjsk_help_forward_nodes_split_image_and_text(tmp_path):
    pjsk_plugin = _pjsk_plugin()
    help_image = tmp_path / "help.png"
    help_image.write_bytes(b"fake image")

    nodes = pjsk_plugin._build_help_forward_nodes(
        _FakeForwardBot(), [help_image, "links"]
    )

    assert len(nodes) == 2
    assert nodes[0]["data"]["content"][0].type == "image"
    assert nodes[1]["data"]["content"].extract_plain_text() == "links"


@pytest.mark.asyncio
async def test_pjsk_help_forward_sends_text_only_as_forward(monkeypatch):
    pjsk_plugin = _pjsk_plugin()
    bot = _FakeForwardBot()
    monkeypatch.setattr(pjsk_plugin, "_build_help_message_items", lambda: ["links"])

    await pjsk_plugin._send_help_forward(bot, _private_event("skhelp"))

    assert bot.sent == [
        (
            "private",
            {
                "user_id": 222,
                "messages": [
                    {
                        "type": "node",
                        "data": {
                            "name": pjsk_plugin.BotConfig.self_nickname or "PJSK",
                            "uin": "111",
                            "content": Message("links"),
                        },
                    }
                ],
            },
        )
    ]


def _group_event(
    text: str,
    *,
    group_id: int = 444,
    role: str = "member",
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=111,
        post_type="message",
        sub_type="normal",
        user_id=222,
        message_type="group",
        message_id=333,
        message=Message([MessageSegment.text(text)]),
        original_message=Message([MessageSegment.text(text)]),
        raw_message=text,
        font=0,
        sender={
            "user_id": 222,
            "nickname": "tester",
            "card": "",
            "sex": "unknown",
            "age": 0,
            "area": "",
            "level": "",
            "role": role,
            "title": "",
        },
        group_id=group_id,
    )


def _private_event(text: str) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=1,
        self_id=111,
        post_type="message",
        sub_type="friend",
        user_id=222,
        message_type="private",
        message_id=333,
        message=Message([MessageSegment.text(text)]),
        original_message=Message([MessageSegment.text(text)]),
        raw_message=text,
        font=0,
        sender={
            "user_id": 222,
            "nickname": "tester",
            "sex": "unknown",
            "age": 0,
        },
    )


@pytest.mark.asyncio
async def test_pjsk_rule_ignores_all_connected_backend_bots(monkeypatch):
    event = _group_event("查卡")
    event.user_id = 222
    pjsk_plugin = _pjsk_plugin()

    monkeypatch.setattr(nonebot, "get_bots", lambda: {"111": object(), "222": object()})

    assert await pjsk_plugin._pjsk_rule(event) is False
    assert await pjsk_plugin._pjsk_help_rule(event) is False


@pytest.mark.asyncio
async def test_pjsk_rule_allows_normal_user(monkeypatch):
    event = _group_event("查卡")
    event.user_id = 333
    pjsk_plugin = _pjsk_plugin()

    monkeypatch.setattr(nonebot, "get_bots", lambda: {"111": object(), "222": object()})

    assert await pjsk_plugin._pjsk_rule(event) is True


def test_pjsk_loose_mode_command_parser_accepts_aliases():
    pjsk_plugin = _pjsk_plugin()

    parsed = pjsk_plugin._parse_loose_mode_command("pjsk免前缀 开启 123456")

    assert parsed is not None
    assert parsed.action == "enable"
    assert parsed.group_id == "123456"
    assert parsed.error is None


def test_pjsk_loose_mode_command_parser_rejects_bad_group_id():
    pjsk_plugin = _pjsk_plugin()

    parsed = pjsk_plugin._parse_loose_mode_command("pjsk快捷模式 关闭 abc")

    assert parsed is not None
    assert parsed.error == "群号必须是纯数字。\n" + pjsk_plugin.LOOSE_MODE_USAGE


def test_pjsk_loose_mode_target_allows_group_admin_current_group():
    pjsk_plugin = _pjsk_plugin()
    parsed = pjsk_plugin.LooseModeCommand("enable")

    target_group_id, error = pjsk_plugin._resolve_loose_mode_target_group(
        _group_event("pjsk宽松模式 开启", group_id=444, role="admin"),
        parsed,
        is_superuser=False,
    )

    assert target_group_id == "444"
    assert error is None


def test_pjsk_loose_mode_target_rejects_admin_group_argument():
    pjsk_plugin = _pjsk_plugin()
    parsed = pjsk_plugin.LooseModeCommand("enable", group_id="555")

    target_group_id, error = pjsk_plugin._resolve_loose_mode_target_group(
        _group_event("pjsk宽松模式 开启 555", group_id=444, role="admin"),
        parsed,
        is_superuser=False,
    )

    assert target_group_id is None
    assert error == "只有超级用户可以指定群号；群管理员请在本群使用不带群号的指令。"


def test_pjsk_loose_mode_target_allows_superuser_private_with_group_argument():
    pjsk_plugin = _pjsk_plugin()
    parsed = pjsk_plugin.LooseModeCommand("enable", group_id="555")

    target_group_id, error = pjsk_plugin._resolve_loose_mode_target_group(
        _private_event("pjsk宽松模式 开启 555"),
        parsed,
        is_superuser=True,
    )

    assert target_group_id == "555"
    assert error is None


def test_pjsk_loose_mode_target_requires_superuser_private_group_argument():
    pjsk_plugin = _pjsk_plugin()
    parsed = pjsk_plugin.LooseModeCommand("enable")

    target_group_id, error = pjsk_plugin._resolve_loose_mode_target_group(
        _private_event("pjsk宽松模式 开启"),
        parsed,
        is_superuser=True,
    )

    assert target_group_id is None
    assert error == "超级用户私聊操作时需要指定群号。\n" + pjsk_plugin.LOOSE_MODE_USAGE


def test_pjsk_loose_mode_target_rejects_normal_member():
    pjsk_plugin = _pjsk_plugin()
    parsed = pjsk_plugin.LooseModeCommand("status")

    target_group_id, error = pjsk_plugin._resolve_loose_mode_target_group(
        _group_event("pjsk宽松模式 状态", group_id=444, role="member"),
        parsed,
        is_superuser=False,
    )

    assert target_group_id is None
    assert error == "该指令仅群管理员或超级用户可用。"


def test_pjsk_loose_mode_status_updates_config(monkeypatch):
    pjsk_plugin = _pjsk_plugin()
    store = {
        "ENABLE_AUTO_SLASH": True,
        "AUTO_SLASH_ENABLED_GROUP_IDS": [],
        "AUTO_SLASH_DISABLED_GROUP_IDS": ["444"],
    }

    class FakeConfigGroup:
        def get(self, key: str, default=None):
            return store.get(key, default)

    def fake_set_config(module: str, key: str, value, auto_save: bool = False):
        assert module == "pjsk"
        store[key] = value

    monkeypatch.setattr(pjsk_plugin.Config, "get", lambda module: FakeConfigGroup())
    monkeypatch.setattr(pjsk_plugin.Config, "set_config", fake_set_config)

    result = pjsk_plugin._set_loose_mode_status("enable", "444")

    assert store["AUTO_SLASH_ENABLED_GROUP_IDS"] == ["444"]
    assert store["AUTO_SLASH_DISABLED_GROUP_IDS"] == []
    assert "已为群 444 开启 PJSK 宽松模式" in result
    assert "互相回响" in result


def test_pjsk_loose_mode_status_parses_string_false(monkeypatch):
    pjsk_plugin = _pjsk_plugin()
    store = {
        "ENABLE_AUTO_SLASH": "false",
        "AUTO_SLASH_ENABLED_GROUP_IDS": ["444"],
        "AUTO_SLASH_DISABLED_GROUP_IDS": [],
    }

    class FakeConfigGroup:
        def get(self, key: str, default=None):
            return store.get(key, default)

    monkeypatch.setattr(pjsk_plugin.Config, "get", lambda module: FakeConfigGroup())

    result = pjsk_plugin._set_loose_mode_status("status", "444")

    assert "PJSK 宽松模式：关闭" in result
    assert "ENABLE_AUTO_SLASH 当前为关闭" in result


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


def test_parse_config_bool_handles_common_string_values():
    assert parse_config_bool("false", True) is False
    assert parse_config_bool("0", True) is False
    assert parse_config_bool("开启", False) is True
    assert parse_config_bool("unknown", True) is True


def test_should_apply_auto_slash_requires_enabled_group():
    settings = _settings()

    assert should_apply_auto_slash(settings, 444) is False


def test_should_apply_auto_slash_allows_enabled_group():
    settings = _settings(auto_slash_enabled_group_ids={"444"})

    assert should_apply_auto_slash(settings, 444) is True


def test_should_apply_auto_slash_disabled_group_overrides_enabled_group():
    settings = replace(
        _settings(auto_slash_enabled_group_ids={"444"}),
        auto_slash_disabled_group_ids={"444"},
    )

    assert should_apply_auto_slash(settings, 444) is False


def test_should_apply_auto_slash_skips_private_context():
    settings = _settings(auto_slash_enabled_group_ids={"444"})

    assert should_apply_auto_slash(settings, None) is False


def test_default_content_filter_preserves_low_misfire_haruki_commands():
    settings = ContentFilterSettings(
        mode="blacklist",
        patterns=tuple(DEFAULT_CONTENT_FILTER_REGEX),
    )
    allowed_texts = [
        "生日",
        "生日 miku",
        "活动",
        "活动 123",
        "活动123",
        "活动mnr1",
        "活动列表",
        "活动一览",
        "活动记录",
        "活动组卡",
        "活动组队",
        "活动卡组",
        "活动配",
        "活动配队",
        "歌曲列表",
        "歌曲一览",
        "歌曲定数",
        "歌曲奖励",
        "歌曲挖矿",
        "歌曲进度",
        "歌曲排行",
        "歌曲别名",
        "歌曲别名待审核",
        "歌曲meta",
        "乐曲列表",
        "乐曲一览",
    ]
    blocked_texts = [
        "生日快乐",
        "活动快乐",
        "活动真多",
        "歌曲",
        "歌曲 六兆年",
        "歌曲真好听",
        "乐曲",
        "乐曲 六兆年",
        "乐曲真好听",
        "音乐真好听",
    ]

    for text in allowed_texts:
        assert content_filter_allows(settings, text), text
    for text in blocked_texts:
        assert not content_filter_allows(settings, text), text


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


def test_attribution_cache_clear_source_also_prunes_fifo_order():
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    base = {"group_id": "g", "plugin_module": "pjsk", "app_name": "pjsk"}
    cache.put("1", user_id="u1", source_bot_self_id="bot1", now=100, **base)
    cache.put("2", user_id="u2", source_bot_self_id="bot2", now=101, **base)

    cache.clear(source_bot_self_id="bot1")

    assert cache.get("1", source_bot_self_id="bot1", now=102) is None
    assert cache.get("2", source_bot_self_id="bot2", now=102) is not None
    assert all(key[0] != "bot1" for key, _ in cache._order)


def test_pjsk_timing_logs_slow_detail_and_summary(monkeypatch):
    logs: list[tuple[str, str]] = []
    settings = replace(_settings(), timing_slow_ms=1, timing_summary_interval_seconds=1)
    stats = PJSKTimingStats()

    monkeypatch.setattr(gateway_module, "_ensure_timing_log_sink", lambda: None)
    monkeypatch.setattr(
        gateway_module.logger,
        "info",
        lambda info, command=None, **kwargs: logs.append((info, command)),
    )

    stats.record(
        kind="inbound",
        settings=settings,
        total_ms=350,
        stages={"ws_send": 320, "queue_wait": 20},
        fields={"trace_id": "t1", "status": "ok"},
    )
    stats.maybe_log_summary(settings, force=True)

    assert logs[0][1] == gateway_module.TIMING_LOG_COMMAND
    assert "kind=inbound" in logs[0][0]
    assert "ws_send=320.0" in logs[0][0]
    assert "summary=inbound" in logs[1][0]
    assert "slow_count=1" in logs[1][0]


def test_pjsk_timing_disabled_skips_logs(monkeypatch):
    logs: list[tuple[str, str]] = []
    settings = replace(_settings(), timing_enabled=False)
    stats = PJSKTimingStats()

    monkeypatch.setattr(gateway_module, "_ensure_timing_log_sink", lambda: None)
    monkeypatch.setattr(
        gateway_module.logger,
        "info",
        lambda info, command=None, **kwargs: logs.append((info, command)),
    )

    stats.record(
        kind="action",
        settings=settings,
        total_ms=10000,
        stages={"call_api": 10000},
        fields={"status": "ok"},
        force_log=True,
    )
    stats.maybe_log_summary(settings, force=True)

    assert logs == []


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
    settings = _settings(auto_slash_enabled_group_ids={"444"})
    session = _session(settings)

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", event), settings
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is True
    assert built_event.source_plain_text == "查卡"
    payload = built_event.payload
    assert payload["self_id"] == 999
    assert payload["message"][1]["data"]["text"] == " /查卡"
    assert payload["raw_message"] == "[CQ:at,qq=123] /查卡"
    assert "original_message" not in payload


def test_build_event_payload_skips_auto_slash_by_default():
    settings = _settings()
    session = _session(settings)

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is False
    payload = built_event.payload
    assert payload["message"][0]["data"]["text"] == "查卡"
    assert payload["raw_message"] == "查卡"


def test_build_event_payload_keeps_explicit_slash_when_loose_mode_off():
    settings = _settings()
    session = _session(settings)

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("/查卡", group_id=444)),
        settings,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is False
    payload = built_event.payload
    assert payload["message"][0]["data"]["text"] == "/查卡"
    assert payload["raw_message"] == "/查卡"


def test_build_event_payload_auto_slash_applies_for_enabled_group():
    settings = _settings(auto_slash_enabled_group_ids={"555"})
    session = _session(settings)

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=555)),
        settings,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is True
    payload = built_event.payload
    assert payload["message"][0]["data"]["text"] == "/查卡"
    assert payload["raw_message"] == "/查卡"


def test_build_event_payload_global_auto_slash_switch_has_precedence():
    settings = replace(
        _settings(auto_slash_enabled_group_ids={"555"}),
        enable_auto_slash=False,
        auto_slash_disabled_group_ids=set(),
    )
    session = _session(settings)

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=555)),
        settings,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is False
    payload = built_event.payload
    assert payload["message"][0]["data"]["text"] == "查卡"
    assert payload["raw_message"] == "查卡"


def test_build_event_payload_disabled_group_overrides_enabled_group():
    settings = replace(
        _settings(auto_slash_enabled_group_ids={"444"}),
        auto_slash_disabled_group_ids={"444"},
    )
    session = _session(settings)

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is False
    payload = built_event.payload
    assert payload["message"][0]["data"]["text"] == "查卡"
    assert payload["raw_message"] == "查卡"


def test_build_event_payload_group_filter_remains_hard_block():
    settings = replace(
        _settings(),
        group_filter=IdFilterSettings(mode="blacklist", ids={"444"}),
    )
    session = _session(settings)

    explicit_payload = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("/查卡", group_id=444)),
        settings,
    )
    implicit_payload = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
    )

    assert explicit_payload is None
    assert implicit_payload is None


@pytest.mark.asyncio
async def test_send_queued_event_records_slow_inbound_timing(monkeypatch):
    logs: list[tuple[str, str]] = []
    settings = replace(
        _settings(auto_slash_enabled_group_ids={"444"}),
        timing_slow_ms=1,
    )
    session = _session(settings)

    async def fake_send_ws(payload: dict):
        await asyncio.sleep(0.002)

    monkeypatch.setattr(gateway_module, "_ensure_timing_log_sink", lambda: None)
    monkeypatch.setattr(
        gateway_module.logger,
        "info",
        lambda info, command=None, **kwargs: logs.append((info, command)),
    )
    monkeypatch.setattr(session, "_send_ws", fake_send_ws)

    await session._send_queued_event(
        QueuedOneBotEvent(
            "111",
            _group_event("查卡", group_id=444),
            enqueued_at=time.monotonic() - 0.002,
            queue_size=3,
            trace_id="trace-inbound",
        )
    )

    assert logs
    assert logs[0][1] == gateway_module.TIMING_LOG_COMMAND
    assert "kind=inbound" in logs[0][0]
    assert "trace_id=trace-inbound" in logs[0][0]
    assert "queue_size=3" in logs[0][0]
    assert "auto_slash=True" in logs[0][0]
    assert "ws_send=" in logs[0][0]


def test_echo_source_tracker_keeps_per_source_records():
    tracker = EchoSourceTracker()

    tracker.put(
        group_id="444",
        user_id="user-a",
        source_message_id="m1",
        source_auto_slash_applied=True,
        now=100,
    )
    tracker.put(
        group_id="444",
        user_id="user-b",
        source_message_id="m2",
        source_auto_slash_applied=True,
        now=101,
    )

    assert (
        tracker.get(group_id="444", user_id="user-a", ttl_seconds=3, now=102)
        is not None
    )
    assert (
        tracker.get(group_id="444", user_id="user-b", ttl_seconds=3, now=102)
        is not None
    )
    assert tracker.sweep(ttl_seconds=3, now=104) == 1
    assert (
        tracker.get(group_id="444", user_id="user-a", ttl_seconds=3, now=104)
        is None
    )
    assert (
        tracker.get(group_id="444", user_id="user-b", ttl_seconds=3, now=104)
        is not None
    )


def test_build_event_payload_marks_same_source_echo_suspect():
    settings = _settings(auto_slash_enabled_group_ids={"444"})
    session = _session(settings)
    session._echo_source_tracker.put(
        group_id="444",
        user_id="222",
        source_message_id="1001",
        source_auto_slash_applied=True,
        now=100,
    )

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
        now=102,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is True
    assert built_event.echo_suspect is True
    assert built_event.echo_source_gap_seconds == 2


def test_build_event_payload_does_not_cross_pollinate_echo_sources():
    settings = _settings(auto_slash_enabled_group_ids={"444"})
    session = _session(settings)
    session._echo_source_tracker.put(
        group_id="444",
        user_id="333",
        source_message_id="1001",
        source_auto_slash_applied=True,
        now=100,
    )

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
        now=102,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is True
    assert built_event.echo_suspect is False
    assert built_event.echo_source_gap_seconds is None


def test_build_event_payload_ignores_expired_echo_source():
    settings = _settings(auto_slash_enabled_group_ids={"444"})
    session = _session(settings)
    session._echo_source_tracker.put(
        group_id="444",
        user_id="222",
        source_message_id="1001",
        source_auto_slash_applied=True,
        now=100,
    )

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
        now=104,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is True
    assert built_event.echo_suspect is False


def test_build_event_payload_requires_auto_slash_for_echo_suspect():
    settings = replace(
        _settings(auto_slash_enabled_group_ids={"444"}),
        auto_slash_disabled_group_ids={"444"},
    )
    session = _session(settings)
    session._echo_source_tracker.put(
        group_id="444",
        user_id="222",
        source_message_id="1001",
        source_auto_slash_applied=True,
        now=100,
    )

    built_event = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
        now=102,
    )

    assert built_event is not None
    assert built_event.auto_slash_applied is False
    assert built_event.echo_suspect is False


def test_auto_slash_fuse_triggers_after_echo_threshold_and_dedupes_reply_id():
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    fuse = AutoSlashFuse()
    base = {
        "group_id": "444",
        "source_bot_self_id": "111",
        "plugin_module": "pjsk",
        "app_name": "pjsk",
        "auto_slash_applied": True,
        "source_plain_text": "活动测试",
        "echo_suspect": True,
        "echo_source_gap_seconds": 1.0,
    }

    cache.put("1", user_id="222", now=100, **base)
    record = cache.get("1", now=101)
    assert record is not None
    assert record.auto_slash_applied is True
    assert record.source_plain_text == "活动测试"

    assert (
        fuse.record_reply(
            attribution=record,
            reply_id="1",
            enabled=True,
            window_seconds=15,
            max_replies=4,
            suspend_seconds=30,
            now=101,
        )
        is None
    )
    assert (
        fuse.record_reply(
            attribution=record,
            reply_id="1",
            enabled=True,
            window_seconds=15,
            max_replies=4,
            suspend_seconds=30,
            now=102,
        )
        is None
    )

    for index, timestamp in ((2, 103), (3, 106)):
        cache.put(str(index), user_id="222", now=timestamp, **base)
        next_record = cache.get(str(index), now=timestamp)
        assert next_record is not None
        assert (
            fuse.record_reply(
                attribution=next_record,
                reply_id=str(index),
                enabled=True,
                window_seconds=15,
                max_replies=4,
                suspend_seconds=30,
                now=timestamp,
            )
            is None
        )

    cache.put("4", user_id="222", now=109, **base)
    final_record = cache.get("4", now=109)
    assert final_record is not None
    trigger = fuse.record_reply(
        attribution=final_record,
        reply_id="4",
        enabled=True,
        window_seconds=15,
        max_replies=4,
        suspend_seconds=30,
        now=109,
    )

    assert trigger is not None
    assert trigger.suspend_until == 139
    assert fuse.is_suspended(app_name="pjsk", user_id="222", enabled=True, now=110)
    assert not fuse.is_suspended(app_name="pjsk", user_id="222", enabled=True, now=140)
    assert fuse.sweep(window_seconds=15, suspend_seconds=30, now=140) == 1
    assert len(fuse) == 0


def test_auto_slash_fuse_ignores_non_echo_attribution():
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    fuse = AutoSlashFuse()
    cache.put(
        "1",
        user_id="222",
        group_id="444",
        source_bot_self_id="111",
        plugin_module="pjsk",
        app_name="pjsk",
        auto_slash_applied=True,
        source_plain_text="查卡",
        echo_suspect=False,
        now=100,
    )
    record = cache.get("1", now=101)
    assert record is not None

    for index in range(5):
        assert (
            fuse.record_reply(
                attribution=record,
                reply_id=str(index),
                enabled=True,
                window_seconds=15,
                max_replies=4,
                suspend_seconds=30,
                now=101 + index,
            )
            is None
        )

    assert not fuse.is_suspended(app_name="pjsk", user_id="222", enabled=True, now=106)


def test_build_event_payload_skips_auto_slash_while_user_fuse_suspended():
    settings = replace(
        _settings(auto_slash_enabled_group_ids={"444"}),
        auto_slash_fuse_max_replies=1,
        auto_slash_fuse_window_seconds=5,
        auto_slash_fuse_suspend_seconds=30,
    )
    session = _session(settings)
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    cache.put(
        "1",
        user_id="222",
        group_id="444",
        source_bot_self_id="111",
        plugin_module="pjsk",
        app_name="pjsk",
        auto_slash_applied=True,
        source_plain_text="查卡",
        echo_suspect=True,
        echo_source_gap_seconds=1.0,
        now=100,
    )
    record = cache.get("1", now=101)
    assert record is not None
    session._auto_slash_fuse.record_reply(
        attribution=record,
        reply_id="1",
        enabled=True,
        window_seconds=5,
        max_replies=1,
        suspend_seconds=30,
    )

    implicit = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("查卡", group_id=444)),
        settings,
    )
    explicit = session._build_event_payload(
        QueuedOneBotEvent("111", _group_event("/查卡", group_id=444)),
        settings,
    )

    assert implicit is not None
    assert implicit.auto_slash_applied is False
    assert implicit.payload["message"][0]["data"]["text"] == "查卡"
    assert explicit is not None
    assert explicit.auto_slash_applied is False
    assert explicit.payload["message"][0]["data"]["text"] == "/查卡"


@pytest.mark.asyncio
async def test_run_connection_waits_when_runtime_self_id_unavailable(monkeypatch):
    settings = replace(_settings(), virtual_self_id="", route_bot_self_id="")
    session = _session(settings)

    monkeypatch.setattr(nonebot, "get_bots", lambda: {})

    with pytest.raises(_OneBotSelfIdUnavailable):
        await session._run_connection(settings)


@pytest.mark.asyncio
async def test_app_runtime_reconciles_available_real_bot_sessions(monkeypatch):
    settings = replace(_settings(), virtual_self_id="", route_bot_self_id="")
    created: list[object] = []

    class FakeBot:
        def __init__(self, self_id: str) -> None:
            self.self_id = self_id

    class FakeSession:
        def __init__(self, spec, *, bound_bot_self_id=None, **kwargs):
            self.spec = spec
            self.bound_bot_self_id = bound_bot_self_id
            self.is_running = False
            self.is_closing = False
            self.closed = False
            self.shared = kwargs
            created.append(self)

        def update_spec(self, spec):
            self.spec = spec

        async def start(self):
            self.is_running = True

        async def close(self):
            self.closed = True
            self.is_running = False
            self.is_closing = True

        def submit_event(self, bot, event):
            return str(bot.self_id) == self.bound_bot_self_id

    monkeypatch.setattr(
        gateway_module,
        "_get_available_onebot_bots",
        lambda: {"111": FakeBot("111"), "222": FakeBot("222")},
    )
    monkeypatch.setattr(gateway_module, "ExternalOneBotSession", FakeSession)
    runtime = ExternalOneBotAppRuntime(_spec(settings))

    await runtime.reconcile()

    assert set(runtime._sessions) == {"111", "222"}
    assert {session.bound_bot_self_id for session in created} == {"111", "222"}
    assert len({id(session.shared["attribution"]) for session in created}) == 1
    assert len({id(session.shared["auto_slash_fuse"]) for session in created}) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_app_runtime_virtual_mode_uses_single_virtual_session(monkeypatch):
    settings = _settings()
    created: list[object] = []

    class FakeSession:
        def __init__(self, spec, *, bound_bot_self_id=None, **kwargs):
            self.bound_bot_self_id = bound_bot_self_id
            self.is_running = False
            self.is_closing = False
            created.append(self)

        def update_spec(self, spec):
            return None

        async def start(self):
            self.is_running = True

        async def close(self):
            self.is_running = False
            self.is_closing = True

    monkeypatch.setattr(gateway_module, "_get_available_onebot_bots", lambda: {})
    monkeypatch.setattr(gateway_module, "ExternalOneBotSession", FakeSession)
    runtime = ExternalOneBotAppRuntime(_spec(settings))

    await runtime.reconcile()

    assert list(runtime._sessions) == [_VIRTUAL_SESSION_KEY]
    assert created[0].bound_bot_self_id is None
    await runtime.close()


@pytest.mark.asyncio
async def test_app_runtime_reconcile_closes_stale_sessions(monkeypatch):
    settings = replace(_settings(), virtual_self_id="", route_bot_self_id="")
    available: dict[str, object] = {"111": object()}
    closed: list[str | None] = []

    class FakeSession:
        def __init__(self, spec, *, bound_bot_self_id=None, **kwargs):
            self.bound_bot_self_id = bound_bot_self_id
            self.is_running = False
            self.is_closing = False

        def update_spec(self, spec):
            return None

        async def start(self):
            self.is_running = True

        async def close(self):
            closed.append(self.bound_bot_self_id)
            self.is_running = False
            self.is_closing = True

    monkeypatch.setattr(
        gateway_module,
        "_get_available_onebot_bots",
        lambda: available,
    )
    monkeypatch.setattr(gateway_module, "ExternalOneBotSession", FakeSession)
    runtime = ExternalOneBotAppRuntime(_spec(settings))

    await runtime.reconcile()
    available.clear()
    await runtime.reconcile()

    assert runtime._sessions == {}
    assert closed == ["111"]
    await runtime.close()


@pytest.mark.asyncio
async def test_app_runtime_reconcile_runs_pending_request(monkeypatch):
    runtime = ExternalOneBotAppRuntime(_spec(_settings()))
    calls = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def fake_reconcile_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()

    monkeypatch.setattr(runtime, "_reconcile_once", fake_reconcile_once)

    task = asyncio.create_task(runtime.reconcile())
    await first_started.wait()
    runtime.request_reconcile()
    release_first.set()
    await task
    if runtime._reconcile_task is not None:
        await runtime._reconcile_task

    assert calls == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_app_runtime_reconcile_request_after_loop_check_runs_again(monkeypatch):
    runtime = ExternalOneBotAppRuntime(_spec(_settings()))
    calls = 0
    second_call_started = asyncio.Event()

    async def fake_reconcile_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            runtime.request_reconcile()
        else:
            second_call_started.set()

    monkeypatch.setattr(runtime, "_reconcile_once", fake_reconcile_once)

    await runtime.reconcile()

    assert calls == 2
    assert second_call_started.is_set()
    await runtime.close()


def test_resolve_action_bot_prefers_attribution_then_bound(monkeypatch):
    settings = replace(_settings(), virtual_self_id="", route_bot_self_id="")
    session = ExternalOneBotSession(_spec(settings), bound_bot_self_id="111")

    class FakeBot:
        def __init__(self, self_id: str) -> None:
            self.self_id = self_id

    monkeypatch.setattr(
        gateway_module,
        "_get_available_onebot_bots",
        lambda: {"111": FakeBot("111"), "222": FakeBot("222")},
    )
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    cache.put(
        "1001",
        user_id="333",
        group_id="444",
        source_bot_self_id="222",
        plugin_module="pjsk",
        app_name="pjsk",
    )
    attribution = cache.get("1001", source_bot_self_id="222")

    assert attribution is not None
    assert session._resolve_action_bot(settings, attribution).self_id == "222"
    assert (
        session._resolve_action_bot(
            settings,
            None,
            bound_bot_self_id=session.bound_bot_self_id,
        ).self_id
        == "111"
    )


@pytest.mark.asyncio
async def test_handle_action_returns_failed_when_bound_bot_offline(monkeypatch):
    sent: list[dict] = []
    settings = replace(
        _settings(action_allowlist={"send_group_msg"}),
        virtual_self_id="",
        route_bot_self_id="",
    )
    session = ExternalOneBotSession(_spec(settings), bound_bot_self_id="111")

    async def fake_send(payload: dict):
        sent.append(payload)

    monkeypatch.setattr(gateway_module, "_get_available_onebot_bots", lambda: {})
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {"group_id": 444, "message": "ok"},
            "echo": "offline",
        }
    )

    assert sent == [
        {
            "status": "failed",
            "retcode": 100,
            "data": None,
            "msg": "no available OneBot V11 bot for action routing",
            "wording": "no available OneBot V11 bot for action routing",
            "echo": "offline",
        }
    ]


@pytest.mark.asyncio
async def test_handle_action_uses_bound_bot_for_attribution_lookup(monkeypatch):
    sent: list[dict] = []
    captured: list[object] = []
    settings = replace(
        _settings(action_allowlist={"send_group_msg"}),
        virtual_self_id="",
        route_bot_self_id="",
    )
    session = ExternalOneBotSession(_spec(settings), bound_bot_self_id="111")
    for bot_id, user_id in (("111", "user-a"), ("222", "user-b")):
        session._attribution.put(
            "1001",
            user_id=user_id,
            group_id="444",
            source_bot_self_id=bot_id,
            plugin_module="pjsk",
            app_name="pjsk",
            auto_slash_applied=True,
            source_plain_text="查卡",
            echo_suspect=True,
        )

    class FakeBot:
        self_id = "111"

        async def call_api(self, action: str, **params):
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        captured.append(attr)

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [
                    {"type": "reply", "data": {"id": "1001"}},
                    {"type": "text", "data": {"text": "ok"}},
                ],
            },
            "echo": "e-bound",
        }
    )

    assert captured
    assert captured[0].user_id == "user-a"
    assert sent[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_handle_action_records_auto_withdraw_for_attributed_reply(monkeypatch):
    _clear_auto_withdraw_state()
    sent: list[dict] = []
    settings = _settings(action_allowlist={"send_group_msg"})
    session = ExternalOneBotSession(_spec(settings), bound_bot_self_id="111")
    session._attribution.put(
        "1001",
        user_id="user-a",
        group_id="444",
        source_bot_self_id="111",
        plugin_module="pjsk",
        app_name="pjsk",
    )

    class FakeBot:
        self_id = "111"

        async def call_api(self, action: str, **params):
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        return None

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [
                    {"type": "reply", "data": {"id": "1001"}},
                    {"type": "text", "data": {"text": "ok"}},
                ],
            },
            "echo": "e-auto-withdraw",
        }
    )

    assert sent[0]["status"] == "ok"
    assert MessageManager.pop_triggered("111", "1001") == ["2001"]


@pytest.mark.asyncio
async def test_handle_action_deletes_late_reply_when_source_already_recalled(
    monkeypatch,
):
    _clear_auto_withdraw_state()
    sent: list[dict] = []
    settings = _settings(action_allowlist={"send_group_msg"})
    session = ExternalOneBotSession(_spec(settings), bound_bot_self_id="111")
    session._attribution.put(
        "1001",
        user_id="user-a",
        group_id="444",
        source_bot_self_id="111",
        plugin_module="pjsk",
        app_name="pjsk",
    )
    MessageManager.mark_trigger_source_recalled("111", "1001")

    class FakeBot:
        self_id = "111"

        def __init__(self):
            self.deleted: list[int] = []

        async def call_api(self, action: str, **params):
            return {"message_id": 2001}

        async def delete_msg(self, *, message_id: int):
            self.deleted.append(message_id)

    bot = FakeBot()

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        return None

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: bot,
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [
                    {"type": "reply", "data": {"id": "1001"}},
                    {"type": "text", "data": {"text": "ok"}},
                ],
            },
            "echo": "e-late-auto-withdraw",
        }
    )

    assert sent[0]["status"] == "ok"
    assert bot.deleted == [2001]
    assert MessageManager.pop_triggered("111", "1001") == []


@pytest.mark.asyncio
async def test_handle_action_does_not_record_auto_withdraw_without_attribution_or_id(
    monkeypatch,
):
    _clear_auto_withdraw_state()
    sent: list[dict] = []
    settings = _settings(action_allowlist={"send_group_msg"})
    session = ExternalOneBotSession(_spec(settings), bound_bot_self_id="111")

    class FakeBot:
        self_id = "111"

        def __init__(self, result: dict):
            self.result = result

        async def call_api(self, action: str, **params):
            return self.result

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        return None

    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)
    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot({"message_id": 2001}),
    )

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {"group_id": 444, "message": "ok"},
            "echo": "e-no-attribution",
        }
    )

    session._attribution.put(
        "1001",
        user_id="user-a",
        group_id="444",
        source_bot_self_id="111",
        plugin_module="pjsk",
        app_name="pjsk",
    )
    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot({}),
    )

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [{"type": "reply", "data": {"id": "1001"}}],
            },
            "echo": "e-no-result-id",
        }
    )

    assert [payload["status"] for payload in sent] == ["ok", "ok"]
    assert MessageManager.triggered_data == {}


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

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
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
async def test_handle_action_records_slow_action_timing(monkeypatch):
    logs: list[tuple[str, str]] = []
    sent: list[dict] = []
    settings = replace(
        _settings(action_allowlist={"send_group_msg"}),
        timing_slow_ms=1,
    )
    session = _session(settings)

    class FakeBot:
        self_id = "999"

        async def call_api(self, action: str, **params):
            await asyncio.sleep(0.002)
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        timing_stages = kwargs.get("timing_stages")
        if timing_stages is not None:
            timing_stages["permission_plugin"] = 0.1
        return True

    monkeypatch.setattr(gateway_module, "_ensure_timing_log_sink", lambda: None)
    monkeypatch.setattr(
        gateway_module.logger,
        "info",
        lambda info, command=None, **kwargs: logs.append((info, command)),
    )
    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {"group_id": 444, "message": "ok"},
            "echo": "trace-action",
        }
    )

    assert sent[0]["status"] == "ok"
    assert logs
    assert logs[0][1] == gateway_module.TIMING_LOG_COMMAND
    assert "kind=action" in logs[0][0]
    assert "trace_id=trace-action" in logs[0][0]
    assert "action=send_group_msg" in logs[0][0]
    assert "call_api=" in logs[0][0]
    assert "permission_plugin=0.1" in logs[0][0]


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

    async def fake_can_send(bot, action, params, **kwargs):
        return False

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
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


@pytest.mark.asyncio
async def test_handle_action_without_reply_does_not_count_fuse(monkeypatch):
    sent: list[dict] = []
    calls: list[tuple[str, dict]] = []
    statistics: list[tuple[object, str]] = []
    session = _session(_settings(action_allowlist={"send_group_msg"}))

    class FakeBot:
        self_id = "999"

        async def call_api(self, action: str, **params):
            calls.append((action, params))
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        statistics.append((attr, bot_id))

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {"group_id": 444, "message": "ok"},
            "echo": "e4",
        }
    )

    assert calls == [("send_group_msg", {"group_id": 444, "message": "ok"})]
    assert statistics == []
    assert len(session._auto_slash_fuse) == 0
    assert sent[0]["status"] == "ok"
    assert sent[0]["echo"] == "e4"


@pytest.mark.asyncio
async def test_handle_action_with_expired_reply_does_not_count_fuse(monkeypatch):
    sent: list[dict] = []
    calls: list[tuple[str, dict]] = []
    statistics: list[tuple[object, str]] = []
    session = _session(_settings(action_allowlist={"send_group_msg"}))
    session._attribution.put(
        "1001",
        user_id="222",
        group_id="444",
        source_bot_self_id="999",
        plugin_module="pjsk",
        app_name="pjsk",
        auto_slash_applied=True,
        source_plain_text="活动测试",
        now=100,
    )

    class FakeBot:
        self_id = "999"

        async def call_api(self, action: str, **params):
            calls.append((action, params))
            return {"message_id": 2001}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        statistics.append((attr, bot_id))

    monkeypatch.setattr(
        session._attribution,
        "get",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)

    await session._handle_action(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [
                    {"type": "reply", "data": {"id": "1001"}},
                    {"type": "text", "data": {"text": "ok"}},
                ],
            },
            "echo": "e5",
        }
    )

    assert calls[0][0] == "send_group_msg"
    assert statistics == []
    assert len(session._auto_slash_fuse) == 0
    assert sent[0]["status"] == "ok"
    assert sent[0]["echo"] == "e5"


@pytest.mark.asyncio
async def test_handle_action_triggers_auto_slash_fuse_notices_once(monkeypatch):
    settings = replace(
        _settings(action_allowlist={"send_group_msg"}),
        auto_slash_fuse_max_replies=1,
        auto_slash_fuse_window_seconds=5,
        auto_slash_fuse_suspend_seconds=30,
    )
    sent: list[dict] = []
    calls: list[tuple[str, dict]] = []
    session = _session(settings)
    session._attribution.put(
        "1001",
        user_id="222",
        group_id="444",
        source_bot_self_id="999",
        plugin_module="pjsk",
        app_name="pjsk",
        auto_slash_applied=True,
        source_plain_text="活动测试",
        echo_suspect=True,
        echo_source_gap_seconds=1.2,
    )

    class FakeBot:
        self_id = "999"

        async def call_api(self, action: str, **params):
            calls.append((action, params))
            return {"message_id": len(calls)}

    async def fake_send(payload: dict):
        sent.append(payload)

    async def fake_can_send(bot, action, params, **kwargs):
        return True

    async def fake_record_statistics(attr, bot_id):
        return None

    monkeypatch.setattr(
        session,
        "_resolve_action_bot",
        lambda settings, attr, **kwargs: FakeBot(),
    )
    monkeypatch.setattr(session, "_can_send_action", fake_can_send)
    monkeypatch.setattr(session, "_record_statistics", fake_record_statistics)
    monkeypatch.setattr(session, "_send_action_response", fake_send)
    monkeypatch.setattr(
        gateway_module.driver.config,
        "superusers",
        {"10000"},
        raising=False,
    )

    def make_payload() -> dict:
        return {
            "action": "send_group_msg",
            "params": {
                "group_id": 444,
                "message": [
                    {"type": "reply", "data": {"id": "1001"}},
                    {"type": "text", "data": {"text": "ok"}},
                ],
            },
            "echo": "e4",
        }

    await session._handle_action(make_payload())
    await session._handle_action(make_payload())

    group_notices = [
        params["message"]
        for action, params in calls
        if action == "send_group_msg" and "疑似在响应 PJSK 回复" in str(params)
    ]
    superuser_notices = [
        params["message"] for action, params in calls if action == "send_private_msg"
    ]
    assert len(group_notices) == 1
    assert "暂停该账号自动补 /" in group_notices[0]
    assert len(superuser_notices) == 1
    assert "message_id: 1001" in superuser_notices[0]
    assert "echo_suspect: True" in superuser_notices[0]
    assert "echo_gap: 1.2" in superuser_notices[0]
    assert "source: 活动测试" in superuser_notices[0]
    assert [item["echo"] for item in sent] == ["e4", "e4"]


def test_auto_slash_fuse_superuser_notice_truncates_source_text():
    session = _session(_settings())
    cache = AttributionCache(ttl_seconds=10, max_size=10)
    cache.put(
        "1001",
        user_id="222",
        group_id="444",
        source_bot_self_id="999",
        plugin_module="pjsk",
        app_name="pjsk",
        auto_slash_applied=True,
        source_plain_text="x" * 200,
        echo_suspect=True,
        echo_source_gap_seconds=1.2,
        now=100,
    )
    record = cache.get("1001", now=101)
    assert record is not None

    notice = session._build_auto_slash_fuse_superuser_notice(
        attribution=record,
        routed_bot_self_id="999",
        suspend_seconds=30,
    )

    assert "source: " + "x" * 120 + "..." in notice
    assert "x" * 121 not in notice
