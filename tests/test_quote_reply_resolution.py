from __future__ import annotations

import asyncio
from importlib import util as importlib_util
import json
import os
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import httpx
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
tag_utils = _load_module(
    f"{PLUGIN_PACKAGE}.utils.tag_utils",
    PLUGIN_ROOT / "utils" / "tag_utils.py",
)
config_module = sys.modules[f"{PLUGIN_PACKAGE}.config"]
text_recognition_module = sys.modules[
    f"{PLUGIN_PACKAGE}.services.text_recognition"
]
paddleocr_api_module = sys.modules[f"{PLUGIN_PACKAGE}.services.paddleocr_api"]

QuoteService = quote_service_module.QuoteService
Image = manage_commands.Image
At = upload_commands.At
Text = upload_commands.Text


class _FakeQuery:
    def __init__(
        self,
        rows: list[SimpleNamespace],
        *,
        update_calls: list[dict] | None = None,
    ):
        self._rows = rows
        self._limit: int | None = None
        self._offset = 0
        self._order_by: tuple[str, ...] = ()
        self._values_field: str | None = None
        self._values_flat = False
        self._update_calls = update_calls

    def limit(self, count: int):
        self._limit = count
        return self

    def offset(self, count: int):
        self._offset = count
        return self

    def order_by(self, *fields: str):
        self._order_by = fields
        return self

    def values_list(self, field: str, flat: bool = False):
        self._values_field = field
        self._values_flat = flat
        return self

    async def first(self):
        rows = self._resolve_rows()
        return rows[0] if rows else None

    async def update(self, **kwargs):
        if self._update_calls is not None:
            self._update_calls.append(kwargs)
        return len(self._rows)

    def _resolve_rows(self):
        rows = list(self._rows)
        for field in reversed(self._order_by):
            reverse = field.startswith("-")
            attr_name = field[1:] if reverse else field
            rows.sort(key=lambda row: getattr(row, attr_name), reverse=reverse)
        if self._offset:
            rows = rows[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def __await__(self):
        async def _resolve():
            rows = self._resolve_rows()
            if self._values_field is not None:
                if self._values_flat:
                    return [getattr(row, self._values_field) for row in rows]
                return [(getattr(row, self._values_field),) for row in rows]
            return rows

        return _resolve().__await__()


class _FakeExistsQuery:
    def __init__(self, exists_result: bool):
        self._exists_result = exists_result

    async def exists(self):
        return self._exists_result


def _build_filter(
    quotes: list[SimpleNamespace], *, update_calls: list[dict] | None = None
):
    def _filter(**kwargs):
        group_id = kwargs.get("group_id")
        ids = kwargs.get("id__in")
        image_path_iendswith = kwargs.get("image_path__iendswith")
        image_path_icontains = kwargs.get("image_path__icontains")

        results = list(quotes)
        if group_id is not None:
            results = [quote for quote in results if quote.group_id == group_id]
        if ids is not None:
            id_set = set(ids)
            results = [quote for quote in results if quote.id in id_set]
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
        return _FakeQuery(results, update_calls=update_calls)

    return _filter


def _patch_upload_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """将上传与记录测试的所有图片路径统一隔离到 pytest 临时目录。"""
    monkeypatch.setattr(upload_commands, "ensure_quote_path", lambda: tmp_path)

    def _get_group_path(group_id: str) -> Path:
        group_path = tmp_path / str(group_id)
        group_path.mkdir(parents=True, exist_ok=True)
        return group_path

    monkeypatch.setattr(upload_commands, "get_quote_group_path", _get_group_path)


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


@pytest.mark.asyncio
async def test_search_quotes_exact_stage_merges_manual_tags_with_text_matches(
    monkeypatch: pytest.MonkeyPatch,
):
    quotes = [
        SimpleNamespace(
            id=77,
            group_id="123",
            image_path="quote/images/auto.png",
            ocr_text="1969 live",
            recorded_text=None,
            tags=["1969"],
            manual_tags=[],
            quoted_user_id=None,
        ),
        SimpleNamespace(
            id=116,
            group_id="123",
            image_path="quote/images/manual.png",
            ocr_text=None,
            recorded_text=None,
            tags=[],
            manual_tags=["1969"],
            quoted_user_id=None,
        ),
    ]

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter(quotes))

    matches = await QuoteService._search_quotes_by_text_and_filter_by_tags(
        "123", "1969"
    )

    assert {quote.id for quote in matches} == {77, 116}


def test_compact_quote_shortcut_matches_regular_queries_only():
    matched_cases = {
        "语录777": ("777",),
        "语录统计学": ("统计学",),
        "语录管理学 test": ("管理学", "test"),
        "语录xxx-3": ("xxx-3",),
        "语录1969*3": ("1969*3",),
    }
    for text, expected in matched_cases.items():
        result = query_commands.quote_alc.parse(text)
        assert result.matched is True
        assert result.all_matched_args["search_keywords"] == expected

    for text in ("语录统计", "语录统计 热门", "语录主题", "语录管理"):
        assert query_commands.quote_alc.parse(text).matched is False


def test_quote_query_num_option_parses():
    arp = query_commands.quote_alc.parse("语录 -n 3 1969")

    assert arp.matched is True
    assert arp.query("num.count") == 3
    assert arp.all_matched_args["search_keywords"] == ("1969",)


@pytest.mark.parametrize(
    ("text", "expected_keywords"),
    [
        ("语录 x5", ("x5",)),
        ("语录 *5", ("*5",)),
        ("语录 1969 x3", ("1969", "x3")),
        ("语录 1969 *3", ("1969", "*3")),
        ("语录 xxx -3", ("xxx", "-3")),
    ],
)
def test_quote_query_new_forms_parse_as_keywords(
    text: str, expected_keywords: tuple[str, ...]
):
    arp = query_commands.quote_alc.parse(text)

    assert arp.matched is True
    assert arp.all_matched_args["search_keywords"] == expected_keywords


@pytest.mark.parametrize(
    ("keywords", "option_count", "expected"),
    [
        (["3连"], None, ("", 3)),
        (["五连"], None, ("", 5)),
        (["x5"], None, ("", 5)),
        (["*5"], None, ("", 5)),
        (["1969", "五连"], None, ("1969", 5)),
        (["1969", "3连"], None, ("1969", 3)),
        (["1969", "x3"], None, ("1969", 3)),
        (["1969", "*3"], None, ("1969", 3)),
        (["1969五连"], None, ("1969", 5)),
        (["1969三连"], None, ("1969", 3)),
        (["1969x3"], None, ("1969", 3)),
        (["1969*3"], None, ("1969", 3)),
        (["X10"], None, ("", 10)),
        (["xxxx3"], None, ("xxxx3", 1)),
        (["五连发"], None, ("五连发", 1)),
        (["10连续查询"], None, ("10连续查询", 1)),
        (["五连"], 1, ("五连", 1)),
        (["*5"], 1, ("*5", 1)),
    ],
)
def test_extract_quote_query_and_count_cases(
    keywords: list[str],
    option_count: int | None,
    expected: tuple[str, int],
):
    assert (
        query_commands._extract_quote_query_and_count(keywords, option_count) == expected
    )


@pytest.mark.parametrize(
    ("keywords", "option_count", "expected_keyword", "expected_index"),
    [
        (["-1"], None, "", 1),
        (["xxx", "-3"], None, "xxx", 3),
        (["xxx-3"], None, "xxx", 3),
        (["南极", "-5"], None, "南极", 5),
        (["xxx", "-3"], 1, "xxx -3", None),
    ],
)
def test_extract_quote_query_params_history_index_cases(
    keywords: list[str],
    option_count: int | None,
    expected_keyword: str,
    expected_index: int | None,
):
    params = query_commands._extract_quote_query_params(keywords, option_count)

    assert params.keyword == expected_keyword
    assert params.history_index == expected_index


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image_path",
    [
        "quote/images/missing.png",
        "../../outside.png",
        "quote/images/io-error.png",
    ],
)
async def test_collect_valid_quotes_skips_missing_without_deleting_records(
    monkeypatch: pytest.MonkeyPatch,
    image_path: str,
):
    quote = SimpleNamespace(id=31, group_id="123", image_path=image_path)

    monkeypatch.setattr(
        query_commands.QuoteService,
        "select_quotes_without_record",
        lambda memory_key, candidates, count: list(candidates)[:count],
    )
    monkeypatch.setattr(query_commands, "safe_file_exists", lambda path: False)

    class _DeleteGuard:
        async def delete(self):
            raise AssertionError("查询链路禁止删除数据库记录")

    monkeypatch.setattr(
        query_commands.Quote,
        "filter",
        lambda **kwargs: _DeleteGuard(),
    )

    quotes = await query_commands._collect_valid_quotes_from_candidates(
        "123_all",
        [quote],
        1,
    )

    assert quotes == []


