from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot.exception import ActionFailed
from nonebot_plugin_alconna.uniseg import (
    CustomNode,
    FallbackStrategy,
    Reference,
    SerializeFailed,
    UniMessage,
)

from zhenxun.services.log import logger
from zhenxun.utils.exception import RenderingError
from zhenxun.utils.message import MessageUtils

from .config import PicSearchSettings
from .models import SearchPresentation
from .renderer import render_search_result


def should_fallback_plain(node_count: int) -> bool:
    """判断合并转发失败后是否允许退回普通消息。"""

    return node_count < 3


def _build_fallback_text(presentation: SearchPresentation) -> str:
    """渲染失败时汇总错误、提示和链接，避免只给用户一个空标题。"""

    lines = [presentation.title]
    lines.extend(notice for notice in presentation.notices if notice)
    for section in presentation.sections:
        if section.error:
            lines.append(f"{section.title}：{section.error}")
        lines.extend(notice for notice in section.notices if notice)
        if not section.error and not section.items:
            lines.append(f"{section.title}：没有可展示的结果。")
    if presentation.link_lines:
        lines.append(presentation.build_link_text())
    return "\n\n".join(lines)


async def _build_result_messages(presentation: SearchPresentation) -> list[UniMessage]:
    """构造结果汇总图和可选文本清单。"""

    try:
        image_bytes = await render_search_result(presentation)
        messages = [MessageUtils.build_message(image_bytes)]
        # 只有存在真实链接或可复制结果文本时才发送第二条，避免识角色出现
        # “无可用链接”这类和场景不匹配的提示。
        if presentation.link_lines:
            messages.append(MessageUtils.build_message(presentation.build_link_text()))
        return messages
    except RenderingError as e:
        logger.warning(f"搜图结果渲染失败，降级为文本发送：{e}", "搜图")
        return [MessageUtils.build_message(_build_fallback_text(presentation))]


async def send_presentation(
    bot: Bot,
    event: Event,
    presentation: SearchPresentation,
    settings: PicSearchSettings,
) -> None:
    """发送搜索结果，优先合并转发并按刷屏风险降级。"""

    messages = await _build_result_messages(presentation)
    if settings.forward_search_result and len(messages) > 1:
        nodes = [
            CustomNode(uid=bot.self_id, name=presentation.title, content=message)
            for message in messages
        ]
        try:
            await UniMessage(Reference(nodes=nodes)).send(
                target=event,
                bot=bot,
                fallback=FallbackStrategy.forbid,
            )
            return
        except (SerializeFailed, ActionFailed) as e:
            logger.warning(f"合并转发发送失败：{e}", "搜图")

    # 合并转发失败后只在节点少于 3 时降级普通消息，避免 Google Lens 结果刷屏。
    if should_fallback_plain(len(messages)):
        for message in messages:
            await message.send(target=event, bot=bot, reply_to=True)
        return

    await MessageUtils.build_message(
        "搜图结果较多，合并转发发送失败，请稍后重试。"
    ).send(
        target=event,
        bot=bot,
        reply_to=True,
    )
