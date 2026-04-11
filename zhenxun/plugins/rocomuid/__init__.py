from __future__ import annotations

from nonebot import on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11 import MessageEvent as OneBotV11MessageEvent
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.typing import T_State

from zhenxun.configs.utils import Command, PluginExtraData, PluginSetting
from zhenxun.services.external_bot_bridge import (
    BridgeConnectionTimeout,
    BridgeDependencyUnavailable,
    BridgeResponseTimeout,
    BridgeUnsupportedEvent,
    external_bot_bridge,
)
from zhenxun.services.external_bot_bridge_config import REGISTER_CONFIGS
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils


async def _rocomuid_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, OneBotV11MessageEvent):
        return False
    plain_text = (event.get_plaintext() or "").strip()
    if not plain_text or not plain_text.lower().startswith("rc"):
        return False
    state["rocomuid_plain_text"] = plain_text
    return True


__plugin_meta__ = PluginMetadata(
    name="RocomUID",
    description="通过外部侧车接入 RocomUID 指令族，并纳入真寻插件管理体系。",
    usage="""
## 🎮 RocomUID

- **rc帮助** - 查看 RocomUID 原生帮助列表
- **rc图鉴 [名称]** - 查询精灵图鉴
- **rc查蛋 [参数]** - 查询精灵蛋信息
- **rcuid / rc我的信息** - 查询个人信息

完整功能与详细参数请直接发送 `rc帮助` 查看 RocomUID 原生帮助。
""".strip(),
    extra=PluginExtraData(
        author="jiluoQAQ",
        version="1.0.0",
        plugin_type=PluginType.NORMAL,
        menu_type="游戏相关",
        setting=PluginSetting(default_status=True),
        configs=REGISTER_CONFIGS,
        commands=[
            Command(command="rc帮助", description="查看 RocomUID 原生帮助"),
            Command(command="rc图鉴 [名称]", description="查询精灵图鉴"),
            Command(command="rc查蛋 [参数]", description="查询精灵蛋信息"),
            Command(command="rcuid / rc我的信息", description="查询个人信息"),
        ],
    ).to_dict(),
)


matcher = on_message(priority=5, block=True, rule=Rule(_rocomuid_rule))


async def _finish_local_error(matcher: Matcher, message: str) -> None:
    matcher.state["_statistics_skip"] = True
    await MessageUtils.build_message(message).finish(reply_to=True)


@matcher.handle()
async def _(
    bot: OneBotV11Bot,
    event: OneBotV11MessageEvent,
    matcher: Matcher,
):
    if not isinstance(bot, OneBotV11Bot) or not isinstance(
        event, OneBotV11MessageEvent
    ):
        await _finish_local_error(matcher, "RocomUID 当前仅支持 OneBot V11 协议。")

    try:
        responses = await external_bot_bridge.send_request(bot, event)
    except BridgeDependencyUnavailable:
        await _finish_local_error(
            matcher,
            "RocomUID 侧车依赖不可用，"
            "请先执行初始化命令准备 sidecar/.runtime 运行目录。",
        )
    except BridgeConnectionTimeout:
        await _finish_local_error(
            matcher,
            "RocomUID 外部 sidecar 未连接，请先启动侧车服务后再试。",
        )
    except BridgeResponseTimeout:
        await _finish_local_error(
            matcher,
            "RocomUID 暂时没有响应，请稍后再试。",
        )
    except BridgeUnsupportedEvent:
        await _finish_local_error(
            matcher,
            "当前消息暂不支持转发到 RocomUID。",
        )

    try:
        sent_count = await external_bot_bridge.replay_responses(bot, responses)
    except BridgeUnsupportedEvent:
        await _finish_local_error(
            matcher,
            "RocomUID 返回了当前协议暂不支持的响应内容。",
        )
    if sent_count <= 0:
        await _finish_local_error(
            matcher,
            "RocomUID 没有返回可发送内容，请直接发送 `rc帮助` 再试一次。",
        )