@pytest.mark.asyncio
async def test_history_quote_missing_file_does_not_delete_record(
    monkeypatch: pytest.MonkeyPatch,
):
    quote = SimpleNamespace(
        id=32,
        group_id="123",
        image_path="quote/images/missing.png",
    )
    sent_messages: list[object] = []

    async def _fake_get_history(*args, **kwargs):
        return quote

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    class _DeleteGuard:
        async def delete(self):
            raise AssertionError("倒序查询禁止删除数据库记录")

    monkeypatch.setattr(
        query_commands.QuoteService,
        "get_quote_by_history_index",
        _fake_get_history,
    )
    monkeypatch.setattr(query_commands, "safe_file_exists", lambda path: False)
    monkeypatch.setattr(
        query_commands.MessageUtils,
        "build_message",
        lambda payload: _FakeMessage(payload),
    )
    monkeypatch.setattr(
        query_commands.Quote,
        "filter",
        lambda **kwargs: _DeleteGuard(),
    )

    await query_commands._send_history_quote(
        SimpleNamespace(),
        SimpleNamespace(),
        "123",
        "",
        1,
    )

    assert sent_messages == ["这条语录图片暂时不可用，请联系管理员检查。"]


@pytest.mark.asyncio
async def test_empty_library_message_reports_unavailable_images_when_records_exist(
    monkeypatch: pytest.MonkeyPatch,
):
    class _ExistsQuery:
        async def exists(self):
            return True

    monkeypatch.setattr(
        query_commands.Quote,
        "filter",
        lambda **kwargs: _ExistsQuery(),
    )

    message = await query_commands._get_empty_quote_library_message("123")

    assert message == "语录记录存在，但图片暂时不可用，请联系管理员检查。"


@pytest.mark.asyncio
async def test_empty_library_message_reports_empty_database(
    monkeypatch: pytest.MonkeyPatch,
):
    class _ExistsQuery:
        async def exists(self):
            return False

    monkeypatch.setattr(
        query_commands.Quote,
        "filter",
        lambda **kwargs: _ExistsQuery(),
    )

    message = await query_commands._get_empty_quote_library_message("123")

    assert message == "当前无语录库"


@pytest.mark.asyncio
async def test_random_quote_lookup_reports_matching_but_unavailable_candidates(
    monkeypatch: pytest.MonkeyPatch,
):
    quote = SimpleNamespace(id=33, group_id="123", image_path="missing.png")
    monkeypatch.setattr(
        query_commands.Quote,
        "filter",
        _build_filter([quote]),
    )
    monkeypatch.setattr(
        query_commands.QuoteService,
        "_match_user_filter",
        lambda candidate, user_id: True,
    )
    monkeypatch.setattr(
        query_commands.QuoteService,
        "select_quotes_without_record",
        lambda memory_key, candidates, count: list(candidates)[:count],
    )
    monkeypatch.setattr(query_commands, "safe_file_exists", lambda path: False)

    quotes, memory_key, has_candidates = (
        await query_commands._get_valid_random_quotes("123", 1, "42")
    )

    assert quotes == []
    assert memory_key == "123_42"
    assert has_candidates is True


@pytest.mark.asyncio
async def test_send_quote_batch_rechecks_files_before_sending(
    monkeypatch: pytest.MonkeyPatch,
):
    quote = SimpleNamespace(id=34, group_id="123", image_path="missing.png")
    sent_messages: list[object] = []

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(query_commands, "safe_file_exists", lambda path: False)
    monkeypatch.setattr(
        query_commands.MessageUtils,
        "build_message",
        lambda payload: _FakeMessage(payload),
    )

    sent_quotes = await query_commands._send_quote_batch(
        SimpleNamespace(),
        SimpleNamespace(),
        "123",
        [quote],
    )

    assert sent_quotes == []
    assert sent_messages == ["语录记录存在，但图片暂时不可用，请联系管理员检查。"]


@pytest.mark.asyncio
async def test_send_small_quote_batch_skips_file_lost_after_recheck(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    valid_path = tmp_path / "valid.png"
    valid_path.write_bytes(b"valid-image")
    valid_quote = SimpleNamespace(
        id=35,
        group_id="123",
        image_path="valid.png",
    )
    missing_quote = SimpleNamespace(
        id=36,
        group_id="123",
        image_path="missing.png",
    )
    sent_segments: list[object] = []

    async def _fake_send(message, target=None, bot=None):
        sent_segments.extend(message)

    monkeypatch.setattr(query_commands, "safe_file_exists", lambda path: True)
    monkeypatch.setattr(
        query_commands,
        "resolve_quote_image_path",
        lambda path: tmp_path / path,
    )
    monkeypatch.setattr(query_commands.UniMessage, "send", _fake_send)

    sent_quotes = await query_commands._send_quote_batch(
        SimpleNamespace(),
        SimpleNamespace(),
        "123",
        [valid_quote, missing_quote],
    )

    assert sent_quotes == [valid_quote]
    assert len(sent_segments) == 1
    assert sent_segments[0].raw == b"valid-image"


def test_make_record_alc_supports_compact_no_space_input():
    result = upload_commands.make_record_alc.parse("记录语录aaa bbb")

    assert result.matched is True
    assert tuple(part.text for part in result.all_matched_args["parts"]) == (
        "aaa",
        "bbb",
    )


def test_idiom_record_alc_supports_compact_no_space_input():
    result = upload_commands.idiom_record_alc.parse("入典aaa bbb")

    assert result.matched is True
    assert tuple(part.text for part in result.all_matched_args["parts"]) == (
        "aaa",
        "bbb",
    )


@pytest.mark.asyncio
async def test_match_upload_with_image_requires_current_or_reply_upload_segment():
    class _FakeEvent:
        def __init__(self, message, reply=None):
            self.message = message
            self.reply = reply

    assert await upload_commands._match_upload_with_image(
        _FakeEvent([SimpleNamespace(type="text")])
    ) is False
    assert await upload_commands._match_upload_with_image(
        _FakeEvent([SimpleNamespace(type="image")])
    ) is True
    assert await upload_commands._match_upload_with_image(
        _FakeEvent(
            [SimpleNamespace(type="text")],
            reply=SimpleNamespace(message=[SimpleNamespace(type="image")]),
        )
    ) is True
    assert await upload_commands._match_upload_with_image(
        _FakeEvent([SimpleNamespace(type="forward")])
    ) is True
    assert await upload_commands._match_upload_with_image(
        _FakeEvent(
            [SimpleNamespace(type="text")],
            reply=SimpleNamespace(message=[SimpleNamespace(type="forward")]),
        )
    ) is True


@pytest.mark.asyncio
async def test_match_record_reply_requires_reply():
    assert await upload_commands._match_record_reply(SimpleNamespace(reply=None)) is False
    assert (
        await upload_commands._match_record_reply(
            SimpleNamespace(reply=SimpleNamespace(message=[]))
        )
        is True
    )


@pytest.mark.asyncio
async def test_extract_manual_tags_with_mention_names_uses_card_and_normalizes():
    class _Bot:
        async def get_group_member_info(self, **kwargs):
            return {"card": "群主 张三", "nickname": "张三"}

    tags = await tag_utils.parse_tag_segments_with_mention_names(
        _Bot(),
        "123",
        [
            At(flag="user", target="114514", display="展示 名"),
            Text("经典"),
        ],
    )

    assert tags == ["user:114514", "群主_张三", "经典"]


@pytest.mark.asyncio
async def test_extract_manual_tags_with_mention_names_falls_back_to_display_or_user_only():
    class _FailBot:
        async def get_group_member_info(self, **kwargs):
            raise RuntimeError("boom")

    display_tags = await tag_utils.parse_tag_segments_with_mention_names(
        _FailBot(),
        "123",
        [At(flag="user", target="114514", display="展示 名")],
    )
    user_only_tags = await tag_utils.parse_tag_segments_with_mention_names(
        _FailBot(),
        "123",
        [At(flag="user", target="114514")],
    )

    assert display_tags == ["user:114514", "展示_名"]
    assert user_only_tags == ["user:114514"]


@pytest.mark.asyncio
async def test_get_quotes_by_ids_in_order_preserves_input_order(monkeypatch):
    quotes = [
        SimpleNamespace(id=1, group_id="123"),
        SimpleNamespace(id=2, group_id="123"),
        SimpleNamespace(id=3, group_id="123"),
    ]

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter(quotes))

    result = await QuoteService.get_quotes_by_ids_in_order([3, 1, 2])

    assert [quote.id for quote in result] == [3, 1, 2]


