from __future__ import annotations

import nonebot
from nonebot import on_message
from nonebot.plugin import PluginMetadata

from .emoji_map import extract_emoji_queries
from .service import apply_emoji_like_id

__plugin_meta__ = PluginMetadata(
    name="贴表情",
    description="回复消息后给目标消息贴上指定 QQ 表情。",
    usage="""
回复一条消息后发送单个 emoji，或发送 `贴<emoji>`，例如：

- `㊗️`
- `㊗️❤️`
- `👍`
- `贴㊗️`
- `贴 128077`
- `贴 ㊗️ 128077 ❤️`
- `贴✨`

仅支持单码位 emoji；一次最多贴 5 个，超出部分、组合 emoji 或协议端不支持时会静默跳过。
    """.strip(),
    supported_adapters={"~onebot.v11"},
    extra={
        "author": "k1yuyu",
        "version": "0.1.2",
        "menu_type": "一些工具",
        "commands": [
            {
                "command": "[回复消息] <emoji> / 贴<emoji|数字ID>",
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
            if not extract_emoji_queries(event.message.extract_plain_text()):
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

        results = extract_emoji_queries(event.message.extract_plain_text())
        if not results:
            return

        reply = await reply_fetch(event, bot)
        if reply is None:
            return

        pasted_ids: list[str] = []
        for result in results:
            try:
                await apply_emoji_like_id(bot, reply.id, result)
            except ActionFailed as exc:
                logger.warning(
                    (
                        "贴表情失败: "
                        f"message_id={reply.id}, emoji_id={result.emoji_id}, err={exc}"
                    ),
                    "贴表情",
                    session=session,
                )
                continue
            pasted_ids.append(result.emoji_id)

        if pasted_ids:
            logger.info(
                f"贴表情: message_id={reply.id}, emoji_ids={pasted_ids}",
                "贴表情",
                session=session,
            )
