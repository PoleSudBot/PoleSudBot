from __future__ import annotations

import asyncio
import contextlib

import nonebot
from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils.models import Command, PluginCdBlock, PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from .command_parser import ParsedCommand, parse_command
from .config import REGISTER_CONFIGS, get_settings
from .constants import MODULE_NAME
from .master_data import master_data_service
from .services import (
    build_auto_update_notifications,
    handle_activity_deck,
    handle_admin_blacklist,
    handle_admin_query_binding,
    handle_bind,
    handle_default_server,
    handle_personal_archive,
    handle_prediction,
    handle_query_archive,
    handle_unbind,
    handle_update,
    handle_visibility,
    handle_ycx,
    migrate_legacy_bindings,
)

__plugin_meta__ = PluginMetadata(
    name="MoeSekai",
    description="Project Sekai 档案、榜线与活动组卡助手",
    usage="""
## 🌟 快速开始
`个人档案`
> 查看你当前默认区服绑定的档案截图。
> 示例：`个人档案` / `cn个人档案`

`查询档案 [区服可选] <游戏ID|@用户>`
> 查询指定游戏 ID 或指定用户的档案截图。
> 示例：`查询档案 1234567890123` / `查询档案 @南极萝卜` / `cn档案查询 1234567890123`

## 👤 档案相关
`给看` / `不给看`
> 控制别人能不能通过 `查询档案 @你` 查看你的绑定档案。

`默认区服 [cn|jp|tw]`
> 查看或切换你的默认服务器。
> 示例：`默认区服` / `默认区服 jp`

## 🌍 区服与绑定
`绑定 [区服可选] <游戏ID>`
> 绑定游戏账号；不写区服时默认按 `jp` 处理。
> 示例：`绑定 1234567890123` / `cn绑定 1234567890123`

`解绑 [区服可选]`
> 解绑指定区服；不写区服时解绑当前默认区服。
> 示例：`解绑` / `tw解绑`

## 📈 榜线与预测
`ycx [活动ID可选]`
> 查看榜线截图；支持指定活动 ID。
> 示例：`ycx` / `ycx 166` / `cnycx 166`

`sk预测 [活动ID可选]`
> 查看预测线或历史结榜文字数据；支持指定活动 ID。
> 示例：`sk预测` / `sk预测 178` / `jpsk预测 178`

## 🃏 活动组卡
`活动组卡 [区服可选] [@用户可选] [活动ID可选] [歌曲ID可选] [难度可选] [模式可选]`
> 生成活动推荐组卡截图，未填写的参数会按配置默认值补全。
> 示例：`活动组卡` / `活动组卡 195 226 hd multi` / `cn活动组卡 @123456 195`

## 🛠️ 管理命令
`pjsk update [区服可选|all]`
> 刷新活动主数据；不写区服时使用默认区服。
> 示例：`pjsk update` / `cnpjsk update`

`pjsk blacklist ...`
> 超级用户管理 UID / QQ 黑名单。

`pjsk 查询绑定 ...`
> 超级用户查询 QQ 或 UID 的绑定信息。

## 💡 说明
- `个人档案`、`ycx`、`活动组卡` 都以截图完整性为优先。
- `ycx` 当前仅支持 `cn / jp`，台服不会进入截图流程。
- 未指定区服时，优先使用你的默认区服。
- `pjsk update all` 仅超级用户可用。
- 难度支持 `ez / nm / hd / ex / ma / apd`，模式支持 `multi / solo / auto / cheerful` 及常见中文别名。
""".strip(),
    extra=PluginExtraData(
        author="Codex",
        version="2.0.0",
        menu_type="游戏相关",
        commands=[
            Command(command="个人档案"),
            Command(command="查询档案 [区服可选] <游戏ID|@用户>"),
            Command(command="绑定 [区服可选] <游戏ID>"),
            Command(command="解绑 [区服可选]"),
            Command(command="默认区服 [cn|jp|tw]"),
            Command(command="给看 / 不给看"),
            Command(command="sk预测 [活动ID可选]"),
            Command(command="ycx [活动ID可选]"),
            Command(command="活动组卡 [@用户可选] [活动ID可选]"),
            Command(command="pjsk update [区服可选|all]"),
        ],
        superuser_help="""
pjsk update [区服可选|all]
pjsk blacklist <add|remove|check> <uid|qq> ...
pjsk 查询绑定 <uid|qq> ...
""".strip(),
        limits=[PluginCdBlock(cd=3, result="操作太快啦，请 {cd} 秒后再试~")],
        configs=REGISTER_CONFIGS,
    ).to_dict(),
)

