from typing import Annotated

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.params import Command
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import UniMsg

from zhenxun.configs.utils import Command as MetadataCommand
from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from .data_source import AutoGreetingManager, GreetingScene

__plugin_meta__ = PluginMetadata(
    name="自动介绍",
    description="好友通过后与 Bot 入群后发送自定义介绍消息",
    usage="""
    设置好友欢迎 <消息>
    设置好友欢迎（引用一条消息）
    查看好友欢迎
    删除好友欢迎
    设置入群介绍 <消息>
    设置入群介绍（引用一条消息）
    查看入群介绍
    删除入群介绍
    """.strip(),
    extra=PluginExtraData(
        author="k1yuyu",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
        superuser_help="""
        ### 自动介绍

        用于设置 Bot 添加好友后私聊发送的欢迎消息，
        以及 Bot 加入群聊后发送的群内介绍。消息支持文本与图片。

        #### 好友欢迎
        - `设置好友欢迎 <消息>`：设置好友申请通过后自动发送的私聊欢迎消息。
        - 引用一条消息后发送 `设置好友欢迎`：将被引用消息设置为好友欢迎。
        - `查看好友欢迎`：查看当前好友欢迎消息。
        - `删除好友欢迎`：删除好友欢迎消息，删除后通过好友申请时不再自动发送。

        #### 入群介绍
        - `设置入群介绍 <消息>`：设置 Bot 成功加入群聊后自动发送的群内介绍。
        - 引用一条消息后发送 `设置入群介绍`：将被引用消息设置为入群介绍。
        - `查看入群介绍`：查看当前入群介绍。
        - `删除入群介绍`：删除入群介绍，删除后 Bot 入群时不再自动发送。

        示例：
        - `设置好友欢迎 你好，我是机器人，有问题可以发送 帮助`
        - `设置入群介绍 大家好，我是机器人，可以发送 帮助 查看功能列表 [图片]`
        """.strip(),
        commands=[
            MetadataCommand(
                command="设置好友欢迎", description="设置好友通过后的私聊欢迎"
            ),
            MetadataCommand(
                command="查看好友欢迎", description="查看好友通过后的私聊欢迎"
            ),
            MetadataCommand(
                command="删除好友欢迎", description="删除好友通过后的私聊欢迎"
            ),
            MetadataCommand(
                command="设置入群介绍", description="设置 Bot 入群后的群内介绍"
            ),
            MetadataCommand(
                command="查看入群介绍", description="查看 Bot 入群后的群内介绍"
            ),
            MetadataCommand(
                command="删除入群介绍", description="删除 Bot 入群后的群内介绍"
            ),
        ],
    ).to_dict(),
)

_SET_SCENES = {
    "设置好友欢迎": GreetingScene.FRIEND,
    "设置入群介绍": GreetingScene.GROUP,
}
_SHOW_SCENES = {
    "查看好友欢迎": GreetingScene.FRIEND,
    "查看入群介绍": GreetingScene.GROUP,
}
_DELETE_SCENES = {
    "删除好友欢迎": GreetingScene.FRIEND,
    "删除入群介绍": GreetingScene.GROUP,
}
_SCENE_NAMES = {
    GreetingScene.FRIEND: "好友欢迎",
    GreetingScene.GROUP: "入群介绍",
}

_set_matcher = on_command(
    "设置好友欢迎",
    aliases={"设置入群介绍"},
    permission=SUPERUSER,
    priority=1,
    block=True,
)
_show_matcher = on_command(
    "查看好友欢迎",
    aliases={"查看入群介绍"},
    permission=SUPERUSER,
    priority=1,
    block=True,
)
_delete_matcher = on_command(
    "删除好友欢迎",
    aliases={"删除入群介绍"},
    permission=SUPERUSER,
    priority=1,
    block=True,
)


def _strip_command_text(message: UniMsg, command: str) -> UniMsg:
    # 引用回复时 reply 段可能排在文本前面，需要找到命令文本段再移除命令头。
    cleaned_message = message.copy()
    for segment in cleaned_message:
        text = getattr(segment, "text", None)
        if isinstance(text, str) and command in text:
            segment.text = text.replace(command, "", 1).strip()
            break
    return cleaned_message


def _has_content(message: UniMsg) -> bool:
    # reply 段只负责定位被引用消息，空文本和 reply 段都不算实际欢迎内容。
    for segment in message.dump(True):
        if segment["type"] == "reply":
            continue
        if segment["type"] == "text" and not str(segment.get("text", "")).strip():
            continue
        return True
    return False


@_set_matcher.handle()
async def _(
    bot: Bot,
    event: Event,
    message: UniMsg,
    command: Annotated[tuple[str, ...], Command()],
):
    scene = _SET_SCENES[command[0]]
    message = _strip_command_text(message, command[0])
    if not _has_content(message):
        message = await AutoGreetingManager.get_reply_message(bot, event) or message
    try:
        saved_message = await AutoGreetingManager.save_message(scene, message)
    except ValueError as e:
        await MessageUtils.build_message(str(e)).finish(reply_to=True)
    await (
        MessageUtils.build_message(f"设置{_SCENE_NAMES[scene]}成功：\n")
        + saved_message
    ).send(reply_to=True)
    logger.info(f"设置{_SCENE_NAMES[scene]}成功", command[0])


@_show_matcher.handle()
async def _(command: Annotated[tuple[str, ...], Command()]):
    scene = _SHOW_SCENES[command[0]]
    message = AutoGreetingManager.get_message(scene)
    if not message:
        await MessageUtils.build_message(f"当前未设置{_SCENE_NAMES[scene]}").finish(
            reply_to=True
        )
    await (MessageUtils.build_message(f"当前{_SCENE_NAMES[scene]}：\n") + message).send(
        reply_to=True
    )
    logger.info(f"查看{_SCENE_NAMES[scene]}", command[0])


@_delete_matcher.handle()
async def _(command: Annotated[tuple[str, ...], Command()]):
    scene = _DELETE_SCENES[command[0]]
    old_message = AutoGreetingManager.delete_message(scene)
    if not old_message:
        await MessageUtils.build_message(f"当前未设置{_SCENE_NAMES[scene]}").finish(
            reply_to=True
        )
    await (
        MessageUtils.build_message(f"删除{_SCENE_NAMES[scene]}成功，原内容：\n")
        + old_message
    ).send(reply_to=True)
    logger.info(f"删除{_SCENE_NAMES[scene]}", command[0])
