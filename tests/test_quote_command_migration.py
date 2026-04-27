from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
import sys
import types

import nonebot
PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1] / "zhenxun" / "plugins" / "zhenxun_plugin_quote"
)
PLUGIN_PACKAGE = "zhenxun.plugins.zhenxun_plugin_quote"
PLUGIN_INIT_PATH = PLUGIN_ROOT / "__init__.py"


def _ensure_nonebot_initialized() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


def _ensure_nonebot_plugin_loaded(module_name: str) -> None:
    if nonebot.get_plugin(module_name) or nonebot.get_plugin_by_module_name(module_name):
        return
    nonebot.load_plugin(module_name)


def _register_namespace_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _load_module(module_name: str, path: Path):
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib_util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_upload_commands_module():
    existing_module = sys.modules.get(
        f"{PLUGIN_PACKAGE}.command.upload_commands"
    )
    if existing_module is not None:
        return existing_module

    _ensure_nonebot_initialized()
    for plugin in [
        "nonebot_plugin_apscheduler",
        "nonebot_plugin_waiter",
        "nonebot_plugin_alconna",
        "nonebot_plugin_session",
        "nonebot_plugin_htmlrender",
        "nonebot_plugin_uninfo",
    ]:
        _ensure_nonebot_plugin_loaded(plugin)

    _register_namespace_package(PLUGIN_PACKAGE, PLUGIN_ROOT)
    _register_namespace_package(f"{PLUGIN_PACKAGE}.command", PLUGIN_ROOT / "command")
    _register_namespace_package(f"{PLUGIN_PACKAGE}.services", PLUGIN_ROOT / "services")
    _register_namespace_package(f"{PLUGIN_PACKAGE}.utils", PLUGIN_ROOT / "utils")

    _load_module(
        f"{PLUGIN_PACKAGE}.services.quote_service",
        PLUGIN_ROOT / "services" / "quote_service.py",
    )
    _load_module(
        f"{PLUGIN_PACKAGE}.command.manage_commands",
        PLUGIN_ROOT / "command" / "manage_commands.py",
    )
    _load_module(
        f"{PLUGIN_PACKAGE}.command.query_commands",
        PLUGIN_ROOT / "command" / "query_commands.py",
    )
    return _load_module(
        f"{PLUGIN_PACKAGE}.command.upload_commands",
        PLUGIN_ROOT / "command" / "upload_commands.py",
    )


upload_commands = _load_upload_commands_module()


def _extract_texts(parts) -> list[str]:
    if not parts:
        return []
    return [getattr(part, "text", str(part)) for part in parts]


def test_legacy_upload_parser_would_swallow_new_command_without_guard():
    parsed = upload_commands.upload_hint_cmd.command().parse("上传语录 南极")

    assert parsed.matched
    assert _extract_texts(parsed.query("parts")) == ["语录", "南极"]
    assert upload_commands._is_new_upload_command_text("上传语录 南极")
    assert upload_commands._is_new_upload_command_text("/上传语录南极")
    assert not upload_commands._is_new_upload_command_text("上传 南极")


def test_new_upload_command_keeps_compact_old_capability():
    parsed = upload_commands.save_img_cmd.command().parse("上传语录南极")

    assert parsed.matched
    assert _extract_texts(parsed.query("parts")) == ["南极"]


def test_upload_migration_and_success_prompt_share_new_usage_hint():
    migration_hint = upload_commands._build_upload_migration_hint_text()
    success_hint = upload_commands._build_upload_success_text()

    assert "上传语录 [图片] [tag/@用户 ...]" in migration_hint
    assert "记录语录 [tag/@用户 ...]" in migration_hint
    assert "记录（需回复消息）" in migration_hint
    assert success_hint.startswith("保存成功\n")
    assert "基础用法：" in success_hint
    assert "语录 [关键词/@用户]" in success_hint


def test_legacy_record_parser_would_treat_new_command_name_as_tag_without_guard():
    parsed = upload_commands.legacy_record_cmd.command().parse("记录语录 南极")

    assert parsed.matched
    assert _extract_texts(parsed.query("parts")) == ["语录", "南极"]
    assert upload_commands._is_new_record_command_text("记录语录 南极")
    assert upload_commands._is_new_record_command_text("/记录语录南极")
    assert not upload_commands._is_new_record_command_text("记录 南极")


def test_record_command_keeps_new_main_name_and_old_alias():
    new_parsed = upload_commands.make_record_cmd.command().parse("记录语录南极")
    legacy_parsed = upload_commands.legacy_record_cmd.command().parse("记录南极")

    assert new_parsed.matched
    assert _extract_texts(new_parsed.query("parts")) == ["南极"]
    assert legacy_parsed.matched
    assert _extract_texts(legacy_parsed.query("parts")) == ["南极"]


def test_record_success_message_keeps_image_and_usage_hint_in_one_message():
    message_parts = upload_commands._build_record_success_message(b"fake-image")

    assert message_parts[0] == b"fake-image"
    assert "保存成功" in message_parts[1]
    assert "记录语录 [tag/@用户 ...]" in message_parts[2]
    assert "语录 [关键词/@用户]" in message_parts[2]


def test_plugin_usage_text_is_updated_to_new_commands():
    usage_text = PLUGIN_INIT_PATH.read_text(encoding="utf-8")

    assert "`上传语录 [图片] [tag/@用户 ...]`" in usage_text
    assert "`上传` - 迁移提示命令" in usage_text
    assert "`记录语录 [tag/@用户 ...]`" in usage_text
    assert "记录语录aaa bbb" in usage_text
    assert "上传语录xxx" in usage_text
    assert "上传xxx" not in usage_text
