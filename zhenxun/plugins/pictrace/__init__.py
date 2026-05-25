from __future__ import annotations

# ruff: noqa: I001

import random
from typing import Any

from arclet.alconna import Alconna, Args, CommandMeta, MultiVar
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.exception import ActionFailed
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State

require("nonebot_plugin_alconna")
require("nonebot_plugin_waiter")

from nonebot_plugin_alconna import Text, UniMsg, on_alconna
from nonebot_plugin_alconna.uniseg import Image as UniImage
from nonebot_plugin_alconna.uniseg import SerializeFailed

# 先初始化项目服务层，避免单独导入插件入口时配置层反向导入 services 造成循环导入。
import zhenxun.services  # noqa: F401

from zhenxun.configs.utils.models import Command, PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

from .config import REGISTER_CONFIGS, load_settings
from .image_input import resolve_image_input
from .sender import send_presentation, send_tag_result
from .tagger import ImageTagClient, ImageTagClientError
from .workflow import search_anime, search_character, search_picture

__plugin_meta__ = PluginMetadata(
    name="图片搜索",
    description=(
        "基于 SauceNAO、Google Lens、TraceMoe、AnimeTrace、WD14 的图片来源、"
        "动画与 tag 识别插件。"
    ),
    usage="""
`搜图 / 识图 / 以图搜图 [图片]`
查询图片来源；优先使用 SauceNAO，相似度不足时补充 Google Lens。
示例：`搜图` 后发送图片 / 回复图片后发送 `搜图`

`搜番 / 识番 / 查番 [图片]`
查询动画截图出处，返回番名、集数、时间点和预览。
示例：`搜番` / 回复截图后发送 `识番`

`识角色 / 识人物 / 角色识别 [图片]`
识别动画、Gal 或二游角色，返回角色候选和来源作品。
示例：`识角色` / 回复图片后发送 `角色识别`

`识别tag / tag识别 / 图片tag [图片]`
识别图片中的 tag，返回原图预览、tag 置信度和可复制 tag 文本。
示例：`识别tag` / 回复图片后发送 `tag识别`

    """.strip(),
    extra=PluginExtraData(
        author="k1yuyu",
        version="0.1.1",
        menu_type="一些工具",
        configs=REGISTER_CONFIGS,
        commands=[
            Command(command="搜图 ?[图片]"),
            Command(command="识图 ?[图片]"),
            Command(command="以图搜图 ?[图片]"),
            Command(command="谷歌搜图 ?[图片]"),
            Command(command="Google搜图 ?[图片]"),
            Command(command="lens搜图 ?[图片]"),
            Command(command="Lens搜图 ?[图片]"),
            Command(command="搜番 ?[图片]"),
            Command(command="识番 ?[图片]"),
            Command(command="查番 ?[图片]"),
            Command(command="识角色 ?[图片]"),
            Command(command="识人物 ?[图片]"),
            Command(command="角色识别 ?[图片]"),
            Command(command="人物识别 ?[图片]"),
            Command(command="识别tag ?[图片]"),
            Command(command="tag识别 ?[图片]"),
            Command(command="图片tag ?[图片]"),
            Command(command="鉴赏图片 ?[图片]"),
        ],
    ).to_dict(),
)

_tag_client = ImageTagClient()


def _alc(command: str) -> Alconna:
    """创建允许图片和尾随文本的宽松命令解析器。"""

    return Alconna(
        command,
        Args["parts?", MultiVar(Text | UniImage)],
        meta=CommandMeta(strict=False, compact=True),
    )


search_cmd = on_alconna(
    _alc("搜图"),
    aliases={"识图", "以图搜图"},
    priority=5,
    block=True,
    auto_send_output=False,
)
google_cmd = on_alconna(
    _alc("谷歌搜图"),
    aliases={"Google搜图", "google搜图"},
    priority=5,
    block=True,
    auto_send_output=False,
)
lens_cmd = on_alconna(
    _alc("lens搜图"),
    aliases={"Lens搜图"},
    priority=5,
    block=True,
    auto_send_output=False,
)
anime_cmd = on_alconna(
    _alc("搜番"),
    aliases={"识番", "查番"},
    priority=5,
    block=True,
    auto_send_output=False,
)
character_cmd = on_alconna(
    _alc("识角色"),
    aliases={"识人物", "角色识别", "人物识别"},
    priority=5,
    block=True,
    auto_send_output=False,
)
tag_cmd = on_alconna(
    _alc("识别tag"),
    aliases={"tag识别", "图片tag", "鉴赏图片"},
    priority=5,
    block=True,
    auto_send_output=False,
)

SEARCHING_MESSAGES = (
    "正在检索，请稍候...",
)
TAGGING_NOTICE = "正在识别图片标签，请稍候..."


def _extract_receipt_message_id(receipt: Any) -> str | int | None:
    """从 UniMessage 发送回执里提取 OneBot 可撤回的 message_id。"""

    if isinstance(receipt, dict):
        return receipt.get("message_id")
    msg_ids = getattr(receipt, "msg_ids", None)
    if not msg_ids:
        return None
    first = msg_ids[0]
    if isinstance(first, dict):
        return first.get("message_id")
    if isinstance(first, str | int):
        return first
    return None


async def _send_searching_notice():
    """发送随机检索占位消息并返回发送回执。"""

    try:
        return await MessageUtils.build_message(random.choice(SEARCHING_MESSAGES)).send(
            reply_to=True
        )
    except (ActionFailed, SerializeFailed) as e:
        logger.debug(f"检索占位消息发送失败：{e}", "搜图")
        return None


