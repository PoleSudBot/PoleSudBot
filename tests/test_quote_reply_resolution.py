from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()
nonebot.load_plugin("nonebot_plugin_apscheduler")
nonebot.load_plugin("nonebot_plugin_waiter")
nonebot.load_plugin("nonebot_plugin_alconna")
nonebot.load_plugin("nonebot_plugin_session")
nonebot.load_plugin("nonebot_plugin_htmlrender")
nonebot.load_plugin("nonebot_plugin_uninfo")

PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1] / "zhenxun" / "plugins" / "zhenxun_plugin_quote"
)
PLUGIN_PACKAGE = "zhenxun.plugins.zhenxun_plugin_quote"


def _register_namespace_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _load_module(module_name: str, path: Path):
    spec = importlib_util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_register_namespace_package(PLUGIN_PACKAGE, PLUGIN_ROOT)
_register_namespace_package(f"{PLUGIN_PACKAGE}.command", PLUGIN_ROOT / "command")
_register_namespace_package(f"{PLUGIN_PACKAGE}.services", PLUGIN_ROOT / "services")
_register_namespace_package(f"{PLUGIN_PACKAGE}.utils", PLUGIN_ROOT / "utils")

quote_service_module = _load_module(
    f"{PLUGIN_PACKAGE}.services.quote_service",
    PLUGIN_ROOT / "services" / "quote_service.py",
)
manage_commands = _load_module(
    f"{PLUGIN_PACKAGE}.command.manage_commands",
    PLUGIN_ROOT / "command" / "manage_commands.py",
)
query_commands = _load_module(
    f"{PLUGIN_PACKAGE}.command.query_commands",
    PLUGIN_ROOT / "command" / "query_commands.py",
)
upload_commands = _load_module(
    f"{PLUGIN_PACKAGE}.command.upload_commands",
    PLUGIN_ROOT / "command" / "upload_commands.py",
)

QuoteService = quote_service_module.QuoteService
Image = manage_commands.Image


class _FakeQuery:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows
        self._limit: int | None = None

    def limit(self, count: int):
        self._limit = count
        return self

    def __await__(self):
        async def _resolve():
            rows = list(self._rows)
            if self._limit is not None:
                rows = rows[: self._limit]
            return rows

        return _resolve().__await__()


def _build_filter(quotes: list[SimpleNamespace]):
    def _filter(**kwargs):
        group_id = kwargs.get("group_id")
        image_path_iendswith = kwargs.get("image_path__iendswith")
        image_path_icontains = kwargs.get("image_path__icontains")

        results = [quote for quote in quotes if quote.group_id == group_id]
        if image_path_iendswith is not None:
            suffix = str(image_path_iendswith).lower()
            results = [
                quote
                for quote in results
                if str(quote.image_path).lower().endswith(suffix)
            ]
        if image_path_icontains is not None:
            keyword = str(image_path_icontains).lower()
            results = [
                quote for quote in results if keyword in str(quote.image_path).lower()
            ]
        return _FakeQuery(results)

    return _filter


def test_extract_reply_image_identifiers_prefers_id_over_url_query_hex():
    image_md5 = "6d2c0bdf1c3c9731b4e6e3364e1bd53d"
    image = Image(
        id=f"{image_md5.upper()}.jpg",
        url=f"https://example.com/download/ignored.png?token={'a' * 32}",
    )

    extracted_md5, extracted_basename = (
        manage_commands._extract_reply_image_identifiers(image)
    )

    assert extracted_md5 == image_md5
    assert extracted_basename == f"{image_md5.upper()}.jpg"


def test_extract_reply_image_identifiers_uses_url_path_and_ignores_query_hex():
    image_md5 = "6d2c0bdf1c3c9731b4e6e3364e1bd53d"
    image = Image(
        url=(
            f"https://example.com/download/{image_md5.upper()}.jpg"
            f"?fileid={'b' * 32}&rkey={'c' * 32}"
        )
    )

    extracted_md5, extracted_basename = (
        manage_commands._extract_reply_image_identifiers(image)
    )

    assert extracted_md5 == image_md5
    assert extracted_basename == f"{image_md5.upper()}.jpg"


@pytest.mark.parametrize(
    "filename",
    [
        "prefix_6d2c0bdf1c3c9731b4e6e3364e1bd53d.jpg",
        "6d2c0bdf1c3c9731b4e6e3364e1bd53d_suffix.jpg",
    ],
)
def test_extract_reply_image_identifiers_rejects_non_plain_md5_stem(filename: str):
    extracted_md5, extracted_basename = (
        manage_commands._extract_reply_image_identifiers(Image(id=filename))
    )

    assert extracted_md5 is None
    assert extracted_basename == filename


@pytest.mark.asyncio
async def test_find_quote_by_reply_image_matches_exact_png_candidate(monkeypatch):
    image_md5 = "6d2c0bdf1c3c9731b4e6e3364e1bd53d"
    quotes = [
        SimpleNamespace(
            id=1,
            group_id="123",
            image_path=f"quote/images/{image_md5}.png",
        )
    ]

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter(quotes))

    result = await QuoteService.find_quote_by_reply_image(
        "123", reply_image_md5=image_md5
    )

    assert result is not None
    assert result.id == 1


