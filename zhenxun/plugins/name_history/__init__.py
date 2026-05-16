from __future__ import annotations

# ruff: noqa: I001

import asyncio
from pathlib import Path

from nonebot import on_command, on_message, require
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import is_type

require("nonebot_plugin_htmlrender")

from nonebot_plugin_htmlrender import template_to_pic

from zhenxun.configs.utils import Command, PluginExtraData
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.group_name_history import GroupNameHistory
from zhenxun.services.avatar_service import avatar_service
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from ._logic import (
    GROUP_CARD,
    HISTORY_LIMIT,
    QQ_NAME,
    SenderNameSnapshot,
    build_history_display_data,
    build_group_info_defaults,
    extract_sender_snapshot,
    format_history_records,
    normalize_name_type_filter,
    record_sender_snapshot,
    resolve_query_target,
)

MODULE = "name_history"
TEMPLATE_DIR = (Path(__file__).parent / "templates").resolve()
_CACHE_MAX_SIZE = 20000
_HISTORY_ALIASES = {"曾用名", "过去昵称", "nickname"}
_GROUP_CARD_ALIASES = {"群名片历史"}
_QQ_NAME_ALIASES = {"历史qq名", "QQ名历史", "qq名历史"}

_record_locks: dict[tuple[str, str, str, str], asyncio.Lock] = {}
_latest_name_cache: dict[tuple[str, str, str, str], str | None] = {}
_group_info_synced: set[tuple[str, str, str]] = set()

__plugin_meta__ = PluginMetadata(
    name="历史昵称",
    description="查看群成员过去用过的群名片和 QQ 名称",
    usage="""
    历史昵称 ?[@用户|QQ号]      : 查看群名片和 QQ 名称变化
    历史群名片 ?[@用户|QQ号]    : 只查看群名片变化
    历史QQ名 ?[@用户|QQ号]      : 只查看 QQ 名称变化
    """.strip(),
    extra=PluginExtraData(
        author="k1yuyu",
        version="0.1",
        plugin_type=PluginType.NORMAL,
        menu_type="数据统计",
        commands=[
            Command(command="历史昵称 ?[@用户|QQ号]"),
            Command(command="历史群名片 ?[@用户|QQ号]"),
            Command(command="历史QQ名 ?[@用户|QQ号]"),
        ],
        aliases=_HISTORY_ALIASES | _GROUP_CARD_ALIASES | _QQ_NAME_ALIASES,
    ).to_dict(),
)


