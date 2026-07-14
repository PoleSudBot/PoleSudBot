from __future__ import annotations

# ruff: noqa: I001

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
import pytest

nonebot.init()
nonebot.require("nonebot_plugin_alconna")

from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_alconna.uniseg import (
    At,
    Audio,
    Emoji,
    File,
    Hyper,
    Image,
    Reference,
    Reply,
    Text,
    Video,
)
from tortoise import Tortoise

from zhenxun.builtin_plugins.chat_history import chat_message as chat_message_mod
from zhenxun.models.chat_history import ChatHistory
from zhenxun.services.chat_history import recorder as recorder_mod
from zhenxun.services.chat_history.recorder import (
    ChatHistoryQuery,
    MAX_QUERY_LIMIT,
    SEGMENT_SCAN_PAGE_SIZE,
    build_incoming_record,
    build_outgoing_record,
    normalize_message_segments,
    segments_to_readable_text,
)


def _clear_history_queue():
    while True:
        try:
            chat_message_mod._HISTORY_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            break


def _reset_history_queue_stats():
    _clear_history_queue()
    chat_message_mod._DROP_COUNT = 0
    chat_message_mod._FLUSH_FAILURE_COUNT = 0
    chat_message_mod._REQUEUE_DROP_COUNT = 0


class _FakeChatHistoryQuery:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.q_filters: list[object] = []
        self.filters: list[dict[str, object]] = []
        self.excludes: list[dict[str, object]] = []
        self.order_fields: tuple[str, ...] = ()
        self.limit_value: int | None = None
        self.annotated = False
        self.group_fields: tuple[str, ...] = ()
        self.value_fields: tuple[str, ...] = ()

    def filter(self, *args, **kwargs):
        self.q_filters.extend(args)
        self.filters.append(kwargs)
        return self

    def exclude(self, **kwargs):
        self.excludes.append(kwargs)
        return self

    def order_by(self, *fields):
        self.order_fields = fields
        return self

    def limit(self, limit):
        self.limit_value = limit
        return self

    def annotate(self, **_kwargs):
        self.annotated = True
        return self

    def group_by(self, *fields):
        self.group_fields = fields
        return self

    async def values_list(self, *_fields):
        return [("1000", 3), ("1001", 1)]

    async def values(self, *fields):
        self.value_fields = fields
        return self.rows

    async def count(self):
        return 7

    async def first(self):
        return self.rows[0] if self.rows else None

    def __await__(self):
        rows = self.rows
        if self.limit_value is not None:
            rows = rows[: self.limit_value]

        async def _result():
            return rows

        return _result().__await__()


class _FakeChatHistory:
    last_query: ClassVar[_FakeChatHistoryQuery | None] = None
    rows: ClassVar[list[object]] = []

    @classmethod
    def all(cls):
        cls.last_query = _FakeChatHistoryQuery(cls.rows)
        return cls.last_query

    @classmethod
    def filter(cls, **kwargs):
        query = cls.all()
        return query.filter(**kwargs)


class _PagedSegmentQuery:
    pages: ClassVar[list[list[object]]] = []
    created: ClassVar[list["_PagedSegmentQuery"]] = []

    def __init__(self):
        self.q_filters: list[object] = []
        self.filters: list[dict[str, object]] = []
        self.excludes: list[dict[str, object]] = []
        self.order_fields: tuple[str, ...] = ()
        self.limit_value: int | None = None
        _PagedSegmentQuery.created.append(self)

    def filter(self, *args, **kwargs):
        self.q_filters.extend(args)
        self.filters.append(kwargs)
        return self

    def exclude(self, **kwargs):
        self.excludes.append(kwargs)
        return self

    def order_by(self, *fields):
        self.order_fields = fields
        return self

    def limit(self, limit):
        self.limit_value = limit
        return self

    def __await__(self):
        page_index = len(_PagedSegmentQuery.created) - 1
        rows = (
            _PagedSegmentQuery.pages[page_index]
            if page_index < len(_PagedSegmentQuery.pages)
            else []
        )

        async def _result():
            return rows

        return _result().__await__()


