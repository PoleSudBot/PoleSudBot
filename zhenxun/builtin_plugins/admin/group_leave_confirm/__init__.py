from __future__ import annotations

from dataclasses import dataclass

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.exception import ActionFailed
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, is_type

from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.utils import Command, PluginExtraData, RegisterConfig
from zhenxun.services.group_leave import (
    consume_leave_confirmation,
    create_leave_confirmation,
    execute_group_leave,
    is_bot_joined_group,
)
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

MODULE = "group_leave_confirm"
LEAVE_COMMAND = "退群"
LEAVE_COMMAND_ALIASES = (LEAVE_COMMAND, "bot退群", "bot leave")
DEFAULT_CONFIRM_TIMEOUT_SECONDS = 60
DEFAULT_LEAVE_DELAY_SECONDS = 3


def _bot_mention_text() -> str:
    """生成帮助和确认提示中的 Bot 昵称展示文本。"""
    return f"@{BotConfig.self_nickname or 'Bot'}"


@dataclass(slots=True)
class LeaveCommandParseResult:
    matched: bool
    confirm_code: str | None = None
    invalid_reason: str | None = None


__plugin_meta__ = PluginMetadata(
    name="退群",
    description="管理员确认后让 Bot 退出当前群聊",
    usage=f"""
## 退群

- `{_bot_mention_text()} 退群` - 申请让 Bot 退出当前群聊
- `{_bot_mention_text()} 退群 <确认码>` - 二次确认并执行退群
- `{_bot_mention_text()} bot退群` / `{_bot_mention_text()} bot leave` - 兼容旧入口
""".strip(),
    extra=PluginExtraData(
        author="k1yuyu",
        version="0.1",
        menu_type="群管功能",
        plugin_type=PluginType.SUPER_AND_ADMIN,
        configs=[
            RegisterConfig(
                module=MODULE,
                key="CONFIRM_TIMEOUT_SECONDS",
                value=DEFAULT_CONFIRM_TIMEOUT_SECONDS,
                help="退群确认码有效时间（秒）",
                default_value=DEFAULT_CONFIRM_TIMEOUT_SECONDS,
                type=int,
            ),
            RegisterConfig(
                module=MODULE,
                key="LEAVE_DELAY_SECONDS",
                value=DEFAULT_LEAVE_DELAY_SECONDS,
                help="确认后退群前等待时间（秒）",
                default_value=DEFAULT_LEAVE_DELAY_SECONDS,
                type=int,
            ),
        ],
        commands=[
            Command(command=f"{_bot_mention_text()} 退群"),
            Command(command=f"{_bot_mention_text()} bot退群"),
            Command(command=f"{_bot_mention_text()} bot leave"),
        ],
    ).to_dict(),
)


def _get_config_int(key: str, default: int) -> int:
    """读取整数配置，配置异常时回退默认值并记录原因。"""
    value = Config.get_config(MODULE, key, default)
    try:
        return max(int(value), 0)
    except (TypeError, ValueError) as e:
        logger.warning(f"退群配置 {key} 无法转换为整数，将使用默认值", e=e)
        return default


def _is_confirm_code(text: str) -> bool:
    """判断文本是否为退群确认码，只接受生成器会产生的 ASCII 四位数字。"""
    return text.isascii() and text.isdigit() and len(text) == 4


def parse_leave_command_text(text: str) -> LeaveCommandParseResult:
    """解析退群指令文本，只接受明确的退群命令与兼容别名。"""
    raw_text = " ".join(text.strip().split())
    if not raw_text:
        return LeaveCommandParseResult(matched=False)

    for command in LEAVE_COMMAND_ALIASES:
        if raw_text == command:
            return LeaveCommandParseResult(matched=True)
        if raw_text.startswith(f"{command} "):
            # 参数只允许一个 4 位确认码，避免“退群 1234 其他内容”被宽松接受。
            args = raw_text.removeprefix(command).strip().split()
            if len(args) == 1 and _is_confirm_code(args[0]):
                return LeaveCommandParseResult(matched=True, confirm_code=args[0])
            return LeaveCommandParseResult(matched=True, invalid_reason="confirm_code")

        compact_arg = raw_text.removeprefix(command)
        if compact_arg == raw_text or not compact_arg or not compact_arg[0].isdigit():
            continue

        # 兼容“退群1234”这类紧贴写法，但只在数字开头时进入确认码分支。
        if _is_confirm_code(compact_arg):
            return LeaveCommandParseResult(matched=True, confirm_code=compact_arg)
        return LeaveCommandParseResult(matched=True, invalid_reason="confirm_code")

    return LeaveCommandParseResult(matched=False)


def has_at_bot(message: Message, bot_id: str) -> bool:
    """检查消息中是否显式 @ 当前 Bot。"""
    for segment in message:
        if segment.type == "at" and str(segment.data.get("qq")) == str(bot_id):
            return True
    return False


