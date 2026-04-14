from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import SupportScope
from nonebot_plugin_session import Session as EventSession
from nonebot_plugin_uninfo import SceneType
from nonebot_plugin_uninfo.model import Scene, Session, User

LightSession = Session

_PLATFORM_SCOPE_MAP = {
    "qq": SupportScope.qq_client,
    "qqguild": SupportScope.qq_guild,
    "qq_api": SupportScope.qq_api,
    "discord": SupportScope.discord,
    "dodo": SupportScope.dodo,
    "kaiheila": SupportScope.kook,
    "kook": SupportScope.kook,
    "telegram": SupportScope.telegram,
    "feishu": SupportScope.feishu,
}


def _adapter_name(bot: Bot, session: EventSession) -> str:
    adapter = getattr(bot, "adapter", None)
    if adapter is None:
        return session.bot_type
    get_name = getattr(adapter, "get_name", None)
    if get_name is None:
        return session.bot_type
    return str(get_name())


def _resolve_scope(platform: str | None, adapter_name: str) -> SupportScope:
    normalized = (platform or "").strip().lower()
    if normalized in _PLATFORM_SCOPE_MAP:
        return _PLATFORM_SCOPE_MAP[normalized]
    if adapter_name == "OneBot V11":
        return SupportScope.qq_client
    return SupportScope.onebot12_other


def _resolve_user_id(bot: Bot, event: Event, session: EventSession) -> str:
    event_user_id = getattr(event, "user_id", None)
    if event_user_id is not None:
        return str(event_user_id)
    if session.id1:
        return str(session.id1)
    return str(bot.self_id)


def _resolve_group_and_channel(
    event: Event, session: EventSession
) -> tuple[str | None, str | None]:
    group_id = str(session.id3 or session.id2) if (session.id3 or session.id2) else None
    channel_id = str(session.id2) if session.id3 and session.id2 else None

    event_group_id = getattr(event, "group_id", None)
    if event_group_id is not None:
        group_id = str(event_group_id)

    event_channel_id = getattr(event, "channel_id", None)
    if event_channel_id is not None:
        channel_id = str(event_channel_id)

    return group_id, channel_id


def build_light_session(bot: Bot, event: Event, session: EventSession) -> LightSession:
    adapter_name = _adapter_name(bot, session)
    scope = _resolve_scope(session.platform, adapter_name)
    user_id = _resolve_user_id(bot, event, session)
    group_id, channel_id = _resolve_group_and_channel(event, session)

    # 这里只复用 EventSession 里已经解析好的本地 ID，避免再走 Uninfo fetcher
    # 触发 get_group_info / get_group_list 之类的远程控制面调用。
    if channel_id and group_id:
        scene = Scene(
            id=channel_id,
            type=SceneType.GROUP,
            parent=Scene(id=group_id, type=SceneType.GROUP),
        )
    elif group_id:
        scene = Scene(id=group_id, type=SceneType.GROUP)
    else:
        scene = Scene(id=user_id, type=SceneType.PRIVATE)

    return Session(
        self_id=str(bot.self_id),
        adapter=adapter_name,
        scope=scope,
        scene=scene,
        user=User(id=user_id),
        platform=session.platform,
    )
