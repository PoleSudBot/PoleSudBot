from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaQuery,
    Args,
    Arparma,
    At,
    Match,
    Option,
    Query,
    Subcommand,
    on_alconna,
    store_true,
)
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import BaseBlock, Command, PluginExtraData
from zhenxun.models.user_console import UserConsole
from zhenxun.services.log import logger
from zhenxun.utils.enum import BlockType, PluginType
from zhenxun.utils.exception import InsufficientGold
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from .data_source import get_balance, wallet_rank

__plugin_meta__ = PluginMetadata(
    name="钱包",
    description="查看金币余额、排行与转账",
    usage="""
    指令：
        我的金币
        查看我的钱包
        查看钱包余额 [@用户]
        金币排行 ?[num=10]
        金币总排行 ?[num=10]
        查看钱包排名
        钱包转账 <金额> @用户
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.NORMAL,
        menu_type="钱包",
        commands=[
            Command(command="我的金币"),
            Command(command="查看我的钱包"),
            Command(command="查看钱包余额"),
            Command(command="金币排行"),
            Command(command="金币总排行"),
            Command(command="查看钱包排名"),
            Command(command="钱包转账"),
        ],
        limits=[BaseBlock(check_type=BlockType.GROUP)],
    ).to_dict(),
)

_matcher = on_alconna(
    Alconna(
        "钱包",
        Option("--global", action=store_true),
        Subcommand("balance", Args["at_user?", At], help_text="查看钱包余额"),
        Subcommand("rank", Args["num?", int], help_text="查看钱包排名"),
        Subcommand("transfer", Args["gold", int]["at_user", At], help_text="钱包转账"),
    ),
    priority=5,
    block=True,
)

_matcher.shortcut(
    "我的金币",
    command="钱包",
    arguments=["balance"],
    prefix=True,
)

_matcher.shortcut(
    "查看我的钱包",
    command="钱包",
    arguments=["balance"],
    prefix=True,
)

_matcher.shortcut(
    "查看钱包余额",
    command="钱包",
    arguments=["balance", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    "金币排行",
    command="钱包",
    arguments=["rank"],
    prefix=True,
)

_matcher.shortcut(
    "金币总排行",
    command="钱包",
    arguments=["--global", "rank"],
    prefix=True,
)

_matcher.shortcut(
    "查看钱包排名",
    command="钱包",
    arguments=["rank"],
    prefix=True,
)

_matcher.shortcut(
    "钱包转账",
    command="钱包",
    arguments=["transfer", "{%0}"],
    prefix=True,
)


@_matcher.assign("$main")
async def _(session: Uninfo, arparma: Arparma):
    logger.info("查看钱包", arparma.header_result, session=session)
    await MessageUtils.build_message("可发送“查看我的钱包”查看余额。").send(
        reply_to=True
    )


@_matcher.assign("balance")
async def _(session: Uninfo, arparma: Arparma, at_user: Match[At]):
    target_id = at_user.result.target if at_user.available else session.user.id
    gold = await get_balance(target_id, PlatformUtils.get_platform(session))
    logger.info("查看钱包余额", arparma.header_result, session=session)
    if at_user.available:
        await MessageUtils.build_message(
            ["用户", at_user.result, f"的钱包余额: {gold}"]
        ).send(reply_to=True)
    else:
        await MessageUtils.build_message(f"你的钱包余额: {gold}").send(reply_to=True)


@_matcher.assign("rank")
async def _(
    session: Uninfo, arparma: Arparma, num: Query[int] = AlconnaQuery("num", 10)
):
    if num.result > 50:
        await MessageUtils.build_message("排行榜人数不能超过50哦...").finish()
    group_id = session.group.id if session.group else None
    if not arparma.find("global") and not group_id:
        await MessageUtils.build_message(
            "私聊中无法查看 '金币排行'，请发送 '金币总排行'"
        ).finish()
    if arparma.find("global"):
        group_id = None
    result = await wallet_rank(session, group_id, num.result)
    logger.info("查看钱包排名", arparma.header_result, session=session)
    await MessageUtils.build_message(result).send(reply_to=True)


@_matcher.assign("transfer")
async def _(session: Uninfo, arparma: Arparma, gold: int, at_user: At):
    platform = PlatformUtils.get_platform(session)
    try:
        await UserConsole.transfer_gold(
            session.user.id,
            at_user.target,
            gold,
            platform,
        )
    except ValueError as e:
        await MessageUtils.build_message(str(e)).finish(reply_to=True)
    except InsufficientGold:
        await MessageUtils.build_message("钱包余额不足，转账失败。").finish(
            reply_to=True
        )

    logger.info(
        f"钱包转账 {gold} -> {at_user.target}",
        arparma.header_result,
        session=session,
    )
    await MessageUtils.build_message(
        ["转账成功，已给", at_user, f"转账 {gold} 金币。"]
    ).send(reply_to=True)