def test_normalize_message_segments_keeps_lightweight_media_metadata():
    message = Message(
        [
            MessageSegment.text("看看"),
            MessageSegment.at(123),
            MessageSegment.image("base64://very-large-payload"),
            MessageSegment(
                "record",
                {"file": "voice.silk", "url": "https://example.com/v"},
            ),
        ]
    )

    segments, segment_types, readable_text = normalize_message_segments(message)

    assert segment_types == ["text", "mention", "image", "audio"]
    assert readable_text == "看看@123[图片][语音]"
    assert segments[2] == {
        "type": "image",
        "data": {},
    }
    assert segments[3] == {
        "type": "audio",
        "data": {"file": "voice.silk"},
    }
    assert "base64://" not in readable_text


def test_normalize_unimsg_uses_cross_platform_contract():
    long_url = "https://example.com/" + "x" * 600
    message = UniMsg(
        [
            Text("看看"),
            At("user", "123"),
            Image(url=long_url, name="pic.png"),
            Audio(url="https://example.com/audio.silk"),
            Video(url="https://example.com/video.mp4"),
            File(url="https://example.com/file.zip", name="file.zip"),
            Emoji("88"),
            Reply("reply-id"),
            Reference("forward-id"),
            Hyper("json", '{"large":"payload"}'),
        ]
    )

    segments, segment_types, readable_text = normalize_message_segments(message)

    assert segment_types == [
        "text",
        "mention",
        "image",
        "audio",
        "video",
        "file",
        "emoji",
        "reply",
        "reference",
        "card",
    ]
    assert segments[1] == {
        "type": "mention",
        "data": {"target": "123", "kind": "user"},
    }
    for segment in segments:
        assert "url" not in segment["data"]
    assert segments[6] == {"type": "emoji", "data": {"id": "88"}}
    assert segments[7] == {
        "type": "reply",
        "data": {"message_id": "reply-id"},
    }
    assert segments[8] == {
        "type": "reference",
        "data": {"id": "forward-id"},
    }
    assert segments[9] == {
        "type": "card",
        "data": {"format": "json"},
    }
    assert readable_text == (
        "看看@123[图片][语音][视频][文件][表情:88][引用消息][合并转发][卡片]"
    )
    assert segments[2]["data"] == {"name": "pic.png"}
    assert segments[5]["data"] == {"name": "file.zip"}


def test_normalize_unknown_segment_keeps_safe_metadata():
    message = [
        {
            "type": "platform_magic",
            "data": {
                "value": "ok",
                "url": "https://example.com/temporary",
                "raw": "base64://large-payload",
                "nested": {},
            },
        }
    ]

    segments, segment_types, readable_text = normalize_message_segments(message)

    assert segment_types == ["unknown"]
    assert segments == [
        {
            "type": "unknown",
            "data": {"raw_type": "platform_magic", "value": "ok"},
        }
    ]
    assert readable_text == "[unknown]"


def test_normalize_text_does_not_treat_base64_prefix_as_media():
    segments, _, readable_text = normalize_message_segments("base64://这是普通文本")

    assert segments == [
        {"type": "text", "data": {"text": "base64://这是普通文本"}}
    ]
    assert readable_text == "base64://这是普通文本"


def test_segments_to_readable_text_outputs_stable_placeholders():
    segments = [
        {"type": "text", "data": {"text": "看看"}},
        {"type": "image", "data": {"file": "pic.png"}},
        {"type": "reference", "data": {"id": "forward-id"}},
    ]

    assert segments_to_readable_text(segments) == "看看[图片][合并转发]"


def test_segments_to_readable_text_supports_legacy_segment_names():
    segments = [
        {"type": "at", "data": {"qq": "123"}},
        {"type": "record", "data": {"file": "voice.silk"}},
        {"type": "reply", "data": {"id": "999"}},
    ]

    assert segments_to_readable_text(segments) == "@123[语音][引用消息]"


def test_build_incoming_record_records_create_time_and_reply_id(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _session: "qq",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(id="1000"),
        group=SimpleNamespace(id="2000", parent=None),
        self_id="9000",
    )
    event = SimpleNamespace(message_id=3456, message_type="group", time=1_700_000_000)
    message = Message(
        [
            MessageSegment.reply(1234),
            MessageSegment("forward", {"id": "forward-id"}),
            MessageSegment.text("总结一下"),
        ]
    )

    record = build_incoming_record(message, session, event)

    assert record.direction == "in"
    assert record.message_id == "3456"
    assert record.message_type == "group"
    assert record.platform == "qq"
    assert int(record.create_time.timestamp()) == 1_700_000_000
    assert record.reply_to_message_id == "1234"
    assert record.segment_types == ["reply", "reference", "text"]
    assert record.text == "[引用消息][合并转发]总结一下"
    assert "[CQ:" not in record.text
    assert not hasattr(record, "reply_preview")
    assert not hasattr(record, "forward_meta")
    assert not hasattr(record, "record_version")


