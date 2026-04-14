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
from ..utils.scope import SummaryScope, build_preset_scope, parse_summary_scope


def _collect_parts(
    parts: Match[list[At | Text]],
    result: CommandResult,
) -> list[At | Text]:
    collected_parts: list[At | Text] = []
    if parts.available:
        collected_parts.extend(parts.result)

    arp = result.result
    if arp and "$extra" in arp.main_args:
        extra_args = arp.main_args.get("$extra", [])
        collected_parts.extend(arg for arg in extra_args if isinstance(arg, At | Text))
    return collected_parts


def _consume_leading_text_parts(
    parts: list[At | Text],
    text_count: int,
) -> list[At | Text]:
    if text_count <= 0:
        return list(parts)

    consumed = 0
    remaining: list[At | Text] = []
    for part in parts:
        if isinstance(part, Text) and part.text.strip() and consumed < text_count:
            consumed += 1
            continue
        remaining.append(part)
    return remaining


def _extract_text_parts(parts: list[At | Text]) -> list[str]:
    return [
        part.text.strip()
        for part in parts
        if isinstance(part, Text) and part.text.strip()
    ]


def _extract_reply_message_id(
    event: GroupMessageEvent | PrivateMessageEvent,
) -> str | None:
    # 只让最终结果回复原指令，避免“正在生成”之类的过程提示也被串成回复链。
    message_id = getattr(event, "message_id", None)
    return str(message_id) if message_id else None


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
        "请在群聊中使用此命令，或使用 -g <群号> 参数指定目标群聊。"
    ).send(target)
    return None


def _build_summary_feedback(
    scope: SummaryScope,
    style_text: str | None,
    target_user_ids: set[str],
    target_group_id: int,
) -> str:
    feedback = f"正在生成群聊 {target_group_id} 的总结"
    if scope.is_time_based:
        feedback += f"（范围: {scope.label}）"
    if style_text:
        feedback += f"（风格: {style_text}）"
    if target_user_ids:
        feedback += "（指定用户）"
    return feedback + "，请稍候..."


async def handle_summary(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    result: CommandResult,
    scope_input: str,
    style: Match[str],
    parts: Match[list[At | Text]],
    target: MsgTarget,
):
    user_id_str = event.get_user_id()
    originating_group_id = (
        event.group_id if isinstance(event, GroupMessageEvent) else None
    )
    reply_to_message_id = _extract_reply_message_id(event)
    target_group_id = await _resolve_target_group_id(bot, event, result, target)
    if target_group_id is None:
        return

    collected_parts = _collect_parts(parts, result)
    text_parts = _extract_text_parts(collected_parts)

    try:
        parsed_scope = parse_summary_scope(scope_input, text_parts)
    except ValueError as e:
        await UniMessage.text(str(e)).send(target)
        return

    scope = parsed_scope.scope
    remaining_parts = _consume_leading_text_parts(
        collected_parts,
        parsed_scope.consumed_text_count,
    )
    remaining_parts = [
        part
        for part in remaining_parts
        if not isinstance(part, Text) or part.text.strip()
    ]

    target_user_ids: set[str] = set()
    content_parts: list[str] = []

    if scope.is_time_based:
        if remaining_parts:
            await UniMessage.text(
                "时间范围总结暂不支持附加 @用户 或关键词过滤，请仅保留范围和选项参数。"
            ).send(target)
            return
    else:
        from .. import validate_msg_count_range

        try:
            validate_msg_count_range(int(scope.count or 0))
        except ValueError as e:
            await UniMessage.text(str(e)).send(target)
            return

        for part in remaining_parts:
            if isinstance(part, At) and part.target:
                target_user_ids.add(str(part.target))
            elif isinstance(part, Text):
                stripped_text = part.text.strip()
                if stripped_text:
                    content_parts.append(stripped_text)

    content_value = " ".join(content_parts)

    logger.debug(
        f"总结参数: 目标群={target_group_id}, "
        f"scope={scope.raw}, 用户过滤={target_user_ids}",
        command="总结",
    )

    await UniMessage.text(
        _build_summary_feedback(
            scope,
            style.result if style.available else None,
            target_user_ids,
            target_group_id,
        )
    ).send(target)

    params = SummaryParameters(
        bot=bot,
        target_group_id=target_group_id,
        scope=scope,
        style=style.result if style.available else None,
        content_filter=content_value,
        target_user_ids=target_user_ids,
        response_target=target,
        reply_to_message_id=reply_to_message_id,
    )

    service = SummaryService(params)
    success = await service.execute()

    if success and not scope.is_time_based:
        try:
            await Statistics.create(
                user_id=str(user_id_str),
                group_id=(str(originating_group_id) if originating_group_id else None),
                plugin_name="summary_group",
                bot_id=str(bot.self_id),
                message_count=int(scope.count or 0),
                style=style.result if style.available else None,
                target_users=list(target_user_ids),
                content_filter=content_value,
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
    scope = build_preset_scope(time_range_type)
    reply_to_message_id = _extract_reply_message_id(event)
    target_group_id = await _resolve_target_group_id(bot, event, result, target)
    if target_group_id is None:
        return

    await UniMessage.text(
        _build_summary_feedback(
            scope,
            style.result if style.available else None,
            set(),
            target_group_id,
        )
    ).send(target)

    service = SummaryService(
        SummaryParameters(
            bot=bot,
            target_group_id=target_group_id,
            scope=scope,
            style=style.result if style.available else None,
            response_target=target,
            reply_to_message_id=reply_to_message_id,
        )
    )
    await service.execute()


async def handle_export_records(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    result: CommandResult,
    scope_input: str,
    parts: Match[list[At | Text]],
    target: MsgTarget,
):
    target_group_id = await _resolve_target_group_id(bot, event, result, target)
    if target_group_id is None:
        return

    collected_parts = _collect_parts(parts, result)
    text_parts = _extract_text_parts(collected_parts)

    try:
        parsed_scope = parse_summary_scope(scope_input, text_parts)
    except ValueError as e:
        await UniMessage.text(str(e)).send(target)
        return

    scope = parsed_scope.scope
    remaining_parts = _consume_leading_text_parts(
        collected_parts,
        parsed_scope.consumed_text_count,
    )
    remaining_parts = [
        part
        for part in remaining_parts
        if not isinstance(part, Text) or part.text.strip()
    ]
    if remaining_parts:
        await UniMessage.text(
            "导出记录仅支持数量或时间范围指令，不支持附加 @用户 或关键词过滤。"
        ).send(target)
        return

    if not scope.is_time_based:
        from .. import validate_msg_count_range

        try:
            validate_msg_count_range(int(scope.count or 0))
        except ValueError as e:
            await UniMessage.text(str(e)).send(target)
            return

    await UniMessage.text(
        f"正在导出群聊 {target_group_id} 的聊天记录（范围: {scope.label}），请稍候..."
    ).send(target)

    service = ExportService(
        ExportParameters(
            bot=bot,
            target_group_id=target_group_id,
            scope=scope,
            response_target=target,
        )
    )
    await service.execute()
