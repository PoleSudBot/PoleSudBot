from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from tortoise import Tortoise

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .cache import MoeSekaiCache
from .command_parser import ParsedCommand
from .config import get_settings
from .constants import (
    MODULE_NAME,
    PREDICTION_SUPPORTED_SERVERS,
    SERVER_SET,
    YCX_SUPPORTED_SERVERS,
    normalize_deck_difficulty,
    normalize_live_type,
    server_label,
)
from .master_data import RegionUpdateResult, master_data_service
from .repositories import (
    add_blacklist_entry,
    delete_user_binding,
    get_blacklist_entry,
    get_or_create_user_settings,
    get_user_binding,
    get_user_settings,
    list_user_bindings,
    query_bindings_by_uid,
    remove_blacklist_entry,
    set_allow_share_profile,
    set_default_server,
    upsert_user_binding,
)
from .screenshot import ScreenshotError, screenshot_service

PREDICTION_RANKS = [50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000]


def validate_game_id(game_id: str) -> str | None:
    if not game_id.isdigit():
        return "游戏ID必须全部为数字"
    if not 13 <= len(game_id) <= 20:
        return "游戏ID长度必须在 13 到 20 位之间"
    return None


def format_time(value: datetime | None) -> str:
    if not value:
        return "-"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


async def migrate_legacy_bindings() -> None:
    try:
        connection = Tortoise.get_connection("default")
        rows = await connection.execute_query_dict(
            "SELECT user_id, server, pjsk_id FROM pjsk_bind"
        )
    except Exception:
        return

    for row in rows:
        user_id = str(row.get("user_id", "")).strip()
        server = str(row.get("server", "")).lower().strip()
        game_id = str(row.get("pjsk_id", "")).strip()
        if not user_id or server not in SERVER_SET or validate_game_id(game_id):
            continue
        await upsert_user_binding("qq", user_id, server, game_id)
        settings = await get_or_create_user_settings("qq", user_id)
        if not settings.default_server:
            await set_default_server("qq", user_id, server)


async def _is_qq_blacklisted(
    platform: str, user_id: str, is_superuser: bool
) -> str | None:
    if is_superuser:
        return None
    if await get_blacklist_entry("qq", user_id):
        return "你已被拉黑，当前无法使用 MoeSekai 相关功能"
    return None


async def _is_uid_blacklisted(
    server: str, game_id: str, is_superuser: bool
) -> str | None:
    if is_superuser:
        return None
    if await get_blacklist_entry("uid", game_id, server=server):
        return f"该游戏ID({game_id})已被拉黑"
    return None


async def _resolve_default_server(
    platform: str,
    user_id: str,
    *,
    explicit_server: str | None = None,
    fallback_jp: bool = False,
) -> tuple[str | None, str | None]:
    if explicit_server:
        return explicit_server, None
    settings = await get_or_create_user_settings(platform, user_id)
    if settings.default_server:
        return settings.default_server, None
    bindings = await list_user_bindings(platform, user_id)
    if bindings:
        await set_default_server(platform, user_id, bindings[0].server)
        return bindings[0].server, None
    if fallback_jp:
        return "jp", None
    return None, "请先绑定账号或使用区服前缀指定区服"


async def _resolve_binding_for_user(
    platform: str,
    requester_is_superuser: bool,
    target_user_id: str,
    explicit_server: str | None,
    *,
    ignore_share: bool = False,
) -> tuple[Any | None, str | None]:
    if not requester_is_superuser and await get_blacklist_entry("qq", target_user_id):
        return None, "该用户已被拉黑，无法查询"
    settings = await get_or_create_user_settings(platform, target_user_id)
    if not requester_is_superuser and not ignore_share and not settings.allow_share_profile:
        return None, "对方设置了不给看，无法查询其绑定档案"

    server, error = await _resolve_default_server(
        platform,
        target_user_id,
        explicit_server=explicit_server,
        fallback_jp=False,
    )
    if error or not server:
        return None, "对方还没有设置可用的默认区服"

    binding = await get_user_binding(platform, target_user_id, server)
    if not binding:
        return None, f"对方还没有绑定{server_label(server)}账号"

    blocked = await _is_uid_blacklisted(server, binding.game_id, requester_is_superuser)
    if blocked:
        return None, blocked
    return binding, None