async def _send_tagging_notice():
    """发送图片 tag 识别占位消息并返回发送回执。"""

    try:
        return await MessageUtils.build_message(TAGGING_NOTICE).send(reply_to=True)
    except (ActionFailed, SerializeFailed) as e:
        logger.debug(f"图片 tag 识别占位消息发送失败：{e}", "识别tag")
        return None


async def _recall_searching_notice(bot: Bot, receipt: Any) -> None:
    """结果返回后 best-effort 撤回命令占位消息。"""

    message_id = _extract_receipt_message_id(receipt)
    if message_id is None:
        return
    try:
        await bot.delete_msg(message_id=int(message_id))
    except (ActionFailed, TypeError, ValueError) as e:
        logger.debug(f"命令占位消息撤回失败：{message_id} {e}", "搜图")


async def _load_image_or_reply(
    bot: Bot,
    event: MessageEvent,
    message: UniMsg,
    state: T_State,
):
    """读取命令图片，并在成功后给出占位提示。"""

    settings = load_settings()
    image = await resolve_image_input(
        bot,
        event,
        message,
        state,
        settings.wait_image_timeout,
        settings.max_image_size_mb,
    )
    receipt = await _send_searching_notice()
    return image, settings, receipt


async def _load_image_for_tagging(
    bot: Bot,
    event: MessageEvent,
    message: UniMsg,
    state: T_State,
):
    """读取 tag 识别图片，并在成功后给出识别占位提示。"""

    settings = load_settings()
    image = await resolve_image_input(
        bot,
        event,
        message,
        state,
        settings.wait_image_timeout,
        settings.max_image_size_mb,
    )
    receipt = await _send_tagging_notice()
    return image, settings, receipt


async def _handle_user_error(message: str) -> None:
    """向用户返回可预期错误。"""

    await MessageUtils.build_message(message).send(reply_to=True)


@search_cmd.handle()
async def _(bot: Bot, event: MessageEvent, message: UniMsg, state: T_State):
    """处理普通搜图命令。"""

    notice_receipt = None
    try:
        image, settings, notice_receipt = await _load_image_or_reply(
            bot, event, message, state
        )
        presentation = await search_picture(image, settings=settings)
        await send_presentation(bot, event, presentation, settings)
    except ValueError as e:
        await _handle_user_error(str(e))
    except Exception as e:
        # 命令入口是框架边界，未知异常在这里记录上下文后转成统一用户提示。
        logger.error("搜图处理失败", "搜图", e=e)
        await _handle_user_error("搜图处理失败，请稍后再试。")
    finally:
        await _recall_searching_notice(bot, notice_receipt)


@google_cmd.handle()
@lens_cmd.handle()
async def _(bot: Bot, event: MessageEvent, message: UniMsg, state: T_State):
    """处理强制 Google Lens 搜图命令。"""

    notice_receipt = None
    try:
        image, settings, notice_receipt = await _load_image_or_reply(
            bot, event, message, state
        )
        presentation = await search_picture(image, force_lens=True, settings=settings)
        await send_presentation(bot, event, presentation, settings)
    except ValueError as e:
        await _handle_user_error(str(e))
    except Exception as e:
        # 命令入口是框架边界，未知异常在这里记录上下文后转成统一用户提示。
        logger.error("Google Lens 搜图处理失败", "搜图", e=e)
        await _handle_user_error("Google Lens 搜图处理失败，请稍后再试。")
    finally:
        await _recall_searching_notice(bot, notice_receipt)


@anime_cmd.handle()
async def _(bot: Bot, event: MessageEvent, message: UniMsg, state: T_State):
    """处理搜番命令。"""

    notice_receipt = None
    try:
        image, settings, notice_receipt = await _load_image_or_reply(
            bot, event, message, state
        )
        presentation = await search_anime(image, settings=settings)
        await send_presentation(bot, event, presentation, settings)
    except ValueError as e:
        await _handle_user_error(str(e))
    except Exception as e:
        # 命令入口是框架边界，未知异常在这里记录上下文后转成统一用户提示。
        logger.error("搜番处理失败", "搜番", e=e)
        await _handle_user_error("搜番处理失败，请稍后再试。")
    finally:
        await _recall_searching_notice(bot, notice_receipt)


@character_cmd.handle()
async def _(bot: Bot, event: MessageEvent, message: UniMsg, state: T_State):
    """处理识角色命令。"""

    notice_receipt = None
    try:
        image, settings, notice_receipt = await _load_image_or_reply(
            bot, event, message, state
        )
        presentation = await search_character(image, settings=settings)
        await send_presentation(bot, event, presentation, settings)
    except ValueError as e:
        await _handle_user_error(str(e))
    except Exception as e:
        # 命令入口是框架边界，未知异常在这里记录上下文后转成统一用户提示。
        logger.error("识角色处理失败", "识角色", e=e)
        await _handle_user_error("识角色处理失败，请稍后再试。")
    finally:
        await _recall_searching_notice(bot, notice_receipt)


@tag_cmd.handle()
async def _(bot: Bot, event: MessageEvent, message: UniMsg, state: T_State):
    """处理图片 tag 识别命令。"""

    notice_receipt = None
    try:
        image, settings, notice_receipt = await _load_image_for_tagging(
            bot, event, message, state
        )
        result = await _tag_client.recognize(image, settings)
        await send_tag_result(bot, event, image, result, settings)
    except (ValueError, ImageTagClientError) as e:
        await _handle_user_error(str(e))
    except Exception as e:
        # 命令入口是框架边界，未知异常在这里记录上下文后转成统一用户提示。
        logger.error("图片 tag 识别处理失败", "识别tag", e=e)
        await _handle_user_error("图片 tag 识别失败，请稍后再试。")
    finally:
        await _recall_searching_notice(bot, notice_receipt)