@pytest.mark.asyncio
async def test_build_incoming_record_keeps_timestamp_with_project_timezone(monkeypatch):
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [],
                    "default_connection": "default",
                }
            },
            "timezone": "Asia/Shanghai",
        }
    )
    try:
        monkeypatch.setattr(
            "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
            lambda _session: "qq",
        )
        session = SimpleNamespace(
            user=SimpleNamespace(id="1000"),
            group=SimpleNamespace(id="2000", parent=None),
            self_id="9000",
        )

        record = build_incoming_record(
            UniMsg([Text("hello")]),
            session,
            SimpleNamespace(time=1_700_000_000),
        )

        assert int(record.create_time.timestamp()) == 1_700_000_000
        assert record.create_time.utcoffset() == timedelta(hours=8)
    finally:
        await Tortoise.close_connections()


def test_build_incoming_record_keeps_plain_text_text_only(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _session: "qq",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(id="1000"),
        group=SimpleNamespace(id="2000", parent=None),
        self_id="9000",
    )
    message = Message([MessageSegment.text("看看"), MessageSegment.image("pic.png")])

    record = build_incoming_record(message, session, SimpleNamespace())

    assert record.plain_text == "看看"
    assert record.segment_types == ["text", "image"]
    assert record.text == "看看[图片]"
    assert segments_to_readable_text(record.segments) == "看看[图片]"


def test_build_incoming_record_allows_empty_plain_text_for_image(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _session: "qq",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(id="1000"),
        group=SimpleNamespace(id="2000", parent=None),
        self_id="9000",
    )
    message = Message([MessageSegment.image("pic.png")])

    record = build_incoming_record(message, session, SimpleNamespace())

    assert record.plain_text == ""
    assert record.segment_types == ["image"]
    assert segments_to_readable_text(record.segments) == "[图片]"


def test_build_incoming_record_falls_back_when_event_time_invalid(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _session: "qq",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(id="1000"),
        group=SimpleNamespace(id="2000", parent=None),
        self_id="9000",
    )

    before = datetime.now().timestamp()
    record = build_incoming_record(
        Message([MessageSegment.text("hello")]),
        session,
        SimpleNamespace(time="bad"),
    )
    after = datetime.now().timestamp()

    assert before <= record.create_time.timestamp() <= after


def test_build_incoming_record_reads_reply_id_from_event_fallback(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _session: "qq",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(id="1000"),
        group=SimpleNamespace(id="2000", parent=None),
        self_id="9000",
    )
    event = SimpleNamespace(reply=SimpleNamespace(message_id=9876))

    record = build_incoming_record(
        Message([MessageSegment.image("pic.png")]),
        session,
        event,
    )

    assert record.reply_to_message_id == "9876"


def test_build_incoming_record_normalizes_channel_scene_type(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _session: "qq",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(id="1000"),
        group=SimpleNamespace(id="2000", parent=None),
        scene=SimpleNamespace(type=SimpleNamespace(name="CHANNEL_TEXT")),
        self_id="9000",
    )

    record = build_incoming_record(
        UniMsg([Text("hello")]),
        session,
        SimpleNamespace(message_id="1"),
    )

    assert record.message_type == "channel"


@pytest.mark.asyncio
async def test_chat_history_query_recent_defaults_to_inbound_and_clamps_limit(
    monkeypatch,
):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    _FakeChatHistory.rows = [SimpleNamespace(id=1)]

    rows = await ChatHistoryQuery.recent(
        group_id="2000",
        user_id="1000",
        limit=MAX_QUERY_LIMIT + 1,
    )

    query = _FakeChatHistory.last_query
    assert rows == _FakeChatHistory.rows
    assert query is not None
    assert query.filters == [
        {"group_id": "2000"},
        {"user_id": "1000"},
        {"direction": "in"},
    ]
    assert query.order_fields == ("-create_time", "-id")
    assert query.limit_value == MAX_QUERY_LIMIT