def is_group_leave_request(message: Message, bot_id: str, plain_text: str) -> bool:
    """判断原始消息是否是带 @ 的退群请求。"""
    return has_at_bot(message, bot_id) and parse_leave_command_text(plain_text).matched


async def _match_group_leave_command(bot: Bot, event: GroupMessageEvent) -> bool:
    """使用原始消息检查 @bot，避免 OneBot 预处理删掉 at 段后无法匹配。"""
    return is_group_leave_request(
        event.original_message,
        bot.self_id,
        event.get_plaintext(),
    )


async def _can_leave_group_operator(bot: Bot, event: GroupMessageEvent) -> bool:
    """校验操作者是否为群主、群管理员或超级用户。"""
    if await SUPERUSER(bot, event):
        return True

    role = getattr(event.sender, "role", "")
    if role in {"owner", "admin"}:
        return True

    member_info = await bot.get_group_member_info(
        group_id=event.group_id,
        user_id=event.user_id,
        no_cache=True,
    )
    return member_info.get("role") in {"owner", "admin"}


group_leave_matcher = on_message(
    rule=is_type(GroupMessageEvent) & Rule(_match_group_leave_command),
    priority=0,
    block=True,
)


@group_leave_matcher.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    parse_result = parse_leave_command_text(event.get_plaintext())
    if parse_result.invalid_reason:
        await MessageUtils.build_message(
            f"唔，确认码格式好像不对……请使用 `{_bot_mention_text()} 退群 1234`。"
        ).finish(reply_to=True)

    try:
        if not await _can_leave_group_operator(bot, event):
            await MessageUtils.build_message(
                "这个决定需要由群主、管理员或超级用户来确认哦。\n"
                "我不能只凭普通成员的消息就离开，抱歉啦。"
            ).finish(reply_to=True)
    except ActionFailed as e:
        logger.warning("获取操作者群权限失败", "退群", e=e)
        await MessageUtils.build_message(
            "我现在没能确认你的群权限，所以这次先不执行退群啦。\n"
            "可以稍后再试一次，或者请群主/管理员来操作。"
        ).finish(reply_to=True)

    group_id = event.group_id
    operator_id = event.user_id
    timeout_seconds = _get_config_int(
        "CONFIRM_TIMEOUT_SECONDS", DEFAULT_CONFIRM_TIMEOUT_SECONDS
    )
    delay_seconds = _get_config_int("LEAVE_DELAY_SECONDS", DEFAULT_LEAVE_DELAY_SECONDS)

    try:
        if not await is_bot_joined_group(bot, group_id):
            await MessageUtils.build_message(
                "咦，我好像已经不在这个群里了，就不用再退一次啦。"
            ).finish(
                reply_to=True
            )
    except ActionFailed as e:
        logger.warning("获取 Bot 群列表失败", "退群", e=e)
        await MessageUtils.build_message(
            "我现在没能确认自己还在不在这个群，所以这次先不执行退群啦。\n"
            "可以稍后再试一次。"
        ).finish(reply_to=True)

    if parse_result.confirm_code is None:
        confirmation = create_leave_confirmation(
            bot.self_id,
            group_id,
            operator_id,
            timeout_seconds,
        )
        logger.info(
            f"发出退群确认码，操作者: {operator_id}",
            "退群",
            target=group_id,
        )
        await MessageUtils.build_message(
            "收到申请啦！如果已经决定让我离开本群，"
            f"请在 {timeout_seconds} 秒内发送：\n\n"
            f"{_bot_mention_text()} {LEAVE_COMMAND} {confirmation.code}\n\n"
            "确认码要认真核对哦，过时就需要重新申请啦。"
        ).finish(reply_to=True)

    status = consume_leave_confirmation(
        bot.self_id,
        group_id,
        operator_id,
        parse_result.confirm_code,
    )
    if status != "ok":
        await MessageUtils.build_message(
            "唔，这个确认码好像对不上……\n"
            "可能是输错了，或者已经过期啦。\n\n"
            f"请重新发送 {_bot_mention_text()} {LEAVE_COMMAND} 获取新的确认码吧。"
        ).finish(reply_to=True)

    await MessageUtils.build_message(
        f"确认收到！我会在 {delay_seconds} 秒后离开本群。\n"
        "虽然有点舍不得，但既然已经决定好了，我会好好道别的。"
    ).send(reply_to=True)

    try:
        await execute_group_leave(
            bot,
            group_id,
            delay_seconds=delay_seconds,
            log_command="退群",
            operator_id=operator_id,
        )
    except ActionFailed as e:
        logger.error("退出群组失败", "退群", target=group_id, e=e)
        await MessageUtils.build_message(
            "退群请求没有成功……\n"
            "可能是 OneBot 端状态不太对，请检查协议端日志后再试一次吧。"
        ).send(reply_to=True)