driver = nonebot.get_driver()
_auto_update_task: asyncio.Task | None = None


async def _command_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    parsed = parse_command(event)
    if not parsed:
        return False
    state["moesekai_parsed_command"] = parsed
    return True


matcher = on_message(priority=5, block=True, rule=Rule(_command_rule))


async def _send_result(result: bytes | str) -> None:
    await MessageUtils.build_message(result).send(reply_to=True)


@matcher.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    session: Uninfo,
    state: T_State,
):
    parsed: ParsedCommand = state["moesekai_parsed_command"]
    is_superuser = await SUPERUSER(bot, event)
    if parsed.action.startswith("admin_") and not is_superuser:
        await _send_result("该指令仅超级用户可用")
        return
    if parsed.error:
        await _send_result(parsed.error)
        return

    platform = PlatformUtils.get_platform(session)
    user_id = session.user.id
    result: bytes | str

    if parsed.action == "bind":
        result = await handle_bind(
            platform,
            user_id,
            parsed.server,
            parsed.game_id or "",
            is_superuser=is_superuser,
        )
    elif parsed.action == "unbind":
        result = await handle_unbind(
            platform,
            user_id,
            parsed.server,
            is_superuser=is_superuser,
        )
    elif parsed.action == "default_server":
        result = await handle_default_server(
            platform,
            user_id,
            parsed.server,
            is_superuser=is_superuser,
        )
    elif parsed.action == "visibility":
        result = await handle_visibility(
            platform,
            user_id,
            allow_share_profile=parsed.admin_value == "allow",
        )
    elif parsed.action == "personal_archive":
        result = await handle_personal_archive(
            platform,
            user_id,
            parsed.server,
            is_superuser=is_superuser,
        )
    elif parsed.action == "query_archive":
        result = await handle_query_archive(
            platform,
            user_id,
            server=parsed.server,
            game_id=parsed.game_id,
            target_user_id=parsed.target_user_id,
            is_superuser=is_superuser,
        )
    elif parsed.action == "prediction":
        result = await handle_prediction(
            platform,
            user_id,
            parsed.server,
            parsed.event_id,
            is_superuser=is_superuser,
        )
    elif parsed.action == "ycx":
        result = await handle_ycx(
            platform,
            user_id,
            parsed.server,
            parsed.event_id,
            is_superuser=is_superuser,
        )
    elif parsed.action == "activity_deck":
        result = await handle_activity_deck(
            platform,
            user_id,
            server=parsed.server,
            target_user_id=parsed.target_user_id,
            event_id=parsed.event_id,
            music_id=parsed.music_id,
            difficulty=parsed.difficulty,
            live_type=parsed.live_type,
            is_superuser=is_superuser,
        )
    elif parsed.action == "update":
        result = await handle_update(
            platform,
            user_id,
            parsed.server,
            update_all=parsed.all_servers,
            is_superuser=is_superuser,
        )
    elif parsed.action == "admin_blacklist":
        result = await handle_admin_blacklist(parsed, user_id)
    elif parsed.action == "admin_query_binding":
        result = await handle_admin_query_binding(parsed)
    else:
        result = "暂不支持的指令"

    await _send_result(result)
    logger.info(
        f"MoeSekai 执行命令: {parsed.action}",
        parsed.raw_text,
        session=session,
        platform=platform,
    )


@PriorityLifecycle.on_startup(priority=2)
async def _migrate_legacy_data():
    get_settings()
    await migrate_legacy_bindings()
    await master_data_service.update_all(force=False)


async def _auto_update_loop(bot: Bot) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            results = await master_data_service.update_all(force=False)
            if get_settings().master_notify_superusers:
                for message in await build_auto_update_notifications(results):
                    await PlatformUtils.send_superuser(bot, message)
        except Exception as exc:
            logger.error("MoeSekai 自动更新循环失败", MODULE_NAME, e=exc)
        await asyncio.sleep(get_settings().master_auto_check_interval_seconds)


@driver.on_bot_connect
async def _start_auto_update(bot: Bot):
    global _auto_update_task
    if _auto_update_task is None or _auto_update_task.done():
        _auto_update_task = asyncio.create_task(_auto_update_loop(bot))


@driver.on_shutdown
async def _stop_auto_update():
    global _auto_update_task
    if _auto_update_task and not _auto_update_task.done():
        _auto_update_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _auto_update_task
    _auto_update_task = None
