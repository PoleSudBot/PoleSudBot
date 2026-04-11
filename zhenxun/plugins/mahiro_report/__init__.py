from datetime import datetime

import nonebot
from nonebot.adapters import Bot
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import Alconna, Arparma, on_alconna
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_session import EventSession
from playwright.async_api import TimeoutError

from zhenxun.configs.utils import Command, PluginExtraData, RegisterConfig, Task
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import broadcast_group

from .config import (
    DEFAULT_FETCH_TIME,
    DEFAULT_SEND_TIME,
    get_fetch_time,
    get_send_time,
)
from .data_source import Report

FETCH_HOUR, FETCH_MINUTE = get_fetch_time()
SEND_HOUR, SEND_MINUTE = get_send_time((FETCH_HOUR, FETCH_MINUTE))

__plugin_meta__ = PluginMetadata(
    name="真寻日报",
    description="嗨嗨，这里是小记者真寻哦",
    usage="""
    指令：
        真寻日报
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.7",
        superuser_help="""重置真寻日报""",
        commands=[Command(command="真寻日报")],
        tasks=[Task(module="mahiro_report", name="真寻日报")],
        configs=[
            RegisterConfig(
                module="alapi",
                key="ALAPI_TOKEN",
                value=None,
                help="在https://admin.alapi.cn/user/login登录后获取token",
            ),
            RegisterConfig(
                key="FULL_SHOW",
                value=False,
                help="完全显示it资讯和60s",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                key="FETCH_TIME",
                value=DEFAULT_FETCH_TIME,
                help="日报抓取时间，格式 HH:MM",
                default_value=DEFAULT_FETCH_TIME,
                type=str,
            ),
            RegisterConfig(
                key="SEND_TIME",
                value=DEFAULT_SEND_TIME,
                help="日报定时发送时间，格式 HH:MM",
                default_value=DEFAULT_SEND_TIME,
                type=str,
            ),
        ],
    ).to_dict(),
)


_matcher = on_alconna(Alconna("真寻日报"), priority=5, block=True, use_origin=True)

_reset_matcher = on_alconna(
    Alconna("重置真寻日报"), priority=5, block=True, permission=SUPERUSER
)


@_reset_matcher.handle()
async def _(session: EventSession, arparma: Arparma):
    file = Report.get_visible_report_file(datetime.now())
    if file.exists():
        file.unlink()
        logger.info("重置真寻日报", arparma.header_result, session=session)
    await MessageUtils.build_message("真寻日报已重置!").send()


@_matcher.handle()
async def _(session: EventSession, arparma: Arparma):
    try:
        await MessageUtils.build_message(await Report.get_report_image()).send()
        logger.info("查看真寻日报", arparma.header_result, session=session)
    except TimeoutError:
        await MessageUtils.build_message("真寻日报生成超时...").send(at_sender=True)
        logger.error("真寻日报生成超时", arparma.header_result, session=session)


driver = nonebot.get_driver()


async def check(bot: Bot, group_id: str) -> bool:
    return not await CommonUtils.task_is_block(bot, "mahiro_report", group_id)


@scheduler.scheduled_job(
    "cron",
    hour=FETCH_HOUR,
    minute=FETCH_MINUTE,
)
async def _generate_daily_report():
    for _ in range(3):
        try:
            await Report.get_report_image(force_refresh=True)
            logger.info("自动生成日报成功...")
            break
        except TimeoutError:
            logger.warning("自动生成日报失败...")


@scheduler.scheduled_job(
    "cron",
    hour=SEND_HOUR,
    minute=SEND_MINUTE,
)
async def _send_daily_report():
    try:
        file = await Report.get_report_image()
    except TimeoutError:
        logger.warning("每日真寻日报发送失败，日报生成超时...")
        return

    message = MessageUtils.build_message(file)
    await broadcast_group(message, log_cmd="真寻日报", check_func=check)
    logger.info("每日真寻日报发送...")