@pytest.mark.asyncio
async def test_get_quote_by_history_index_uses_desc_id_without_keyword(monkeypatch):
    quotes = [
        SimpleNamespace(id=1, group_id="123"),
        SimpleNamespace(id=3, group_id="123"),
        SimpleNamespace(id=2, group_id="123"),
        SimpleNamespace(id=9, group_id="456"),
    ]

    monkeypatch.setattr(quote_service_module.Quote, "filter", _build_filter(quotes))

    result = await QuoteService.get_quote_by_history_index("123", 2)

    assert result is not None
    assert result.id == 2


@pytest.mark.asyncio
async def test_get_quote_by_history_index_sorts_search_matches(monkeypatch):
    quotes = [
        SimpleNamespace(id=2, group_id="123"),
        SimpleNamespace(id=9, group_id="123"),
        SimpleNamespace(id=5, group_id="123"),
    ]
    calls: list[tuple[str, str, str | None]] = []

    async def _fake_search_quotes(
        group_id: str, keyword: str, user_id_filter: str | None = None
    ):
        calls.append((group_id, keyword, user_id_filter))
        return list(quotes)

    monkeypatch.setattr(
        QuoteService, "search_quotes", staticmethod(_fake_search_quotes)
    )

    result = await QuoteService.get_quote_by_history_index(
        "123", 2, "南极", "114514"
    )

    assert result is not None
    assert result.id == 5
    assert calls == [("123", "南极", "114514")]


def test_select_quote_ids_without_record_prefers_unseen(monkeypatch):
    monkeypatch.setattr(QuoteService, "_recent_quotes", {})
    monkeypatch.setattr(quote_service_module.random, "shuffle", lambda seq: None)
    QuoteService._recent_quotes["group_all"] = [1, 2]

    result = QuoteService.select_quote_ids_without_record(
        "group_all", [1, 2, 3, 4], 2
    )

    assert result == [3, 4]


def test_record_recent_quote_ids_trims_history_window(monkeypatch):
    monkeypatch.setattr(QuoteService, "_recent_quotes", {})

    QuoteService.record_recent_quote_ids(
        "group_all", list(range(1, QuoteService._max_history_per_key + 5))
    )

    assert len(QuoteService._recent_quotes["group_all"]) == QuoteService._max_history_per_key
    assert QuoteService._recent_quotes["group_all"][0] == 5


@pytest.mark.asyncio
async def test_increment_view_counts_uses_single_bulk_update(monkeypatch):
    quotes = [
        SimpleNamespace(id=1, group_id="123"),
        SimpleNamespace(id=2, group_id="123"),
    ]
    update_calls: list[dict] = []

    monkeypatch.setattr(
        quote_service_module.Quote, "filter", _build_filter(quotes, update_calls=update_calls)
    )

    await QuoteService.increment_view_counts([1, 2, 2])

    assert len(update_calls) == 1
    assert "view_count" in update_calls[0]


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
async def test_save_img_handle_writes_dual_mention_tags(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}
    sent_api_calls: list[tuple[str, dict]] = []
    sent_messages: list[object] = []

    async def _fake_extract_tags(bot, group_id, arp, arg_name="parts"):
        assert group_id == "123"
        return ["user:114514", "群主_张三"]

    async def _fake_get_img_hash(path):
        return "image-hash"

    async def _fake_recognize_text(path: str):
        return "ocr text"

    async def _fake_set_pending_emoji_like(*args, **kwargs):
        return None

    async def _fake_add_quote(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1), True

    class _Bot:
        async def call_api(self, name: str, **kwargs):
            sent_api_calls.append((name, kwargs))

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(
        upload_commands,
        "extract_manual_tags_with_mention_names",
        _fake_extract_tags,
    )
    _patch_upload_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(upload_commands, "get_img_hash", _fake_get_img_hash)
    monkeypatch.setattr(
        upload_commands.TextRecognitionService,
        "recognize_single",
        _fake_recognize_text,
    )
    monkeypatch.setattr(
        upload_commands.QuoteService, "add_quote", _fake_add_quote
    )
    monkeypatch.setattr(
        upload_commands.Quote,
        "filter",
        lambda **kwargs: _FakeExistsQuery(False),
    )
    monkeypatch.setattr(
        upload_commands,
        "_set_pending_emoji_like",
        _fake_set_pending_emoji_like,
    )
    monkeypatch.setattr(
        upload_commands.MessageUtils,
        "build_message",
        lambda payload: _FakeMessage(payload),
    )

    arp = SimpleNamespace(
        all_matched_args={"parts": [upload_commands.UniImage(raw=b"fake-image-bytes")]},
        main_args={},
    )
    event = SimpleNamespace(
        get_session_id=lambda: "group_123_456",
        message_id=1001,
        get_user_id=lambda: "42",
        reply=None,
    )

    await upload_commands.save_img_handle(_Bot(), event, arp, {})

    assert captured["group_id"] == "123"
    assert captured["manual_tags"] == ["user:114514", "群主_张三"]
    assert sent_messages
    assert sent_messages[0][0] == b"fake-image-bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_shape",
    ["messages", "data_message", "lagrange_message"],
)
async def test_extract_forward_images_supports_onebot_response_shapes(
    monkeypatch: pytest.MonkeyPatch,
    response_shape: str,
):
    node_messages = [
        [{"type": "image", "data": {"file": "first.png"}}],
        [
            {"type": "text", "data": {"text": "ignored"}},
            {"type": "image", "data": {"file": "second.png"}},
        ],
    ]
    napcat_nodes = [{"message": message} for message in node_messages]
    if response_shape == "messages":
        response = {"messages": napcat_nodes}
    elif response_shape == "data_message":
        response = {"data": {"message": napcat_nodes}}
    else:
        response = {
            "message": [
                {"data": {"content": message}} for message in node_messages
            ]
        }

    async def _fake_extract_direct_images(bot, message):
        return [
            upload_commands.UniImage(id=segment["data"]["file"])
            for segment in message
            if segment["type"] == "image"
        ]

    class _Bot:
        async def call_api(self, name: str, **kwargs):
            assert name == "get_forward_msg"
            assert kwargs == {"id": "forward-id"}
            return response

    monkeypatch.setattr(
        upload_commands,
        "_extract_direct_images",
        _fake_extract_direct_images,
    )

    images, has_forward = await upload_commands._extract_forward_images(
        _Bot(),
        [{"type": "forward", "data": {"id": "forward-id"}}],
    )

    assert has_forward is True
    assert [image.id for image in images] == ["first.png", "second.png"]


