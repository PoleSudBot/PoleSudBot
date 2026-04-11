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


async def _xutheringwavesuid_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, OneBotV11MessageEvent):
        return False
    plain_text = (event.get_plaintext() or "").strip()
    if not plain_text or not plain_text.lower().startswith("ww"):
        return False
    state["xutheringwavesuid_plain_text"] = plain_text
    return True


__plugin_meta__ = PluginMetadata(
    name="XutheringWavesUID",
    description="通过外部侧车接入 XutheringWavesUID 指令族，并纳入真寻插件管理体系。",
    usage="""
## 🌊 XutheringWavesUID

- **ww帮助** - 查看 XutheringWavesUID 原生帮助列表
- **ww分析帮助** - 查看分析与评分相关帮助
- **ww下载全部资源** - 下载或刷新插件资源

完整功能与详细参数请直接发送 `ww帮助` 查看 XutheringWavesUID 原生帮助。
""".strip(),
    extra=PluginExtraData(
        author="tyql688 / Loping151",
        version="1.0.0",
        plugin_type=PluginType.NORMAL,
        menu_type="游戏相关",
        setting=PluginSetting(default_status=True),
        configs=REGISTER_CONFIGS,
        commands=[
            Command(command="ww帮助", description="查看 XutheringWavesUID 原生帮助"),
            Command(command="ww分析帮助", description="查看分析与评分相关帮助"),
            Command(command="ww下载全部资源", description="下载或刷新插件资源"),
        ],
    ).to_dict(),
)


matcher = on_message(priority=5, block=True, rule=Rule(_xutheringwavesuid_rule))


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
        await _finish_local_error(
            matcher, "XutheringWavesUID 当前仅支持 OneBot V11 协议。"
        )

    try:
        responses = await external_bot_bridge.send_request(bot, event)
    except BridgeDependencyUnavailable:
        await _finish_local_error(
            matcher,
            "XutheringWavesUID 侧车依赖不可用，"
            "请先执行初始化命令准备 sidecar/.runtime 运行目录。",
        )
    except BridgeConnectionTimeout:
        await _finish_local_error(
            matcher,
            "XutheringWavesUID 外部 sidecar 未连接，请先启动侧车服务后再试。",
        )
    except BridgeResponseTimeout:
        await _finish_local_error(
            matcher,
            "XutheringWavesUID 暂时没有响应，请稍后再试。",
        )
    except BridgeUnsupportedEvent:
        await _finish_local_error(
            matcher,
            "当前消息暂不支持转发到 XutheringWavesUID。",
        )

    try:
        sent_count = await external_bot_bridge.replay_responses(bot, responses)
    except BridgeUnsupportedEvent:
        await _finish_local_error(
            matcher,
            "XutheringWavesUID 返回了当前协议暂不支持的响应内容。",
        )
    if sent_count <= 0:
        await _finish_local_error(
            matcher,
            "XutheringWavesUID 没有返回可发送内容，请直接发送 `ww帮助` 再试一次。",
        )