async def handle_bind(
    platform: str,
    user_id: str,
    server: str | None,
    game_id: str,
    *,
    is_superuser: bool,
) -> str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error
    server = server or "jp"
    if server not in SERVER_SET:
        return "绑定区服仅支持 cn / jp / tw"
    if error := validate_game_id(game_id):
        return error
    if error := await _is_uid_blacklisted(server, game_id, is_superuser):
        return error

    binding = await upsert_user_binding(platform, user_id, server, game_id)
    settings = await get_or_create_user_settings(platform, user_id)
    if not settings.default_server:
        settings = await set_default_server(platform, user_id, server)

    default_server = settings.default_server or server
    lines = [
        "绑定成功！",
        f"区服：{server_label(binding.server)}",
        f"游戏ID：{binding.game_id}",
        f"当前默认服务器为 {server_label(default_server)}",
        "可使用“默认区服 <cn|jp|tw>”切换默认服务器",
    ]
    return "\n".join(lines)


async def handle_unbind(
    platform: str,
    user_id: str,
    server: str | None,
    *,
    is_superuser: bool,
) -> str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error

    resolved_server, error = await _resolve_default_server(
        platform,
        user_id,
        explicit_server=server,
        fallback_jp=False,
    )
    if error or not resolved_server:
        return "你还没有绑定任何账号"

    removed = await delete_user_binding(platform, user_id, resolved_server)
    if not removed:
        return f"你还没有绑定{server_label(resolved_server)}账号"

    settings = await get_or_create_user_settings(platform, user_id)
    message = [f"已解绑 {server_label(resolved_server)} 账号"]
    if settings.default_server == resolved_server:
        bindings = await list_user_bindings(platform, user_id)
        if bindings:
            await set_default_server(platform, user_id, bindings[0].server)
            message.append(f"默认服务器已切换为 {server_label(bindings[0].server)}")
        else:
            await set_default_server(platform, user_id, None)
            message.append("你目前没有任何绑定，默认服务器已清空")
    return "\n".join(message)


async def handle_default_server(
    platform: str,
    user_id: str,
    server: str | None,
    *,
    is_superuser: bool,
) -> str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error

    settings = await get_or_create_user_settings(platform, user_id)
    bindings = await list_user_bindings(platform, user_id)
    if server is None:
        if not bindings:
            return "你还没有绑定任何账号"
        lines = [f"当前默认服务器：{server_label(settings.default_server or bindings[0].server)}", "已绑定区服："]
        for binding in bindings:
            tag = " (默认)" if binding.server == (settings.default_server or bindings[0].server) else ""
            lines.append(f"- {server_label(binding.server)}：{binding.game_id}{tag}")
        return "\n".join(lines)

    if server not in SERVER_SET:
        return "默认区服仅支持 cn / jp / tw"
    binding = await get_user_binding(platform, user_id, server)
    if not binding:
        return f"你还没有绑定{server_label(server)}账号"
    await set_default_server(platform, user_id, server)
    return f"已将默认服务器切换为 {server_label(server)}"


async def handle_visibility(
    platform: str,
    user_id: str,
    allow_share_profile: bool,
) -> str:
    await set_allow_share_profile(platform, user_id, allow_share_profile)
    return "已设置为给看" if allow_share_profile else "已设置为不给看"


async def handle_personal_archive(
    platform: str,
    user_id: str,
    server: str | None,
    *,
    is_superuser: bool,
) -> bytes | str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error

    resolved_server, error = await _resolve_default_server(
        platform,
        user_id,
        explicit_server=server,
        fallback_jp=False,
    )
    if error or not resolved_server:
        return "你还没有绑定任何账号，请先使用“绑定 [区服] <游戏ID>”"

    binding = await get_user_binding(platform, user_id, resolved_server)
    if not binding:
        return f"你还没有绑定{server_label(resolved_server)}账号"
    if error := await _is_uid_blacklisted(
        binding.server, binding.game_id, is_superuser
    ):
        return error
    try:
        return await screenshot_service.capture_profile(binding.server, binding.game_id)
    except ScreenshotError as exc:
        return exc.to_user_message()


async def handle_query_archive(
    platform: str,
    requester_user_id: str,
    *,
    server: str | None,
    game_id: str | None,
    target_user_id: str | None,
    is_superuser: bool,
) -> bytes | str:
    if error := await _is_qq_blacklisted(platform, requester_user_id, is_superuser):
        return error
    if game_id and target_user_id:
        return "查询档案不能同时指定游戏ID和 @用户"

    if game_id:
        resolved_server = server or "jp"
        if resolved_server not in SERVER_SET:
            return "查询档案区服仅支持 cn / jp / tw"
        if error := validate_game_id(game_id):
            return error
        if error := await _is_uid_blacklisted(resolved_server, game_id, is_superuser):
            return error
        try:
            return await screenshot_service.capture_profile(resolved_server, game_id)
        except ScreenshotError as exc:
            return exc.to_user_message()

    if not target_user_id:
        return "请提供游戏ID或 @用户"

    binding, error = await _resolve_binding_for_user(
        platform,
        is_superuser,
        target_user_id,
        server,
        ignore_share=target_user_id == requester_user_id,
    )
    if error:
        return error
    try:
        return await screenshot_service.capture_profile(binding.server, binding.game_id)
    except ScreenshotError as exc:
        return exc.to_user_message()