@pytest.mark.asyncio
async def test_chat_history_query_time_range_uses_create_time(
    monkeypatch,
):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    start = datetime(2026, 6, 17, 0, 0, 0)
    end = start + timedelta(hours=24)

    await ChatHistoryQuery.time_range(
        start=start,
        end=end,
        group_id="2000",
        limit=10000,
    )

    query = _FakeChatHistory.last_query
    assert query is not None
    assert query.filters[0] == {"group_id": "2000"}
    assert query.filters[1] == {"direction": "in"}
    assert query.filters[2] == {"create_time__range": (start, end)}
    assert query.q_filters == []
    assert query.order_fields == ("create_time", "id")
    assert query.limit_value == 10000


@pytest.mark.asyncio
async def test_chat_history_query_time_range_defaults_to_max_limit(monkeypatch):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    start = datetime(2026, 6, 17, 0, 0, 0)
    end = start + timedelta(hours=24)

    await ChatHistoryQuery.time_range(start=start, end=end, group_id="2000")

    query = _FakeChatHistory.last_query
    assert query is not None
    assert query.limit_value == MAX_QUERY_LIMIT


@pytest.mark.asyncio
async def test_chat_history_query_text_range_selects_only_lightweight_fields(
    monkeypatch,
):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    _FakeChatHistory.rows = [
        {
            "id": 1,
            "user_id": "1000",
            "create_time": datetime(2026, 6, 17, 0, 0, 0),
            "text": "看看[图片]",
            "message_id": "10",
            "reply_to_message_id": None,
        }
    ]
    start = datetime(2026, 6, 17, 0, 0, 0)
    end = start + timedelta(hours=24)

    rows = await ChatHistoryQuery.text_range(
        start=start,
        end=end,
        group_id="2000",
        limit=500,
    )

    query = _FakeChatHistory.last_query
    assert rows == _FakeChatHistory.rows
    assert query is not None
    assert query.value_fields == (
        "id",
        "user_id",
        "create_time",
        "text",
        "message_id",
        "reply_to_message_id",
        "direction",
        "bot_id",
        "platform",
        "message_type",
    )
    assert "segments" not in query.value_fields
    assert query.order_fields == ("create_time", "id")
    assert query.limit_value == 500


@pytest.mark.asyncio
async def test_chat_history_query_structured_range_includes_segments(monkeypatch):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    _FakeChatHistory.rows = [{"id": 1, "segments": []}]
    start = datetime(2026, 6, 17, 0, 0, 0)
    end = start + timedelta(hours=24)

    rows = await ChatHistoryQuery.structured_range(
        start=start,
        end=end,
        group_id="2000",
        limit=200,
    )

    query = _FakeChatHistory.last_query
    assert rows == _FakeChatHistory.rows
    assert query is not None
    assert "segments" in query.value_fields
    assert "segment_types" in query.value_fields
    assert "direction" in query.value_fields
    assert "bot_id" in query.value_fields
    assert "platform" in query.value_fields
    assert "message_type" in query.value_fields
    assert query.limit_value == 200


@pytest.mark.asyncio
async def test_chat_history_query_structured_range_can_read_newest_first(monkeypatch):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    start = datetime(2026, 6, 17, 0, 0, 0)
    end = start + timedelta(hours=24)

    await ChatHistoryQuery.structured_range(
        start=start,
        end=end,
        group_id="2000",
        descending=True,
        limit=200,
    )

    query = _FakeChatHistory.last_query
    assert query is not None
    assert query.order_fields == ("-create_time", "-id")


@pytest.mark.asyncio
async def test_chat_history_query_text_recent_uses_lightweight_projection(monkeypatch):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    _FakeChatHistory.rows = [{"id": 1, "text": "hello"}]

    rows = await ChatHistoryQuery.text_recent(
        group_id="2000",
        direction="all",
        limit=MAX_QUERY_LIMIT + 1,
    )

    query = _FakeChatHistory.last_query
    assert rows == _FakeChatHistory.rows
    assert query is not None
    assert query.filters == [{"group_id": "2000"}]
    assert query.order_fields == ("-create_time", "-id")
    assert query.limit_value == MAX_QUERY_LIMIT
    assert "segments" not in query.value_fields
    assert "direction" in query.value_fields
    assert "bot_id" in query.value_fields