@pytest.mark.asyncio
async def test_save_img_handle_batch_uses_batch_recognition_and_reports_partial_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    target_images = [
        upload_commands.UniImage(raw=b"first"),
        upload_commands.UniImage(raw=b"second"),
        upload_commands.UniImage(raw=b"third"),
    ]
    pending_results = [
        upload_commands._UploadImageResult(status="success", image_data=b"first"),
        upload_commands._UploadImageResult(status="duplicate"),
        upload_commands._UploadImageResult(status="failed", error="下载失败"),
    ]
    process_calls: list[dict[str, object]] = []
    sent_messages: list[object] = []

    async def _fake_extract_tags(bot, group_id, arp, arg_name="parts"):
        return ["batch-tag"]

    async def _fake_collect_upload_images(bot, event, upload_parts):
        return target_images, True

    async def _fake_process_upload_image(bot, target_image, **kwargs):
        process_calls.append({"target": target_image, **kwargs})
        return pending_results.pop(0)

    async def _fake_set_pending_emoji_like(bot, event):
        return None

    def _fake_get_config(module, key, default=None):
        return {"QUOTE_MAX_IMAGE_SIZE_MB": 15}.get(key, default)

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(
        upload_commands,
        "extract_manual_tags_with_mention_names",
        _fake_extract_tags,
    )
    monkeypatch.setattr(
        upload_commands,
        "_collect_upload_images",
        _fake_collect_upload_images,
    )
    monkeypatch.setattr(
        upload_commands,
        "_process_upload_image",
        _fake_process_upload_image,
    )
    monkeypatch.setattr(
        upload_commands,
        "_set_pending_emoji_like",
        _fake_set_pending_emoji_like,
    )
    monkeypatch.setattr(upload_commands, "ensure_quote_path", lambda: tmp_path)
    monkeypatch.setattr(upload_commands.Config, "get_config", _fake_get_config)
    monkeypatch.setattr(
        upload_commands.MessageUtils,
        "build_message",
        lambda payload: _FakeMessage(payload),
    )

    event = SimpleNamespace(
        get_session_id=lambda: "group_123_456",
        get_user_id=lambda: "42",
        message_id=1001,
    )
    arp = SimpleNamespace(all_matched_args={"parts": []}, main_args={})

    await upload_commands.save_img_handle(SimpleNamespace(), event, arp, {})

    assert [call["target"] for call in process_calls] == target_images
    assert all(call["batch"] is True for call in process_calls)
    assert all(call["manual_tags"] == ["batch-tag"] for call in process_calls)
    assert sent_messages == [
        "批量上传完成：成功 1/3 张，重复 1 张，失败 1 张。\n"
        "失败明细：第 3 张：下载失败"
    ]


@pytest.mark.asyncio
async def test_process_upload_image_cleans_temp_file_after_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    async def _raise_hash_error(path):
        raise RuntimeError("hash failed")

    monkeypatch.setattr(upload_commands, "get_img_hash", _raise_hash_error)

    result = await upload_commands._process_upload_image(
        SimpleNamespace(),
        upload_commands.UniImage(raw=b"image-bytes"),
        quote_path=tmp_path,
        group_id="123",
        user_id="42",
        manual_tags=[],
        max_bytes=1024,
        max_size_mb=1,
        batch=False,
    )

    assert result.status == "failed"
    assert list(tmp_path.glob("temp_*.png")) == []


def test_invalid_text_recognition_priority_uses_context_default(monkeypatch):
    service = text_recognition_module.TextRecognitionService
    monkeypatch.setattr(
        text_recognition_module.Config,
        "get_config",
        lambda *args, **kwargs: "unknown",
    )

    assert service._get_priority("TEXT_RECOGNITION_PRIORITY", "llm") == "llm"
    assert (
        service._get_priority(
            "BATCH_TEXT_RECOGNITION_PRIORITY",
            "paddleocr_api",
        )
        == "paddleocr_api"
    )


@pytest.mark.asyncio
async def test_paddleocr_api_submits_polls_and_parses_jsonl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    image_path = tmp_path / "quote.png"
    image_path.write_bytes(b"image-data")
    settings = paddleocr_api_module.PaddleOCRAPISettings(
        token="test-token",
        job_url="https://example.test/jobs",
        model="PaddleOCR-VL-1.6",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )
    client = paddleocr_api_module.PaddleOCRAPIClient(settings)
    get_calls: list[str] = []

    async def _fake_post(url, **kwargs):
        assert url == settings.job_url
        assert kwargs["headers"] == {"Authorization": "bearer test-token"}
        assert kwargs["data"]["model"] == settings.model
        assert json.loads(kwargs["data"]["optionalPayload"]) == {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        assert kwargs["files"]["file"] == ("quote.png", b"image-data", "image/png")
        return httpx.Response(200, json={"data": {"jobId": "job-1"}})

    async def _fake_get(url, **kwargs):
        get_calls.append(url)
        assert url == "https://example.test/jobs/job-1"
        if get_calls.count(url) == 1:
            return httpx.Response(200, json={"data": {"state": "running"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "done",
                    "resultUrl": {"jsonUrl": "https://result.test/result.jsonl"},
                }
            },
        )

    async def _fake_client_get(self, url, **kwargs):
        assert url == "https://result.test/result.jsonl"
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            text=(
                '{"result":{"layoutParsingResults":'
                '[{"markdown":{"text":"第一页"}}]}}\n'
                '{"result":{"layoutParsingResults":'
                '[{"markdown":{"text":"第二页"}}]}}\n'
            ),
        )

    monkeypatch.setattr(paddleocr_api_module.AsyncHttpx, "post", _fake_post)
    monkeypatch.setattr(paddleocr_api_module.AsyncHttpx, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_client_get)

    assert await client.recognize(image_path) == "第一页\n第二页"
    assert get_calls == [
        "https://example.test/jobs/job-1",
        "https://example.test/jobs/job-1",
    ]


@pytest.mark.parametrize(
    ("markdown_text", "expected"),
    [
        (
            '<div style="text-align: center;"><img src="imgs/image.jpg" '
            'alt="Image" /></div>\n',
            "",
        ),
        (
            "花点时间挑战难题\n\n"
            '<div style="text-align: center;"><img src="imgs/image.jpg" '
            'alt="Image" /></div>\n',
            "花点时间挑战难题",
        ),
        ("识别正文 ![Image](imgs/image.jpg)", "识别正文"),
    ],
)
def test_paddleocr_api_cleans_image_placeholders(markdown_text, expected):
    assert (
        paddleocr_api_module.PaddleOCRAPIClient._clean_markdown_text(markdown_text)
        == expected
    )


@pytest.mark.asyncio
async def test_paddleocr_api_missing_token_skips_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    settings = paddleocr_api_module.PaddleOCRAPISettings(
        token="",
        job_url="https://example.test/jobs",
        model="PaddleOCR-VL-1.6",
        poll_interval_seconds=5,
        timeout_seconds=180,
    )

    async def _fail_post(*args, **kwargs):
        raise AssertionError("缺少 Token 时不应发起请求")

    monkeypatch.setattr(paddleocr_api_module.AsyncHttpx, "post", _fail_post)

    client = paddleocr_api_module.PaddleOCRAPIClient(settings)

    assert client.is_configured is False
    with pytest.raises(
        paddleocr_api_module.PaddleOCRAPIError,
        match="PADDLEOCR_API_TOKEN",
    ):
        await client.recognize(tmp_path / "missing.png")


@pytest.mark.asyncio
async def test_paddleocr_api_failed_job_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    image_path = tmp_path / "quote.png"
    image_path.write_bytes(b"image-data")
    settings = paddleocr_api_module.PaddleOCRAPISettings(
        token="test-token",
        job_url="https://example.test/jobs",
        model="PaddleOCR-VL-1.6",
        poll_interval_seconds=5,
        timeout_seconds=180,
    )

    async def _fake_post(url, **kwargs):
        return httpx.Response(200, json={"data": {"jobId": "job-1"}})

    async def _fake_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"data": {"state": "failed", "errorMsg": "quota exceeded"}},
        )

    monkeypatch.setattr(paddleocr_api_module.AsyncHttpx, "post", _fake_post)
    monkeypatch.setattr(paddleocr_api_module.AsyncHttpx, "get", _fake_get)

    with pytest.raises(
        paddleocr_api_module.PaddleOCRAPIError,
        match="quota exceeded",
    ):
        await paddleocr_api_module.PaddleOCRAPIClient(settings).recognize(image_path)


@pytest.mark.asyncio
async def test_paddleocr_api_polling_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = paddleocr_api_module.PaddleOCRAPISettings(
        token="test-token",
        job_url="https://example.test/jobs",
        model="PaddleOCR-VL-1.6",
        poll_interval_seconds=5,
        timeout_seconds=1,
    )
    client = paddleocr_api_module.PaddleOCRAPIClient(settings)
    monotonic_values = iter([0.0, 0.0, 2.0])

    async def _fake_get(url, **kwargs):
        return httpx.Response(200, json={"data": {"state": "pending"}})

    monkeypatch.setattr(paddleocr_api_module.AsyncHttpx, "get", _fake_get)
    monkeypatch.setattr(client, "_monotonic", lambda: next(monotonic_values))

    async with httpx.AsyncClient() as http_client:
        with pytest.raises(TimeoutError, match="1 秒内未完成"):
            await client._wait_for_result(
                http_client,
                "job-1",
            )


