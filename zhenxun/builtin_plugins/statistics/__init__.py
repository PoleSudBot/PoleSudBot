import os
from pathlib import Path

import nonebot
import ujson as json

from zhenxun.configs.path_config import DATA_PATH

nonebot.load_plugins(str(Path(__file__).parent.resolve()))

old_file1 = DATA_PATH / "_prefix_count.json"
old_file2 = DATA_PATH / "_prefix_user_count.json"
new_path = DATA_PATH / "statistics"
new_path.mkdir(parents=True, exist_ok=True)
if old_file1.exists():
    os.rename(old_file1, new_path / "_prefix_count.json")
if old_file2.exists():
    os.rename(old_file2, new_path / "_prefix_user_count.json")


# 修改旧数据

statistics_group_file = DATA_PATH / "statistics" / "_prefix_count.json"
statistics_user_file = DATA_PATH / "statistics" / "_prefix_user_count.json"


def _merge_count(data: dict, old_key: str, new_key: str) -> None:
    """把旧统计键合并到新统计键"""
    if data.get(old_key) is None:
        return
    data[new_key] = data.get(new_key, 0) + data[old_key]
    del data[old_key]


def _drop_count(data: dict, key: str) -> None:
    """删除已经下线的旧统计键"""
    if data.get(key) is not None:
        del data[key]


def _migrate_plugin_counts(data: dict) -> None:
    """迁移历史插件统计键，避免旧命令名称继续污染新插件统计"""
    _merge_count(data, "ai", "Ai")
    _merge_count(data, "抽卡", "游戏抽卡")
    _merge_count(data, "我的金币", "钱包")
    for key in ("我的道具", "使用道具", "购买", "商店"):
        _drop_count(data, key)


for file in [statistics_group_file, statistics_user_file]:
    if file.exists():
        with open(file, encoding="utf8") as f:
            data = json.load(f)
            if not (statistics_group_file.parent / f"{file}.bak").exists():
                with open(f"{file}.bak", "w", encoding="utf8") as wf:
                    json.dump(data, wf, ensure_ascii=False, indent=4)
            for x in ["total_statistics", "day_statistics"]:
                for key in data[x].keys():
                    _migrate_plugin_counts(data[x][key])
            for x in ["week_statistics", "month_statistics"]:
                for key in data[x].keys():
                    if key == "total":
                        _migrate_plugin_counts(data[x][key])
                    else:
                        for day in data[x][key].keys():
                            _migrate_plugin_counts(data[x][key][day])
        with open(file, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
