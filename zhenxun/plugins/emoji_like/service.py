from __future__ import annotations

from typing import Protocol

from .emoji_map import EmojiLookupResult, extract_emoji_query


class EmojiLikeBot(Protocol):
    """贴表情只依赖 Bot 的 call_api 能力，便于测试时使用轻量替身。"""

    async def call_api(self, api: str, **data):
        """调用 OneBot / NapCat 协议接口。"""


async def apply_emoji_like(
    bot: EmojiLikeBot,
    message_id: int | str,
    raw_query: str,
) -> EmojiLookupResult | None:
    """解析用户表情输入，并对目标消息发起贴表情协议调用。"""

    result = extract_emoji_query(raw_query)
    if result is None:
        return None

    await bot.call_api(
        "set_msg_emoji_like",
        message_id=message_id,
        emoji_id=result.emoji_id,
    )
    return result