@pytest.mark.asyncio
async def test_text_recognition_uses_separate_single_and_batch_priorities(
    monkeypatch: pytest.MonkeyPatch,
):
    service = text_recognition_module.TextRecognitionService
    priorities: list[str] = []

    def _fake_get_config(module, key, default=None):
        return {
            "TEXT_RECOGNITION_PRIORITY": "paddleocr_api",
            "BATCH_TEXT_RECOGNITION_PRIORITY": "llm",
        }.get(key, default)

    async def _fake_recognize(cls, image_path, priority):
        priorities.append(priority)
        return priority

    monkeypatch.setattr(
        text_recognition_module.Config,
        "get_config",
        _fake_get_config,
    )
    monkeypatch.setattr(service, "_recognize", classmethod(_fake_recognize))

    assert await service.recognize_single("single.png") == "paddleocr_api"
    assert await service.recognize_batch("batch.png") == "llm"
    assert priorities == ["paddleocr_api", "llm"]


@pytest.mark.asyncio
async def test_text_recognition_api_priority_stops_after_text(
    monkeypatch: pytest.MonkeyPatch,
):
    service = text_recognition_module.TextRecognitionService
    status = text_recognition_module.RecognitionStatus
    result = text_recognition_module.RecognitionResult
    executed: list[str] = []

    async def _successful_api(cls, image_path):
        executed.append("paddleocr_api")
        return result(status.TEXT, "api text")

    async def _fail_llm(cls, image_path):
        raise AssertionError("API 有文字时不应调用视觉模型")

    monkeypatch.setattr(service, "_recognize_with_api", classmethod(_successful_api))
    monkeypatch.setattr(service, "_recognize_with_llm", classmethod(_fail_llm))

    assert await service._recognize("image.png", "paddleocr_api") == "api text"
    assert executed == ["paddleocr_api"]


@pytest.mark.asyncio
async def test_text_recognition_api_empty_falls_back_to_llm(
    monkeypatch: pytest.MonkeyPatch,
):
    service = text_recognition_module.TextRecognitionService
    status = text_recognition_module.RecognitionStatus
    result = text_recognition_module.RecognitionResult
    executed: list[str] = []

    async def _empty_api(cls, image_path):
        executed.append("paddleocr_api")
        return result(status.NO_TEXT)

    async def _successful_llm(cls, image_path):
        executed.append("llm")
        return result(status.TEXT, "llm text")

    monkeypatch.setattr(service, "_recognize_with_api", classmethod(_empty_api))
    monkeypatch.setattr(service, "_recognize_with_llm", classmethod(_successful_llm))

    assert await service._recognize("image.png", "paddleocr_api") == "llm text"
    assert executed == ["paddleocr_api", "llm"]


@pytest.mark.asyncio
async def test_text_recognition_llm_empty_is_conclusive(
    monkeypatch: pytest.MonkeyPatch,
):
    service = text_recognition_module.TextRecognitionService
    status = text_recognition_module.RecognitionStatus
    result = text_recognition_module.RecognitionResult
    executed: list[str] = []

    async def _empty_llm(cls, image_path):
        executed.append("llm")
        return result(status.NO_TEXT)

    async def _fail_api(cls, image_path):
        raise AssertionError("视觉模型成功判定无文字时不应降级")

    monkeypatch.setattr(service, "_recognize_with_llm", classmethod(_empty_llm))
    monkeypatch.setattr(service, "_recognize_with_api", classmethod(_fail_api))

    assert await service._recognize("image.png", "llm") == ""
    assert executed == ["llm"]


@pytest.mark.asyncio
async def test_text_recognition_llm_failure_falls_back_to_api(
    monkeypatch: pytest.MonkeyPatch,
):
    service = text_recognition_module.TextRecognitionService
    status = text_recognition_module.RecognitionStatus
    result = text_recognition_module.RecognitionResult
    executed: list[str] = []

    async def _failed_llm(cls, image_path):
        executed.append("llm")
        return result(status.FAILED)

    async def _successful_api(cls, image_path):
        executed.append("paddleocr_api")
        return result(status.TEXT, "api text")

    monkeypatch.setattr(service, "_recognize_with_llm", classmethod(_failed_llm))
    monkeypatch.setattr(service, "_recognize_with_api", classmethod(_successful_api))

    assert await service._recognize("image.png", "llm") == "api text"
    assert executed == ["llm", "paddleocr_api"]


@pytest.mark.asyncio
async def test_make_record_handle_writes_dual_mention_tags(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}
    sent_messages: list[object] = []

    async def _fake_extract_tags(bot, group_id, arp, arg_name="parts"):
        assert group_id == "123"
        return ["user:114514", "群主_张三"]

    async def _fake_handle_generation(bot, event, arp, session, issuer_user_id=None):
        return b"generated-image", "recorded text", "114514", None

    async def _fake_add_quote(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1), True

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(
        upload_commands,
        "extract_manual_tags_with_mention_names",
        _fake_extract_tags,
    )
    monkeypatch.setattr(
        upload_commands, "_handle_quote_generation", _fake_handle_generation
    )
    _patch_upload_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        upload_commands.Quote,
        "filter",
        lambda **kwargs: _FakeExistsQuery(False),
    )
    monkeypatch.setattr(
        upload_commands.QuoteService, "add_quote", _fake_add_quote
    )
    monkeypatch.setattr(
        upload_commands.MessageUtils, "build_message", lambda payload: _FakeMessage(payload)
    )

    event = SimpleNamespace(get_user_id=lambda: "42")
    session = SimpleNamespace(group=SimpleNamespace(id="123"))

    await upload_commands.make_record_handle(
        SimpleNamespace(), event, SimpleNamespace(), session
    )

    assert captured["group_id"] == "123"
    assert Path(captured["image_path"]).is_relative_to(tmp_path)
    assert captured["manual_tags"] == ["user:114514", "群主_张三"]
    assert sent_messages == [
        upload_commands._build_record_success_message(b"generated-image")
    ]


@pytest.mark.asyncio
async def test_idiom_record_handle_forces_classic_and_reuses_record_flow(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, object] = {}
    sent_messages: list[object] = []

    async def _fake_extract_tags(bot, group_id, arp, arg_name="parts"):
        assert group_id == "123"
        return ["user:114514", "群主_张三"]

    async def _fake_handle_generation(
        bot,
        event,
        arp,
        session,
        issuer_user_id=None,
        forced_variant=None,
    ):
        captured["forced_variant"] = forced_variant
        return b"generated-image", "recorded text", "114514", None

    async def _fake_add_quote(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1), True

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(
        upload_commands,
        "extract_manual_tags_with_mention_names",
        _fake_extract_tags,
    )
    monkeypatch.setattr(
        upload_commands, "_handle_quote_generation", _fake_handle_generation
    )
    _patch_upload_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        upload_commands.Quote,
        "filter",
        lambda **kwargs: _FakeExistsQuery(False),
    )
    monkeypatch.setattr(upload_commands.QuoteService, "add_quote", _fake_add_quote)
    monkeypatch.setattr(
        upload_commands.MessageUtils, "build_message", lambda payload: _FakeMessage(payload)
    )

    event = SimpleNamespace(get_user_id=lambda: "42")
    session = SimpleNamespace(group=SimpleNamespace(id="123"))
    arp = SimpleNamespace(all_matched_args={}, main_args={})

    await upload_commands.idiom_record_handle(
        SimpleNamespace(), event, arp, session
    )

    assert captured["forced_variant"] == "classic"
    assert captured["group_id"] == "123"
    assert Path(captured["image_path"]).is_relative_to(tmp_path)
    assert captured["manual_tags"] == ["user:114514", "群主_张三"]
    assert sent_messages == [
        upload_commands._build_record_success_message(b"generated-image")
    ]


