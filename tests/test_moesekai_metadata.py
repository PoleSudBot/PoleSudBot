from __future__ import annotations

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai import __plugin_meta__


def test_usage_contains_markdown_sections_and_examples():
    usage = __plugin_meta__.usage or ""
    assert "## 🌟 快速开始" in usage
    assert "## 👤 档案相关" in usage
    assert "## 📈 榜线与预测" in usage
    assert "`个人档案`" in usage
    assert "`ycx 166`" in usage
    assert "`活动组卡 195 226 hd multi`" in usage
    assert "`四格 351`" in usage
    assert "`活动剧情 强制刷新 199`" in usage
    assert "`倍率计算 150 130 120 115 100`" in usage
    assert "`cnpjsk update`" in usage
    assert "`新卡上线提醒 <开启|关闭|状态> [区服]`" in usage
