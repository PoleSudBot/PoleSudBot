from __future__ import annotations

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai import __plugin_meta__


def test_usage_contains_markdown_sections_and_examples():
    usage = __plugin_meta__.usage or ""
    assert "## 🌟 快速开始" in usage
    assert "## 👤 档案相关" in usage
    assert "## 📈 榜线与预测" in usage
    assert "## 🃏 组卡相关" in usage
    assert "`个人档案`" in usage
    assert "`ycx166`" in usage
    assert "`活动组卡 195 Tell Your World hd multi`" in usage
    assert "`烤森组卡 201`" in usage
    assert "`最强组卡 Tell Your World 实效`" in usage
    assert "`挑战组卡 初音未来 Tell Your World`" in usage
    assert "`组卡 绿 vbs`" not in usage
    assert "`四格351`" in usage
    assert "`活动剧情 强制刷新 199`" in usage
    assert "`倍率 150 130 120 115 100`" in usage
    assert "`cnpjsk update`" in usage
    assert "`新卡上线提醒 <开启|关闭|状态> [区服]`" in usage