@pytest.mark.asyncio
async def test_find_quote_by_reply_image_uses_icontains_with_strict_stem(monkeypatch):
    image_md5 = "6d2c0bdf1c3c9731b4e6e3364e1bd53d"
    quotes = [
        SimpleNamespace(
            id=1,
            group_id="123",
            image_path=f"quote/images/{image_md5}.jpg",
        )
    ]

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter(quotes))

    result = await QuoteService.find_quote_by_reply_image(
        "123", reply_image_md5=image_md5
    )

    assert result is not None
    assert result.id == 1


@pytest.mark.asyncio
async def test_find_quote_by_reply_image_rejects_icontains_without_stem_match(
    monkeypatch: pytest.MonkeyPatch,
):
    image_md5 = "6d2c0bdf1c3c9731b4e6e3364e1bd53d"
    quotes = [
        SimpleNamespace(
            id=1,
            group_id="123",
            image_path=f"quote/images/prefix_{image_md5}.png",
        )
    ]

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter(quotes))

    result = await QuoteService.find_quote_by_reply_image(
        "123", reply_image_md5=image_md5
    )

    assert result is None


@pytest.mark.asyncio
async def test_find_quote_by_reply_image_falls_back_to_basename(monkeypatch):
    expected_quote = SimpleNamespace(
        id=99,
        group_id="123",
        image_path="quote/images/demo.png",
    )

    async def _fake_find_quote_by_basename(group_id: str, image_basename: str):
        assert group_id == "123"
        assert image_basename == "demo.jpg"
        return expected_quote

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter([]))
    monkeypatch.setattr(
        QuoteService, "find_quote_by_basename", _fake_find_quote_by_basename
    )

    result = await QuoteService.find_quote_by_reply_image(
        "123",
        reply_image_basename="demo.jpg",
    )

    assert result is expected_quote


def test_compact_quote_shortcut_matches_regular_queries_only():
    matched_cases = {
        "语录777": ("777",),
        "语录统计学": ("统计学",),
        "语录管理学 test": ("管理学", "test"),
    }
    for text, expected in matched_cases.items():
        result = query_commands.quote_alc.parse(text)
        assert result.matched is True
        assert result.all_matched_args["search_keywords"] == expected

    for text in ("语录统计", "语录统计 热门", "语录主题", "语录管理"):
        assert query_commands.quote_alc.parse(text).matched is False


def test_make_record_alc_supports_compact_no_space_input():
    result = upload_commands.make_record_alc.parse("记录aaa bbb")

    assert result.matched is True
    assert tuple(part.text for part in result.all_matched_args["parts"]) == (
        "aaa",
        "bbb",
    )


def test_delete_quote_reply_command_has_high_priority():
    assert manage_commands.delete_quote_reply_cmd.priority == 0


@pytest.mark.asyncio
async def test_match_reply_quote_delete_matches_exact_delete_and_caches_quote(
    monkeypatch: pytest.MonkeyPatch,
):
    quote = SimpleNamespace(id=7)

    class _FakeMessageEvent:
        def __init__(self, text: str):
            self._text = text

        def get_plaintext(self):
            return self._text

    async def _fake_get_quote_from_reply(bot, event, session):
        return quote

    monkeypatch.setattr(manage_commands, "MessageEvent", _FakeMessageEvent)
    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)

    state = {}
    matched = await manage_commands._match_reply_quote_delete(
        SimpleNamespace(),
        _FakeMessageEvent("删除"),
        SimpleNamespace(group=SimpleNamespace(id="123")),
        state,
    )

    assert matched is True
    assert state["reply_quote"] is quote


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["删除语录", "删除 test", " test 删除 "])
async def test_match_reply_quote_delete_rejects_non_exact_delete_without_lookup(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
):
    calls = 0

    class _FakeMessageEvent:
        def __init__(self, plain_text: str):
            self._plain_text = plain_text

        def get_plaintext(self):
            return self._plain_text

    async def _fake_get_quote_from_reply(bot, event, session):
        nonlocal calls
        calls += 1
        return SimpleNamespace(id=1)

    monkeypatch.setattr(manage_commands, "MessageEvent", _FakeMessageEvent)
    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)

    state = {}
    matched = await manage_commands._match_reply_quote_delete(
        SimpleNamespace(),
        _FakeMessageEvent(text),
        SimpleNamespace(group=SimpleNamespace(id="123")),
        state,
    )

    assert matched is False
    assert calls == 0
    assert "reply_quote" not in state


@pytest.mark.asyncio
async def test_match_reply_quote_delete_rejects_when_reply_is_not_quote(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeMessageEvent:
        def get_plaintext(self):
            return "删除"

    async def _fake_get_quote_from_reply(bot, event, session):
        return None

    monkeypatch.setattr(manage_commands, "MessageEvent", _FakeMessageEvent)
    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)

    state = {}
    matched = await manage_commands._match_reply_quote_delete(
        SimpleNamespace(),
        _FakeMessageEvent(),
        SimpleNamespace(group=SimpleNamespace(id="123")),
        state,
    )

    assert matched is False
    assert "reply_quote" not in state