@pytest.mark.asyncio
async def test_chat_history_query_structured_recent_includes_segments(monkeypatch):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    _FakeChatHistory.rows = [{"id": 1, "segments": []}]

    rows = await ChatHistoryQuery.structured_recent(
        group_id="2000",
        direction="in",
        limit=250,
    )

    query = _FakeChatHistory.last_query
    assert rows == _FakeChatHistory.rows
    assert query is not None
    assert query.filters == [{"group_id": "2000"}, {"direction": "in"}]
    assert query.order_fields == ("-create_time", "-id")
    assert query.limit_value == 250
    assert "segments" in query.value_fields
    assert "segment_types" in query.value_fields


@pytest.mark.asyncio
async def test_chat_history_query_count_first_and_rank_share_base_filters(
    monkeypatch,
):
    monkeypatch.setattr(recorder_mod, "ChatHistory", _FakeChatHistory)
    _FakeChatHistory.rows = [SimpleNamespace(id=1)]

    count = await ChatHistoryQuery.count(group_id="2000", direction="all")
    first = await ChatHistoryQuery.first(group_id="2000")
    rank = await ChatHistoryQuery.rank(group_id="2000", descending=False)

    query = _FakeChatHistory.last_query
    assert count == 7
    assert first is _FakeChatHistory.rows[0]
    assert rank == [("1000", 3), ("1001", 1)]
    assert query is not None
    assert query.annotated is True
    assert query.group_fields == ("user_id",)
    assert query.order_fields == ("count",)


@pytest.mark.asyncio
async def test_chat_history_query_by_segment_type_scans_multiple_pages(
    monkeypatch,
):
    base_time = datetime(2026, 6, 17, 12, 0, 0)
    page_one = [
        SimpleNamespace(
            id=3,
            create_time=base_time,
            segment_types=["text"],
        )
        for _ in range(SEGMENT_SCAN_PAGE_SIZE)
    ]
    page_one[-1] = SimpleNamespace(
        id=2,
        create_time=base_time - timedelta(seconds=1),
        segment_types=["text"],
    )
    target = SimpleNamespace(
        id=1,
        create_time=base_time - timedelta(seconds=2),
        segment_types=None,
        segments=[{"type": "image", "data": {}}],
    )
    _PagedSegmentQuery.pages = [page_one, [target]]
    _PagedSegmentQuery.created = []
    monkeypatch.setattr(recorder_mod.ChatHistory, "all", _PagedSegmentQuery)

    rows = await ChatHistoryQuery.by_segment_type("image", group_id="2000", limit=1)

    assert rows == [target]
    assert len(_PagedSegmentQuery.created) == 2
    assert _PagedSegmentQuery.created[0].filters == [
        {"group_id": "2000"},
        {"direction": "in"},
    ]
    assert _PagedSegmentQuery.created[1].excludes == [
        {
            "create_time": page_one[-1].create_time,
            "id__gte": page_one[-1].id,
        }
    ]


@pytest.mark.asyncio
async def test_flush_history_queue_bulk_creates_and_clears_queue(monkeypatch):
    _clear_history_queue()
    records = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    created: list[object] = []

    async def _bulk_create(items):
        created.extend(items)

    monkeypatch.setattr(chat_message_mod.ChatHistory, "bulk_create", _bulk_create)
    for record in records:
        chat_message_mod._HISTORY_QUEUE.put_nowait(record)

    flushed = await chat_message_mod._flush_history_queue()

    assert flushed == 2
    assert created == records
    assert chat_message_mod._HISTORY_QUEUE.empty()


@pytest.mark.asyncio
async def test_flush_history_queue_requeues_batch_when_bulk_create_fails(monkeypatch):
    _clear_history_queue()
    records = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

    async def _bulk_create(_items):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_message_mod.ChatHistory, "bulk_create", _bulk_create)
    for record in records:
        chat_message_mod._HISTORY_QUEUE.put_nowait(record)

    flushed = await chat_message_mod._flush_history_queue()
    requeued = chat_message_mod._drain_history_queue()

    assert flushed == 0
    assert requeued == records