async def _fetch_prediction_payload(server: str, event_id: int) -> dict[str, Any]:
    cache_key = f"prediction:{server}:{event_id}"
    cached = await MoeSekaiCache.get(cache_key)
    if cached:
        return cached
    base = get_settings().ranking_api_base.rstrip("/")
    path = (
        f"/api/public/v1/jp/data/{event_id}"
        if server == "jp"
        else f"/api/public/v1/data/{event_id}"
    )
    payload = await AsyncHttpx.get_json(f"{base}{path}", raise_on_failure=True)
    await MoeSekaiCache.set(cache_key, payload)
    return payload


def _format_prediction_time(timestamp: int | None) -> str:
    if not timestamp:
        return "未知"
    if timestamp > 10_000_000_000:
        timestamp = int(timestamp / 1000)
    return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")


async def _get_event_by_id(server: str, event_id: int) -> dict[str, Any] | None:
    for event in await master_data_service.get_events(server):
        try:
            if int(event.get("id")) == event_id:
                return event
        except (TypeError, ValueError):
            continue
    return None


def _extract_rank_map(payload: dict[str, Any] | None) -> dict[int, int]:
    data = payload.get("data") if isinstance(payload, dict) else None
    charts = data.get("charts") if isinstance(data, dict) else None
    if not isinstance(charts, list):
        return {}
    rank_map: dict[int, int] = {}
    for item in charts:
        if not isinstance(item, dict):
            continue
        rank = item.get("Rank")
        score = item.get("PredictedScore")
        if isinstance(rank, int) and score not in (None, 0):
            rank_map[rank] = int(score)
    return rank_map


def _build_rank_message(
    *,
    server: str,
    event: dict[str, Any],
    payload: dict[str, Any] | None,
    title: str,
    event_label: str,
    note: str | None = None,
) -> str | None:
    rank_map = _extract_rank_map(payload)
    if not rank_map:
        return None
    lines = [
        f"{server_label(server)}{title}",
        f"{event_label}：第{event['id']}期 {event.get('name', '')}",
        "----------------",
    ]
    if note:
        lines.insert(1, note)
    for rank in PREDICTION_RANKS:
        score = rank_map.get(rank)
        if not score:
            continue
        lines.append(f"T{rank}: {score:,}")
    lines.extend(
        [
            "----------------",
            f"更新时间：{_format_prediction_time(payload.get('timestamp')) if isinstance(payload, dict) else '未知'}",
            "数据来源：sekaibangdan.exmeaning.com",
        ]
    )
    return "\n".join(lines)


async def handle_prediction(
    platform: str,
    user_id: str,
    server: str | None,
    event_id: int | None = None,
    *,
    is_superuser: bool,
) -> str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error
    resolved_server, error = await _resolve_default_server(
        platform,
        user_id,
        explicit_server=server,
        fallback_jp=False,
    )
    if error or not resolved_server:
        return "请先绑定账号或使用区服前缀指定查询区服"
    if resolved_server not in PREDICTION_SUPPORTED_SERVERS:
        return f"{server_label(resolved_server)}暂不支持预测线查询"
    binding = await get_user_binding(platform, user_id, resolved_server)
    if binding:
        if error := await _is_uid_blacklisted(
            binding.server, binding.game_id, is_superuser
        ):
            return error

    if event_id is not None:
        current_event = await _get_event_by_id(resolved_server, event_id)
        if not current_event:
            return f"{server_label(resolved_server)}不存在活动 {event_id}"
        used_previous_event = False
        event_label = "活动"
    else:
        current_event = await master_data_service.get_current_event(
            resolved_server,
            fallback="prev",
        )
        if not current_event:
            return f"{server_label(resolved_server)}当前没有进行中的活动，且没有可用的上期活动数据"

        now = datetime.now()
        used_previous_event = False
        try:
            start_time = datetime.fromtimestamp(current_event["startAt"] / 1000)
            end_time = datetime.fromtimestamp(current_event["aggregateAt"] / 1000 + 1)
            used_previous_event = not (start_time <= now <= end_time)
        except (KeyError, TypeError, ValueError):
            used_previous_event = False
        event_label = "当前活动"

    try:
        payload = await _fetch_prediction_payload(resolved_server, int(current_event["id"]))
    except Exception as exc:
        logger.warning("MoeSekai 预测线请求失败", MODULE_NAME, e=exc)
        return f"预测线查询失败：{type(exc).__name__}: {exc}"
    message = _build_rank_message(
        server=resolved_server,
        event=current_event,
        payload=payload,
        title="预测线",
        event_label=event_label,
        note="当前无进行中活动，已回退到上期活动结榜线" if used_previous_event else None,
    )
    if message:
        return message
    return f"第{current_event['id']}期活动暂无可用预测线数据"


