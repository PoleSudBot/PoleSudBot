from __future__ import annotations

from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import Match, on_alconna
from nonebot_plugin_uninfo import Uninfo

from ..adapters.runtime import PlatformUtils, logger
from ..command_schema import best30_command, resolve_best30_query
from ..services import handle_best30
from .router import _send_result

best30_matcher = on_alconna(
    best30_command(),
    aliases={"pjskb30"},
    priority=5,
    block=True,
)


@best30_matcher.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    session: Uninfo,
    parts: Match[tuple],
):
    query_parts = parts.result if parts.available else ()
    server, game_id, target_user_id, error = resolve_best30_query(
        query_parts,
        self_id=str(getattr(event, "self_id", "") or ""),
    )
    if error:
        await _send_result(error, bot=bot, event=event)
        return

    platform = PlatformUtils.get_platform(session)
    user_id = session.user.id
    result = await handle_best30(
        platform,
        user_id,
        server=server,
        game_id=game_id,
        target_user_id=target_user_id,
        is_superuser=await SUPERUSER(bot, event),
    )
    await _send_result(result, bot=bot, event=event)
    logger.info(
        "MoeSekai 执行命令: best30",
        event.get_plaintext(),
        session=session,
        platform=platform,
    )
