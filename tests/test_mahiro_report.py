from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sys
import types

import pytest

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1] / "zhenxun" / "plugins" / "mahiro_report"
)


def _make_package(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []  # type: ignore[attr-defined]
    return module


def _load_mahiro_modules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    config_values: dict[str, str] | None = None,
):
    config_values = {key.upper(): value for key, value in (config_values or {}).items()}

    async def fake_render(*_args, **_kwargs) -> bytes:
        return b"rendered"

    class DummyLogger:
        def info(self, *_args, **_kwargs):
            return None

        def warning(self, *_args, **_kwargs):
            return None

        def error(self, *_args, **_kwargs):
            return None

    config_manager = types.SimpleNamespace(
        get_config=lambda _module, key, default=None: config_values.get(
            key.upper(), default
        )
    )

    zhenxun_module = _make_package("zhenxun")
    zhenxun_module.ui = types.SimpleNamespace(
        template=lambda *_args, **_kwargs: object(),
        render=fake_render,
    )

    monkeypatch.setitem(sys.modules, "zhenxun", zhenxun_module)
    monkeypatch.setitem(
        sys.modules, "zhenxun.configs", _make_package("zhenxun.configs")
    )
    monkeypatch.setitem(
        sys.modules, "zhenxun.services", _make_package("zhenxun.services")
    )
    monkeypatch.setitem(sys.modules, "zhenxun.utils", _make_package("zhenxun.utils"))
    monkeypatch.setitem(
        sys.modules, "zhenxun.plugins", _make_package("zhenxun.plugins")
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.plugins.mahiro_report",
        _make_package("zhenxun.plugins.mahiro_report"),
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.configs.config",
        types.SimpleNamespace(Config=config_manager),
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.configs.path_config",
        types.SimpleNamespace(DATA_PATH=tmp_path),
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.services.log",
        types.SimpleNamespace(logger=DummyLogger()),
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.utils.http_utils",
        types.SimpleNamespace(AsyncHttpx=types.SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.plugins.mahiro_report.date",
        types.SimpleNamespace(get_festivals_dates=lambda _today=None: []),
    )

    def load_module(module_name: str, file_path: Path):
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)
        return module

    config_module = load_module(
        "zhenxun.plugins.mahiro_report.config",
        PLUGIN_DIR / "config.py",
    )
    data_source_module = load_module(
        "zhenxun.plugins.mahiro_report.data_source",
        PLUGIN_DIR / "data_source.py",
    )
    return config_module, data_source_module.Report


def test_get_configured_schedule_time_parses_valid_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, _ = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00"},
    )

    assert config_module.get_fetch_time() == (6, 0)


def test_get_configured_schedule_time_falls_back_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, _ = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"SEND_TIME": "25:99"},
    )

    assert config_module.get_send_time() == (9, 1)


def test_get_send_time_falls_back_when_earlier_than_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, _ = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00", "SEND_TIME": "05:59"},
    )

    assert config_module.get_send_time() == (9, 1)


def test_get_send_time_uses_fetch_time_when_default_send_is_still_earlier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, _ = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "10:00", "SEND_TIME": "05:59"},
    )

    assert config_module.get_send_time() == (10, 0)


@pytest.mark.asyncio
async def test_get_report_image_before_fetch_reuses_previous_day_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, report_class = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00"},
    )
    fixed_now = datetime(2026, 4, 7, 5, 59)
    report_file = config_module.REPORT_PATH / "2026-04-06.png"
    report_file.write_bytes(b"yesterday-cache")

    render_calls = {"count": 0}

    async def fake_render(_report_time: datetime) -> bytes:
        render_calls["count"] += 1
        return b"should-not-render"

    monkeypatch.setattr(report_class, "_now", lambda: fixed_now)
    monkeypatch.setattr(report_class, "_render_report_image", fake_render)

    result = await report_class.get_report_image()

    assert report_class.get_visible_report_file(fixed_now) == report_file
    assert result == report_file
    assert render_calls["count"] == 0
    assert report_file.read_bytes() == b"yesterday-cache"


