from __future__ import annotations

from typing import Protocol

from .emoji_map import EmojiLookupResult, extract_emoji_queries


class EmojiLikeBot(Protocol):
    """贴表情只依赖 Bot 的 call_api 能力，便于测试时使用轻量替身。"""

    async def call_api(self, api: str, **data):
        """调用 OneBot / NapCat 协议接口。"""


async def apply_emoji_like_id(
    bot: EmojiLikeBot,
    message_id: int | str,
    result: EmojiLookupResult,
) -> EmojiLookupResult:
    """对目标消息发起一次贴表情协议调用。"""

    await bot.call_api(
        "set_msg_emoji_like",
        message_id=message_id,
        emoji_id=result.emoji_id,
    )
    return result


async def apply_emoji_likes(
    bot: EmojiLikeBot,
    message_id: int | str,
    raw_query: str,
) -> list[EmojiLookupResult]:
    """解析用户表情输入，并按顺序对目标消息贴多个表情。"""

    results = extract_emoji_queries(raw_query)
    pasted: list[EmojiLookupResult] = []
    for result in results:
        pasted.append(await apply_emoji_like_id(bot, message_id, result))
    return pasted
