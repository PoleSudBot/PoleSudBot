import ast
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).parents[1]
MY_INFO_PATH = ROOT / "zhenxun/builtin_plugins/info/my_info.py"
DASHBOARD_DATA_SOURCE_PATH = (
    ROOT / "zhenxun/builtin_plugins/web_ui/api/tabs/dashboard/data_source.py"
)
MAIN_DATA_SOURCE_PATH = (
    ROOT / "zhenxun/builtin_plugins/web_ui/api/tabs/main/data_source.py"
)


def _parse(path: Path) -> ast.Module:
    """解析消费者源码，避免加载 WebUI 插件时触发无关运行时依赖。"""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _find_async_function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    """按名称定位模块或类中的异步消费者函数。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"未找到异步函数: {name}")


def _has_inbound_chat_history_filter(function: ast.AsyncFunctionDef) -> bool:
    """检查函数是否从 ChatHistory 的入站查询开始构造统计。"""
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "filter" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "ChatHistory":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "direction"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "in"
            ):
                return True
    return False


class _FakeActivityQuery:
    filters: ClassVar[list[dict]] = []

    @classmethod
    def filter(cls, **kwargs):
        cls.filters.append(kwargs)
        return cls()

    def annotate(self, **_kwargs):
        return self

    def group_by(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    async def values_list(self, *_args, **_kwargs):
        return ["1"]


def _load_activity_rank_function():
    """只编译排行函数本身，以便行为验证而不加载整个 info 插件。"""
    function = _find_async_function(_parse(MY_INFO_PATH), "get_activity_rank")
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"Count": lambda _field: object(), "datetime": datetime}
    exec(compile(module, str(MY_INFO_PATH), "exec"), namespace)
    return namespace["get_activity_rank"]


@pytest.mark.asyncio
async def test_activity_rank_only_adds_direction_for_chat_history() -> None:
    get_activity_rank = _load_activity_rank_function()
    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 8)
    _FakeActivityQuery.filters = []

    await get_activity_rank(
        _FakeActivityQuery,
        "1",
        "123",
        start,
        end,
        direction="in",
    )
    await get_activity_rank(_FakeActivityQuery, "1", "123", start, end)

    assert _FakeActivityQuery.filters[0]["direction"] == "in"
    assert "direction" not in _FakeActivityQuery.filters[1]


def test_my_info_passes_inbound_direction_only_to_chat_rank() -> None:
    function = _find_async_function(_parse(MY_INFO_PATH), "get_user_info")
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_activity_rank"
    ]

    chat_call = next(
        call for call in calls if ast.unparse(call.args[0]) == "ChatHistory"
    )
    stat_call = next(
        call for call in calls if ast.unparse(call.args[0]) == "Statistics"
    )

    assert any(
        keyword.arg == "direction"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "in"
        for keyword in chat_call.keywords
    )
    assert all(keyword.arg != "direction" for keyword in stat_call.keywords)


@pytest.mark.parametrize(
    ("path", "function_name"),
    [
        (DASHBOARD_DATA_SOURCE_PATH, "get_chat_and_call_month"),
        (MAIN_DATA_SOURCE_PATH, "get_all_chat_count"),
        (MAIN_DATA_SOURCE_PATH, "get_active_group"),
    ],
)
def test_webui_chat_statistics_start_from_inbound_history(
    path: Path,
    function_name: str,
) -> None:
    function = _find_async_function(_parse(path), function_name)

    assert _has_inbound_chat_history_filter(function)
