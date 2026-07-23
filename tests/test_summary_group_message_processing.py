from __future__ import annotations

from datetime import datetime
from importlib import util as importlib_util
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest

PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1]
    / "zhenxun"
    / "plugins"
    / "zhenxun_plugin_summary_group"
)


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def trace(self, *_args, **_kwargs):
        return None


class _FakeRetry:
    @staticmethod
    def simple(*_args, **_kwargs):
        return lambda func: func

    @staticmethod
    def api(*_args, **_kwargs):
        return lambda func: func

    @staticmethod
    def download(*_args, **_kwargs):
        return lambda func: func


class _FakeBaseConfig:
    def __init__(self):
        self.values = {
            "MESSAGE_CACHE_TTL_SECONDS": 0,
            "SUMMARY_MAX_LENGTH": 3,
            "SUMMARY_DB_SUPPLEMENT_MAX_LENGTH": 5,
            "SUMMARY_MIN_LENGTH": 1,
            "EXCLUDE_BOT_MESSAGES": False,
        }

    def get(self, key: str, default=None):
        return self.values.get(key, default)


class _FakeSummaryConfig:
    def get_username_max_length(self) -> int:
        return 20

    def get_max_retries(self) -> int:
        return 1

    def get_retry_delay(self) -> int:
        return 0

    def get_user_info_max_retries(self) -> int:
        return 0

    def get_user_info_retry_delay(self) -> float:
        return 0

    def get_user_info_timeout(self) -> int:
        return 1

    def get_concurrent_user_fetch_limit(self) -> int:
        return 1

    def get_message_process_timeout(self) -> int:
        return 1

    def get_day_boundary(self) -> tuple[int, int]:
        return 6, 0


class _FakePlatformUtils:
    @staticmethod
    async def get_user(_bot, user_id: str, _group_id: str):
        return SimpleNamespace(card=None, name=f"user_{user_id}")


class _FakeQuery:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows
        self._order_by: tuple[str, ...] = ()
        self._limit: int | None = None

    def order_by(self, *fields: str):
        self._order_by = fields
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    async def count(self) -> int:
        raise AssertionError("DB time-range queries should not call count()")

    async def all(self):
        rows = list(self._rows)
        for field in reversed(self._order_by):
            reverse = field.startswith("-")
            attr_name = field[1:] if reverse else field
            rows.sort(key=lambda row: getattr(row, attr_name), reverse=reverse)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class _FakeChatHistory:
    rows: ClassVar[list[SimpleNamespace]] = []
    filters: ClassVar[list[dict]] = []

    @classmethod
    def filter(cls, **kwargs):
        cls.filters.append(kwargs)
        rows = list(cls.rows)

        group_id = kwargs.get("group_id")
        if group_id is not None:
            rows = [row for row in rows if str(row.group_id) == str(group_id)]

        start = kwargs.get("create_time__gte")
        if start is not None:
            rows = [row for row in rows if row.create_time >= start]

        end_lte = kwargs.get("create_time__lte")
        if end_lte is not None:
            rows = [row for row in rows if row.create_time <= end_lte]

        end_lt = kwargs.get("create_time__lt")
        if end_lt is not None:
            rows = [row for row in rows if row.create_time < end_lt]

        return _FakeQuery(rows)


class _FailingChatHistory:
    @classmethod
    def filter(cls, **_kwargs):
        raise RuntimeError("db down")