@pytest.mark.asyncio
async def test_idiom_record_handle_rejects_style_override(monkeypatch):
    sent_messages: list[object] = []
    generation_called = False

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    async def _fake_handle_generation(*args, **kwargs):
        nonlocal generation_called
        generation_called = True
        return None, None, None, "unexpected"

    arp = SimpleNamespace(
        all_matched_args={"parts": [Text("-s"), Text("1"), Text("南极")]},
        main_args={"parts": [Text("-s"), Text("1"), Text("南极")]},
    )

    monkeypatch.setattr(
        upload_commands.MessageUtils, "build_message", lambda payload: _FakeMessage(payload)
    )
    monkeypatch.setattr(
        upload_commands, "_handle_quote_generation", _fake_handle_generation
    )

    await upload_commands.idiom_record_handle(
        SimpleNamespace(),
        SimpleNamespace(get_user_id=lambda: "42"),
        arp,
        SimpleNamespace(group=SimpleNamespace(id="123")),
    )

    assert generation_called is False
    assert sent_messages == ["入典 为固定 classic 预设，不支持 -s/--style。"]


@pytest.mark.asyncio
async def test_handle_quote_generation_forced_classic_rejects_pure_image_message(
    monkeypatch,
):
    async def _fake_extract_info(event, bot):
        return (
            upload_commands.UniMessage([upload_commands.UniImage(raw=b"image-bytes")]),
            "南极",
            "114514",
        ), None

    async def _fake_superuser(bot, event):
        return False

    monkeypatch.setattr(upload_commands, "_extract_info_from_reply", _fake_extract_info)
    monkeypatch.setattr(upload_commands, "SUPERUSER", _fake_superuser)
    monkeypatch.setattr(
        upload_commands.Config,
        "get_config",
        lambda module, key, default=None: False if key.startswith("QUOTE_ALLOW") else default,
    )

    class _FakeBot:
        async def get_msg(self, message_id: int):
            return {"message": []}

    class _FakeArp:
        def query(self, key, default=None):
            return default

        def find(self, key):
            return False

    event = SimpleNamespace(
        self_id="1919810",
        reply=SimpleNamespace(
            message_id=1,
            sender=SimpleNamespace(user_id="114514"),
        ),
        group_id=123,
    )

    img_data, recorded_text, quoted_user_id, error = await upload_commands._handle_quote_generation(
        _FakeBot(),
        event,
        _FakeArp(),
        SimpleNamespace(group=SimpleNamespace(id="123")),
        issuer_user_id="42",
        forced_variant="classic",
    )

    assert img_data is None
    assert recorded_text is None
    assert quoted_user_id is None
    assert error == "不支持使用 classic 主题记录纯图片消息。"


@pytest.mark.asyncio
async def test_handle_quote_tag_add_uses_dual_mention_tags(monkeypatch):
    quote = SimpleNamespace(id=1)
    captured: dict[str, object] = {}

    async def _fake_get_quote_from_reply(bot, event, session):
        return quote

    async def _fake_extract_tags(bot, group_id, arp, arg_name="parts"):
        assert group_id == "123"
        return ["user:114514", "群主_张三"]

    async def _fake_update(bot, event, quote_arg, tags, action):
        captured["quote"] = quote_arg
        captured["tags"] = tags
        captured["action"] = action

    class _FakeArp:
        def query(self, key, default=""):
            return "add" if key == "action" else default

    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)
    monkeypatch.setattr(
        manage_commands,
        "extract_manual_tags_with_mention_names",
        _fake_extract_tags,
    )
    monkeypatch.setattr(
        manage_commands, "_update_quote_manual_tags", _fake_update
    )

    await manage_commands.handle_quote_tag(
        SimpleNamespace(),
        SimpleNamespace(),
        _FakeArp(),
        SimpleNamespace(group=SimpleNamespace(id="123")),
    )

    assert captured == {
        "quote": quote,
        "tags": ["user:114514", "群主_张三"],
        "action": "add",
    }


@pytest.mark.asyncio
async def test_handle_quote_deltag_uses_dual_mention_tags(monkeypatch):
    quote = SimpleNamespace(id=1)
    captured: dict[str, object] = {}

    async def _fake_require_reply_quote(bot, event, session):
        return quote

    async def _fake_extract_tags(bot, group_id, arp, arg_name="parts"):
        assert group_id == "123"
        return ["user:114514", "群主_张三"]

    async def _fake_update(bot, event, quote_arg, tags, action):
        captured["quote"] = quote_arg
        captured["tags"] = tags
        captured["action"] = action

    monkeypatch.setattr(
        manage_commands, "_require_reply_quote", _fake_require_reply_quote
    )
    monkeypatch.setattr(
        manage_commands,
        "extract_manual_tags_with_mention_names",
        _fake_extract_tags,
    )
    monkeypatch.setattr(
        manage_commands, "_update_quote_manual_tags", _fake_update
    )

    await manage_commands.handle_quote_deltag(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(group=SimpleNamespace(id="123")),
    )

    assert captured == {
        "quote": quote,
        "tags": ["user:114514", "群主_张三"],
        "action": "del",
    }


@pytest.mark.parametrize(
    ("command", "expected_group_id"),
    [
        ("quote manager check", None),
        ("quote manager check 456", "456"),
        ("quote manager 检查 全部", "全部"),
    ],
)
def test_quote_storage_audit_command_parses_scope(
    command: str,
    expected_group_id: str | None,
):
    arp = manage_commands.quote_manage_cmd.command().parse(command)

    assert arp.matched is True
    assert arp.find("manager.check") is True
    assert arp.query("manager.check.group_id") == expected_group_id


def test_quote_storage_audit_command_keeps_superuser_permission():
    assert str(manage_commands.quote_manage_cmd.permission) == str(
        manage_commands.SUPERUSER
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_group_id", "expected_audit_group_id", "expected_scope"),
    [
        (None, "123", "当前群 123"),
        ("456", "456", "群 456"),
        ("全部", None, "全部语录"),
    ],
)
async def test_handle_storage_audit_reports_read_only_summary(
    monkeypatch: pytest.MonkeyPatch,
    requested_group_id: str | None,
    expected_audit_group_id: str | None,
    expected_scope: str,
):
    captured_groups: list[str | None] = []
    sent_messages: list[str] = []

    async def _fake_audit(group_id=None):
        captured_groups.append(group_id)
        return quote_service_module.QuoteAuditSummary(
            total=3,
            valid=1,
            missing=1,
            out_of_bounds=1,
            orphan_files=2 if group_id is None else 0,
            issues=[
                quote_service_module.QuoteAuditIssue(
                    id=7,
                    group_id="123",
                    image_path="quote/images/missing.png",
                    reason="missing",
                )
            ],
        )

    class _Arp:
        def query(self, key, default=None):
            if key == "manager.check.group_id":
                return requested_group_id
            return default

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(
        manage_commands.QuoteService,
        "audit_storage",
        _fake_audit,
    )
    monkeypatch.setattr(
        manage_commands.MessageUtils,
        "build_message",
        lambda payload: _FakeMessage(payload),
    )

    await manage_commands.handle_storage_audit(
        SimpleNamespace(),
        SimpleNamespace(get_user_id=lambda: "42"),
        _Arp(),
        SimpleNamespace(group=SimpleNamespace(id="123")),
    )

    assert captured_groups == [expected_audit_group_id]
    assert expected_scope in sent_messages[0]
    assert "数据库记录：3" in sent_messages[0]
    assert "本操作只读，不会删除数据库或图片" in sent_messages[0]
    assert "ID 7" in sent_messages[0]
    if expected_audit_group_id is None:
        assert "孤儿图片：2" in sent_messages[0]
    else:
        assert "孤儿图片" not in sent_messages[0]


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
async def test_delete_quote_standalone_without_reply_only_sends_guidance(monkeypatch):
    calls = {"reply": 0, "last": 0}
    sent_messages: list[object] = []

    async def _fake_get_quote_from_reply(bot, event, session):
        return None

    async def _fake_handle_delete_reply_quote(bot, event, session, quote=None):
        calls["reply"] += 1

    async def _fake_handle_delete_last_quote(bot, event, session):
        calls["last"] += 1

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(manage_commands, "_get_quote_from_reply", _fake_get_quote_from_reply)
    monkeypatch.setattr(
        manage_commands, "_handle_delete_reply_quote", _fake_handle_delete_reply_quote
    )
    monkeypatch.setattr(
        manage_commands, "_handle_delete_last_quote", _fake_handle_delete_last_quote
    )
    monkeypatch.setattr(
        manage_commands.MessageUtils, "build_message", lambda payload: _FakeMessage(payload)
    )

    session = SimpleNamespace(group=SimpleNamespace(id="123"))

    await manage_commands.handle_delete_quote_standalone(
        SimpleNamespace(), SimpleNamespace(), session
    )

    assert calls == {"reply": 0, "last": 0}
    assert sent_messages == ["请回复需要删除的语录图片后再使用此命令。"]


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