async def handle_update(
    platform: str,
    user_id: str,
    server: str | None,
    *,
    update_all: bool,
    is_superuser: bool,
) -> str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error

    if update_all:
        if not is_superuser:
            return "pjsk update all 仅超级用户可用"
        results = await master_data_service.update_all(force=True)
        return "\n\n".join(result.to_message() for result in results)

    resolved_server, error = await _resolve_default_server(
        platform,
        user_id,
        explicit_server=server,
        fallback_jp=False,
    )
    if error or not resolved_server:
        return "请先绑定账号设置默认区服，或使用 pjsk update <cn|jp|tw> 显式指定区服"
    result = await master_data_service.update_region(resolved_server, force=True)
    return result.to_message()


async def handle_ycx(
    platform: str,
    user_id: str,
    server: str | None,
    event_id: int | None = None,
    *,
    is_superuser: bool,
) -> bytes | str:
    if error := await _is_qq_blacklisted(platform, user_id, is_superuser):
        return error
    resolved_server, error = await _resolve_default_server(
        platform,
        user_id,
        explicit_server=server,
        fallback_jp=False,
    )
    if error or not resolved_server:
        return "请先绑定账号或使用区服前缀指定查询区服"
    if resolved_server not in YCX_SUPPORTED_SERVERS:
        return f"{server_label(resolved_server)}暂不支持ycx榜线查询"
    binding = await get_user_binding(platform, user_id, resolved_server)
    if binding:
        if error := await _is_uid_blacklisted(
            binding.server, binding.game_id, is_superuser
        ):
            return error
    current_event = None
    if event_id is not None:
        current_event = await _get_event_by_id(resolved_server, event_id)
        if not current_event:
            return f"{server_label(resolved_server)}不存在活动 {event_id}"
    try:
        return await screenshot_service.capture_ranking(resolved_server, event_id=event_id)
    except ScreenshotError as exc:
        if event_id is None or current_event is None:
            return exc.to_user_message()
        logger.warning("MoeSekai 历史 ycx 截图失败，回退文字数据", MODULE_NAME, e=exc)
        try:
            payload = await _fetch_prediction_payload(resolved_server, event_id)
        except Exception as payload_exc:
            logger.warning("MoeSekai 历史 ycx 文字回退失败", MODULE_NAME, e=payload_exc)
            return f"第{event_id}期活动暂无可用榜线数据"
        message = _build_rank_message(
            server=resolved_server,
            event=current_event,
            payload=payload,
            title="结榜榜线",
            event_label="活动",
            note="历史活动页面暂不可用，已回退为文字数据",
        )
        if message:
            return message
        return f"第{event_id}期活动暂无可用榜线数据"


async def handle_activity_deck(
    platform: str,
    requester_user_id: str,
    *,
    server: str | None,
    target_user_id: str | None,
    event_id: int | None,
    music_id: int | None,
    difficulty: str | None,
    live_type: str | None,
    is_superuser: bool,
) -> bytes | str:
    if error := await _is_qq_blacklisted(platform, requester_user_id, is_superuser):
        return error

    target_user_id = target_user_id or requester_user_id
    binding, error = await _resolve_binding_for_user(
        platform,
        is_superuser,
        target_user_id,
        server,
        ignore_share=target_user_id == requester_user_id,
    )
    if error:
        return error

    resolved_event = event_id
    if resolved_event is None:
        current_event = await master_data_service.get_current_event(
            binding.server,
            fallback="next_first",
        )
        if not current_event:
            return "当前和下一期活动都不可用，请手动指定活动ID"
        resolved_event = int(current_event["id"])

    settings = get_settings()
    resolved_difficulty = normalize_deck_difficulty(
        difficulty or settings.deck_default_difficulty
    )
    if not resolved_difficulty:
        return "难度仅支持 easy/normal/hard/expert/master/append 及其缩写"
    resolved_live_type = normalize_live_type(
        live_type or settings.deck_default_live_type
    )
    if not resolved_live_type:
        return "模式仅支持 multi/solo/auto/cheerful 及中文别名"
    try:
        return await screenshot_service.capture_deck(
            server=binding.server,
            game_id=binding.game_id,
            event_id=resolved_event,
            music_id=music_id or settings.deck_default_music_id,
            difficulty=resolved_difficulty,
            live_type=resolved_live_type,
        )
    except ScreenshotError as exc:
        return exc.to_user_message()


