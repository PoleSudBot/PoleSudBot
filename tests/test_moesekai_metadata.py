from __future__ import annotations

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai import __plugin_meta__


def test_metadata_does_not_register_rate_limits():
    assert not __plugin_meta__.extra.get("limits")


def test_usage_contains_markdown_sections_and_examples():
    usage = __plugin_meta__.usage or ""
    assert "## 🌟 快速开始" in usage
    assert "## 👤 档案相关" in usage
    assert "`个人档案`" in usage
    assert "sk预测" not in usage
    assert "ycx" not in usage
    assert "活动组卡" not in usage
    assert "烤森组卡" not in usage
    assert "最强组卡" not in usage
    assert "挑战组卡" not in usage
    assert "`组卡 绿 vbs`" not in usage
    assert "`四格351`" in usage
    assert "`活动剧情 强制刷新 199`" in usage
    assert "倍率计算" not in usage
    assert "`倍率 150 130 120 115 100`" not in usage
    assert "角色别名" not in usage
    assert "歌曲别名" not in usage
    assert "`cnpjsk update`" in usage
    assert "`新卡上线提醒 <开启|关闭|状态> [区服]`" in usage