def test_group_quote_paths_isolate_same_filename_between_groups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(config_module, "get_quote_path", lambda: tmp_path)

    first = config_module.get_quote_group_path("10001") / "same.png"
    second = config_module.get_quote_group_path("10002") / "same.png"

    assert first != second
    assert first.parent == tmp_path / "10001"
    assert second.parent == tmp_path / "10002"


def test_resolve_quote_image_path_supports_legacy_flat_default_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    data_path = tmp_path / "data"
    quote_path = data_path / "quote" / "images"
    legacy_image = quote_path / "legacy.png"
    legacy_image.parent.mkdir(parents=True)
    legacy_image.write_bytes(b"legacy")
    monkeypatch.setattr(config_module, "DATA_PATH", data_path)
    monkeypatch.setattr(config_module, "get_quote_path", lambda: quote_path)

    stored_path = "quote/images/legacy.png"

    assert config_module.resolve_quote_image_path(stored_path) == legacy_image
    assert config_module.safe_file_exists(stored_path) is True


def test_resolve_quote_image_path_supports_legacy_external_relative_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    data_path = tmp_path / "data"
    quote_path = tmp_path / "external" / "quote" / "images"
    legacy_image = quote_path / "legacy.png"
    data_path.mkdir()
    legacy_image.parent.mkdir(parents=True)
    legacy_image.write_bytes(b"legacy")
    monkeypatch.setattr(config_module, "DATA_PATH", data_path)
    monkeypatch.setattr(config_module, "get_quote_path", lambda: quote_path)

    stored_path = Path(os.path.relpath(legacy_image, data_path)).as_posix()

    assert stored_path.startswith("../")
    assert config_module.resolve_quote_image_path(stored_path) == legacy_image
    assert config_module.safe_file_exists(stored_path) is True


def test_resolve_quote_image_path_rejects_paths_outside_managed_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    data_path = tmp_path / "data"
    quote_path = tmp_path / "quotes"
    data_path.mkdir()
    quote_path.mkdir()
    monkeypatch.setattr(config_module, "DATA_PATH", data_path)
    monkeypatch.setattr(config_module, "get_quote_path", lambda: quote_path)

    with pytest.raises(ValueError, match="语录图片路径越界"):
        config_module.resolve_quote_image_path("../../etc/passwd")


@pytest.mark.asyncio
async def test_audit_storage_classifies_paths_and_preserves_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    data_path = tmp_path / "data"
    quote_path = data_path / "quote" / "images"
    valid_path = quote_path / "123" / "valid.png"
    orphan_path = quote_path / "orphan.png"
    ignored_temp_path = quote_path / "temp_upload.png"
    valid_path.parent.mkdir(parents=True)
    valid_path.write_bytes(b"valid")
    orphan_path.write_bytes(b"orphan")
    ignored_temp_path.write_bytes(b"temp")
    monkeypatch.setattr(config_module, "DATA_PATH", data_path)
    monkeypatch.setattr(config_module, "get_quote_path", lambda: quote_path)
    monkeypatch.setattr(quote_service_module, "DATA_PATH", data_path)
    monkeypatch.setattr(
        quote_service_module,
        "get_quote_path",
        lambda: quote_path,
        raising=False,
    )

    quotes = [
        SimpleNamespace(
            id=41,
            group_id="123",
            image_path="quote/images/123/valid.png",
        ),
        SimpleNamespace(
            id=42,
            group_id="123",
            image_path="quote/images/123/missing.png",
        ),
        SimpleNamespace(
            id=43,
            group_id="123",
            image_path="../../outside.png",
        ),
    ]
    monkeypatch.setattr(
        quote_service_module.Quote,
        "filter",
        _build_filter(quotes),
    )
    before_snapshot = {
        path.relative_to(quote_path).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in quote_path.rglob("*")
        if path.is_file()
    }

    result = await QuoteService.audit_storage()

    after_snapshot = {
        path.relative_to(quote_path).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in quote_path.rglob("*")
        if path.is_file()
    }
    assert result.total == 3
    assert result.valid == 1
    assert result.missing == 1
    assert result.out_of_bounds == 1
    assert result.orphan_files == 1
    assert [(issue.id, issue.reason) for issue in result.issues] == [
        (42, "missing"),
        (43, "out_of_bounds"),
    ]
    assert before_snapshot == after_snapshot


@pytest.mark.asyncio
async def test_audit_storage_limits_issue_details_without_truncating_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    data_path = tmp_path / "data"
    quote_path = data_path / "quote" / "images"
    quote_path.mkdir(parents=True)
    monkeypatch.setattr(config_module, "DATA_PATH", data_path)
    monkeypatch.setattr(config_module, "get_quote_path", lambda: quote_path)
    monkeypatch.setattr(quote_service_module, "DATA_PATH", data_path)
    monkeypatch.setattr(
        quote_service_module,
        "get_quote_path",
        lambda: quote_path,
        raising=False,
    )
    quotes = [
        SimpleNamespace(
            id=index,
            group_id="123",
            image_path=f"quote/images/missing-{index}.png",
        )
        for index in range(1, 26)
    ]
    monkeypatch.setattr(
        quote_service_module.Quote,
        "filter",
        _build_filter(quotes),
    )

    result = await QuoteService.audit_storage("123")

    assert result.total == 25
    assert result.missing == 25
    assert result.orphan_files == 0
    assert len(result.issues) == 20


@pytest.mark.asyncio
async def test_delete_quote_restores_file_when_database_delete_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    image_path = tmp_path / "quote.png"
    image_path.write_bytes(b"image")
    quote = SimpleNamespace(id=7, group_id="123", image_path="quote.png")

    class _FailingDeleteQuery:
        async def delete(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        quote_service_module, "resolve_quote_image_path", lambda path: image_path
    )
    monkeypatch.setattr(
        quote_service_module.Quote, "filter", lambda **kwargs: _FailingDeleteQuery()
    )

    result = await QuoteService.delete_quote_instance(quote)

    assert result is False
    assert image_path.read_bytes() == b"image"


@pytest.mark.asyncio
async def test_concurrent_delete_does_not_restore_orphan_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    image_path = tmp_path / "quote.png"
    image_path.write_bytes(b"image")
    quote = SimpleNamespace(id=17, group_id="123", image_path="quote.png")
    first_delete_started = asyncio.Event()
    allow_first_delete = asyncio.Event()
    delete_call_count = 0

    class _DeleteQuery:
        def __init__(self, call_index: int):
            self.call_index = call_index

        async def delete(self):
            if self.call_index == 0:
                first_delete_started.set()
                await allow_first_delete.wait()
                return 0
            return 1

    def _filter(**kwargs):
        nonlocal delete_call_count
        query = _DeleteQuery(delete_call_count)
        delete_call_count += 1
        return query

    monkeypatch.setattr(
        quote_service_module, "resolve_quote_image_path", lambda path: image_path
    )
    monkeypatch.setattr(quote_service_module.Quote, "filter", _filter)

    first_task = asyncio.create_task(QuoteService.delete_quote_instance(quote))
    await first_delete_started.wait()
    second_task = asyncio.create_task(QuoteService.delete_quote_instance(quote))
    await asyncio.sleep(0)
    allow_first_delete.set()

    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result is False
    assert second_result is True
    assert not image_path.exists()


@pytest.mark.asyncio
async def test_add_manual_tags_uses_locked_latest_value(
    monkeypatch: pytest.MonkeyPatch,
):
    stale_quote = SimpleNamespace(id=8, manual_tags=["stale"])
    locked_quote = SimpleNamespace(id=8, manual_tags=["latest"])
    saved_values: list[list[str]] = []

    async def _save(*, using_db=None, update_fields=None):
        saved_values.append(list(locked_quote.manual_tags))

    locked_quote.save = _save

    class _LockedQuery:
        def select_for_update(self):
            return self

        def using_db(self, connection):
            return self

        async def get(self):
            return locked_quote

    class _Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        quote_service_module.Quote, "filter", lambda **kwargs: _LockedQuery()
    )
    monkeypatch.setattr(
        quote_service_module, "in_transaction", lambda: _Transaction()
    )

    result = await QuoteService.add_manual_tags(stale_quote, ["new"])

    assert result.success is True
    assert result.tags == ["latest", "new"]
    assert result.changed_tags == ["new"]
    assert stale_quote.manual_tags == ["latest", "new"]
    assert saved_values == [["latest", "new"]]