class _FakeChatHistoryQueryService:
    message_id_batch_calls: ClassVar[list[dict]] = []

    @staticmethod
    def _row_value(row, key: str, default=None):
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    @classmethod
    def _filter_rows(cls, **kwargs) -> list[SimpleNamespace]:
        _FakeChatHistory.filters.append(kwargs)
        rows = list(_FakeChatHistory.rows)
        group_id = kwargs.get("group_id")
        if group_id is not None:
            rows = [
                row
                for row in rows
                if str(cls._row_value(row, "group_id")) == str(group_id)
            ]
        direction = kwargs.get("direction")
        if direction != "all":
            rows = [
                row
                for row in rows
                if cls._row_value(row, "direction", "in") == direction
            ]
        start = kwargs.get("start")
        if start is not None:
            rows = [row for row in rows if cls._row_value(row, "create_time") >= start]
        end = kwargs.get("end")
        if end is not None:
            rows = [row for row in rows if cls._row_value(row, "create_time") <= end]
        return rows

    @staticmethod
    def _to_dict(row) -> dict:
        return dict(row) if isinstance(row, dict) else vars(row).copy()

    @classmethod
    async def structured_recent(cls, **kwargs) -> list[dict]:
        rows = cls._filter_rows(**kwargs)
        rows.sort(
            key=lambda row: (
                cls._row_value(row, "create_time"),
                cls._row_value(row, "id"),
            ),
            reverse=True,
        )
        return [cls._to_dict(row) for row in rows[: kwargs["limit"]]]

    @classmethod
    async def structured_range(cls, **kwargs) -> list[dict]:
        rows = cls._filter_rows(**kwargs)
        rows.sort(
            key=lambda row: (
                cls._row_value(row, "create_time"),
                cls._row_value(row, "id"),
            ),
            reverse=kwargs.get("descending", False),
        )
        return [cls._to_dict(row) for row in rows[: kwargs["limit"]]]

    @classmethod
    async def structured_by_message_ids(cls, message_ids, **kwargs) -> list[dict]:
        cls.message_id_batch_calls.append(
            {"message_ids": list(message_ids), **kwargs}
        )
        wanted = {str(message_id) for message_id in message_ids}
        rows = [
            row
            for row in cls._filter_rows(direction="all", **kwargs)
            if str(cls._row_value(row, "message_id")) in wanted
        ]
        rows.sort(
            key=lambda row: (
                cls._row_value(row, "create_time"),
                cls._row_value(row, "id"),
            ),
            reverse=True,
        )
        return [cls._to_dict(row) for row in rows]


class _FailingChatHistoryQueryService:
    @classmethod
    async def structured_recent(cls, **_kwargs):
        raise RuntimeError("db down")

    @classmethod
    async def structured_range(cls, **_kwargs):
        raise RuntimeError("db down")


class _FakeBot:
    self_id = "999"

    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.calls: list[tuple[int, int]] = []

    async def get_group_msg_history(self, group_id: int, count: int):
        self.calls.append((group_id, count))
        return {"messages": list(self.messages)}


class _FakeStatistics:
    records: ClassVar[list[dict]] = []

    @classmethod
    async def create(cls, **kwargs):
        cls.records.append(kwargs)


def _register_package(monkeypatch: pytest.MonkeyPatch, name: str, path: Path) -> None:
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    monkeypatch.setitem(sys.modules, name, package)