@pytest.mark.asyncio
async def test_set_pending_emoji_like_calls_napcat_api(monkeypatch):
    monkeypatch.setattr(upload_commands.Config, "get_config", lambda *args, **kwargs: "10024")

    called: list[tuple[str, dict]] = []

    class _Bot:
        async def call_api(self, name: str, **kwargs):
            called.append((name, kwargs))

    event = SimpleNamespace(message_id=123456)

    await upload_commands._set_pending_emoji_like(_Bot(), event)

    assert called == [
        ("set_msg_emoji_like", {"message_id": 123456, "emoji_id": "10024"})
    ]


@pytest.mark.asyncio
async def test_set_pending_emoji_like_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(upload_commands.Config, "get_config", lambda *args, **kwargs: "")

    called = False

    class _Bot:
        async def call_api(self, name: str, **kwargs):
            nonlocal called
            called = True

    await upload_commands._set_pending_emoji_like(_Bot(), SimpleNamespace(message_id=1))

    assert called is False


@pytest.mark.asyncio
async def test_set_pending_emoji_like_ignores_api_failure(monkeypatch):
    monkeypatch.setattr(upload_commands.Config, "get_config", lambda *args, **kwargs: "10024")

    class _Bot:
        async def call_api(self, name: str, **kwargs):
            raise RuntimeError("unsupported")

    await upload_commands._set_pending_emoji_like(_Bot(), SimpleNamespace(message_id=1))


@pytest.mark.asyncio
async def test_delete_quote_standalone_prefers_reply_quote(monkeypatch):
    reply_quote = SimpleNamespace(id=42)
    calls = {"reply": 0, "last": 0}

    async def _fake_get_quote_from_reply(bot, event, session):
        return reply_quote

    async def _fake_handle_delete_reply_quote(bot, event, session, quote=None):
        calls["reply"] += 1
        assert quote is reply_quote

    async def _fake_handle_delete_last_quote(bot, event, session):
        calls["last"] += 1

    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)
    monkeypatch.setattr(
        manage_commands, "_handle_delete_reply_quote", _fake_handle_delete_reply_quote
    )
    monkeypatch.setattr(
        manage_commands, "_handle_delete_last_quote", _fake_handle_delete_last_quote
    )

    session = SimpleNamespace(group=SimpleNamespace(id="123"))

    await manage_commands.handle_delete_quote_standalone(
        SimpleNamespace(), SimpleNamespace(), session
    )

    assert calls == {"reply": 1, "last": 0}


@pytest.mark.asyncio
async def test_delete_quote_standalone_falls_back_to_last_quote(monkeypatch):
    calls = {"reply": 0, "last": 0}

    async def _fake_get_quote_from_reply(bot, event, session):
        return None

    async def _fake_handle_delete_reply_quote(bot, event, session, quote=None):
        calls["reply"] += 1

    async def _fake_handle_delete_last_quote(bot, event, session):
        calls["last"] += 1

    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)
    monkeypatch.setattr(
        manage_commands, "_handle_delete_reply_quote", _fake_handle_delete_reply_quote
    )
    monkeypatch.setattr(
        manage_commands, "_handle_delete_last_quote", _fake_handle_delete_last_quote
    )

    session = SimpleNamespace(group=SimpleNamespace(id="123"))

    await manage_commands.handle_delete_quote_standalone(
        SimpleNamespace(), SimpleNamespace(), session
    )

    assert calls == {"reply": 0, "last": 1}


@pytest.mark.asyncio
async def test_handle_delete_reply_quote_reuses_cached_state_quote(
    monkeypatch: pytest.MonkeyPatch,
):
    cached_quote = SimpleNamespace(id=9, uploader_user_id="456")
    lookup_calls = 0
    deleted_quotes: list[SimpleNamespace] = []
    sent_messages: list[object] = []

    async def _fake_get_quote_from_reply(bot, event, session):
        nonlocal lookup_calls
        lookup_calls += 1
        return None

    async def _fake_uploader_or_admin_check(bot, event, session, quote=None):
        return quote is cached_quote

    async def _fake_delete_quote_instance(quote):
        deleted_quotes.append(quote)
        return True

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)
    monkeypatch.setattr(
        manage_commands, "uploader_or_admin_check", _fake_uploader_or_admin_check
    )
    monkeypatch.setattr(
        manage_commands.QuoteService, "delete_quote_instance", _fake_delete_quote_instance
    )
    monkeypatch.setattr(
        manage_commands.MessageUtils, "build_message", lambda payload: _FakeMessage(payload)
    )

    session = SimpleNamespace(
        group=SimpleNamespace(id="123"),
        user=SimpleNamespace(id="456"),
    )

    await manage_commands._handle_delete_reply_quote(
        SimpleNamespace(),
        SimpleNamespace(),
        session,
        state={"reply_quote": cached_quote},
    )

    assert lookup_calls == 0
    assert deleted_quotes == [cached_quote]
    assert sent_messages