@pytest.mark.asyncio
async def test_delete_manual_tags_failure_keeps_original_entity(
    monkeypatch: pytest.MonkeyPatch,
):
    quote = SimpleNamespace(id=9, manual_tags=["keep", "remove"])

    class _FailingTransaction:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        quote_service_module, "in_transaction", lambda: _FailingTransaction()
    )

    result = await QuoteService.delete_manual_tags(quote, ["remove"])

    assert result.success is False
    assert result.tags == ["keep", "remove"]
    assert result.changed_tags == []
    assert quote.manual_tags == ["keep", "remove"]


def test_deletion_keyword_matching_includes_manual_and_auto_tags():
    quote = SimpleNamespace(
        ocr_text="",
        recorded_text="",
        tags=["自动标签"],
        manual_tags=["手动标签"],
    )

    assert QuoteService.matches_deletion_keywords(quote, ["自动标签"])
    assert QuoteService.matches_deletion_keywords(quote, ["手动标签"])
    assert not QuoteService.matches_deletion_keywords(quote, ["不存在"])


@pytest.mark.asyncio
async def test_private_quote_query_sends_group_only_guidance(
    monkeypatch: pytest.MonkeyPatch,
):
    sent_messages: list[object] = []

    class _FakeMessage:
        def __init__(self, payload):
            self.payload = payload

        async def send(self, target=None, bot=None):
            sent_messages.append(self.payload)

    monkeypatch.setattr(
        query_commands.MessageUtils,
        "build_message",
        lambda payload: _FakeMessage(payload),
    )
    event = SimpleNamespace(get_session_id=lambda: "private_10001")

    await query_commands.record_pool_handle(
        SimpleNamespace(), event, SimpleNamespace(), {}
    )

    assert sent_messages == ["请在群聊中使用语录查询。"]


@pytest.mark.asyncio
async def test_referenced_image_cleanup_keeps_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    image_path = tmp_path / "quote.png"
    image_path.write_bytes(b"image")

    class _ReferencedQuery:
        async def exists(self):
            return True

    monkeypatch.setattr(
        quote_service_module.Quote, "filter", lambda **kwargs: _ReferencedQuery()
    )

    removed = await QuoteService.delete_image_if_unreferenced(image_path)

    assert removed is False
    assert image_path.read_bytes() == b"image"


@pytest.mark.asyncio
async def test_read_local_upload_rejects_file_over_size_limit(tmp_path: Path):
    image_path = tmp_path / "large.png"
    image_path.write_bytes(b"123456")

    with pytest.raises(upload_commands.ImageProcessError, match="5 字节"):
        await upload_commands._read_local_upload_image(image_path, max_bytes=5)


def test_validate_raw_upload_accepts_content_at_size_limit():
    upload_commands._validate_raw_upload_size(b"12345", max_bytes=5, max_size_mb=1)


def test_validate_raw_upload_rejects_content_over_size_limit():
    with pytest.raises(upload_commands.ImageProcessError, match="最大 1 MB"):
        upload_commands._validate_raw_upload_size(
            b"123456",
            max_bytes=5,
            max_size_mb=1,
        )


@pytest.mark.asyncio
async def test_write_quote_image_tracks_file_creation_ownership(tmp_path: Path):
    image_path = tmp_path / "quote.png"

    first_created = await upload_commands._write_quote_image_if_absent(
        image_path,
        b"first",
    )
    second_created = await upload_commands._write_quote_image_if_absent(
        image_path,
        b"second",
    )

    assert first_created is True
    assert second_created is False
    assert image_path.read_bytes() == b"first"


@pytest.mark.asyncio
async def test_concurrent_save_failure_does_not_delete_successful_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    image_path = tmp_path / "quote.png"
    first_save_started = asyncio.Event()
    allow_first_save = asyncio.Event()
    add_call_count = 0

    async def _fake_add_quote(**kwargs):
        nonlocal add_call_count
        call_index = add_call_count
        add_call_count += 1
        if call_index == 0:
            first_save_started.set()
            await allow_first_save.wait()
            return None, True
        return SimpleNamespace(id=18), True

    async def _fake_delete_if_unreferenced(path):
        Path(path).unlink(missing_ok=True)
        return True

    monkeypatch.setattr(
        upload_commands.QuoteService,
        "add_quote",
        _fake_add_quote,
    )
    monkeypatch.setattr(
        upload_commands.QuoteService,
        "delete_image_if_unreferenced",
        _fake_delete_if_unreferenced,
    )

    first_task = asyncio.create_task(
        upload_commands._save_quote_image_and_record(
            image_path,
            b"image",
            group_id="123",
        )
    )
    await first_save_started.wait()
    second_task = asyncio.create_task(
        upload_commands._save_quote_image_and_record(
            image_path,
            b"image",
            group_id="123",
        )
    )
    await asyncio.sleep(0)
    allow_first_save.set()

    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result == (None, True)
    assert second_result[0].id == 18
    assert image_path.read_bytes() == b"image"


@pytest.mark.asyncio
async def test_same_perceptual_hash_serializes_different_file_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    active_saves = 0
    max_active_saves = 0
    add_call_count = 0
    existing_quote = SimpleNamespace(id=19)

    async def _fake_add_quote(**kwargs):
        nonlocal active_saves, max_active_saves, add_call_count
        call_index = add_call_count
        add_call_count += 1
        active_saves += 1
        max_active_saves = max(max_active_saves, active_saves)
        await asyncio.sleep(0.01)
        active_saves -= 1
        return existing_quote, call_index == 0

    async def _fake_delete_if_unreferenced(path):
        Path(path).unlink(missing_ok=True)
        return True

    monkeypatch.setattr(upload_commands.QuoteService, "add_quote", _fake_add_quote)
    monkeypatch.setattr(
        upload_commands.QuoteService,
        "delete_image_if_unreferenced",
        _fake_delete_if_unreferenced,
    )

    await asyncio.gather(
        upload_commands._save_quote_image_and_record(
            first_path,
            b"first",
            group_id="123",
            image_hash="same-hash",
        ),
        upload_commands._save_quote_image_and_record(
            second_path,
            b"second",
            group_id="123",
            image_hash="same-hash",
        ),
    )

    assert max_active_saves == 1
    assert sum(path.exists() for path in (first_path, second_path)) == 1


@pytest.mark.asyncio
async def test_stream_download_accepts_content_at_size_limit(tmp_path: Path):
    payload = b"12345"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=payload, request=request)
        )
    )
    target = tmp_path / "image.bin"

    try:
        success = await upload_commands.AsyncHttpx.download_file(
            "https://example.com/image.bin",
            target,
            stream=True,
            max_bytes=len(payload),
            client=client,
        )
    finally:
        await client.aclose()

    assert success is True
    assert target.read_bytes() == payload


@pytest.mark.asyncio
async def test_stream_download_rejects_content_over_size_limit(tmp_path: Path):
    payload = b"123456"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=payload, request=request)
        )
    )
    target = tmp_path / "image.bin"

    try:
        success = await upload_commands.AsyncHttpx.download_file(
            "https://example.com/image.bin",
            target,
            stream=True,
            max_bytes=len(payload) - 1,
            client=client,
        )
    finally:
        await client.aclose()

    assert success is False
    assert not target.exists()


@pytest.mark.asyncio
async def test_stream_download_rejects_chunked_content_without_length(
    tmp_path: Path,
):
    class _ChunkedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"123"
            yield b"456"

    def _handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ChunkedStream(), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handle_request))
    target = tmp_path / "image.bin"

    try:
        success = await upload_commands.AsyncHttpx.download_file(
            "https://example.com/image.bin",
            target,
            stream=True,
            max_bytes=5,
            client=client,
        )
    finally:
        await client.aclose()

    assert success is False
    assert not target.exists()
