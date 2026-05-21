import asyncio

from nonebot.adapters import Bot
from nonebot.exception import ActionFailed, NetworkError

from zhenxun.services.log import logger
from zhenxun.utils.manager.message_manager import MessageManager

WITHDRAW_EXCEPTIONS = (ActionFailed, NetworkError, TypeError, ValueError)


async def _delete_msg_with_retry(bot: Bot, message_id: str) -> bool:
    """撤回单条机器人回复，临时失败时做一次短重试。"""
    try:
        await bot.delete_msg(message_id=int(message_id))
        return True
    except WITHDRAW_EXCEPTIONS as e:
        logger.warning(f"自动撤回消息失败，准备重试: {message_id}", "消息撤回", e=e)
        try:
            # 部分协议端刚发完消息会短暂不可撤回，轻量重试能覆盖这种抖动。
            await asyncio.sleep(0.2)
            await bot.delete_msg(message_id=int(message_id))
            return True
        except WITHDRAW_EXCEPTIONS as retry_e:
            logger.warning(f"自动撤回消息失败: {message_id}", "消息撤回", e=retry_e)
            return False


async def auto_withdraw_bot_message(bot: Bot, message_id: str | int) -> None:
    """立即撤回一条已知需要自动清理的机器人消息。"""
    if await _delete_msg_with_retry(bot, str(message_id)):
        logger.info(f"自动撤回消息: {message_id}", "消息撤回")


async def record_triggered_bot_message(
    bot: Bot, source_message_id: str | int, bot_message_id: str | int
) -> bool:
    """记录触发消息与机器人回复的关联，源消息已撤回时直接清理晚到回复。"""
    source_message_id = str(source_message_id)
    bot_message_id = str(bot_message_id)
    if MessageManager.is_trigger_source_recalled(bot.self_id, source_message_id):
        # 用户先撤回源消息、回复后到时，不能再放入账本等待下一次 notice。
        await auto_withdraw_bot_message(bot, bot_message_id)
        return False
    MessageManager.add_triggered(bot.self_id, source_message_id, bot_message_id)
    return True


async def auto_withdraw_triggered_messages(
    bot: Bot, source_message_id: str | int
) -> list[str]:
    """撤回被用户撤回消息触发出的机器人回复，并标记后续回复也应清理。"""
    message_ids = MessageManager.mark_trigger_source_recalled(
        bot.self_id, source_message_id
    )
    for message_id in message_ids:
        await auto_withdraw_bot_message(bot, message_id)
    return message_ids
