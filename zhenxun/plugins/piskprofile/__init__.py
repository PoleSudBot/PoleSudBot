import re

from nonebot import on_regex
from nonebot.adapters import Event
from nonebot.plugin import PluginMetadata
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import Config
from zhenxun.configs.utils.models import PluginCdBlock, PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

from .data_source import take_pjsk_screenshot
from .model import PjskBind

__plugin_meta__ = PluginMetadata(
    name="PJSK个人档案",
    description="Project Sekai 个人档案查询",
    usage="""
## 🎮 PJSK 个人档案助手

截图数据与前端页面由 Moesekai (pjsk.moe) 提供，作者：Exmeaning (東雪)。

### 🔗 绑定与解绑

- **s绑定 [区服] <游戏ID>** - 绑定账号（区服可选，默认 jp）
  示例：`s绑定 jp 123456789`
  示例：`s绑定 123456789`
- **s解绑** - 解除当前绑定

### 🔍 查询档案

- **s个人信息** - 查询已绑定账号的档案截图
- **s查询 [区服] <游戏ID>** - 直接查询指定账号（无需绑定）
  示例：`s查询 cn 987654321`

> 💡 提示：区服可选值：jp / cn / tw，默认 jp。查询类指令有 3 秒冷却时间。
""".strip(),
    extra=PluginExtraData(
        author="kiyu (k1yuyu)",
        version="1.1.0",
        menu_type="游戏相关",
        limits=[
            PluginCdBlock(cd=3, result="查询太快啦，请 {cd} 秒后再试~"),
        ],
        configs=[
            RegisterConfig(
                module="piskprofile",
                key="PJSK_PROFILE_TOKEN",
                value="",
                help="PJSK档案页面鉴权Token",
                default_value="",
                type=str,
            ),
        ],
    ).to_dict(),
)


# ========== Matchers ==========

_bind_matcher = on_regex(
    r"^s绑定\s*(jp|cn|tw)?\s*(\d+)\s*$",
    priority=5,
    block=True,
)

_unbind_matcher = on_regex(
    r"^s解绑\s*$",
    priority=5,
    block=True,
)

_profile_matcher = on_regex(
    r"^s个人信息\s*$",
    priority=5,
    block=True,
)

_query_matcher = on_regex(
    r"^s查询\s*(jp|cn|tw)?\s*(\d+)\s*$",
    priority=5,
    block=True,
)


# ========== Handlers ==========


@_bind_matcher.handle()
async def handle_bind(event: Event, session: EventSession):
    """处理绑定指令"""
    user_id = session.id1
    if not user_id:
        await MessageUtils.build_message("无法获取用户ID").send()
        return

    match = re.search(r"^s绑定\s*(jp|cn|tw)?\s*(\d+)\s*$", event.get_plaintext())
    if not match:
        return

    server = match.group(1) or "jp"
    pjsk_id = match.group(2)

    await PjskBind.set_bind(user_id=user_id, server=server, pjsk_id=pjsk_id)
    logger.info(
        f"PJSK绑定成功: user={user_id}, server={server}, pjsk_id={pjsk_id}",
        "piskprofile",
    )
    await MessageUtils.build_message(
        f"绑定成功！\n区服: {server}\n游戏ID: {pjsk_id}"
    ).send(reply_to=True)


@_unbind_matcher.handle()
async def handle_unbind(session: EventSession):
    """处理解绑指令"""
    user_id = session.id1
    if not user_id:
        await MessageUtils.build_message("无法获取用户ID").send()
        return

    success = await PjskBind.del_bind(user_id=user_id)
    if success:
        logger.info(f"PJSK解绑成功: user={user_id}", "piskprofile")
        await MessageUtils.build_message("解绑成功！").send(reply_to=True)
    else:
        await MessageUtils.build_message("你还没有绑定过哦~").send(reply_to=True)


@_profile_matcher.handle()
async def handle_profile(session: EventSession):
    """处理个人信息查询（使用绑定信息）"""
    user_id = session.id1
    if not user_id:
        await MessageUtils.build_message("无法获取用户ID").send()
        return

    bind = await PjskBind.get_bind(user_id=user_id)
    if not bind:
        await MessageUtils.build_message(
            "你还没有绑定哦~\n请先使用 s绑定 [区服] <游戏ID> 进行绑定"
        ).send(reply_to=True)
        return

    token = Config.get_config("piskprofile", "PJSK_PROFILE_TOKEN") or ""
    result = await take_pjsk_screenshot(
        server=bind.server, pjsk_id=bind.pjsk_id, token=token
    )

    if isinstance(result, bytes):
        await MessageUtils.build_message(result).send(reply_to=True)
    else:
        await MessageUtils.build_message(result).send(reply_to=True)


@_query_matcher.handle()
async def handle_query(event: Event, session: EventSession):
    """处理直接查询指令（不读数据库）"""
    user_id = session.id1
    if not user_id:
        await MessageUtils.build_message("无法获取用户ID").send()
        return

    match = re.search(r"^s查询\s*(jp|cn|tw)?\s*(\d+)\s*$", event.get_plaintext())
    if not match:
        return

    server = match.group(1) or "jp"
    pjsk_id = match.group(2)

    await MessageUtils.build_message("正在获取档案截图，请稍候...").send(reply_to=True)

    token = Config.get_config("piskprofile", "PJSK_PROFILE_TOKEN") or ""
    result = await take_pjsk_screenshot(server=server, pjsk_id=pjsk_id, token=token)

    if isinstance(result, bytes):
        await MessageUtils.build_message(result).send(reply_to=True)
    else:
        await MessageUtils.build_message(result).send(reply_to=True)