def _trim_cache(cache: dict | set, *, max_size: int = _CACHE_MAX_SIZE) -> None:
    """按插入顺序丢弃一批旧键，避免被动消息监听让进程缓存无限增长。"""
    overflow = len(cache) - max_size
    if overflow <= 0:
        return
    for key in list(cache)[: overflow + max_size // 10]:
        cache.pop(key, None) if isinstance(cache, dict) else cache.discard(key)


def _trim_record_locks() -> None:
    """只回收空闲的用户写入锁，避免并发记录时为同一用户创建两把锁。"""
    if len(_record_locks) <= _CACHE_MAX_SIZE:
        return
    for key, lock in list(_record_locks.items()):
        if len(_record_locks) <= _CACHE_MAX_SIZE:
            return
        if not lock.locked():
            _record_locks.pop(key, None)


class _GroupNameHistoryRepository:
    async def get_latest_display_name(
        self,
        *,
        platform: str,
        group_id: str,
        user_id: str,
        name_type: str,
    ) -> str | None:
        """读取指定用户某类名称的最新记录。"""
        cache_key = (platform, group_id, user_id, name_type)
        if cache_key in _latest_name_cache:
            return _latest_name_cache[cache_key]
        latest = (
            await GroupNameHistory.filter(
                platform=platform,
                group_id=group_id,
                user_id=user_id,
                name_type=name_type,
            )
            .order_by("-record_time", "-id")
            .first()
        )
        display_name = latest.display_name if latest else None
        _latest_name_cache[cache_key] = display_name
        _trim_cache(_latest_name_cache)
        return display_name

    async def create_history(
        self,
        *,
        platform: str,
        group_id: str,
        user_id: str,
        name_type: str,
        display_name: str,
    ) -> None:
        """写入一次名称变化事件。"""
        await GroupNameHistory.create(
            platform=platform,
            group_id=group_id,
            user_id=user_id,
            name_type=name_type,
            display_name=display_name,
        )
        _latest_name_cache[(platform, group_id, user_id, name_type)] = display_name
        _trim_cache(_latest_name_cache)


_repository = _GroupNameHistoryRepository()

_record_matcher = on_message(
    rule=is_type(GroupMessageEvent),
    priority=1,
    block=False,
)

_history_matcher = on_command(
    "历史昵称",
    aliases=_HISTORY_ALIASES,
    rule=is_type(GroupMessageEvent),
    priority=5,
    block=True,
)

_group_card_matcher = on_command(
    "历史群名片",
    aliases=_GROUP_CARD_ALIASES,
    rule=is_type(GroupMessageEvent),
    priority=5,
    block=True,
)

_qq_name_matcher = on_command(
    "历史QQ名",
    aliases=_QQ_NAME_ALIASES,
    rule=is_type(GroupMessageEvent),
    priority=5,
    block=True,
)


async def _record_history_with_locks(
    *,
    platform: str,
    group_id: str,
    user_id: str,
    snapshot: SenderNameSnapshot,
) -> int:
    """按用户和名称类型串行写入，避免并发消息重复制造同一条变化。"""
    lock_key = (platform, group_id, user_id, "name_history")
    _trim_record_locks()
    lock = _record_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        return await record_sender_snapshot(
            _repository,
            platform=platform,
            group_id=group_id,
            user_id=user_id,
            snapshot=snapshot,
        )


async def _query_history_records(
    *,
    group_id: str,
    user_id: str,
    name_type: str | None,
) -> list[GroupNameHistory]:
    """按名称类型分别查询，避免混合查询时某类历史挤掉另一类。"""
    if name_type:
        return await (
            GroupNameHistory.filter(
                platform="qq",
                group_id=group_id,
                user_id=user_id,
                name_type=name_type,
            )
            .order_by("-record_time", "-id")
            .limit(HISTORY_LIMIT + 1)
        )

    records: list[GroupNameHistory] = []
    for current_type in (GROUP_CARD, QQ_NAME):
        records.extend(
            await GroupNameHistory.filter(
                platform="qq",
                group_id=group_id,
                user_id=user_id,
                name_type=current_type,
            )
            .order_by("-record_time", "-id")
            .limit(HISTORY_LIMIT + 1)
        )
    return records


async def _get_avatar_uri(user_id: str) -> str:
    """获取目标用户头像 URI，失败时交给模板展示占位头像。"""
    try:
        avatar_path = await avatar_service.get_avatar_path("qq", user_id)
    except Exception as e:
        logger.warning(
            "历史昵称头像获取失败，使用占位头像", MODULE, target=user_id, e=e
        )
        return ""
    return avatar_path.as_uri() if avatar_path else ""


async def _build_history_picture(
    *,
    target_user_id: str,
    avatar_uri: str,
    records: list[GroupNameHistory],
    name_type: str | None,
) -> bytes:
    """用 htmlrender 将历史昵称分区数据渲染成图片。"""
    display_data = build_history_display_data(
        records,
        target_user_id=target_user_id,
        name_type=name_type,
        limit=HISTORY_LIMIT,
    )
    return await template_to_pic(
        template_path=TEMPLATE_DIR,
        template_name="history.html",
        templates={
            "title": display_data.profile_name,
            "target_user_id": display_data.target_user_id,
            "avatar_uri": avatar_uri,
            "sections": display_data.sections,
            "empty_text": display_data.empty_text,
            "limit": HISTORY_LIMIT,
        },
        pages={"viewport": {"width": 860, "height": 10}},
        wait=80,
    )


@_record_matcher.handle()
async def _(event: GroupMessageEvent):
    """收到群消息时记录发送者当前群名片和 QQ 名称。"""
    platform = "qq"
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    snapshot = extract_sender_snapshot(event.sender)
    created_count = await _record_history_with_locks(
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        snapshot=snapshot,
    )
    sync_key = (platform, group_id, user_id)
    if not created_count and sync_key in _group_info_synced:
        return

    # 历史表只在变化时新增，当前群成员信息则每次启动后至少刷新一次。
    await GroupInfoUser.update_or_create(
        user_id=user_id,
        group_id=group_id,
        defaults=build_group_info_defaults(snapshot, platform),
    )
    _group_info_synced.add(sync_key)
    _trim_cache(_group_info_synced)
    logger.debug(
        f"记录名称变化 {created_count} 条",
        MODULE,
        group_id=group_id,
        session=user_id,
    )


async def _send_history(
    event: GroupMessageEvent,
    args: Message,
    *,
    name_type: str | None,
):
    """查询当前群内指定用户的历史名称并回复。"""
    target = resolve_query_target(args, str(event.user_id))
    if target.error:
        await MessageUtils.build_message(target.error).finish(reply_to=True)

    name_type = normalize_name_type_filter(name_type)
    records = await _query_history_records(
        group_id=str(event.group_id),
        user_id=target.user_id,
        name_type=name_type,
    )
    fallback_text = format_history_records(
        records,
        target_user_id=target.user_id,
        name_type=name_type,
        limit=HISTORY_LIMIT,
    )
    logger.info("查看历史昵称", MODULE, target=target.user_id)
    try:
        avatar_uri = await _get_avatar_uri(target.user_id)
        picture = await _build_history_picture(
            target_user_id=target.user_id,
            avatar_uri=avatar_uri,
            records=records,
            name_type=name_type,
        )
    except Exception as e:
        logger.warning(
            "历史昵称图片渲染失败，回退文本",
            MODULE,
            target=target.user_id,
            e=e,
        )
        await MessageUtils.build_message(fallback_text).finish(reply_to=True)
        return

    await MessageUtils.build_message(picture).finish(reply_to=True)


@_history_matcher.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    """查看群名片和 QQ 名称的混合变化时间线。"""
    await _send_history(event, args, name_type=None)


@_group_card_matcher.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    """只查看群名片变化。"""
    await _send_history(event, args, name_type=GROUP_CARD)


@_qq_name_matcher.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    """只查看 QQ 名称变化。"""
    await _send_history(event, args, name_type=QQ_NAME)
