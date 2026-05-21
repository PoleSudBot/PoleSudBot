import asyncio

from nonebot.adapters import Bot
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor

from zhenxun.services.log import logger
from zhenxun.utils.withdraw_manage import WithdrawManager

_WITHDRAW_TASKS: set[asyncio.Task] = set()


async def _withdraw_message(bot: Bot, message_id: str | int, time: int) -> None:
    """后台执行延迟撤回，避免 postprocessor 等待配置的撤回时间。"""
    try:
        await WithdrawManager.withdraw_message(bot, message_id, time)
    except Exception as e:
        logger.warning(f"延迟撤回消息失败: {message_id}", "WithdrawManager", e=e)


def _schedule_withdraw(bot: Bot, message_id: str | int, time: int) -> None:
    """创建并跟踪后台撤回任务，完成后及时移出集合。"""
    task = asyncio.create_task(_withdraw_message(bot, message_id, time))
    _WITHDRAW_TASKS.add(task)
    task.add_done_callback(_WITHDRAW_TASKS.discard)


@run_postprocessor
async def _(
    matcher: Matcher,
    exception: Exception | None,
    bot: Bot,
):
    tasks = []
    index_list = list(WithdrawManager._data.keys())
    for index in index_list:
        (
            bot,
            message_id,
            time,
        ) = WithdrawManager._data[index]
        tasks.append((bot, message_id, time))
        WithdrawManager.remove(index)
    for bot, message_id, time in tasks:
        _schedule_withdraw(bot, message_id, time)
