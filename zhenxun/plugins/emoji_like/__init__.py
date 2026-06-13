from __future__ import annotations

import nonebot
from nonebot import on_message
from nonebot.plugin import PluginMetadata

from .emoji_map import extract_emoji_query
from .service import apply_emoji_like

__plugin_meta__ = PluginMetadata(
    name="贴表情",
    description="回复消息后给目标消息贴上指定 QQ 表情。",
    usage="""
回复一条消息后发送单个 emoji，或发送 `贴<emoji>`，例如：

- `㊗️`
- `👍`
- `贴㊗️`
- `贴✨`

仅支持单个 emoji；组合 emoji 或协议端不支持时会静默跳过。
    """.strip(),
    supported_adapters={"~onebot.v11"},
    extra={
        "author": "k1yuyu",
        "version": "0.1.0",
        "menu_type": "一些工具",
        "commands": [
            {
                "command": "[回复消息] 贴<emoji>",
                "description": "给被回复消息贴表情",
            }
        ],
    },
)


def _nonebot_ready() -> bool:
    """判断当前导入环境是否已经完成 NoneBot 初始化。"""

    try:
        nonebot.get_driver()
    except ValueError:
        return False
    return True


if _nonebot_ready():
    from nonebot.adapters.onebot.v11 import Bot, MessageEvent
    from nonebot.exception import ActionFailed
    from nonebot.rule import Rule
    from nonebot_plugin_alconna.uniseg.tools import reply_fetch
    from nonebot_plugin_uninfo import Uninfo

    from zhenxun.services.log import logger
    from zhenxun.utils.platform import PlatformUtils

    def reply_emoji_like_rule() -> Rule:
        """只在 QQ 平台消息且存在回复目标时触发贴表情入口。"""

        async def _rule(bot: Bot, event: MessageEvent, session: Uninfo) -> bool:
            if PlatformUtils.get_platform(session) != "qq":
                return False
            if extract_emoji_query(event.message.extract_plain_text()) is None:
                return False
            return bool(await reply_fetch(event, bot))

        return Rule(_rule)

    emoji_like_matcher = on_message(
        priority=5,
        block=True,
        rule=reply_emoji_like_rule(),
    )

    @emoji_like_matcher.handle()
    async def _(bot: Bot, event: MessageEvent, session: Uninfo):
        """解析用户输入并调用 NapCat 扩展接口给被回复消息贴表情。"""

        result = extract_emoji_query(event.message.extract_plain_text())
        if result is None:
            return

        reply = await reply_fetch(event, bot)
        if reply is None:
            return

        try:
            result = await apply_emoji_like(
                bot,
                reply.id,
                result.normalized,
            )
        except ActionFailed as exc:
            logger.warning(
                (
                    "贴表情失败: "
                    f"message_id={reply.id}, emoji_id={result.emoji_id}, err={exc}"
                ),
                "贴表情",
                session=session,
            )
            return

        logger.info(
            (
                "贴表情: "
                f"message_id={reply.id}, emoji={result.normalized}, "
                f"emoji_id={result.emoji_id}"
            ),
            "贴表情",
            session=session,
        )
