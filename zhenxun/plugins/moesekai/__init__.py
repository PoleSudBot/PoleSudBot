from __future__ import annotations

import asyncio
import contextlib
import os
import sys

import nonebot
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules

if not _TEST_MODE:
    from zhenxun.services.log import logger
    from zhenxun.utils.manager.priority_manager import PriorityLifecycle
    from zhenxun.utils.platform import PlatformUtils

    from .commands import matcher
    from .config import get_settings, refresh_settings
    from .config_migration import migrate_legacy_plugin_config
    from .constants import MODULE_NAME
    from .master_data import master_data_service
    from .services import (
        build_auto_update_notifications,
        dispatch_live_reminders,
        dispatch_new_card_notifications,
        migrate_legacy_bindings,
        seed_default_aliases,
        sync_music_aliases,
    )
else:
    matcher = None

from .config import REGISTER_CONFIGS

__plugin_meta__ = PluginMetadata(
    name="MoeSekai",
    description="Project Sekai 档案、榜线、提醒与资料助手",
    usage="""
## 🌟 快速开始
`个人档案`
查看你当前默认区服绑定的档案。
示例：`个人档案` / `cn个人档案`

`查询档案 [区服可选] <游戏ID|@用户>`
查询指定游戏 ID 或指定用户的档案。
示例：`查询档案 1234567890123` / `查询档案 @用户`

`绑定 [区服可选] <游戏ID>`
绑定游戏账号，不写区服时默认绑定 `jp`。
示例：`绑定 1234567890123` / `cn绑定 1234567890123`

## 👤 档案相关
`个人档案`
查询自己的绑定档案。

`查询档案 / 档案查询 [区服可选] <游戏ID|@用户>`
查询指定游戏 ID，或查询对方公开的绑定档案。

`给看 / 不给看`
控制别人能否通过“查询档案 @你”查看你的绑定档案。

## 🌍 区服与绑定
`绑定 [区服可选] <游戏ID>`
支持 `绑定 jp 1234567890123` 和 `jp绑定 1234567890123` 两种写法。

`解绑 [区服可选]`
不写区服时解绑当前默认区服。

`默认区服 [cn|jp|tw]`
查看或切换默认服务器。
示例：`默认区服` / `默认区服 jp`

## 📈 榜线与预测
`sk预测 [活动ID可选]`
查看预测线或结榜线，支持活动图 + 文字数据。
示例：`sk预测` / `sk预测 178`

`ycx [活动ID可选]`
查看当前活动或指定活动的榜线页。
示例：`ycx` / `ycx 166`

## 🃏 活动组卡
`活动组卡 [区服可选] [@用户可选] [活动ID可选] [歌曲ID可选] [难度可选] [模式可选]`
生成活动推荐组卡，未填写的参数会自动补默认值。
示例：`活动组卡` / `活动组卡 195 226 hd multi`

## 📚 资料查询
`活动剧情 <活动ID> [强制刷新]`
查看活动剧情总览。
示例：`活动剧情 199` / `活动剧情 强制刷新 199`

`随机四格`
随机发送一张四格漫画，并附上对应 B 站链接。

`四格 <话数>`
查询指定话数的四格漫画，并附上对应 B 站链接。
示例：`四格 351`

`倍率 <a> <b> <c> <d> <e>`
计算车头 / 内部 / 倍率 / 实效。
示例：`倍率 150 130 120 115 100`

## 🔔 提醒订阅

`订阅live提醒 [区服]`
订阅自己在本群的 live 提醒艾特。

`取消订阅live提醒 [区服]`
取消自己在本群的 live 提醒订阅。

`live提醒 <开启|关闭|状态> [区服]`
群管理员或超级用户可开关群内 live 提醒。

`新卡上线提醒 <开启|关闭|状态> [区服]`
群管理员或超级用户可开关群内新卡/表情提醒。

## 🏷️ 别名系统
`角色别名 <关键词>`
查询角色别名或角色 ID 对应的别名集合。

`角色别名 添加 [全局] <目标> <别名>`
添加角色别名；本群默认需要群管理员，全局默认需要超级用户或白名单群。

`角色别名 删除 [全局] <别名>`
删除角色别名。

`歌曲别名 <关键词>`
查询歌曲别名；优先匹配本群别名，其次全局，再回退官方曲名。

## 🛠️ 管理命令
`pjsk update [区服可选|all]`
刷新 MasterData；不写区服时优先使用默认区服。
示例：`pjsk update` / `cnpjsk update`

`pjsk blacklist <add|remove|check> <uid|qq> ...`
超级用户管理 UID / QQ 黑名单。

`pjsk 查询绑定 <uid|qq> ...`
超级用户查询 QQ 或 UID 的绑定信息。

`pjsk test live提醒 [区服可选] [live_id可选]`
管理员 / 超级用户测试 live 提醒发送效果。

`pjsk test 新卡上线提醒 [区服可选] [card_id可选...]`
管理员 / 超级用户测试新卡上线提醒合并转发效果。

## 💡 说明
- 个人档案、活动组卡、活动剧情都会尽量完整展示内容。
- 未指定区服时，优先使用你的默认区服。
- pjsk update all 仅超级用户可用。
- 难度支持 ez / nm / hd / ex / ma / apd，模式支持 multi / solo / auto / cheerful 及常见中文别名。
""".strip(),
    extra={
        "author": "k1yuyu",
        "version": "3.0.0",
        "menu_type": "游戏相关",
        "configs": [
            c.model_dump() if hasattr(c, "model_dump") else c.dict()
            for c in REGISTER_CONFIGS
        ],
        "commands": [
            {"command": "个人档案"},
            {"command": "查询档案 [区服可选] <游戏ID|@用户>"},
            {"command": "绑定 [区服可选] <游戏ID>"},
            {"command": "解绑 [区服可选]"},
            {"command": "默认区服 [cn|jp|tw]"},
            {"command": "给看 / 不给看"},
            {"command": "sk预测 [活动ID可选]"},
            {"command": "ycx [活动ID可选]"},
            {"command": "活动组卡 [@用户可选] [活动ID可选]"},
            {"command": "活动剧情 <活动ID> [强制刷新]"},
            {"command": "随机四格"},
            {"command": "四格 <话数>"},
            {"command": "倍率计算 <a> <b> <c> <d> <e>"},
            {"command": "live提醒 <开启|关闭|状态> [区服]"},
            {"command": "新卡上线提醒 <开启|关闭|状态> [区服]"},
            {"command": "订阅live提醒 / 取消订阅live提醒 [区服]"},
            {"command": "角色别名 / 歌曲别名"},
            {"command": "pjsk update [区服可选|all]"},
        ],
        "superuser_help": """
pjsk update [区服可选|all]
pjsk blacklist <add|remove|check> <uid|qq> ...
pjsk 查询绑定 <uid|qq> ...
pjsk test live提醒 [区服] [live_id]
pjsk test 新卡上线提醒 [区服] [card_id...]
""".strip(),
        "limits": [{"cd": 3, "result": "操作太快啦，请 {cd} 秒后再试~"}],
    },
)