def _load_module(monkeypatch: pytest.MonkeyPatch, module_name: str, path: Path):
    spec = importlib_util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def summary_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    package_name = f"_summary_group_test_{id(monkeypatch)}"
    utils_name = f"{package_name}.utils"
    base_config = _FakeBaseConfig()
    summary_config = _FakeSummaryConfig()

    _register_package(monkeypatch, package_name, PLUGIN_ROOT)
    _register_package(monkeypatch, utils_name, PLUGIN_ROOT / "utils")
    sys.modules[package_name].base_config = base_config

    def validate_msg_count_range(count: int) -> int:
        min_len = int(base_config.get("SUMMARY_MIN_LENGTH"))
        max_len = int(base_config.get("SUMMARY_MAX_LENGTH"))
        if not (min_len <= count <= max_len):
            raise ValueError(f"总结消息数量应在 {min_len} 到 {max_len} 之间")
        return count

    sys.modules[package_name].validate_msg_count_range = validate_msg_count_range

    config_module = types.ModuleType(f"{package_name}.config")
    config_module.summary_config = summary_config
    monkeypatch.setitem(sys.modules, f"{package_name}.config", config_module)

    logger_module = types.ModuleType("zhenxun.services.log")
    logger_module.logger = _FakeLogger()
    services_module = types.ModuleType("zhenxun.services")
    monkeypatch.setitem(sys.modules, "zhenxun.services", services_module)
    monkeypatch.setitem(sys.modules, "zhenxun.services.log", logger_module)

    chat_history_service_module = types.ModuleType("zhenxun.services.chat_history")
    chat_history_service_module.ChatHistoryQuery = _FakeChatHistoryQueryService
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.services.chat_history",
        chat_history_service_module,
    )

    path_config_module = types.ModuleType("zhenxun.configs.path_config")
    path_config_module.TEMP_PATH = tmp_path
    configs_module = types.ModuleType("zhenxun.configs")
    monkeypatch.setitem(sys.modules, "zhenxun.configs", configs_module)
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.configs.path_config",
        path_config_module,
    )

    retry_module = types.ModuleType("zhenxun.utils.decorator.retry")
    retry_module.Retry = _FakeRetry
    platform_module = types.ModuleType("zhenxun.utils.platform")
    platform_module.PlatformUtils = _FakePlatformUtils
    utils_module = types.ModuleType("zhenxun.utils")
    decorator_module = types.ModuleType("zhenxun.utils.decorator")
    utils_utils_module = types.ModuleType("zhenxun.utils.utils")

    async def fake_get_user_avatar(_user_id: str):
        return None

    utils_utils_module.get_user_avatar = fake_get_user_avatar
    monkeypatch.setitem(sys.modules, "zhenxun.utils", utils_module)
    monkeypatch.setitem(sys.modules, "zhenxun.utils.decorator", decorator_module)
    monkeypatch.setitem(sys.modules, "zhenxun.utils.decorator.retry", retry_module)
    monkeypatch.setitem(sys.modules, "zhenxun.utils.platform", platform_module)
    monkeypatch.setitem(sys.modules, "zhenxun.utils.utils", utils_utils_module)

    chat_history_module = types.ModuleType("zhenxun.models.chat_history")
    chat_history_module.ChatHistory = _FakeChatHistory
    models_module = types.ModuleType("zhenxun.models")
    models_module.__path__ = []
    statistics_module = types.ModuleType("zhenxun.models.statistics")
    statistics_module.Statistics = _FakeStatistics
    monkeypatch.setitem(sys.modules, "zhenxun.models", models_module)
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.models.chat_history",
        chat_history_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.models.statistics",
        statistics_module,
    )

    core = _load_module(
        monkeypatch,
        f"{utils_name}.core",
        PLUGIN_ROOT / "utils" / "core.py",
    )
    scope = _load_module(
        monkeypatch,
        f"{utils_name}.scope",
        PLUGIN_ROOT / "utils" / "scope.py",
    )
    message_processing = _load_module(
        monkeypatch,
        f"{utils_name}.message_processing",
        PLUGIN_ROOT / "utils" / "message_processing.py",
    )
    message_processing._message_cache.clear()
    message_processing._reply_message_cache.clear()
    _FakeChatHistoryQueryService.message_id_batch_calls = []

    async def fake_process_message(messages, _bot, _group_id):
        processed = [
            message_processing.ProcessedMessage(
                user_id=str(message.get("user_id")),
                name=f"user_{message.get('user_id')}",
                timestamp=int(message["time"]),
                plain_content=str(message.get("raw_message") or ""),
                message_id=str(message.get("message_id")),
            )
            for message in messages
        ]
        return processed, {}

    monkeypatch.setattr(message_processing, "process_message", fake_process_message)
    return SimpleNamespace(
        base_config=base_config,
        core=core,
        message_processing=message_processing,
        package_name=package_name,
        scope=scope,
    )