async def handle_admin_blacklist(command: ParsedCommand, operator_id: str) -> str:
    if command.admin_target_type == "uid":
        if not command.server or not command.admin_value:
            return "UID 黑名单需要提供区服和 UID"
        if error := validate_game_id(command.admin_value):
            return error
        if command.admin_subaction == "add":
            await add_blacklist_entry("uid", command.admin_value, operator_id, command.server)
            return f"已将 {server_label(command.server)} UID {command.admin_value} 加入黑名单"
        if command.admin_subaction == "remove":
            removed = await remove_blacklist_entry("uid", command.admin_value, command.server)
            return "已移除该 UID 黑名单" if removed else "该 UID 不在黑名单中"
        if command.admin_subaction == "check":
            entry = await get_blacklist_entry("uid", command.admin_value, command.server)
            return "该 UID 在黑名单中" if entry else "该 UID 不在黑名单中"
        return "不支持的黑名单操作"

    if command.admin_target_type == "qq":
        if not command.admin_value or not command.admin_value.isdigit():
            return "QQ 黑名单需要提供数字 QQ 或 @用户"
        if command.admin_subaction == "add":
            await add_blacklist_entry("qq", command.admin_value, operator_id)
            return f"已将 QQ {command.admin_value} 加入黑名单"
        if command.admin_subaction == "remove":
            removed = await remove_blacklist_entry("qq", command.admin_value)
            return "已移除该 QQ 黑名单" if removed else "该 QQ 不在黑名单中"
        if command.admin_subaction == "check":
            entry = await get_blacklist_entry("qq", command.admin_value)
            return "该 QQ 在黑名单中" if entry else "该 QQ 不在黑名单中"
        return "不支持的黑名单操作"

    return "未知的黑名单目标类型"


async def handle_admin_query_binding(command: ParsedCommand) -> str:
    if command.admin_target_type == "qq":
        if not command.admin_value:
            return "请提供要查询的 QQ"
        settings = await get_or_create_user_settings("qq", command.admin_value)
        bindings = await list_user_bindings("qq", command.admin_value)
        lines = [
            f"QQ {command.admin_value} 绑定信息",
            f"默认区服：{server_label(settings.default_server) if settings.default_server else '未设置'}",
            f"给看状态：{'给看' if settings.allow_share_profile else '不给看'}",
        ]
        if not bindings:
            lines.append("当前没有任何绑定")
        else:
            lines.append("绑定列表：")
            for binding in bindings:
                lines.append(
                    f"- {server_label(binding.server)} {binding.game_id} "
                    f"(创建于 {format_time(binding.created_at)})"
                )
        return "\n".join(lines)

    if command.admin_target_type == "uid":
        if not command.admin_value:
            return "请提供要查询的 UID"
        if error := validate_game_id(command.admin_value):
            return error
        bindings = await query_bindings_by_uid(command.admin_value, command.server)
        if not bindings:
            scope = server_label(command.server) if command.server else "全部区服"
            return f"{scope} 下未查询到 UID {command.admin_value} 的绑定记录"
        lines = [f"UID {command.admin_value} 绑定记录："]
        for binding in bindings:
            settings = await get_or_create_user_settings(binding.platform, binding.user_id)
            lines.append(
                f"- QQ {binding.user_id} / {server_label(binding.server)} "
                f"/ 默认区服 {server_label(settings.default_server) if settings.default_server else '未设置'} "
                f"/ {'给看' if settings.allow_share_profile else '不给看'} "
                f"/ 创建于 {format_time(binding.created_at)}"
            )
        return "\n".join(lines)

    return "未知的查询绑定目标类型"


async def build_auto_update_notifications(
    results: list[RegionUpdateResult],
) -> list[str]:
    messages: list[str] = []
    for result in results:
        if not result.updated or not result.download_success or result.error:
            continue
        messages.append(
            "\n".join(
                [
                    "MoeSekai 主数据已自动更新",
                    f"区服：{server_label(result.server)}",
                    f"版本：{result.previous_version or '-'} -> {result.current_version or '-'}",
                    f"来源：{result.selected_source_name or '未知'}",
                    f"时间：{result.checked_at or '-'}",
                ]
            )
        )
    return messages
