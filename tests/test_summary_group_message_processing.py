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
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id or timestamp,
        user_id=user_id,
        group_id="123",
        plain_text=text or f"db-{timestamp}",
        create_time=_dt(timestamp),
    )


def _scope(module, start_ts: int = 100, end_ts: int = 220):
    return module.SummaryScope(
        mode="time",
        label="测试范围",
        raw="test",
        start_ts=start_ts,
        end_ts=end_ts,
    )


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
    assert contents == ["old", "api-boundary", "db-boundary", "new"]
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
    monkeypatch.setattr(m, "ChatHistory", _FailingChatHistory)
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
    assert _FakeChatHistory.filters[0]["create_time__gte"] == _dt(100)
    assert _FakeChatHistory.filters[0]["create_time__lte"] == _dt(220)


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