def _dt(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai"))


def _raw_message(timestamp: int, user_id: int = 1, text: str | None = None) -> dict:
    body = text or f"msg-{timestamp}"
    return {
        "message_id": str(timestamp),
        "user_id": user_id,
        "time": timestamp,
        "message_type": "group",
        "message": [{"type": "text", "data": {"text": body}}],
        "raw_message": body,
        "sender": {"user_id": user_id},
    }


def _db_row(
    timestamp: int,
    user_id: str = "1",
    text: str | None = None,
    row_id: int | None = None,
    *,
    message_id: str | None = None,
    direction: str = "in",
    bot_id: str = "999",
    segments: list[dict] | None = None,
    plain_text: str | None = None,
    reply_to_message_id: str | None = None,
) -> SimpleNamespace:
    content = text or f"db-{timestamp}"
    return SimpleNamespace(
        id=row_id or timestamp,
        user_id=user_id,
        group_id="123",
        text=content,
        plain_text=content if plain_text is None else plain_text,
        create_time=_dt(timestamp),
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
        direction=direction,
        bot_id=bot_id,
        platform="qq",
        message_type="group",
        segments=segments,
        segment_types=[],
    )


def _scope(module, start_ts: int = 100, end_ts: int = 220):
    return module.SummaryScope(
        mode="time",
        label="测试范围",
        raw="test",
        start_ts=start_ts,
        end_ts=end_ts,
    )


def test_format_db_messages_uses_platform_identity_and_structured_segments(
    summary_modules,
):
    m = summary_modules.message_processing
    segments = [
        {"type": "mention", "data": {"target": "123"}},
        {"type": "text", "data": {"text": "看看"}},
        {"type": "image", "data": {"summary": "测试图片"}},
    ]
    row = vars(
        _db_row(
            100,
            user_id="1",
            message_id="platform-100",
            direction="out",
            bot_id="999",
            segments=segments,
        )
    )

    formatted = m._format_db_messages([row])

    assert formatted == [
        {
            "_db_id": 100,
            "message_id": "platform-100",
            "reply_to_message_id": None,
            "user_id": 999,
            "time": 100,
            "message_type": "group",
            "message": segments,
            "raw_message": "db-100",
            "sender": {"user_id": 999},
            "group_id": "123",
            "bot_id": "999",
            "platform": "qq",
            "_summary_source": "db",
        }
    ]


def test_format_db_messages_falls_back_without_segments_or_platform_id(
    summary_modules,
):
    m = summary_modules.message_processing
    row = vars(
        _db_row(
            100,
            text="legacy readable",
            plain_text="",
            message_id=None,
            segments=None,
        )
    )

    formatted = m._format_db_messages([row])

    assert formatted[0]["message_id"] is None
    assert formatted[0]["message"] == [
        {"type": "text", "data": {"text": "legacy readable"}}
    ]
    assert formatted[0]["raw_message"] == "legacy readable"


def test_summary_segment_rendering_supports_canonical_chat_history_types(
    summary_modules,
):
    m = summary_modules.message_processing
    user_cache = {"123": "Alice"}

    assert (
        m._segment_to_text(
            {"type": "mention", "data": {"target": "123"}}, user_cache
        )
        == "@Alice"
    )
    assert m._segment_to_text({"type": "audio", "data": {}}, user_cache) == "[voice]"
    assert m._segment_to_text({"type": "emoji", "data": {}}, user_cache) == "[emoji]"
    assert (
        m._segment_to_text({"type": "reference", "data": {}}, user_cache)
        == "[forward]"
    )
    assert m._segment_to_text({"type": "card", "data": {}}, user_cache) == "[card]"


def test_format_db_messages_synthesizes_reply_from_top_level_relation(summary_modules):
    m = summary_modules.message_processing
    row = vars(
        _db_row(
            100,
            message_id="current",
            reply_to_message_id="target",
            segments=[{"type": "text", "data": {"text": "继续"}}],
        )
    )

    formatted = m._format_db_messages([row])

    assert formatted[0]["message"][0] == {
        "type": "reply",
        "data": {"message_id": "target"},
    }
    assert formatted[0]["message"][1]["data"]["text"] == "继续"


def test_summary_renders_enriched_media_card_and_forward(summary_modules):
    m = summary_modules.message_processing
    user_cache = {"1": "Alice", "2": "Bob"}

    assert (
        m._segment_to_text(
            {"type": "sticker", "data": {"summary": "禁言"}}, user_cache
        )
        == "[sticker: 禁言]"
    )
    assert (
        m._segment_to_text(
            {
                "type": "video",
                "data": {"name": "video.mp4", "id": "BV1TEST_P1.mp4"},
            },
            user_cache,
        )
        == "[video: BV1TEST_P1.mp4]"
    )
    assert (
        m._segment_to_text(
            {"type": "file", "data": {"name": "report.zip"}}, user_cache
        )
        == "[file: report.zip]"
    )
    assert (
        m._segment_to_text(
            {
                "type": "card",
                "data": {
                    "source": "哔哩哔哩",
                    "title": "廉价的感情",
                    "prompt": "廉价的感情",
                    "description": "视频简介",
                },
            },
            user_cache,
        )
        == "[card: 哔哩哔哩 | 廉价的感情 | 视频简介]"
    )
    assert (
        m._segment_to_text(
            {
                "type": "reference",
                "data": {
                    "name": "测试聊天记录",
                    "nodes": [
                        {
                            "user_id": "1",
                            "name": "Alice",
                            "segments": [
                                {"type": "text", "data": {"text": "第一条"}}
                            ],
                        },
                        {
                            "user_id": "2",
                            "name": "Bob",
                            "segments": [{"type": "image", "data": {}}],
                        },
                    ],
                },
            },
            user_cache,
        )
        == "[forward: 测试聊天记录 | Alice(1): 第一条 | Bob(2): [img]]"
    )
    assert (
        m._segment_to_text(
            {"type": "unknown", "data": {"raw_type": "voice"}}, user_cache
        )
        == "[voice]"
    )


def test_sort_raw_messages_uses_db_id_and_keeps_api_order(summary_modules):
    m = summary_modules.message_processing
    db_messages = [
        {"time": 100, "message_id": "a", "_db_id": 2},
        {"time": 100, "message_id": "z", "_db_id": 1},
    ]
    api_messages = [
        {"time": 100, "message_id": "z"},
        {"time": 100, "message_id": "a"},
    ]

    assert [message["_db_id"] for message in m._sort_raw_messages(db_messages)] == [
        1,
        2,
    ]
    assert [
        message["message_id"] for message in m._sort_raw_messages(api_messages)
    ] == ["z", "a"]


def test_export_text_includes_seconds(summary_modules):
    m = summary_modules.message_processing
    timestamp = int(
        datetime(2026, 7, 17, 18, 48, 52, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    )
    message = m.ProcessedMessage(
        user_id="1",
        name="Alice",
        timestamp=timestamp,
        plain_content="hello",
    )

    exported = m.build_export_text(
        [message],
        123,
        _scope(summary_modules.scope),
        "db",
    )

    assert "[07-17 18:48:52] Alice(1): hello" in exported


@pytest.mark.asyncio
async def test_prefetch_reply_messages_reads_db_before_api(summary_modules):
    m = summary_modules.message_processing
    _FakeChatHistory.rows = [
        _db_row(
            90,
            user_id="2",
            message_id="target",
            segments=[{"type": "text", "data": {"text": "数据库引用"}}],
        )
    ]
    api_calls: list[str] = []

    async def _get_msg(**kwargs):
        api_calls.append(str(kwargs["message_id"]))
        return None

    bot = SimpleNamespace(self_id="999", get_msg=_get_msg)
    messages = [
        {
            "message": [
                {"type": "reply", "data": {"message_id": "target"}},
                {"type": "text", "data": {"text": "继续"}},
            ],
            "platform": "qq",
            "bot_id": "999",
            "group_id": "123",
        }
    ]

    replies = await m._prefetch_reply_messages(bot, messages)

    assert replies["target"]["message"] == [
        {"type": "text", "data": {"text": "数据库引用"}}
    ]
    assert api_calls == []


def test_summary_reply_accepts_canonical_message_id(summary_modules):
    m = summary_modules.message_processing

    content, reply = m._render_message_segments(
        {
            "message": [
                {"type": "reply", "data": {"message_id": "platform-99"}},
                {"type": "text", "data": {"text": "继续"}},
            ]
        },
        {},
        {},
    )

    assert content == "继续"
    assert reply is not None
    assert reply.message_id == "platform-99"


@pytest.mark.asyncio
async def test_db_history_query_direction_follows_bot_exclusion_config(summary_modules):
    m = summary_modules.message_processing
    _FakeChatHistory.rows = [_db_row(100)]
    _FakeChatHistory.filters = []

    await m._fetch_raw_messages_from_db(123, 10)

    assert _FakeChatHistory.filters[-1]["direction"] == "all"

    summary_modules.base_config.values["EXCLUDE_BOT_MESSAGES"] = True
    await m._fetch_raw_messages_from_db(123, 10)

    assert _FakeChatHistory.filters[-1]["direction"] == "in"


def _load_summary_handler(monkeypatch: pytest.MonkeyPatch, summary_modules):
    services_module_name = f"{summary_modules.package_name}.services"
    services_module = types.ModuleType(services_module_name)

    class FakeSummaryParameters:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeExportParameters:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeSummaryService:
        params: ClassVar[list[FakeSummaryParameters]] = []

        def __init__(self, params: FakeSummaryParameters):
            self.params = params
            self.__class__.params.append(params)

        async def execute(self):
            return True

    class FakeExportService:
        def __init__(self, params: FakeExportParameters):
            self.params = params

        async def execute(self):
            return True

    services_module.SummaryParameters = FakeSummaryParameters
    services_module.SummaryService = FakeSummaryService
    services_module.ExportParameters = FakeExportParameters
    services_module.ExportService = FakeExportService
    monkeypatch.setitem(sys.modules, services_module_name, services_module)

    handlers_name = f"{summary_modules.package_name}.handlers"
    _register_package(monkeypatch, handlers_name, PLUGIN_ROOT / "handlers")
    handler = _load_module(
        monkeypatch,
        f"{handlers_name}.summary",
        PLUGIN_ROOT / "handlers" / "summary.py",
    )
    return handler, FakeSummaryService


class _FakeArparma:
    def __init__(self, message: str):
        self.context = {"__styles__": {"msg": message}}
        self.main_args: dict = {}

    def query(self, _path: str):
        return None


class _FakeCommandResult:
    def __init__(self, message: str):
        self.result = _FakeArparma(message)


class _FakeEvent:
    group_id = 123
    message_id = 456

    def __init__(self, text: str):
        self._text = text

    def get_user_id(self):
        return "42"

    def get_plaintext(self):
        return self._text


class _FakeMatch:
    def __init__(self, result=None, available: bool = False):
        self.result = result
        self.available = available


async def _fixed_target_group_id(*_args):
    return 123


@pytest.mark.asyncio
async def test_summary_compact_invalid_scope_is_silent(summary_modules, monkeypatch):
    handler, _service = _load_summary_handler(monkeypatch, summary_modules)
    sent_messages: list[str] = []

    class FakeUniMessage:
        def __init__(self, text: str):
            self.text = text

        @classmethod
        def text(cls, text: str):
            return cls(text)

        async def send(self, *_args, **_kwargs):
            sent_messages.append(self.text)

    monkeypatch.setattr(handler, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(handler, "_resolve_target_group_id", _fixed_target_group_id)

    await handler.handle_summary(
        _FakeBot([]),
        _FakeEvent("总结测试"),
        _FakeCommandResult("总结测试"),
        "测试",
        _FakeMatch(),
        _FakeMatch([], available=True),
        object(),
    )

    assert sent_messages == []


@pytest.mark.asyncio
async def test_summary_spaced_invalid_scope_still_prompts(summary_modules, monkeypatch):
    handler, _service = _load_summary_handler(monkeypatch, summary_modules)
    sent_messages: list[str] = []

    class FakeUniMessage:
        def __init__(self, text: str):
            self.text = text

        @classmethod
        def text(cls, text: str):
            return cls(text)

        async def send(self, *_args, **_kwargs):
            sent_messages.append(self.text)

    monkeypatch.setattr(handler, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(handler, "_resolve_target_group_id", _fixed_target_group_id)

    await handler.handle_summary(
        _FakeBot([]),
        _FakeEvent("总结 测试"),
        _FakeCommandResult("总结 测试"),
        "测试",
        _FakeMatch(),
        _FakeMatch([], available=True),
        object(),
    )

    assert sent_messages == [
        "无法识别的范围，请使用数量、今日/昨日、2h、1h30m、2d 或 7:00~8:00 这类格式"
    ]


@pytest.mark.asyncio
async def test_summary_compact_valid_scope_still_executes(summary_modules, monkeypatch):
    handler, service = _load_summary_handler(monkeypatch, summary_modules)
    sent_messages: list[str] = []

    class FakeUniMessage:
        def __init__(self, text: str):
            self.text = text

        @classmethod
        def text(cls, text: str):
            return cls(text)

        async def send(self, *_args, **_kwargs):
            sent_messages.append(self.text)

    monkeypatch.setattr(handler, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(handler, "_resolve_target_group_id", _fixed_target_group_id)
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 1000

    await handler.handle_summary(
        _FakeBot([]),
        _FakeEvent("总结100"),
        _FakeCommandResult("总结100"),
        "100",
        _FakeMatch(),
        _FakeMatch([], available=True),
        object(),
    )

    assert sent_messages == ["正在生成群聊 123 的总结，请稍候..."]
    assert service.params[-1].scope.count == 100


@pytest.mark.asyncio
async def test_time_scope_complete_api_range_does_not_query_db(summary_modules):
    m = summary_modules.message_processing
    _FakeChatHistory.rows = [_db_row(80)]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(100), _raw_message(150), _raw_message(200)])

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))

    assert [message.timestamp for message in result.messages] == [100, 150, 200]
    assert result.source == "api"
    assert result.coverage_complete is True
    assert result.warning_message is None
    assert _FakeChatHistory.filters == []


@pytest.mark.asyncio
async def test_time_scope_supplements_missing_api_prefix_from_db(summary_modules):
    m = summary_modules.message_processing
    _FakeChatHistory.rows = [_db_row(100), _db_row(120), _db_row(140)]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(160), _raw_message(180), _raw_message(200)])

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))

    assert [message.timestamp for message in result.messages] == [
        100,
        120,
        140,
        160,
        180,
        200,
    ]
    assert result.source == "api+db"
    assert result.coverage_complete is True
    assert "已从数据库补充 3 条" in (result.warning_message or "")
    assert "纯文本" in (result.warning_message or "")