@pytest.mark.asyncio
async def test_flush_history_queue_reports_dropped_items_when_requeue_is_full(
    monkeypatch,
):
    _reset_history_queue_stats()
    records = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    warnings: list[str] = []

    async def _bulk_create(_items):
        raise RuntimeError("db down")

    def _requeue_one(items):
        chat_message_mod._HISTORY_QUEUE.put_nowait(items[0])
        return 1

    monkeypatch.setattr(chat_message_mod.ChatHistory, "bulk_create", _bulk_create)
    monkeypatch.setattr(chat_message_mod, "_requeue_history_batch", _requeue_one)
    monkeypatch.setattr(
        chat_message_mod.logger,
        "warning",
        lambda message, *_args, **_kwargs: warnings.append(message),
    )
    for record in records:
        chat_message_mod._HISTORY_QUEUE.put_nowait(record)

    flushed = await chat_message_mod._flush_history_queue()

    assert flushed == 0
    assert "batch=2 requeued=1 dropped=1" in warnings[0]
    state = chat_message_mod.get_history_queue_state()
    assert state.queue_size == 1
    assert state.flush_failures == 1
    assert state.requeue_dropped == 1
    assert chat_message_mod._drain_history_queue() == [records[0]]


def test_history_queue_state_tracks_enqueue_drops(monkeypatch):
    _reset_history_queue_stats()
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(SimpleNamespace(id=1))
    monkeypatch.setattr(chat_message_mod, "_HISTORY_QUEUE", queue)
    monkeypatch.setattr(chat_message_mod, "_LAST_DROP_LOG", 0.0)
    monkeypatch.setattr(chat_message_mod, "_DROP_LOG_INTERVAL", 0.0)
    monkeypatch.setattr(
        chat_message_mod.logger,
        "warning",
        lambda *_args, **_kwargs: None,
    )

    chat_message_mod._DROP_COUNT += 1
    state = chat_message_mod.get_history_queue_state()

    assert state.queue_size == 1
    assert state.queue_max_size == 1
    assert state.enqueue_dropped == 1


@pytest.mark.asyncio
async def test_chat_history_migration_sql_uses_database_specific_syntax(monkeypatch):
    async def _scripts_for(db_type: str) -> list[str]:
        monkeypatch.setattr(
            "zhenxun.models.chat_history.BotConfig",
            SimpleNamespace(get_sql_type=lambda: db_type),
        )
        return await ChatHistory._run_script()

    sqlite_scripts = await _scripts_for("sqlite")
    mysql_scripts = await _scripts_for("mysql")
    postgres_scripts = await _scripts_for("postgres")

    assert any("CREATE INDEX IF NOT EXISTS" in sql for sql in sqlite_scripts)
    assert not any("ALTER COLUMN" in sql for sql in sqlite_scripts)
    assert any(
        "MODIFY COLUMN group_id VARCHAR(255) NULL" in sql
        for sql in mysql_scripts
    )
    assert any("ALTER group_id DROP NOT NULL" in sql for sql in postgres_scripts)
    removed_columns = ("event_time", "reply_preview", "forward_meta", "record_version")
    assert not any(
        removed_column in sql
        for removed_column in removed_columns
        for sql in sqlite_scripts + mysql_scripts + postgres_scripts
    )


@pytest.mark.asyncio
async def test_call_hook_respects_chat_history_flag_for_outgoing_records(monkeypatch):
    from zhenxun.builtin_plugins.hooks import call_hook

    bot = SimpleNamespace(
        self_id="9000",
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
    )
    created_store: list[dict[str, object]] = []
    created_history: list[dict[str, object]] = []

    async def _create_store(**kwargs):
        created_store.append(kwargs)

    async def _create_history(**kwargs):
        created_history.append(kwargs)

    callback_globals = call_hook.handle_api_result.__globals__
    monkeypatch.setitem(callback_globals, "create_outgoing_record", _create_history)
    monkeypatch.setattr(callback_globals["BotMessageStore"], "create", _create_store)
    monkeypatch.setattr(
        callback_globals["PlatformUtils"],
        "get_platform",
        lambda _bot: "qq",
    )

    def _get_config(module, key, default=None, **_kwargs):
        if module == "hook" and key == "RECORD_BOT_SENT_MESSAGES":
            return True
        if module == "chat_history" and key == "FLAG":
            return False
        return default

    monkeypatch.setattr(callback_globals["Config"], "get_config", _get_config)

    await call_hook.handle_api_result(
        bot,
        None,
        "send_private_msg",
        {"user_id": 1000, "message": "private hello"},
        {"message_id": 2},
    )

    assert len(created_store) == 1
    assert created_history == []