@pytest.mark.asyncio
async def test_get_report_image_before_fetch_falls_back_to_today_when_previous_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, report_class = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00"},
    )
    fixed_now = datetime(2026, 4, 7, 5, 59)
    report_file = config_module.REPORT_PATH / "2026-04-07.png"
    render_dates: list[datetime] = []

    async def fake_render(report_time: datetime) -> bytes:
        render_dates.append(report_time)
        return b"today-cache"

    monkeypatch.setattr(report_class, "_now", lambda: fixed_now)
    monkeypatch.setattr(report_class, "_render_report_image", fake_render)

    result = await report_class.get_report_image()

    assert report_class.get_visible_report_file(fixed_now) == report_file
    assert result == report_file
    assert report_file.read_bytes() == b"today-cache"
    assert render_dates == [datetime(2026, 4, 7, 5, 59)]


@pytest.mark.asyncio
async def test_get_report_image_refreshes_stale_cache_after_fetch_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, report_class = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00"},
    )
    fixed_now = datetime(2026, 4, 7, 9, 1)
    report_file = config_module.REPORT_PATH / f"{fixed_now.date()}.png"
    report_file.write_bytes(b"old-cache")
    stale_mtime = datetime(2026, 4, 7, 1, 0).timestamp()
    os.utime(report_file, (stale_mtime, stale_mtime))

    render_times: list[datetime] = []

    async def fake_render(report_time: datetime) -> bytes:
        render_times.append(report_time)
        return b"fresh-cache"

    monkeypatch.setattr(report_class, "_now", lambda: fixed_now)
    monkeypatch.setattr(report_class, "_render_report_image", fake_render)

    result = await report_class.get_report_image()

    assert result == report_file
    assert render_times == [datetime(2026, 4, 7, 9, 1)]
    assert report_file.read_bytes() == b"fresh-cache"


@pytest.mark.asyncio
async def test_get_report_image_reuses_fresh_cache_after_fetch_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, report_class = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00"},
    )
    fixed_now = datetime(2026, 4, 7, 9, 1)
    report_file = config_module.REPORT_PATH / f"{fixed_now.date()}.png"
    report_file.write_bytes(b"fresh-cache")
    fresh_mtime = datetime(2026, 4, 7, 6, 30).timestamp()
    os.utime(report_file, (fresh_mtime, fresh_mtime))

    render_calls = {"count": 0}

    async def fake_render(_report_time: datetime) -> bytes:
        render_calls["count"] += 1
        return b"should-not-render"

    monkeypatch.setattr(report_class, "_now", lambda: fixed_now)
    monkeypatch.setattr(report_class, "_render_report_image", fake_render)

    result = await report_class.get_report_image()

    assert result == report_file
    assert render_calls["count"] == 0
    assert report_file.read_bytes() == b"fresh-cache"


@pytest.mark.asyncio
async def test_get_report_image_force_refresh_ignores_existing_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_module, report_class = _load_mahiro_modules(
        monkeypatch,
        tmp_path,
        config_values={"FETCH_TIME": "06:00"},
    )
    fixed_now = datetime(2026, 4, 7, 9, 1)
    report_file = config_module.REPORT_PATH / f"{fixed_now.date()}.png"
    report_file.write_bytes(b"fresh-cache")
    fresh_mtime = datetime(2026, 4, 7, 7, 0).timestamp()
    os.utime(report_file, (fresh_mtime, fresh_mtime))

    render_times: list[datetime] = []

    async def fake_render(report_time: datetime) -> bytes:
        render_times.append(report_time)
        return b"force-refreshed"

    monkeypatch.setattr(report_class, "_now", lambda: fixed_now)
    monkeypatch.setattr(report_class, "_render_report_image", fake_render)

    result = await report_class.get_report_image(force_refresh=True)

    assert result == report_file
    assert render_times == [datetime(2026, 4, 7, 9, 1)]
    assert report_file.read_bytes() == b"force-refreshed"
