from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import At, CommandResult, Match, Text
from nonebot_plugin_alconna.uniseg import MsgTarget, UniMessage

from zhenxun.models.statistics import Statistics
from zhenxun.services.log import logger

from ..services import (
    ExportParameters,
    ExportService,
    SummaryParameters,
    SummaryService,
)
from ..utils.message_selector import (
    build_count_selector,
    build_selector_from_time_range_type,
    parse_time_expression,
)


def _collect_summary_filters(
    result: CommandResult,
    parts: Match[list[At | Text]],
) -> tuple[set[str], str]:
    target_user_ids: set[str] = set()
    content_parts: list[str] = []

    if parts.available:
        for part in parts.result:
            if isinstance(part, At) and part.target:
                target_user_ids.add(str(part.target))
            elif isinstance(part, Text):
                stripped_text = part.text.strip()
                if stripped_text:
                    content_parts.append(stripped_text)

    arp = result.result
    if arp and "$extra" in arp.main_args:
        for arg in arp.main_args.get("$extra", []):
            if isinstance(arg, At) and arg.target:
                target_user_ids.add(str(arg.target))
            elif isinstance(arg, Text):
                stripped_text = arg.text.strip()
                if stripped_text:
                    content_parts.append(stripped_text)

    return target_user_ids, " ".join(content_parts)


async def _resolve_target_group_id(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    result: CommandResult,
    target: MsgTarget,
) -> int | None:
    is_superuser = await SUPERUSER(bot, event)
    originating_group_id = (
        event.group_id if isinstance(event, GroupMessageEvent) else None
    )
    arp = result.result
    target_group_id_match = arp.query("g.target_group_id") if arp else None

    if target_group_id_match and not is_superuser:
        await UniMessage.text("需要超级用户权限才能使用 -g 参数指定群聊。").send(target)
        return None

    if target_group_id_match and is_superuser:
        return int(target_group_id_match)
    if originating_group_id is not None:
        return originating_group_id

    await UniMessage.text(
        "请在群聊中使用此命令，或使用 -g <群号> 参数指定目标群聊。(仅限超级用户)"
    ).send(target)
    return None


def _join_time_expression(time_expr: Match[list[str]] | Match[str]) -> str | None:
    if not time_expr.available:
        return None
    value = time_expr.result
    if isinstance(value, list):
        joined = " ".join(str(part).strip() for part in value if str(part).strip())
        return joined or None
    stripped = str(value).strip()
    return stripped or None


async def handle_summary(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    result: CommandResult,
    message_count: int | None,
    style: Match[str],
    parts: Match[list[At | Text]],
    time_expr: Match[list[str]] | Match[str],
    target: MsgTarget,
):
    user_id_str = event.get_user_id()
    originating_group_id = (
        event.group_id if isinstance(event, GroupMessageEvent) else None
    )
    selector = None

    target_group_id_to_fetch = await _resolve_target_group_id(bot, event, result, target)
    if target_group_id_to_fetch is None:
        return

    time_expression = _join_time_expression(time_expr)
    if message_count is not None and time_expression:
        await UniMessage.text("不能同时指定消息数量和 -t 时间表达式。").send(target)
        return
    if message_count is None and not time_expression:
        await UniMessage.text("请提供消息数量，或使用 -t 指定时间表达式。").send(target)
        return

    try:
        if time_expression:
            selector = parse_time_expression(time_expression)
        else:
            selector = build_count_selector(int(message_count))
    except ValueError as e:
        await UniMessage.text(str(e)).send(target)
        return

    target_user_ids, content_value = _collect_summary_filters(result, parts)
    logger.debug(
        f"总结参数: 目标群={target_group_id_to_fetch}, selector={selector.mode}, "
        f"expression={time_expression or message_count}",
        command="总结",
    )

    feedback_target_group_part = (
        f"群聊 {target_group_id_to_fetch} 的"
        if target_group_id_to_fetch != originating_group_id
        else "群聊"
    )
    feedback = f"正在生成{feedback_target_group_part}总结"
    if time_expression:
        feedback += f"（时间: {time_expression}）"
    elif message_count is not None:
        feedback += f"（最近 {message_count} 条）"
    if style.available:
        feedback += f"（风格: {style.result}）"
    feedback += f"{'（指定用户）' if target_user_ids else ''}，请稍候..."
    await UniMessage.text(feedback).send(target)

    params = SummaryParameters(
        bot=bot,
        target_group_id=target_group_id_to_fetch,
        selector=selector,
        style=style.result if style.available else None,
        content_filter=content_value,
        target_user_ids=target_user_ids,
        response_target=target,
    )

    service = SummaryService(params)
    success = await service.execute()
    if not success:
        return

    logger.debug(
        f"总结命令成功完成 (Group: {target_group_id_to_fetch})",
        command="总结",
    )
    try:
        await Statistics.create(
            user_id=str(user_id_str),
            group_id=(str(originating_group_id) if originating_group_id else None),
            plugin_name="summary_group",
            bot_id=str(bot.self_id),
            message_count=(message_count or 0),
            style=style.result if style.available else None,
            target_users=list(target_user_ids),
            content_filter=content_value or time_expression,
        )
    except Exception as stat_e:
        logger.error(f"记录统计失败: {stat_e}", command="总结", e=stat_e)


