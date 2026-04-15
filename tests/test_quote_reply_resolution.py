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

quote_service_module = _load_module(
    f"{PLUGIN_PACKAGE}.services.quote_service",
    PLUGIN_ROOT / "services" / "quote_service.py",
)
manage_commands = _load_module(
    f"{PLUGIN_PACKAGE}.command.manage_commands",
    PLUGIN_ROOT / "command" / "manage_commands.py",
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