@pytest.mark.asyncio
async def test_call_hook_history_failure_does_not_rollback_bot_store(monkeypatch):
    from zhenxun.builtin_plugins.hooks import call_hook

    bot = SimpleNamespace(
        self_id="9000",
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
    )
    created_store: list[dict[str, object]] = []
    warnings: list[str] = []

    async def _create_store(**kwargs):
        created_store.append(kwargs)

    async def _create_history(**_kwargs):
        raise RuntimeError("history db down")

    callback_globals = call_hook.handle_api_result.__globals__
    monkeypatch.setitem(callback_globals, "create_outgoing_record", _create_history)
    monkeypatch.setattr(callback_globals["BotMessageStore"], "create", _create_store)
    monkeypatch.setattr(
        callback_globals["PlatformUtils"],
        "get_platform",
        lambda _bot: "qq",
    )
    monkeypatch.setattr(
        callback_globals["Config"],
        "get_config",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        callback_globals["logger"],
        "warning",
        lambda message, *_args, **_kwargs: warnings.append(message),
    )

    await call_hook.handle_api_result(
        bot,
        None,
        "send_private_msg",
        {"user_id": 1000, "message": "private hello"},
        {"message_id": 2},
    )

    assert len(created_store) == 1
    assert warnings == ["记录ChatHistory出站消息失败"]


@pytest.mark.asyncio
async def test_call_hook_bot_store_failure_does_not_block_chat_history(monkeypatch):
    from zhenxun.builtin_plugins.hooks import call_hook

    bot = SimpleNamespace(
        self_id="9000",
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
    )
    history_calls: list[dict[str, object]] = []

    async def _create_store(**_kwargs):
        raise RuntimeError("legacy store down")

    async def _record_history(_bot, **kwargs):
        history_calls.append(kwargs)

    callback_globals = call_hook.handle_api_result.__globals__
    monkeypatch.setitem(
        callback_globals,
        "_record_outgoing_chat_history",
        _record_history,
    )
    monkeypatch.setattr(callback_globals["BotMessageStore"], "create", _create_store)
    monkeypatch.setattr(
        callback_globals["Config"],
        "get_config",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        callback_globals["logger"],
        "warning",
        lambda *_args, **_kwargs: None,
    )

    await call_hook.handle_api_result(
        bot,
        None,
        "send_private_msg",
        {"user_id": 1000, "message": "private hello"},
        {"message_id": 2},
    )

    assert len(history_calls) == 1


def test_build_outgoing_record_marks_bot_direction_message_id_and_reply(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _bot: "qq",
    )
    bot = SimpleNamespace(
        self_id="9000",
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
    )
    message = Message(
        [
            MessageSegment.reply(1234),
            MessageSegment.text("收到"),
            MessageSegment.image("pic.png"),
        ]
    )

    before = datetime.now().timestamp()
    record = build_outgoing_record(
        bot,
        user_id="1000",
        group_id="2000",
        message_type="group",
        message=message,
        result={"message_id": 4567},
    )
    after = datetime.now().timestamp()

    assert record.direction == "out"
    assert record.user_id == "1000"
    assert record.group_id == "2000"
    assert record.bot_id == "9000"
    assert record.message_id == "4567"
    assert before <= record.create_time.timestamp() <= after
    assert record.segment_types == ["reply", "text", "image"]
    assert record.reply_to_message_id == "1234"
    assert record.plain_text == "收到"
    assert record.text == "[引用消息]收到[图片]"
    assert "[CQ:" not in record.text
    assert not hasattr(record, "record_version")


def test_build_outgoing_record_prefers_alconna_conversion(monkeypatch):
    monkeypatch.setattr(
        "zhenxun.services.chat_history.recorder.PlatformUtils.get_platform",
        lambda _bot: "qq",
    )
    converted: list[object] = []

    def _convert(message, bot):
        converted.extend([message, bot])
        return UniMsg([At("user", "123"), Text("hello")])

    monkeypatch.setattr(recorder_mod.UniMessage, "of", _convert)
    bot = SimpleNamespace(self_id="9000")
    message = Message([MessageSegment.at(123), MessageSegment.text("hello")])

    record = build_outgoing_record(
        bot,
        user_id="1000",
        group_id="2000",
        message_type="group",
        message=message,
        result={"message_id": 4567},
    )

    assert converted == [message, bot]
    assert record.segments == [
        {"type": "mention", "data": {"target": "123", "kind": "user"}},
        {"type": "text", "data": {"text": "hello"}},
    ]