@pytest.mark.asyncio
async def test_time_scope_supplements_even_when_api_returns_less_than_requested(
    summary_modules,
):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 5
    _FakeChatHistory.rows = [_db_row(100), _db_row(120), _db_row(140)]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(160), _raw_message(180)])

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))

    assert [message.timestamp for message in result.messages] == [
        100,
        120,
        140,
        160,
        180,
    ]
    assert result.source == "api+db"
    assert result.coverage_complete is True


@pytest.mark.asyncio
async def test_time_scope_includes_boundary_second_and_deduplicates_api_overlap(
    summary_modules,
):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 2
    _FakeChatHistory.rows = [
        _db_row(100, text="old"),
        _db_row(160, text="api-boundary", row_id=1601),
        _db_row(160, text="db-boundary", row_id=1602),
    ]
    _FakeChatHistory.filters = []
    bot = _FakeBot(
        [
            _raw_message(160, text="api-boundary"),
            _raw_message(180, text="new"),
        ]
    )

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))
    contents = [message.plain_content for message in result.messages]

    assert contents.count("api-boundary") == 1
    assert "db-boundary" in contents
    assert contents == ["old", "db-boundary", "api-boundary", "new"]
    assert result.source == "api+db"


@pytest.mark.asyncio
async def test_time_scope_reports_db_supplement_limit_reached(summary_modules):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 2
    summary_modules.base_config.values["SUMMARY_DB_SUPPLEMENT_MAX_LENGTH"] = 2
    _FakeChatHistory.rows = [_db_row(100), _db_row(120), _db_row(140)]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(160), _raw_message(180)])

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))

    assert [message.timestamp for message in result.messages] == [120, 140, 160, 180]
    assert result.source == "api+db"
    assert result.coverage_complete is False
    assert "已达到数据库补全上限" in (result.warning_message or "")
    assert "仍可能有更多较早记录未纳入" in (result.warning_message or "")


