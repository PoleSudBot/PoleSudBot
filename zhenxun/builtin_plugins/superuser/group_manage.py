from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as v11Bot
from nonebot.exception import ActionFailed
from nonebot.params import Depends
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Match,
    Option,
    Subcommand,
    on_alconna,
    store_true,
)
from nonebot_plugin_session import EventSession

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.group_leave import (
    consume_leave_confirmation,
    create_leave_confirmation,
    execute_group_leave,
    is_bot_joined_group,
)
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

CONFIRM_TIMEOUT_SECONDS = 60
LEAVE_DELAY_SECONDS = 3

__plugin_meta__ = PluginMetadata(
    name="管理群操作",
    description="管理群操作",
    usage="""
    群权限 | 群白名单 | 退出群 操作
    退群，添加/删除群白名单，添加/删除群认证，当在群组中这五个命令且没有指定群号时，默认指定当前群组
    指令:
        格式:
        group-manage modify-level [权限等级] ?[群组Id]      : 修改群权限
        group-manage super-handle [群组Id] [--del 删除操作] : 添加/删除群白名单
        group-manage auth-handle [群组Id] [--del 删除操作]  : 添加/删除群认证
        group-manage del-group [群组Id] ?[确认码]           : 退出指定群

        快捷:
        group-manage modify-level : 修改群权限
        group-manage super-handle : 添加/删除群白名单
        group-manage auth-handle  : 添加/删除群认证
        group-manage del-group    : 申请退群确认码

        示例:
        修改群权限 7                              : 在群组中修改当前群组权限为7
        修改群权限 7 1234556                     : 修改 123456 群组的权限等级为7
        添加/删除群白名单 1234567                  : 添加/删除 1234567 为群白名单
        添加/删除群认证 1234567                    : 添加/删除 1234567 为群认证
        group-manage del-group 12344566          : 申请退出指定群组
        group-manage del-group 12344566 1234     : 确认退出指定群组
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna(
        "group-manage",
        Option("--delete", action=store_true, help_text="删除"),
        Subcommand(
            "modify-level", Args["level", int]["group_id?", int], help_text="修改群权限"
        ),
        Subcommand(
            "super-handle",
            Args["group_id", int],
            help_text="添加/删除群白名单",
        ),
        Subcommand(
            "auth-handle",
            Args["group_id", int],
            help_text="添加/删除群认证",
        ),
        Subcommand(
            "del-group",
            Args["group_id", int]["confirm_code?", str],
            help_text="退出群组",
        ),
    ),
    permission=SUPERUSER,
    priority=1,
    block=True,
)

_matcher.shortcut(
    r"修改群权限\s?(?P<level>-?\d+)\s?(?P<group_id>\d+)?",
    command="group-manage",
    arguments=["modify-level", "{level}", "{group_id}"],
    prefix=True,
)

_matcher.shortcut(
    "添加群白名单",
    command="group-manage",
    arguments=["super-handle", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    "删除群白名单",
    command="group-manage",
    arguments=["super-handle", "{%0}", "--delete"],
    prefix=True,
)

_matcher.shortcut(
    "添加群认证",
    command="group-manage",
    arguments=["auth-handle", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    "删除群认证",
    command="group-manage",
    arguments=["auth-handle", "{%0}", "--delete"],
    prefix=True,
)

def CheckGroupId():
    """
    检测群组id
    """

    async def dependency(
        session: EventSession,
        group_id: Match[int],
        state: T_State,
    ):
        gid = session.id3 or session.id2
        if group_id.available:
            gid = group_id.result
        if not gid:
            await MessageUtils.build_message("群组id不能为空...").finish()
        state["group_id"] = gid

    return Depends(dependency)


@_matcher.assign("modify-level", parameterless=[CheckGroupId()])
async def _(session: EventSession, arparma: Arparma, state: T_State, level: int):
    gid = state["group_id"]
    group, _ = await GroupConsole.get_or_create(group_id=gid)
    old_level = group.level
    group.level = level
    await group.save(update_fields=["level"])
    await MessageUtils.build_message("群权限修改成功!").send(reply_to=True)
    logger.info(
        f"修改群权限: {old_level} -> {level}",
        arparma.header_result,
        session=session,
        target=gid,
    )


@_matcher.assign("super-handle", parameterless=[CheckGroupId()])
async def _(session: EventSession, arparma: Arparma, state: T_State):
    gid = state["group_id"]
    group = await GroupConsole.get_group_db(group_id=gid)
    if not group:
        await MessageUtils.build_message("群组信息不存在, 请更新群组信息...").finish()
    s = "删除" if arparma.find("delete") else "添加"
    group.is_super = not arparma.find("delete")
    await group.save(update_fields=["is_super"])
    await MessageUtils.build_message(f"{s}群白名单成功!").send(reply_to=True)
    logger.info(f"{s}群白名单", arparma.header_result, session=session, target=gid)


@_matcher.assign("auth-handle", parameterless=[CheckGroupId()])
async def _(session: EventSession, arparma: Arparma, state: T_State):
    gid = state["group_id"]
    await GroupConsole.update_or_create(
        group_id=gid,
        channel_id__isnull=True,
        defaults={"group_flag": 0 if arparma.find("delete") else 1},
    )
    s = "删除" if arparma.find("delete") else "添加"
    await MessageUtils.build_message(f"{s}群认证成功!").send(reply_to=True)
    logger.info(f"{s}群白名单", arparma.header_result, session=session, target=gid)


@_matcher.assign("del-group")
async def _(
    bot: Bot,
    session: EventSession,
    arparma: Arparma,
    group_id: int,
    confirm_code: Match[str],
):
    if isinstance(bot, v11Bot):
        try:
            is_joined_group = await is_bot_joined_group(bot, group_id)
        except ActionFailed as e:
            logger.warning(
                "获取 Bot 群列表失败",
                "退群",
                session=session,
                target=group_id,
                e=e,
            )
            await MessageUtils.build_message(
                "无法确认 Bot 所在群列表，已拒绝退群操作。"
            ).finish(reply_to=True)

        if not is_joined_group:
            logger.debug("群组不存在", "退群", session=session, target=group_id)
            await MessageUtils.build_message("Bot 未在该群组中...").finish()

        if not confirm_code.available:
            # 跨群退群同样需要确认码，避免超级用户误输入后立即退出群聊。
            confirmation = create_leave_confirmation(
                bot.self_id,
                group_id,
                session.id1 or "superuser",
                CONFIRM_TIMEOUT_SECONDS,
            )
            await MessageUtils.build_message(
                f"收到申请啦！如果已经决定让我离开群组 {group_id}，"
                f"请在 {CONFIRM_TIMEOUT_SECONDS} 秒内发送：\n\n"
                f"group-manage del-group {group_id} {confirmation.code}\n\n"
                "确认码要认真核对哦，过时就需要重新申请啦。"
            ).send(reply_to=True)
            logger.info("发出退群确认码", "退群", session=session, target=group_id)
            return

        status = consume_leave_confirmation(
            bot.self_id,
            group_id,
            session.id1 or "superuser",
            confirm_code.result,
        )
        if status != "ok":
            await MessageUtils.build_message(
                "唔，这个确认码好像对不上……\n"
                "可能是输错了，或者已经过期啦。\n\n"
                "请重新申请新的退群确认码吧。"
            ).finish(reply_to=True)

        await MessageUtils.build_message(
            f"确认收到！我会在 {LEAVE_DELAY_SECONDS} 秒后离开群组 {group_id}。\n"
            "虽然有点舍不得，但既然已经决定好了，我会好好道别的。"
        ).send(reply_to=True)
        try:
            await execute_group_leave(
                bot,
                group_id,
                delay_seconds=LEAVE_DELAY_SECONDS,
                log_command="退群",
                operator_id=session.id1 or "superuser",
                log_session=session,
            )
        except ActionFailed as e:
            logger.error("退出群组失败", "退群", session=session, target=group_id, e=e)
            await MessageUtils.build_message(
                f"退群请求没有成功……群组 {group_id} 暂时还没能退出。\n"
                "可能是 OneBot 端状态不太对，请检查协议端日志后再试一次吧。"
            ).send()
    else:
        # TODO: 其他平台的退群操作
        await MessageUtils.build_message("暂未支持退群操作...").send()