if not _TEST_MODE:
    driver = nonebot.get_driver()
    _auto_update_task: asyncio.Task | None = None
    _live_reminder_task: asyncio.Task | None = None
    _alias_sync_task: asyncio.Task | None = None

    @PriorityLifecycle.on_startup(priority=2)
    async def _bootstrap_moesekai() -> None:
        if migrate_legacy_plugin_config():
            refresh_settings()
        else:
            get_settings()
        await migrate_legacy_bindings()
        await seed_default_aliases()

    async def _auto_update_loop(bot: Bot) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                results = await master_data_service.probe_next_updates()
                if get_settings().master_notify_superusers:
                    for message in await build_auto_update_notifications(results):
                        await PlatformUtils.send_superuser(bot, message)
                await dispatch_new_card_notifications(bot, results)
            except Exception as exc:
                logger.error("MoeSekai 自动更新循环失败", MODULE_NAME, e=exc)
            await asyncio.sleep(master_data_service.get_probe_interval_seconds())

    async def _live_reminder_loop(bot: Bot) -> None:
        await asyncio.sleep(15)
        while True:
            try:
                await dispatch_live_reminders(bot)
            except Exception as exc:
                logger.error("MoeSekai Live 提醒循环失败", MODULE_NAME, e=exc)
            await asyncio.sleep(60)

    async def _alias_sync_loop() -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await sync_music_aliases(force=False)
            except Exception as exc:
                logger.error("MoeSekai 歌曲别名同步失败", MODULE_NAME, e=exc)
            await asyncio.sleep(get_settings().alias_sync_interval_seconds)

    @driver.on_bot_connect
    async def _start_background_tasks(bot: Bot) -> None:
        global _auto_update_task, _live_reminder_task, _alias_sync_task
        if _auto_update_task is None or _auto_update_task.done():
            _auto_update_task = asyncio.create_task(_auto_update_loop(bot))
        if _live_reminder_task is None or _live_reminder_task.done():
            _live_reminder_task = asyncio.create_task(_live_reminder_loop(bot))
        if _alias_sync_task is None or _alias_sync_task.done():
            _alias_sync_task = asyncio.create_task(_alias_sync_loop())

    @driver.on_shutdown
    async def _stop_background_tasks() -> None:
        global _auto_update_task, _live_reminder_task, _alias_sync_task
        for task in (_auto_update_task, _live_reminder_task, _alias_sync_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        _auto_update_task = None
        _live_reminder_task = None
        _alias_sync_task = None


__all__ = ["__plugin_meta__", "matcher"]
