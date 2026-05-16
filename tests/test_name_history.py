from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
import pytest


def _load_logic_module():
    # 直接加载纯逻辑文件，避免测试导入插件入口时触发 NoneBot 注册流程。
    module_path = (
        Path(__file__).resolve().parents[1]
        / "zhenxun"
        / "plugins"
        / "name_history"
        / "_logic.py"
    )
    spec = importlib.util.spec_from_file_location(
        "name_history_logic_test", module_path
    )
    if not spec or not spec.loader:
        raise RuntimeError("无法加载历史昵称逻辑模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


logic = _load_logic_module()


def _load_plugin_tree() -> ast.Module:
    source = (
        Path(__file__).resolve().parents[1]
        / "zhenxun"
        / "plugins"
        / "name_history"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    return ast.parse(source)


def _literal_string_set(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Set):
        return set()
    return {item.value for item in node.elts if isinstance(item, ast.Constant)}


class FakeRepository:
    def __init__(self):
        self.latest: dict[str, str] = {}
        self.created: list[dict[str, Any]] = []

    async def get_latest_display_name(
        self,
        *,
        platform: str,
        group_id: str,
        user_id: str,
        name_type: str,
    ) -> str | None:
        # 测试只关心同一用户的名称类型变化，其他定位参数由调用断言覆盖。
        assert platform == "qq"
        assert group_id == "1000"
        assert user_id == "2000"
        return self.latest.get(name_type)

    async def create_history(
        self,
        *,
        platform: str,
        group_id: str,
        user_id: str,
        name_type: str,
        display_name: str,
    ) -> None:
        # 写入后同步 latest，模拟数据库下一次查询看到刚创建的记录。
        self.latest[name_type] = display_name
        self.created.append(
            {
                "platform": platform,
                "group_id": group_id,
                "user_id": user_id,
                "name_type": name_type,
                "display_name": display_name,
            }
        )


@dataclass(slots=True)
class Record:
    name_type: str
    display_name: str
    record_time: datetime


def test_choose_history_display_name_handles_group_card_empty_rules():
    assert logic.choose_history_display_name(logic.GROUP_CARD, None, None) is None
    assert logic.choose_history_display_name(logic.GROUP_CARD, "小明", None) == "小明"
    assert logic.choose_history_display_name(logic.GROUP_CARD, "小明", "小明") is None
    assert (
        logic.choose_history_display_name(logic.GROUP_CARD, None, "小明")
        == logic.GROUP_CARD_EMPTY_LABEL
    )
    assert (
        logic.choose_history_display_name(
            logic.GROUP_CARD, None, logic.GROUP_CARD_EMPTY_LABEL
        )
        is None
    )


def test_normalize_display_name_flattens_line_breaks():
    assert logic.normalize_display_name(" 小明\n今天\t想睡觉 ") == "小明 今天 想睡觉"


def test_build_group_info_defaults_does_not_override_custom_nickname():
    defaults = logic.build_group_info_defaults(
        logic.SenderNameSnapshot(group_card="群名片", qq_name="QQ名"),
        "qq",
    )

    assert defaults == {"platform": "qq", "user_name": "QQ名"}
    assert "nickname" not in defaults


@pytest.mark.asyncio
async def test_record_sender_snapshot_records_two_name_types_independently():
    repo = FakeRepository()
    snapshot = logic.SenderNameSnapshot(group_card="群名片A", qq_name="QQ名A")

    created = await logic.record_sender_snapshot(
        repo,
        platform="qq",
        group_id="1000",
        user_id="2000",
        snapshot=snapshot,
    )

    assert created == 2
    assert [(item["name_type"], item["display_name"]) for item in repo.created] == [
        (logic.GROUP_CARD, "群名片A"),
        (logic.QQ_NAME, "QQ名A"),
    ]


@pytest.mark.asyncio
async def test_record_sender_snapshot_skips_unchanged_names_and_keeps_a_b_a_chain():
    repo = FakeRepository()
    first = logic.SenderNameSnapshot(group_card="A", qq_name="QQ")
    second = logic.SenderNameSnapshot(group_card="B", qq_name="QQ")
    third = logic.SenderNameSnapshot(group_card="A", qq_name="QQ")

    assert (
        await logic.record_sender_snapshot(
            repo, platform="qq", group_id="1000", user_id="2000", snapshot=first
        )
        == 2
    )
    assert (
        await logic.record_sender_snapshot(
            repo, platform="qq", group_id="1000", user_id="2000", snapshot=first
        )
        == 0
    )
    assert (
        await logic.record_sender_snapshot(
            repo, platform="qq", group_id="1000", user_id="2000", snapshot=second
        )
        == 1
    )
    assert (
        await logic.record_sender_snapshot(
            repo, platform="qq", group_id="1000", user_id="2000", snapshot=third
        )
        == 1
    )

    assert [(item["name_type"], item["display_name"]) for item in repo.created] == [
        (logic.GROUP_CARD, "A"),
        (logic.QQ_NAME, "QQ"),
        (logic.GROUP_CARD, "B"),
        (logic.GROUP_CARD, "A"),
    ]


@pytest.mark.asyncio
async def test_record_sender_snapshot_records_group_card_becoming_empty():
    repo = FakeRepository()

    assert (
        await logic.record_sender_snapshot(
            repo,
            platform="qq",
            group_id="1000",
            user_id="2000",
            snapshot=logic.SenderNameSnapshot(group_card=None, qq_name=None),
        )
        == 0
    )
    assert (
        await logic.record_sender_snapshot(
            repo,
            platform="qq",
            group_id="1000",
            user_id="2000",
            snapshot=logic.SenderNameSnapshot(group_card="有名片", qq_name=None),
        )
        == 1
    )
    assert (
        await logic.record_sender_snapshot(
            repo,
            platform="qq",
            group_id="1000",
            user_id="2000",
            snapshot=logic.SenderNameSnapshot(group_card=None, qq_name=None),
        )
        == 1
    )

    assert repo.created[-1]["display_name"] == logic.GROUP_CARD_EMPTY_LABEL


def test_format_history_records_splits_types_and_marks_latest_per_type_as_current():
    now = datetime(2026, 5, 14, 21, 3)
    records = [
        Record(logic.QQ_NAME, "QQ旧名", now - timedelta(days=2)),
        Record(logic.GROUP_CARD, "群名片旧名", now - timedelta(days=1)),
        Record(logic.QQ_NAME, "QQ当前名", now),
        Record(logic.GROUP_CARD, "群名片当前名", now - timedelta(hours=1)),
    ]

    result = logic.format_history_records(records, target_user_id="2000")

    assert "2000 的历史昵称" in result
    assert "只记录我看到过的变化" not in result
    assert "\n群名片\n" in result
    assert "\nQQ名称\n" in result
    assert result.index("\n群名片\n") < result.index("\nQQ名称\n")
    assert result.index("群名片当前名") < result.index("QQ当前名")
    assert "05-14 20:03  「群名片当前名」（当前）" in result
    assert "05-14 21:03  「QQ当前名」（当前）" in result
    assert "出现次数" not in result


def test_format_history_records_can_filter_group_card_only():
    now = datetime(2026, 5, 14, 21, 3)
    records = [
        Record(logic.QQ_NAME, "QQ名", now),
        Record(logic.GROUP_CARD, "群名片", now - timedelta(minutes=1)),
    ]

    result = logic.format_history_records(
        records,
        target_user_id="2000",
        name_type=logic.GROUP_CARD,
    )

    assert "2000 的历史群名片" in result
    assert "\n群名片\n" in result
    assert "05-14 21:02  「群名片」（当前）" in result
    assert "QQ名" not in result


def test_build_history_display_data_keeps_empty_sections_user_friendly():
    now = datetime(2026, 5, 14, 21, 3)
    records = [Record(logic.QQ_NAME, "QQ名", now)]

    data = logic.build_history_display_data(records, target_user_id="2000")

    assert data.title == "2000 的历史昵称"
    assert data.profile_name == "QQ名"
    assert [section.label for section in data.sections] == ["群名片", "QQ名称"]
    assert data.sections[0].items == []
    assert data.sections[1].items[0].display_name == "QQ名"
    assert data.sections[1].items[0].is_current
    assert data.empty_text == "暂无记录。之后我看到名称变化时会慢慢记下来。"


def test_build_history_display_data_profile_name_prefers_current_group_card():
    now = datetime(2026, 5, 14, 21, 3)
    records = [
        Record(logic.QQ_NAME, "QQ当前名", now),
        Record(logic.GROUP_CARD, "群名片当前名", now - timedelta(minutes=1)),
    ]

    data = logic.build_history_display_data(records, target_user_id="2000")

    assert data.profile_name == "群名片当前名"


def test_format_history_records_empty_single_filter_keeps_compact_text():
    result = logic.format_history_records(
        [],
        target_user_id="2000",
        name_type=logic.QQ_NAME,
    )

    assert result == "2000 的历史QQ名称\n暂无记录。之后我看到名称变化时会慢慢记下来。"


def test_plugin_metadata_declares_all_public_aliases():
    tree = _load_plugin_tree()
    alias_sets: dict[str, set[str]] = {}
    plugin_extra_aliases = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.endswith("_ALIASES"):
                alias_sets[target.id] = _literal_string_set(node.value)
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "PluginExtraData":
            continue
        plugin_extra_aliases = any(
            keyword.arg == "aliases" for keyword in node.keywords
        )

    expected_aliases = {
        "曾用名",
        "过去昵称",
        "nickname",
        "群名片历史",
        "历史qq名",
        "QQ名历史",
        "qq名历史",
    }
    declared_aliases = set().union(*alias_sets.values())

    assert expected_aliases <= declared_aliases
    assert plugin_extra_aliases


def test_plugin_logger_calls_only_use_supported_context_keywords():
    tree = _load_plugin_tree()
    supported_keywords = {"session", "group_id", "adapter", "target", "platform", "e"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "logger"
        ):
            continue
        for keyword in node.keywords:
            assert keyword.arg in supported_keywords


def test_plugin_entry_declares_htmlrender_and_avatar_dependencies():
    tree = _load_plugin_tree()
    required_plugins: set[str] = set()
    imported_names: set[str] = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            required_plugins.add(node.args[0].value)
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)

    assert "nonebot_plugin_htmlrender" in required_plugins
    assert "template_to_pic" in imported_names
    assert "avatar_service" in imported_names


def test_resolve_query_target_supports_self_at_and_qq_number():
    assert logic.resolve_query_target(Message(""), "100").user_id == "100"
    assert (
        logic.resolve_query_target(
            Message([MessageSegment.at("200"), MessageSegment.text(" 其他")]),
            "100",
        ).user_id
        == "200"
    )
    assert logic.resolve_query_target(Message("300"), "100").user_id == "300"

    invalid = logic.resolve_query_target(Message("张三"), "100")
    assert invalid.user_id == "100"
    assert invalid.error