async def handle_time_range_summary(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    result: CommandResult,
    style: Match[str],
    target: MsgTarget,
    time_range_type: str = "today",
):
    target_group_id_to_fetch = await _resolve_target_group_id(bot, event, result, target)
    if target_group_id_to_fetch is None:
        return

    selector = build_selector_from_time_range_type(time_range_type)
    label = "今日" if time_range_type == "today" else "昨日"
    feedback = f"正在生成{label}总结"
    if style.available:
        feedback += f"（风格: {style.result}）"
    feedback += "，请稍候..."
    await UniMessage.text(feedback).send(target)

    params = SummaryParameters(
        bot=bot,
        target_group_id=target_group_id_to_fetch,
        selector=selector,
        style=style.result if style.available else None,
        response_target=target,
    )
    success = await SummaryService(params).execute()
    if success:
        logger.debug(
            f"{label}总结命令成功完成 (Group: {target_group_id_to_fetch})",
            command=f"{label}总结",
        )


async def handle_export_chat_history(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    result: CommandResult,
    selector_value: str | None,
    time_expr: Match[list[str]] | Match[str],
    target: MsgTarget,
):
    target_group_id_to_fetch = await _resolve_target_group_id(bot, event, result, target)
    if target_group_id_to_fetch is None:
        return

    time_expression = _join_time_expression(time_expr)
    if selector_value and time_expression:
        await UniMessage.text("不能同时指定导出方式和 -t 时间表达式。").send(target)
        return

    try:
        if time_expression:
            selector = parse_time_expression(time_expression)
        elif selector_value in {"今日", "today"}:
            selector = build_selector_from_time_range_type("today")
        elif selector_value in {"昨日", "yesterday"}:
            selector = build_selector_from_time_range_type("yesterday")
        elif selector_value and selector_value.isdigit():
            selector = build_count_selector(int(selector_value))
        else:
            await UniMessage.text(
                "请使用 `导出聊天记录 <数量>`、`导出聊天记录 今日`、`导出聊天记录 昨日` 或 `导出聊天记录 -t <时间表达式>`。"
            ).send(target)
            return
    except ValueError as e:
        await UniMessage.text(str(e)).send(target)
        return

    feedback = f"正在导出群聊 {target_group_id_to_fetch} 的聊天记录"
    if time_expression:
        feedback += f"（时间: {time_expression}）"
    else:
        feedback += f"（模式: {selector.label}）"
    feedback += "，请稍候..."
    await UniMessage.text(feedback).send(target)

    params = ExportParameters(
        bot=bot,
        target_group_id=target_group_id_to_fetch,
        selector=selector,
        response_target=target,
    )
    await ExportService(params).execute()