@pytest.mark.asyncio
async def test_time_scope_can_disable_api_db_supplement(summary_modules):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 2
    summary_modules.base_config.values["SUMMARY_DB_SUPPLEMENT_MAX_LENGTH"] = 0
    _FakeChatHistory.rows = [_db_row(100), _db_row(120), _db_row(140)]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(160), _raw_message(180)])

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))

    assert [message.timestamp for message in result.messages] == [160, 180]
    assert result.source == "api"
    assert result.coverage_complete is False
    assert "仅部分覆盖" in (result.warning_message or "")
    assert _FakeChatHistory.filters == []


@pytest.mark.asyncio
async def test_time_scope_keeps_api_partial_result_when_db_supplement_fails(
    summary_modules,
    monkeypatch: pytest.MonkeyPatch,
):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 2
    monkeypatch.setattr(m, "ChatHistoryQuery", _FailingChatHistoryQueryService)
    bot = _FakeBot([_raw_message(160), _raw_message(180)])

    result = await m.get_group_messages(bot, 123, _scope(summary_modules.scope))

    assert [message.timestamp for message in result.messages] == [160, 180]
    assert result.source == "api"
    assert result.coverage_complete is False
    assert "仅部分覆盖" in (result.warning_message or "")


@pytest.mark.asyncio
async def test_use_db_time_scope_queries_by_time_range(summary_modules):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 2
    summary_modules.base_config.values["SUMMARY_DB_SUPPLEMENT_MAX_LENGTH"] = 2
    _FakeChatHistory.rows = [
        _db_row(90),
        _db_row(100),
        _db_row(120),
        _db_row(140),
        _db_row(240),
    ]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(999)])

    result = await m.get_group_messages(
        bot,
        123,
        _scope(summary_modules.scope, start_ts=100, end_ts=220),
        use_db=True,
    )

    assert bot.calls == []
    assert [message.timestamp for message in result.messages] == [100, 120, 140]
    assert result.source == "db"
    assert result.coverage_complete is True
    assert result.warning_message is None
    assert _FakeChatHistory.filters[0]["start"] == _dt(100)
    assert _FakeChatHistory.filters[0]["end"] == _dt(220)


@pytest.mark.asyncio
async def test_use_db_time_scope_warns_when_total_limit_reached(summary_modules):
    m = summary_modules.message_processing
    summary_modules.base_config.values["SUMMARY_MAX_LENGTH"] = 2
    summary_modules.base_config.values["SUMMARY_DB_SUPPLEMENT_MAX_LENGTH"] = 1
    _FakeChatHistory.rows = [
        _db_row(100),
        _db_row(120),
        _db_row(140),
        _db_row(160),
    ]
    _FakeChatHistory.filters = []
    bot = _FakeBot([_raw_message(999)])

    result = await m.get_group_messages(
        bot,
        123,
        _scope(summary_modules.scope, start_ts=100, end_ts=220),
        use_db=True,
    )

    assert bot.calls == []
    assert [message.timestamp for message in result.messages] == [120, 140, 160]
    assert result.source == "db"
    assert result.coverage_complete is False
    assert "已达到时间范围读取上限" in (result.warning_message or "")
    assert "存储时间近似匹配" in (result.warning_message or "")
