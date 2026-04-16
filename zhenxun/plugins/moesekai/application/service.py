from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
import random
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from nonebot.adapters import Bot
from nonebot.exception import ActionFailed, AdapterException
from nonebot_plugin_alconna import At, Image, Text, UniMessage
from tortoise import Tortoise

from ..adapters.results import (
    MoeImageTextMessage,
    build_alias_profile_image,
    build_image_message,
    build_native_image_text_message,
    build_text_block_image,
)
from ..adapters.reminder_renderer import render_reminder_card
from ..adapters.runtime import AsyncHttpx, MessageUtils, PlatformUtils, logger
from ..adapters.viewmodels import ReminderCardViewModel
from .calculators import calculate_multiplier
from ..command_parser import ParsedCommand
from ..config import get_settings
from ..constants import (
    ALIAS_TARGET_CHARACTER,
    ALIAS_TARGET_MUSIC,
    FEATURE_LIVE_REMINDER,
    FEATURE_NEW_CARD_REMINDER,
    MODULE_NAME,
    PREDICTION_SUPPORTED_SERVERS,
    RANK_SOURCE_NAME,
    SERVER_SET,
    SERVERS,
    SCOPE_GLOBAL,
    STATE_DIR,
    YCX_SUPPORTED_SERVERS,
    make_scope_key,
    server_label,
)
from ..deck import DeckCommandRequest, DeckResolvedRequest, deck_mode_label, to_backend_request
from ..master_data import RegionUpdateResult
from ..providers import (
    alias_provider,
    asset_provider,
    character_cache_provider,
    hub_provider,
    master_data_provider,
    ranking_provider,
    story_cache_provider,
)
from ..repositories import (
    add_blacklist_entry,
    create_notification_record,
    delete_user_binding,
    get_blacklist_entry,
    get_group_feature_toggle,
    get_or_create_user_settings,
    get_user_binding,
    list_notification_record_keys_by_groups,
    get_user_feature_subscription,
    get_user_settings,
    has_notification_record,
    list_blacklist_entries,
    list_enabled_group_feature_toggles,
    list_user_bindings,
    list_user_feature_subscriptions,
    query_bindings_by_uid,
    remove_blacklist_entry,
    remove_user_feature_subscription,
    set_allow_share_profile,
    set_default_server,
    set_group_feature_toggle,
    upsert_user_binding,
    upsert_user_feature_subscription,
)
from ..screenshot import ScreenshotError, screenshot_service
from ..storage.state import JsonStateStore

PREDICTION_RANKS = [50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000]
NEW_CARD_TEST_SCAN_LIMIT = 12
ALIAS_IMAGE_TEXT_THRESHOLD = 100
ALIAS_IMAGE_COUNT_THRESHOLD = 20
_ALIAS_STATE = JsonStateStore(STATE_DIR / "alias_sync_state.json")
_NEW_CARD_PENDING_STATE = JsonStateStore(STATE_DIR / "new_card_pending_state.json")


@dataclass(frozen=True)
class _NewCardReminderItemSpec:
    key: str
    kind: Literal["summary", "card", "stamp"]
    payload: Any


@dataclass(frozen=True)
class _NewCardReminderPreparedItem:
    key: str
    kind: Literal["summary", "card", "stamp"]
    message: UniMessage
    estimated_bytes: int


@dataclass
class _NewCardReminderTargetState:
    platform: str
    group_id: str
    pending_keys: set[str]
    consecutive_failures: int = 0
    aborted: bool = False
    started_at: float = 0.0


class MoeSekaiApplication:
    def __init__(self) -> None:
        self._shared_capture_tasks: dict[str, asyncio.Task[bytes]] = {}

    def validate_game_id(self, game_id: str) -> str | None:
        if not game_id.isdigit():
            return "游戏ID必须全部为数字"
        if not 13 <= len(game_id) <= 20:
            return "游戏ID长度必须在 13 到 20 位之间"
        return None

    def format_time(self, value: datetime | None) -> str:
        if not value:
            return "-"
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _format_iso_time(self, value: str | None, *, include_seconds: bool = False) -> str:
        if not value:
            return "未知"
        format_text = "%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M"
        try:
            if value.isdigit():
                timestamp = int(value)
                if timestamp > 10_000_000_000:
                    timestamp = int(timestamp / 1000)
                return datetime.fromtimestamp(timestamp).strftime(format_text)
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime(
                format_text
            )
        except ValueError:
            return value

    async def _build_image_link_message(
        self,
        image_url: str,
        *,
        link: str | None = None,
    ):
        image = await AsyncHttpx.get_content(image_url)
        return build_native_image_text_message(
            image,
            f"B站链接：{link}" if link else None,
        )

    async def migrate_legacy_bindings(self) -> None:
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
            if not user_id or server not in SERVER_SET or self.validate_game_id(game_id):
                continue
            await upsert_user_binding("qq", user_id, server, game_id)
            settings = await get_or_create_user_settings("qq", user_id)
            if not settings.default_server:
                await set_default_server("qq", user_id, server)

    async def seed_default_aliases(self) -> None:
        return None

    async def sync_music_aliases(self, *, force: bool = False) -> None:
        state = _ALIAS_STATE.load({})
        interval = get_settings().alias_sync_interval_seconds
        last_sync = state.get("music_alias_sync_at")
        if not force and isinstance(last_sync, (int, float)):
            if datetime.now().timestamp() - float(last_sync) < interval:
                return
        alias_items = await hub_provider.get_music_alias_index()
        snapshot_items = alias_provider.refresh_music_alias_snapshot(alias_items)
        state["music_alias_source"] = "MoeSekai-Hub/music_aliases.json"
        state["music_alias_sync_count"] = len(snapshot_items)
        state["music_alias_sync_at"] = datetime.now().timestamp()
        _ALIAS_STATE.save(state)

    async def _is_qq_blacklisted(
        self, platform: str, user_id: str, is_superuser: bool
    ) -> str | None:
        if is_superuser:
            return None
        if await get_blacklist_entry("qq", user_id):
            return "你已被拉黑，当前无法使用 MoeSekai 相关功能"
        return None

    async def _is_uid_blacklisted(
        self, server: str, game_id: str, is_superuser: bool
    ) -> str | None:
        if is_superuser:
            return None
        if await get_blacklist_entry("uid", game_id, server=server):
            return f"该游戏ID({game_id})已被拉黑"
        return None

    async def _resolve_default_server(
        self,
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
        return None, "未绑定"

    async def _resolve_binding_for_user(
        self,
        platform: str,
        requester_is_superuser: bool,
        target_user_id: str,
        explicit_server: str | None,
        *,
        ignore_share: bool = False,
    ) -> tuple[Any | None, str | None]:
        if (
            not requester_is_superuser
            and await get_blacklist_entry("qq", target_user_id)
        ):
            return None, "该用户已被拉黑，无法查询"
        settings = await get_or_create_user_settings(platform, target_user_id)
        if (
            not requester_is_superuser
            and not ignore_share
            and not settings.allow_share_profile
        ):
            return None, "对方设置了不给看，无法查询其绑定档案"

        server, error = await self._resolve_default_server(
            platform,
            target_user_id,
            explicit_server=explicit_server,
            fallback_jp=False,
        )
        if error or not server:
            return None, "未绑定"

        binding = await get_user_binding(platform, target_user_id, server)
        if not binding:
            return None, "未绑定"

        blocked = await self._is_uid_blacklisted(server, binding.game_id, requester_is_superuser)
        if blocked:
            return None, blocked
        return binding, None

    async def _get_event_by_id(self, server: str, event_id: int) -> dict[str, Any] | None:
        for event in await master_data_provider.get_events(server):
            try:
                if int(event.get("id")) == event_id:
                    return event
            except (TypeError, ValueError):
                continue
        return None

    async def handle_bind(
        self,
        platform: str,
        user_id: str,
        server: str | None,
        game_id: str,
        *,
        is_superuser: bool,
    ) -> str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        server = server or "jp"
        if server not in SERVER_SET:
            return "绑定区服仅支持 cn / jp / tw"
        if error := self.validate_game_id(game_id):
            return error
        if error := await self._is_uid_blacklisted(server, game_id, is_superuser):
            return error

        binding = await upsert_user_binding(platform, user_id, server, game_id)
        settings = await get_or_create_user_settings(platform, user_id)
        if not settings.default_server:
            settings = await set_default_server(platform, user_id, server)

        default_server = settings.default_server or server
        return "\n".join(
            [
                "绑定成功！",
                f"区服：{server_label(binding.server)}",
                f"游戏ID：{binding.game_id}",
                f"当前默认服务器为 {server_label(default_server)}",
                "可使用“默认区服 <cn|jp|tw>”切换默认服务器",
            ]
        )

    async def handle_unbind(
        self,
        platform: str,
        user_id: str,
        server: str | None,
        *,
        is_superuser: bool,
    ) -> str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        resolved_server, error = await self._resolve_default_server(
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
        self,
        platform: str,
        user_id: str,
        server: str | None,
        *,
        is_superuser: bool,
    ) -> str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        settings = await get_or_create_user_settings(platform, user_id)
        bindings = await list_user_bindings(platform, user_id)
        if server is None:
            if not bindings:
                return "你还没有绑定任何账号"
            current_server = settings.default_server or bindings[0].server
            lines = [f"当前默认服务器：{server_label(current_server)}", "已绑定区服："]
            for binding in bindings:
                tag = " (默认)" if binding.server == current_server else ""
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
        self,
        platform: str,
        user_id: str,
        allow_share_profile: bool,
    ) -> str:
        await set_allow_share_profile(platform, user_id, allow_share_profile)
        return "已设置为给看" if allow_share_profile else "已设置为不给看"

    async def handle_personal_archive(
        self,
        platform: str,
        user_id: str,
        server: str | None,
        *,
        is_superuser: bool,
    ) -> bytes | str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        resolved_server, error = await self._resolve_default_server(
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
        if error := await self._is_uid_blacklisted(binding.server, binding.game_id, is_superuser):
            return error
        try:
            return await screenshot_service.capture_profile(binding.server, binding.game_id)
        except ScreenshotError as exc:
            return exc.to_user_message()

    async def handle_query_archive(
        self,
        platform: str,
        requester_user_id: str,
        *,
        server: str | None,
        game_id: str | None,
        target_user_id: str | None,
        is_superuser: bool,
    ) -> bytes | str:
        if error := await self._is_qq_blacklisted(platform, requester_user_id, is_superuser):
            return error
        if game_id and target_user_id:
            return "查询档案不能同时指定游戏ID和 @用户"
        if game_id:
            resolved_server = server or "jp"
            if resolved_server not in SERVER_SET:
                return "查询档案区服仅支持 cn / jp / tw"
            if error := self.validate_game_id(game_id):
                return error
            if error := await self._is_uid_blacklisted(resolved_server, game_id, is_superuser):
                return error
            try:
                return await screenshot_service.capture_profile(resolved_server, game_id)
            except ScreenshotError as exc:
                return exc.to_user_message()
        if not target_user_id:
            return "请提供游戏ID或 @用户"
        binding, error = await self._resolve_binding_for_user(
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

    def _build_ranking_message(
        self,
        *,
        server: str,
        event: dict[str, Any] | None,
        snapshot: Any,
        title: str,
        note: str | None = None,
    ) -> list[str]:
        lines = []
        event_status = snapshot.status
        lines.append(f"<strong>状态</strong>：{event_status}")
        if note:
            lines.append(f"<strong>注意</strong>：{note}")

        rank_map = {item.rank: item for item in snapshot.items}
        for rank in PREDICTION_RANKS:
            item = rank_map.get(rank)
            if not item:
                continue
            if item.score is not None and item.prediction is not None:
                lines.append(f"<strong>T{rank}</strong>: 当前 {item.score:,} / 预测 {item.prediction:,}")
            elif item.score is not None:
                label = "结榜" if item.is_final else "当前"
                lines.append(f"<strong>T{rank}</strong>: {label} {item.score:,}")
            elif item.prediction is not None:
                lines.append(f"<strong>T{rank}</strong>: 预测 {item.prediction:,}")

        first_collect_time = next(
            (item.collect_time for item in snapshot.items if item.collect_time),
            None,
        )
        lines.append(f"<strong>采集时间</strong>：{self._format_iso_time(first_collect_time, include_seconds=True)}")
        lines.append(f"<strong>数据来源</strong>：{snapshot.source_name or RANK_SOURCE_NAME}")
        return lines

    async def handle_multiplier(self, values: list[int]) -> str:
        try:
            result = calculate_multiplier(values)
        except ValueError:
            return "用法: 倍率计算 <a> <b> <c> <d> <e>"
        return "\n".join(
            [
                f"车头{result.leader}/内部{result.internal}",
                f"倍率为{result.multiplier}",
                f"实效为{result.effective_percent}%",
            ]
        )

    async def handle_prediction(
        self,
        platform: str,
        user_id: str,
        server: str | None,
        event_id: int | None = None,
        *,
        is_superuser: bool,
    ) -> UniMessage | str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或使用区服前缀指定查询区服"
        if resolved_server not in PREDICTION_SUPPORTED_SERVERS:
            return f"{server_label(resolved_server)}暂不支持预测线查询"
        snapshot, _, used_previous_event = await ranking_provider.get_snapshot(
            resolved_server,
            event_id=event_id,
            fallback="prev",
        )
        if event_id is not None:
            event = await self._get_event_by_id(resolved_server, event_id)
            if not event:
                return f"{server_label(resolved_server)}不存在活动 {event_id}"
        else:
            event = await master_data_provider.get_current_event(resolved_server, fallback="prev")
        if not snapshot:
            if event_id is None:
                return f"{server_label(resolved_server)}当前没有进行中的活动，且没有可用的上期活动数据"
            return f"第{event_id}期活动暂无可用预测线数据"
        if not event:
            event = await self._get_event_by_id(resolved_server, snapshot.event_id)
        lines = self._build_ranking_message(
            server=resolved_server,
            event=event,
            snapshot=snapshot,
            title="预测线",
            note="当前无进行中活动，已回退到上期活动结榜线" if used_previous_event else None,
        )
        banner = None
        if event and event.get("assetbundleName"):
            banner = await asset_provider.get_event_banner(
                resolved_server, str(event["assetbundleName"])
            )

        view_model = ReminderCardViewModel(
            title=f"{server_label(resolved_server)}预测线",
            subtitle=f'第{snapshot.event_id}期 {str(event.get("name", "")).strip()}' if event else None,
            lines=lines,
            banner=banner,
            accent="Prediction",
        )
        image_bytes = await render_reminder_card(view_model)
        return build_image_message(image_bytes)

    async def handle_update(
        self,
        platform: str,
        user_id: str,
        server: str | None,
        *,
        update_all: bool,
        is_superuser: bool,
    ) -> str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        if update_all:
            if not is_superuser:
                return "pjsk update all 仅超级用户可用"
            results = await master_data_provider.update_all(force=True)
            return "\n\n".join(result.to_message() for result in results)
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号设置默认区服，或使用 pjsk update <cn|jp|tw> 显式指定区服"
        result = await master_data_provider.update_region(resolved_server, force=True)
        return result.to_message()

    async def handle_ycx(
        self,
        platform: str,
        user_id: str,
        server: str | None,
        event_id: int | None = None,
        *,
        is_superuser: bool,
    ) -> bytes | str:
        if error := await self._is_qq_blacklisted(platform, user_id, is_superuser):
            return error
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或使用区服前缀指定查询区服"
        if resolved_server not in YCX_SUPPORTED_SERVERS:
            return f"{server_label(resolved_server)}暂不支持ycx榜线查询"
        current_event = None
        if event_id is not None:
            current_event = await self._get_event_by_id(resolved_server, event_id)
            if not current_event:
                return f"{server_label(resolved_server)}不存在活动 {event_id}"
        try:
            return await screenshot_service.capture_ranking(resolved_server, event_id=event_id)
        except ScreenshotError as exc:
            if event_id is None or current_event is None:
                return exc.to_user_message()
            snapshot = await ranking_provider.get_latest_snapshot(resolved_server, event_id)
            if not snapshot:
                return f"第{event_id}期活动暂无可用榜线数据"
            lines = self._build_ranking_message(
                server=resolved_server,
                event=current_event,
                snapshot=snapshot,
                title="结榜榜线",
                note="历史活动页面暂不可用，已回退为文字数据",
            )
            return "\n".join(lines).replace("<strong>", "").replace("</strong>", "")

    async def _resolve_music_query(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> tuple[int | None, str | None]:
        music_id = await self._resolve_music_id(
            query,
            platform=platform,
            group_id=group_id,
        )
        if not music_id:
            return None, f"未找到歌曲：{query}"
        return int(music_id), None

    async def _resolve_character_query(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> tuple[int | None, str | None]:
        character_id = await self._resolve_character_id(
            query,
            platform=platform,
            group_id=group_id,
        )
        if not character_id:
            return None, f"未找到角色：{query}"
        return int(character_id), None

    async def _resolve_deck_event_id(
        self,
        server: str,
        *,
        event_id: int | None,
        not_found_message: str,
    ) -> tuple[int | None, str | None]:
        if event_id is not None:
            event = await self._get_event_by_id(server, event_id)
            if not event:
                return None, f"{server_label(server)}不存在活动 {event_id}"
            return event_id, None
        current_event = await master_data_provider.get_current_event(
            server,
            fallback="next_first",
        )
        if not current_event:
            return None, not_found_message
        return int(current_event["id"]), None

    async def _split_challenge_queries(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        tokens = [segment for segment in str(query).split() if segment]
        if not tokens:
            return None, None, "挑战组卡需要至少提供一个角色"

        fallback_character_query: str | None = None
        fallback_music_query: str | None = None
        for index in range(len(tokens), 0, -1):
            character_query = " ".join(tokens[:index])
            character_id = await self._resolve_character_id(
                character_query,
                platform=platform,
                group_id=group_id,
            )
            if not character_id:
                continue
            if index == len(tokens):
                return character_query, None, None
            music_query = " ".join(tokens[index:])
            if await self._resolve_music_id(
                music_query,
                platform=platform,
                group_id=group_id,
            ):
                return character_query, music_query, None
            if fallback_character_query is None:
                fallback_character_query = character_query
                fallback_music_query = music_query

        if fallback_music_query:
            return None, None, f"未找到歌曲：{fallback_music_query}"
        return None, None, f"未找到角色：{query}"

    async def _resolve_deck_request(
        self,
        request: DeckCommandRequest,
        *,
        server: str,
        game_id: str,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> tuple[DeckResolvedRequest | None, str | None]:
        settings = get_settings()
        mode = request.mode
        kind_label = deck_mode_label(mode)

        if mode == "event":
            event_id, error = await self._resolve_deck_event_id(
                server,
                event_id=request.event_id,
                not_found_message="当前和下一期活动都不可用，请手动指定活动ID",
            )
            if error:
                return None, error
            music_id = settings.deck_default_music_id
            if request.explicit_music and request.music_query:
                music_id, error = await self._resolve_music_query(
                    request.music_query,
                    platform=platform,
                    group_id=group_id,
                )
                if error:
                    return None, error
            return (
                DeckResolvedRequest(
                    mode=mode,
                    kind_label=kind_label,
                    server=server,
                    game_id=game_id,
                    event_id=event_id,
                    music_id=music_id,
                    difficulty=request.difficulty or settings.deck_default_difficulty,
                    live_type=request.live_type or settings.deck_default_live_type,
                ),
                None,
            )

        if mode == "custom":
            if request.custom_bonus is None:
                return None, "组卡需要指定箱活加成或混活加成"
            music_id = settings.deck_default_music_id
            if request.explicit_music and request.music_query:
                music_id, error = await self._resolve_music_query(
                    request.music_query,
                    platform=platform,
                    group_id=group_id,
                )
                if error:
                    return None, error
            difficulty = (
                request.difficulty
                or ("master" if request.explicit_music else settings.deck_default_difficulty)
            )
            live_type = request.live_type or settings.deck_default_live_type

            resolved_request = DeckResolvedRequest(
                mode=mode,
                kind_label=kind_label,
                server=server,
                game_id=game_id,
                music_id=music_id,
                difficulty=difficulty,
                live_type=live_type,
            )
            if request.custom_bonus.kind == "unit":
                return (
                    replace(
                        resolved_request,
                        custom_attr=request.custom_bonus.attr,
                        custom_unit=request.custom_bonus.unit,
                    ),
                    None,
                )

            character_ids: list[int] = []
            character_units: dict[int, str] = {}
            seen_ids: set[int] = set()
            for character in request.custom_bonus.characters:
                character_id, error = await self._resolve_character_query(
                    character.query,
                    platform=platform,
                    group_id=group_id,
                )
                if error:
                    return None, error
                if character_id not in seen_ids:
                    seen_ids.add(character_id)
                    character_ids.append(character_id)
                if character.support_unit:
                    existing_unit = character_units.get(character_id)
                    if existing_unit and existing_unit != character.support_unit:
                        return None, f"同一虚拟歌手不能同时指定多个团体：{character.query}"
                    character_units[character_id] = character.support_unit

            return (
                replace(
                    resolved_request,
                    custom_attr=request.custom_bonus.attr,
                    custom_character_ids=tuple(character_ids),
                    custom_character_units=character_units,
                ),
                None,
            )

        if mode == "mysekai":
            event_id, error = await self._resolve_deck_event_id(
                server,
                event_id=request.event_id,
                not_found_message="当前和下一期活动都不可用，请手动指定活动ID",
            )
            if error:
                return None, error
            return (
                DeckResolvedRequest(
                    mode=mode,
                    kind_label=kind_label,
                    server=server,
                    game_id=game_id,
                    event_id=event_id,
                ),
                None,
            )

        if mode == "strongest":
            music_id = settings.deck_strongest_default_music_id
            if request.explicit_music and request.music_query:
                music_id, error = await self._resolve_music_query(
                    request.music_query,
                    platform=platform,
                    group_id=group_id,
                )
                if error:
                    return None, error
            difficulty = (
                request.difficulty
                or (
                    "master"
                    if request.explicit_music
                    else settings.deck_strongest_default_difficulty
                )
            )
            return (
                DeckResolvedRequest(
                    mode=mode,
                    kind_label=kind_label,
                    server=server,
                    game_id=game_id,
                    music_id=music_id,
                    difficulty=difficulty,
                    live_type=request.live_type or settings.deck_default_live_type,
                    strongest_target=request.strongest_target or "power",
                ),
                None,
            )

        if mode == "challenge":
            character_query, music_query, error = await self._split_challenge_queries(
                request.free_text_query or request.character_query or "",
                platform=platform,
                group_id=group_id,
            )
            if error:
                return None, error
            if not character_query:
                return None, "挑战组卡需要至少提供一个角色"
            character_id, error = await self._resolve_character_query(
                character_query,
                platform=platform,
                group_id=group_id,
            )
            if error:
                return None, error
            music_id = settings.deck_challenge_default_music_id
            if music_query:
                music_id, error = await self._resolve_music_query(
                    music_query,
                    platform=platform,
                    group_id=group_id,
                )
                if error:
                    return None, error
            return (
                DeckResolvedRequest(
                    mode=mode,
                    kind_label=kind_label,
                    server=server,
                    game_id=game_id,
                    character_id=character_id,
                    music_id=music_id,
                    difficulty=request.difficulty
                    or settings.deck_challenge_default_difficulty,
                ),
                None,
            )

        return None, f"暂不支持的组卡模式：{mode}"

    async def handle_deck(
        self,
        platform: str,
        requester_user_id: str,
        *,
        request: DeckCommandRequest,
        group_id: str | None = None,
        is_superuser: bool,
    ) -> bytes | str:
        if error := await self._is_qq_blacklisted(platform, requester_user_id, is_superuser):
            return error
        target_user_id = request.target_user_id or requester_user_id
        binding, error = await self._resolve_binding_for_user(
            platform,
            is_superuser,
            target_user_id,
            request.server,
            ignore_share=target_user_id == requester_user_id,
        )
        if error:
            return error
        resolved_request, error = await self._resolve_deck_request(
            request,
            server=binding.server,
            game_id=binding.game_id,
            platform=platform,
            group_id=group_id,
        )
        if error or not resolved_request:
            return error or "组卡参数解析失败"
        try:
            return await screenshot_service.capture_deck(
                request=to_backend_request(resolved_request),
            )
        except ScreenshotError as exc:
            return exc.to_user_message()

    async def handle_story(self, event_id: int, *, force_refresh: bool = False):
        event = await self._get_event_by_id("jp", event_id)
        if not event:
            return f"日服不存在活动 {event_id}"
        if not force_refresh:
            cached = story_cache_provider.get(event_id)
            if cached is not None:
                bvid_url = await hub_provider.get_event_bilibili_url(event_id)
                return build_native_image_text_message(
                    cached,
                    f"B站链接：{bvid_url}" if bvid_url else None,
                )
        try:
            if force_refresh:
                image = await screenshot_service.capture_story(event_id)
            else:
                image = await self._await_shared_capture(
                    f"story:{event_id}",
                    lambda: screenshot_service.capture_story(event_id),
                )
        except ScreenshotError as exc:
            return exc.to_user_message()
        story_cache_provider.set(event_id, image)
        bvid_url = await hub_provider.get_event_bilibili_url(event_id)
        return build_native_image_text_message(
            image,
            f"B站链接：{bvid_url}" if bvid_url else None,
        )

    async def handle_random_manga(self) -> UniMessage | str:
        manga = await hub_provider.get_random_manga()
        if not manga or not manga.get("image_url"):
            return "当前暂无可用四格数据"
        return await self._build_image_link_message(
            manga["image_url"],
            link=manga.get("url"),
        )

    async def handle_manga_by_id(self, manga_id: int) -> UniMessage | str:
        manga = await hub_provider.get_manga_by_id(manga_id)
        if not manga or not manga.get("image_url"):
            return f"未找到第 {manga_id} 话四格漫画"
        return await self._build_image_link_message(
            manga["image_url"],
            link=manga.get("url"),
        )

    async def _resolve_character_id(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        result = await alias_provider.resolve_character(
            query,
            platform=platform,
            group_id=group_id,
        )
        return result.target_id if result else None

    async def _await_shared_capture(
        self,
        key: str,
        factory: Callable[[], Awaitable[bytes]],
    ) -> bytes:
        task = self._shared_capture_tasks.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            self._shared_capture_tasks[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._shared_capture_tasks.get(key) is task:
                self._shared_capture_tasks.pop(key, None)

    async def handle_character(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
        force_refresh: bool = False,
    ) -> bytes | str:
        character_id = await self._resolve_character_id(
            query,
            platform=platform,
            group_id=group_id,
        )
        if not character_id:
            return f"未找到角色：{query}"
        resolved_character_id = int(character_id)
        if not force_refresh:
            cached = character_cache_provider.get(resolved_character_id)
            if cached is not None:
                return cached
        try:
            if force_refresh:
                image = await screenshot_service.capture_character(resolved_character_id)
            else:
                image = await self._await_shared_capture(
                    f"character:{resolved_character_id}",
                    lambda: screenshot_service.capture_character(resolved_character_id),
                )
        except ScreenshotError as exc:
            return exc.to_user_message()
        character_cache_provider.set(resolved_character_id, image)
        return image

    async def _resolve_music_id(
        self,
        query: str,
        *,
        platform: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        result = await alias_provider.resolve_music(
            query,
            platform=platform,
            group_id=group_id,
        )
        return result.target_id if result else None

    @staticmethod
    def _format_alias_profile_text(profile: Any) -> str:
        lines = [profile.display_title]
        if profile.merged_aliases:
            lines.append(f"别名：{'，'.join(profile.merged_aliases)}")
        if profile.group_aliases:
            lines.append(f"本群别名：{'，'.join(profile.group_aliases)}")
        return "\n".join(lines)

    async def _maybe_render_alias_message(
        self,
        text: str,
        *,
        alias_count: int = 0,
    ) -> str | MoeImageTextMessage:
        if len(text) <= ALIAS_IMAGE_TEXT_THRESHOLD and alias_count <= ALIAS_IMAGE_COUNT_THRESHOLD:
            return text
        return build_native_image_text_message(build_text_block_image(text))

    async def _maybe_render_alias_profile_message(
        self,
        profile: Any,
    ) -> str | MoeImageTextMessage:
        text = self._format_alias_profile_text(profile)
        alias_count = len(profile.merged_aliases) + len(profile.group_aliases)
        if len(text) <= ALIAS_IMAGE_TEXT_THRESHOLD and alias_count <= ALIAS_IMAGE_COUNT_THRESHOLD:
            return text
        return build_native_image_text_message(
            build_alias_profile_image(
                title=profile.display_title,
                aliases=profile.merged_aliases,
                group_aliases=profile.group_aliases,
            )
        )

    async def handle_alias_command(
        self,
        *,
        target_type: Literal["character", "music"],
        operation: Literal["query", "add", "remove"],
        query: str,
        alias: str | None = None,
        group_id: str | None = None,
        platform: str | None = None,
        user_id: str | None = None,
        is_superuser: bool = False,
        can_manage_group: bool = False,
        global_scope: bool = False,
    ) -> str | MoeImageTextMessage:
        scope = SCOPE_GLOBAL if global_scope else make_scope_key(group_id, platform=platform)
        allow_global = is_superuser or (
            group_id and group_id in get_settings().alias_global_editor_groups
        )
        if operation in {"add", "remove"}:
            if global_scope and not allow_global:
                return "当前没有编辑全局别名的权限"
            if not global_scope and not (is_superuser or can_manage_group):
                return "只有群管理员或超级用户才能编辑本群别名"

        if operation == "query":
            if target_type == ALIAS_TARGET_CHARACTER:
                resolved = await alias_provider.resolve_character(
                    query,
                    platform=platform,
                    group_id=group_id,
                )
                profile_loader = alias_provider.get_character_profile
                search_loader = alias_provider.search_character
            else:
                resolved = await alias_provider.resolve_music(
                    query,
                    platform=platform,
                    group_id=group_id,
                )
                profile_loader = alias_provider.get_music_profile
                search_loader = alias_provider.search_music

            if resolved:
                profile = await profile_loader(
                    resolved.target_id,
                    platform=platform,
                    group_id=group_id,
                )
                if not profile:
                    return "未找到相关别名"
                return await self._maybe_render_alias_profile_message(profile)

            matches = await search_loader(
                query,
                platform=platform,
                group_id=group_id,
            )
            if not matches:
                return "未找到相关别名"
            result = "\n".join(
                f"{entry.matched_text} -> {entry.target_id}. {entry.canonical_name}"
                for entry in matches
            )
            return await self._maybe_render_alias_message(result)

        if operation == "add":
            if not alias:
                return "请提供要添加的别名"
            if target_type == ALIAS_TARGET_CHARACTER:
                resolved = await alias_provider.resolve_character(
                    query,
                    platform=platform,
                    group_id=group_id,
                )
            else:
                resolved = await alias_provider.resolve_music(
                    query,
                    platform=platform,
                    group_id=group_id,
                )
            target_id = resolved.target_id if resolved else None
            if not target_id:
                return f"未找到目标：{query}"
            await alias_provider.add_managed_alias(
                target_type=target_type,
                target_value=target_id,
                alias=alias,
                scope=scope,
                created_by=user_id or "system",
            )
            return f"已添加{'全局' if scope == SCOPE_GLOBAL else '本群'}别名：{alias} -> {target_id}"

        if not alias:
            return "请提供要删除的别名"
        removed = await alias_provider.remove_managed_alias(
            target_type=target_type,
            alias=alias,
            scope=scope,
        )
        if removed == "deleted":
            return "已删除别名"
        if removed == "system":
            return "该别名为系统别名，不可删除"
        return "该别名不存在"

    async def handle_admin_blacklist(self, command: ParsedCommand, operator_id: str) -> str:
        if command.admin_target_type == "uid":
            if not command.server or not command.admin_value:
                return "UID 黑名单需要提供区服和 UID"
            if error := self.validate_game_id(command.admin_value):
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

    async def handle_admin_query_binding(self, command: ParsedCommand) -> str:
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
                        f"(创建于 {self.format_time(binding.created_at)})"
                    )
            return "\n".join(lines)

        if command.admin_target_type == "uid":
            if not command.admin_value:
                return "请提供要查询的 UID"
            if error := self.validate_game_id(command.admin_value):
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
                    f"/ 创建于 {self.format_time(binding.created_at)}"
                )
            return "\n".join(lines)

        return "未知的查询绑定目标类型"

    async def handle_live_toggle(
        self,
        *,
        platform: str,
        user_id: str,
        group_id: str | None,
        server: str | None,
        action: Literal["enable", "disable", "status"],
        is_superuser: bool,
        can_manage_group: bool,
    ) -> str:
        if not group_id:
            return "live提醒 只能在群聊中使用"
        if action != "status" and not (is_superuser or can_manage_group):
            return "只有群管理员或超级用户才能修改 live 提醒开关"
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或显式指定区服"
        if action == "status":
            toggle = await get_group_feature_toggle(
                platform=platform,
                group_id=group_id,
                feature_name=FEATURE_LIVE_REMINDER,
                server=resolved_server,
            )
            status = "开启" if toggle and toggle.enabled else "关闭"
            return f"{server_label(resolved_server)} live提醒当前为：{status}"
        enabled = action == "enable"
        await set_group_feature_toggle(
            platform=platform,
            group_id=group_id,
            feature_name=FEATURE_LIVE_REMINDER,
            server=resolved_server,
            enabled=enabled,
        )
        return f"已{'开启' if enabled else '关闭'} {server_label(resolved_server)} live提醒"

    async def handle_new_card_toggle(
        self,
        *,
        platform: str,
        user_id: str,
        group_id: str | None,
        server: str | None,
        action: Literal["enable", "disable", "status"],
        is_superuser: bool,
        can_manage_group: bool,
    ) -> str:
        if not group_id:
            return "新卡上线提醒 只能在群聊中使用"
        if action != "status" and not (is_superuser or can_manage_group):
            return "只有群管理员或超级用户才能修改新卡上线提醒开关"
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或显式指定区服"
        if action == "status":
            toggle = await get_group_feature_toggle(
                platform=platform,
                group_id=group_id,
                feature_name=FEATURE_NEW_CARD_REMINDER,
                server=resolved_server,
            )
            status = "开启" if toggle and toggle.enabled else "关闭"
            return f"{server_label(resolved_server)} 新卡上线提醒当前为：{status}"
        enabled = action == "enable"
        await set_group_feature_toggle(
            platform=platform,
            group_id=group_id,
            feature_name=FEATURE_NEW_CARD_REMINDER,
            server=resolved_server,
            enabled=enabled,
        )
        return f"已{'开启' if enabled else '关闭'} {server_label(resolved_server)} 新卡上线提醒"

    async def handle_live_subscription(
        self,
        *,
        platform: str,
        user_id: str,
        group_id: str | None,
        server: str | None,
        subscribe: bool,
    ) -> str:
        if not group_id:
            return "live提醒订阅只能在群聊中使用"
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或显式指定区服"
        if subscribe:
            await upsert_user_feature_subscription(
                platform=platform,
                group_id=group_id,
                user_id=user_id,
                feature_name=FEATURE_LIVE_REMINDER,
                server=resolved_server,
            )
            return f"已订阅 {server_label(resolved_server)} live提醒"
        removed = await remove_user_feature_subscription(
            platform=platform,
            group_id=group_id,
            user_id=user_id,
            feature_name=FEATURE_LIVE_REMINDER,
            server=resolved_server,
        )
        return (
            f"已取消订阅 {server_label(resolved_server)} live提醒"
            if removed
            else "你当前没有订阅该 live 提醒"
        )

    async def _build_live_message(
        self,
        server: str,
        live: dict[str, Any],
        *,
        remind_start: datetime,
        title: str,
    ) -> UniMessage:
        banner = None
        if live.get("assetbundleName"):
            banner = await asset_provider.get_virtual_live_banner(
                server, str(live["assetbundleName"])
            )
        image = await render_reminder_card(
            ReminderCardViewModel(
                title=title,
                subtitle=str(live.get("name", "")).strip() or None,
                lines=[
                    f"区服：{server_label(server)}",
                    f"提醒时间：{remind_start.strftime('%Y-%m-%d %H:%M')}",
                ],
                banner=banner,
                accent="Live Reminder",
            )
        )
        return UniMessage([Image(raw=image)])

    async def handle_test_live_reminder(
        self,
        *,
        platform: str,
        user_id: str,
        server: str | None,
        live_id: int | None = None,
    ) -> UniMessage | str:
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或显式指定区服"
        lives = await master_data_provider.get_virtual_lives(resolved_server)
        target = None
        if live_id is not None:
            target = next((item for item in lives if int(item.get("id", 0)) == live_id), None)
        elif lives:
            target = lives[-1]
        if not target:
            return "未找到可测试的 live 数据"
        return await self._build_live_message(
            resolved_server,
            target,
            remind_start=datetime.now() + timedelta(minutes=5),
            title="Live 提醒测试",
        )

    def _new_card_sort_key(self, card: dict[str, Any]) -> tuple[int, int]:
        try:
            release_at = int(card.get("releaseAt") or 0)
        except (TypeError, ValueError):
            release_at = 0
        try:
            card_id = int(card.get("id") or 0)
        except (TypeError, ValueError):
            card_id = 0
        return release_at, card_id

    def _new_stamp_sort_key(self, stamp: dict[str, Any]) -> tuple[int, int]:
        try:
            seq = int(stamp.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        try:
            stamp_id = int(stamp.get("id") or 0)
        except (TypeError, ValueError):
            stamp_id = 0
        return seq, stamp_id

    def _new_card_base_record_key(
        self,
        *,
        revision: str | None,
        version: str | None = None,
    ) -> str:
        return str(revision or version or "unknown")

    def _new_card_summary_record_key(self, base_key: str) -> str:
        return f"{base_key}:summary"

    def _new_card_card_record_key(self, base_key: str, card_id: int | str) -> str:
        return f"{base_key}:card:{card_id}"

    def _new_card_stamp_record_key(self, base_key: str, stamp_id: int | str) -> str:
        return f"{base_key}:stamp:{stamp_id}"

    def _normalize_new_card_pending_state(self, payload: Any) -> dict[str, dict[str, dict[str, Any]]]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict[str, dict[str, Any]]] = {}
        for server, snapshots in payload.items():
            server_text = str(server).strip().lower()
            if not server_text or not isinstance(snapshots, dict):
                continue
            normalized_snapshots: dict[str, dict[str, Any]] = {}
            for base_key, snapshot in snapshots.items():
                if not isinstance(snapshot, dict):
                    continue
                cards = [
                    item for item in snapshot.get("cards", [])
                    if isinstance(item, dict)
                ]
                stamps = [
                    item for item in snapshot.get("stamps", [])
                    if isinstance(item, dict)
                ]
                if not cards and not stamps:
                    continue
                normalized_snapshots[str(base_key)] = {
                    "current_revision": (
                        str(snapshot.get("current_revision"))
                        if snapshot.get("current_revision") is not None
                        else None
                    ),
                    "current_version": (
                        str(snapshot.get("current_version"))
                        if snapshot.get("current_version") is not None
                        else None
                    ),
                    "cards": cards,
                    "stamps": stamps,
                }
            if normalized_snapshots:
                normalized[server_text] = normalized_snapshots
        return normalized

    def _load_new_card_pending_state(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self._normalize_new_card_pending_state(_NEW_CARD_PENDING_STATE.load({}))

    def _save_new_card_pending_state(self, payload: dict[str, dict[str, dict[str, Any]]]) -> None:
        _NEW_CARD_PENDING_STATE.save(self._normalize_new_card_pending_state(payload))

    def _store_new_card_pending_snapshot(
        self,
        state: dict[str, dict[str, dict[str, Any]]],
        *,
        server: str,
        base_key: str,
        revision: str | None,
        version: str | None,
        cards: list[dict[str, Any]],
        stamps: list[dict[str, Any]],
    ) -> None:
        if not cards and not stamps:
            return
        server_state = state.setdefault(server, {})
        # 这里先持久化“本轮需要补发的原始新增项”，这样即使发送中途失败，
        # 下一轮普通 probe 也还能继续按现有 notification_records 补发遗漏逻辑项。
        server_state[base_key] = {
            "current_revision": revision,
            "current_version": version,
            "cards": cards,
            "stamps": stamps,
        }

    def _delete_new_card_pending_snapshot(
        self,
        state: dict[str, dict[str, dict[str, Any]]],
        *,
        server: str,
        base_key: str,
    ) -> None:
        server_state = state.get(server)
        if not server_state:
            return
        server_state.pop(base_key, None)
        if not server_state:
            state.pop(server, None)

    def _new_card_summary_lines(self, revision: str | None) -> list[str]:
        return [
            f"Revision：{revision or '-'}",
            "检测到新卡/表情更新",
        ]

    @staticmethod
    def _new_card_setting_value(
        settings: Any,
        primary_name: str,
        *,
        default: int | float,
        legacy_name: str | None = None,
    ) -> int | float:
        value = getattr(settings, primary_name, None)
        if value is not None:
            return value
        if legacy_name:
            legacy_value = getattr(settings, legacy_name, None)
            if legacy_value is not None:
                return legacy_value
        return default

    def _new_card_rarity_label(self, rarity: str | None) -> str:
        normalized = str(rarity or "").strip().lower()
        return {
            "rarity_4": "🌟4",
            "rarity_3": "🌟3",
            "rarity_2": "🌟2",
            "rarity_1": "🌟1",
            "rarity_birthday": "生日",
        }.get(normalized, str(rarity or "-").strip() or "-")

    async def _resolve_new_card_character_name(self, character_id: Any) -> str:
        character_text = str(character_id or "").strip()
        if not character_text:
            return "角色-"
        profile = await alias_provider.get_character_profile(character_text)
        if profile and profile.canonical_name:
            return profile.canonical_name
        return f"角色{character_text}"

    async def _build_new_card_text_block(self, card: dict[str, Any]) -> str:
        character_name = await self._resolve_new_card_character_name(card.get("characterId"))
        card_name = (
            str(card.get("name") or "").strip()
            or str(card.get("prefix") or "").strip()
            or "未知卡牌"
        )
        return (
            f"{card.get('id', '-')}:"
            f"{self._new_card_rarity_label(card.get('cardRarityType'))}"
            f"[{card_name}]"
            f"{character_name}"
        )

    @staticmethod
    def _build_new_card_image_segment(
        *,
        local_path: Path | None,
        raw_bytes: bytes | None,
    ) -> Image | None:
        if local_path and local_path.is_file():
            # NapCat/OneBot 直接吃本地路径时，WS 里不需要再塞整段 base64，
            # 能明显减轻大卡图消息的发送体积；只有路径不可用时才退回 raw。
            return Image(path=str(local_path))
        if raw_bytes:
            return Image(raw=raw_bytes)
        return None

    def _new_card_message_bytes_limit(self, *, kind: Literal["card", "stamp"]) -> int:
        settings = get_settings()
        if kind == "card":
            return max(
                1,
                int(
                    self._new_card_setting_value(
                        settings,
                        "new_card_plain_card_max_estimated_bytes",
                        default=41_943_040,
                        legacy_name="new_card_forward_max_estimated_bytes",
                    )
                ),
            )
        return max(
            1,
            int(
                self._new_card_setting_value(
                    settings,
                    "new_card_plain_stamp_max_estimated_bytes",
                    default=10_485_760,
                    legacy_name="new_card_forward_max_estimated_bytes",
                )
            ),
        )

    def _build_new_card_plain_message_batches(
        self,
        items: list[_NewCardReminderPreparedItem],
    ) -> list[list[_NewCardReminderPreparedItem]]:
        def split_items(
            values: list[_NewCardReminderPreparedItem],
            *,
            max_bytes: int,
        ) -> list[list[_NewCardReminderPreparedItem]]:
            batches: list[list[_NewCardReminderPreparedItem]] = []
            current: list[_NewCardReminderPreparedItem] = []
            current_bytes = 0
            for item in values:
                if current and current_bytes + item.estimated_bytes > max_bytes:
                    batches.append(current)
                    current = []
                    current_bytes = 0
                current.append(item)
                current_bytes += item.estimated_bytes
            if current:
                batches.append(current)
            return batches

        summary_items = [item for item in items if item.kind == "summary"]
        card_items = [item for item in items if item.kind == "card"]
        stamp_items = [item for item in items if item.kind == "stamp"]
        batches: list[list[_NewCardReminderPreparedItem]] = [[item] for item in summary_items]
        batches.extend(
            split_items(
                card_items,
                # OneBot 经 WS 发图时还会把原始字节编码成更大的 payload，这里只保留单一字节阈值，
                # 在尽量少发消息和避免超大消息再次卡死之间取一个简单可调的平衡。
                max_bytes=self._new_card_message_bytes_limit(kind="card"),
            )
        )
        batches.extend(
            split_items(
                stamp_items,
                max_bytes=self._new_card_message_bytes_limit(kind="stamp"),
            )
        )
        return batches

    def _compose_new_card_plain_message(
        self,
        items: list[_NewCardReminderPreparedItem],
    ) -> UniMessage:
        message = UniMessage()
        for index, item in enumerate(items):
            if index > 0:
                message += UniMessage([Text("\n")])
            if isinstance(item.message, UniMessage):
                message += item.message
            else:
                message += MessageUtils.build_message(item.message)
        return message

    def _build_new_card_dispatch_results(
        self,
        results: list[RegionUpdateResult],
        pending_state: dict[str, dict[str, dict[str, Any]]],
    ) -> list[RegionUpdateResult]:
        merged: dict[tuple[str, str], RegionUpdateResult] = {}
        for server, snapshots in pending_state.items():
            for base_key, snapshot in snapshots.items():
                merged[(server, base_key)] = RegionUpdateResult(
                    server=server,
                    updated=True,
                    download_success=True,
                    current_revision=snapshot.get("current_revision"),
                    current_version=snapshot.get("current_version"),
                    added_records={
                        "cards": list(snapshot.get("cards", [])),
                        "stamps": list(snapshot.get("stamps", [])),
                    },
                )
        for result in results:
            if not result.updated or not result.download_success or result.error:
                continue
            cards = result.added_records.get("cards", [])
            stamps = result.added_records.get("stamps", [])
            if not cards and not stamps:
                continue
            base_key = self._new_card_base_record_key(
                revision=result.current_revision,
                version=result.current_version,
            )
            self._store_new_card_pending_snapshot(
                pending_state,
                server=result.server,
                base_key=base_key,
                revision=result.current_revision,
                version=result.current_version,
                cards=cards,
                stamps=stamps,
            )
            # fresh result 覆盖同 base_key 的旧快照，避免下一轮 pending 重放仍使用旧 payload。
            merged[(result.server, base_key)] = result
        return [
            merged[key]
            for key in sorted(merged, key=lambda item: (item[0], item[1]))
        ]

    def _log_new_card_stage(
        self,
        server: str,
        stage: str,
        started_at: float,
        *,
        extra: str | None = None,
    ) -> None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        suffix = f" {extra}" if extra else ""
        logger.debug(
            f"MoeSekai 新卡提醒阶段 [{server_label(server)}] {stage} {elapsed_ms:.1f}ms{suffix}",
            MODULE_NAME,
        )

    def _extract_new_card_send_error_code(self, exc: Exception) -> str | None:
        info = getattr(exc, "info", None)
        if isinstance(info, dict):
            for key in ("retcode", "code", "status"):
                value = info.get(key)
                if value is not None:
                    return str(value)
        if exc.args and isinstance(exc.args[0], dict):
            for key in ("retcode", "code", "status"):
                value = exc.args[0].get(key)
                if value is not None:
                    return str(value)
        for attr_name in ("status_code", "code"):
            value = getattr(exc, attr_name, None)
            if value is not None:
                return str(value)
        return None

    def _is_expected_new_card_send_error(self, exc: Exception) -> bool:
        if exc.__class__.__name__ in {"BridgeConnectionTimeout", "BridgeResponseTimeout"}:
            return True
        return isinstance(
            exc,
            (
                asyncio.TimeoutError,
                ActionFailed,
                AdapterException,
            ),
        )

    def _log_new_card_send_warning(
        self,
        *,
        server: str,
        revision: str | None,
        group_id: str,
        item_key: str,
        batch_index: int,
        node_count: int,
        estimated_bytes: int,
        mode: str,
        exc: Exception,
    ) -> None:
        error_code = self._extract_new_card_send_error_code(exc)
        error_code_text = f" error_code={error_code}" if error_code else ""
        logger.warning(
            " ".join(
                [
                    f"MoeSekai 新卡提醒发送失败({mode})",
                    f"server={server}",
                    f"revision={revision or '-'}",
                    f"group_id={group_id}",
                    f"item_key={item_key}",
                    f"batch_index={batch_index}",
                    f"nodes={node_count}",
                    f"estimated_bytes={estimated_bytes}",
                ]
            )
            + error_code_text,
            MODULE_NAME,
            e=exc,
        )

    async def _run_new_card_asset_fetch(
        self,
        semaphore: asyncio.Semaphore | None,
        fetcher: Callable[[], Awaitable[bytes | None]],
    ) -> bytes | None:
        if semaphore is None:
            return await fetcher()
        async with semaphore:
            return await fetcher()

    async def _build_new_card_segments(
        self,
        server: str,
        card: dict[str, Any],
        *,
        asset_timeout: float | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[list[Any], bool, int]:
        text_block = await self._build_new_card_text_block(card)
        segments: list[Any] = [Text(text_block)]
        estimated_bytes = len(text_block.encode("utf-8"))
        has_media = False
        assetbundle = str(card.get("assetbundleName", "")).strip()
        if not assetbundle:
            return segments, has_media, estimated_bytes

        normal_coro: Awaitable[bytes | None] | None = None
        trained_coro: Awaitable[bytes | None] | None = None
        if not asset_provider.only_has_after_training(card):
            normal_coro = self._run_new_card_asset_fetch(
                semaphore,
                lambda: asset_provider.get_card_image(
                    server,
                    assetbundle,
                    timeout=asset_timeout or 20,
                ),
            )
        if asset_provider.has_after_training(card):
            # 同一卡的普通图和训练后图并发抓取，避免自动提醒在 CDN 较慢时顺序卡两次。
            trained_coro = self._run_new_card_asset_fetch(
                semaphore,
                lambda: asset_provider.get_card_image(
                    server,
                    assetbundle,
                    after_training=True,
                    timeout=asset_timeout or 20,
                ),
            )

        normal: bytes | None = None
        trained: bytes | None = None
        if normal_coro and trained_coro:
            normal, trained = await asyncio.gather(normal_coro, trained_coro)
        elif normal_coro:
            normal = await normal_coro
        elif trained_coro:
            trained = await trained_coro

        if normal:
            normal_segment = self._build_new_card_image_segment(
                local_path=asset_provider.get_card_image_local_path(
                    server,
                    assetbundle,
                ),
                raw_bytes=normal,
            )
            if normal_segment:
                segments.append(normal_segment)
            estimated_bytes += len(normal)
            has_media = True
        if trained:
            trained_segment = self._build_new_card_image_segment(
                local_path=asset_provider.get_card_image_local_path(
                    server,
                    assetbundle,
                    after_training=True,
                ),
                raw_bytes=trained,
            )
            if trained_segment:
                segments.append(trained_segment)
            estimated_bytes += len(trained)
            has_media = True
        return segments, has_media, estimated_bytes

    async def _build_new_stamp_segments(
        self,
        server: str,
        stamp: dict[str, Any],
        *,
        asset_timeout: float | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[list[Any] | None, int]:
        assetbundle = str(stamp.get("assetbundleName", "")).strip()
        if not assetbundle:
            return None, 0
        stamp_image = await self._run_new_card_asset_fetch(
            semaphore,
            lambda: asset_provider.get_stamp_image(
                server,
                assetbundle,
                timeout=asset_timeout or 20,
            ),
        )
        if not stamp_image:
            return None, 0
        text_block = f"表情ID：{stamp.get('id')}\n名称：{stamp.get('name', '')}"
        stamp_segment = self._build_new_card_image_segment(
            local_path=asset_provider.get_stamp_image_local_path(server, assetbundle),
            raw_bytes=stamp_image,
        )
        if not stamp_segment:
            return None, 0
        return [
            Text(text_block),
            stamp_segment,
        ], len(text_block.encode("utf-8")) + len(stamp_image)


    async def _prepare_new_card_summary_item(
        self,
        *,
        server: str,
        revision: str | None,
        key: str,
    ) -> _NewCardReminderPreparedItem:
        summary_text = "\n".join(
            [
                f"{server_label(server)} 新卡上线/表情更新",
                *self._new_card_summary_lines(revision),
            ]
        )
        return _NewCardReminderPreparedItem(
            key=key,
            kind="summary",
            message=UniMessage([Text(summary_text)]),
            estimated_bytes=len(summary_text.encode("utf-8")),
        )

    def _build_new_card_item_specs(
        self,
        *,
        server: str,
        revision: str | None,
        base_key: str,
        cards: list[dict[str, Any]],
        stamps: list[dict[str, Any]],
        pending_keys: set[str],
    ) -> list[_NewCardReminderItemSpec]:
        specs: list[_NewCardReminderItemSpec] = []
        summary_key = self._new_card_summary_record_key(base_key)
        if summary_key in pending_keys:
            specs.append(
                _NewCardReminderItemSpec(
                    key=summary_key,
                    kind="summary",
                    payload={
                        "server": server,
                        "revision": revision,
                    },
                )
            )
        for card in sorted(cards, key=self._new_card_sort_key, reverse=True):
            card_key = self._new_card_card_record_key(base_key, card.get("id", 0))
            if card_key in pending_keys:
                specs.append(
                    _NewCardReminderItemSpec(
                        key=card_key,
                        kind="card",
                        payload=card,
                    )
                )
        for stamp in sorted(stamps, key=self._new_stamp_sort_key, reverse=True):
            stamp_key = self._new_card_stamp_record_key(base_key, stamp.get("id", 0))
            if stamp_key in pending_keys:
                specs.append(
                    _NewCardReminderItemSpec(
                        key=stamp_key,
                        kind="stamp",
                        payload=stamp,
                    )
                )
        return specs

    async def _prepare_new_card_item(
        self,
        *,
        server: str,
        spec: _NewCardReminderItemSpec,
        asset_timeout: float,
        semaphore: asyncio.Semaphore,
    ) -> _NewCardReminderPreparedItem | None:
        if spec.kind == "summary":
            payload = spec.payload
            return await self._prepare_new_card_summary_item(
                server=payload["server"],
                revision=payload["revision"],
                key=spec.key,
            )
        if spec.kind == "card":
            segments, has_media, estimated_bytes = await self._build_new_card_segments(
                server,
                spec.payload,
                asset_timeout=asset_timeout,
                semaphore=semaphore,
            )
            if not has_media:
                return None
            return _NewCardReminderPreparedItem(
                key=spec.key,
                kind="card",
                message=UniMessage(segments),
                estimated_bytes=estimated_bytes,
            )
        segments, estimated_bytes = await self._build_new_stamp_segments(
            server,
            spec.payload,
            asset_timeout=asset_timeout,
            semaphore=semaphore,
        )
        if not segments:
            return None
        return _NewCardReminderPreparedItem(
            key=spec.key,
            kind="stamp",
            message=UniMessage(segments),
            estimated_bytes=estimated_bytes,
        )

    async def _iter_new_card_batches(
        self,
        *,
        server: str,
        specs: list[_NewCardReminderItemSpec],
    ) -> AsyncIterator[list[_NewCardReminderPreparedItem]]:
        settings = get_settings()
        asset_timeout = max(0.1, float(settings.new_card_auto_asset_timeout_seconds))
        prepare_chunk_size = max(1, int(settings.new_card_media_fetch_concurrency))
        semaphore = asyncio.Semaphore(max(1, int(settings.new_card_media_fetch_concurrency)))
        index = 0

        while index < len(specs):
            started_at = time.perf_counter()
            current_specs = specs[index : index + prepare_chunk_size]
            index += len(current_specs)
            if current_specs:
                results = await asyncio.gather(
                    *(
                        self._prepare_new_card_item(
                            server=server,
                            spec=spec,
                            asset_timeout=asset_timeout,
                            semaphore=semaphore,
                        )
                        for spec in current_specs
                    )
                )
                prepared_items = [item for item in results if item is not None]
            else:
                prepared_items = []
            if not prepared_items:
                self._log_new_card_stage(server, "单批媒体准备", started_at, extra="items=0")
                continue

            batch_bytes = sum(item.estimated_bytes for item in prepared_items)
            self._log_new_card_stage(
                server,
                "单批媒体准备",
                started_at,
                extra=f"items={len(prepared_items)} bytes~={batch_bytes}",
            )
            yield prepared_items

    @staticmethod
    def _new_card_targets_finished(target_states: list[_NewCardReminderTargetState]) -> bool:
        return all(target_state.aborted or not target_state.pending_keys for target_state in target_states)

    async def _flush_new_card_plain_items(
        self,
        *,
        bot: Bot,
        server: str,
        revision: str | None,
        target_states: list[_NewCardReminderTargetState],
        items: list[_NewCardReminderPreparedItem],
        batch_index: int,
        mark_sent: bool,
    ) -> bool:
        sent_content = False
        for target_state in target_states:
            if target_state.aborted or not target_state.pending_keys:
                continue
            group_items = [
                item for item in items if item.key in target_state.pending_keys
            ]
            if not group_items:
                continue
            pending_before = set(target_state.pending_keys)
            group_batch_bytes = sum(item.estimated_bytes for item in group_items)
            send_started_at = time.perf_counter()
            await self._send_new_card_plain_items(
                bot=bot,
                server=server,
                revision=revision,
                target_state=target_state,
                items=group_items,
                batch_index=batch_index,
                mark_sent=mark_sent,
            )
            self._log_new_card_stage(
                server,
                "单批发送",
                send_started_at,
                extra=(
                    f"group_id={target_state.group_id} mode=plain "
                    f"batch={batch_index} items={len(group_items)} bytes~={group_batch_bytes}"
                ),
            )
            if any(
                item.kind != "summary"
                and item.key in pending_before
                and item.key not in target_state.pending_keys
                for item in group_items
            ):
                sent_content = True
        return sent_content

    async def _stream_new_card_plain_items(
        self,
        *,
        bot: Bot,
        server: str,
        revision: str | None,
        target_states: list[_NewCardReminderTargetState],
        prepared_batches: AsyncIterator[list[_NewCardReminderPreparedItem]],
        mark_sent: bool,
        skip_summary_without_media: bool = False,
    ) -> bool:
        card_limit = self._new_card_message_bytes_limit(kind="card")
        stamp_limit = self._new_card_message_bytes_limit(kind="stamp")
        summary_buffer: list[_NewCardReminderPreparedItem] = []
        card_buffer: list[_NewCardReminderPreparedItem] = []
        stamp_buffer: list[_NewCardReminderPreparedItem] = []
        card_bytes = 0
        stamp_bytes = 0
        batch_index = 0
        sent_content = False
        saw_media = False

        async def flush(
            items: list[_NewCardReminderPreparedItem],
            *,
            allow_summary_only: bool = True,
        ) -> None:
            nonlocal batch_index, sent_content
            if not items:
                return
            if not allow_summary_only and all(item.kind == "summary" for item in items):
                return
            batch_index += 1
            if await self._flush_new_card_plain_items(
                bot=bot,
                server=server,
                revision=revision,
                target_states=target_states,
                items=items,
                batch_index=batch_index,
                mark_sent=mark_sent,
            ):
                sent_content = True

        async for prepared_batch in prepared_batches:
            if self._new_card_targets_finished(target_states):
                return sent_content
            for item in prepared_batch:
                if item.kind == "summary":
                    if summary_buffer:
                        await flush(
                            summary_buffer,
                            allow_summary_only=not skip_summary_without_media or saw_media,
                        )
                        summary_buffer = []
                    summary_buffer.append(item)
                    continue

                if summary_buffer:
                    # summary 需要保持单独一条，但又不能因为测试路径只有 summary 就误报“发送成功”；
                    # 因此只有在确认后面确实有媒体项时才立即冲刷它。
                    await flush(summary_buffer)
                    summary_buffer = []

                saw_media = True
                if item.kind == "card":
                    if stamp_buffer:
                        await flush(stamp_buffer)
                        stamp_buffer = []
                        stamp_bytes = 0
                    if card_buffer and card_bytes + item.estimated_bytes > card_limit:
                        await flush(card_buffer)
                        card_buffer = []
                        card_bytes = 0
                    card_buffer.append(item)
                    card_bytes += item.estimated_bytes
                    continue

                if card_buffer:
                    # stamp 永远不和卡图混发；进入 stamp 流前先把已攒好的卡图包冲出去，
                    # 这样卡图消息边界只受 card_limit 控制，不再受媒体准备窗口影响。
                    await flush(card_buffer)
                    card_buffer = []
                    card_bytes = 0
                if stamp_buffer and stamp_bytes + item.estimated_bytes > stamp_limit:
                    await flush(stamp_buffer)
                    stamp_buffer = []
                    stamp_bytes = 0
                stamp_buffer.append(item)
                stamp_bytes += item.estimated_bytes

        if summary_buffer:
            await flush(
                summary_buffer,
                allow_summary_only=not skip_summary_without_media or saw_media,
            )
        if card_buffer:
            await flush(card_buffer)
        if stamp_buffer:
            await flush(stamp_buffer)
        return sent_content

    async def _mark_new_card_keys_sent(
        self,
        *,
        platform: str,
        group_id: str,
        server: str,
        keys: list[str],
    ) -> None:
        # 发送成功后立即写入逻辑项级幂等记录，避免下一轮整批重复推送。
        for key in keys:
            await create_notification_record(
                platform=platform,
                group_id=group_id,
                feature_name=FEATURE_NEW_CARD_REMINDER,
                server=server,
                record_key=key,
            )

    async def _send_new_card_plain_items(
        self,
        *,
        bot: Bot,
        server: str,
        revision: str | None,
        target_state: _NewCardReminderTargetState,
        items: list[_NewCardReminderPreparedItem],
        batch_index: int,
        mark_sent: bool = True,
    ) -> None:
        settings = get_settings()
        timeout_seconds = max(0.1, float(settings.new_card_send_timeout_seconds))
        min_delay = max(
            0.0,
            float(
                self._new_card_setting_value(
                    settings,
                    "new_card_send_delay_min_seconds",
                    default=0.2,
                    legacy_name="new_card_fallback_delay_min_seconds",
                )
            ),
        )
        max_delay = max(
            min_delay,
            float(
                self._new_card_setting_value(
                    settings,
                    "new_card_send_delay_max_seconds",
                    default=0.8,
                    legacy_name="new_card_fallback_delay_max_seconds",
                )
            ),
        )
        abort_after = max(
            1,
            int(
                self._new_card_setting_value(
                    settings,
                    "new_card_abort_after_consecutive_failures",
                    default=2,
                    legacy_name="new_card_fallback_abort_after_consecutive_failures",
                )
            ),
        )
        message_batches = self._build_new_card_plain_message_batches(items)
        for index, message_batch in enumerate(message_batches):
            if target_state.aborted:
                return
            message = self._compose_new_card_plain_message(message_batch)
            keys = [item.key for item in message_batch]
            estimated_bytes = sum(item.estimated_bytes for item in message_batch)
            try:
                await asyncio.wait_for(
                    PlatformUtils.send_message(
                        bot,
                        None,
                        target_state.group_id,
                        message,
                    ),
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                if not self._is_expected_new_card_send_error(exc):
                    raise
                target_state.consecutive_failures += 1
                self._log_new_card_send_warning(
                    server=server,
                    revision=revision,
                    group_id=target_state.group_id,
                    item_key=keys[0],
                    batch_index=batch_index,
                    node_count=len(message_batch),
                    estimated_bytes=estimated_bytes,
                    mode="plain",
                    exc=exc,
                )
                if target_state.consecutive_failures >= abort_after:
                    # 普通消息已经连续失败时继续硬发只会放大刷屏和风控风险，因此直接中止该群本轮剩余发送。
                    target_state.aborted = True
                    logger.warning(
                        f"MoeSekai 新卡提醒已中止该群本轮发送 server={server} group_id={target_state.group_id} batch_index={batch_index}",
                        MODULE_NAME,
                    )
                    return
                continue

            if mark_sent:
                await self._mark_new_card_keys_sent(
                    platform=target_state.platform,
                    group_id=target_state.group_id,
                    server=server,
                    keys=keys,
                )
            target_state.pending_keys.difference_update(keys)
            target_state.consecutive_failures = 0
            if index < len(message_batches) - 1:
                await asyncio.sleep(random.uniform(min_delay, max_delay))

    async def handle_test_new_card_reminder(
        self,
        *,
        bot: Bot,
        group_id: str | None,
        platform: str,
        user_id: str,
        server: str | None,
        card_ids: list[int] | None = None,
    ) -> str | None:
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=True,
        )
        if error or not resolved_server:
            return "请先绑定账号或显式指定区服"
        if not group_id:
            return "新卡上线提醒测试仅支持群聊"

        cards = await master_data_provider.get_cards(resolved_server)
        stamps = await master_data_provider.get_stamps(resolved_server)
        if card_ids:
            card_id_set = set(card_ids)
            selected_cards = sorted(
                [
                    card
                    for card in cards
                    if int(card.get("id", 0)) in card_id_set
                ],
                key=self._new_card_sort_key,
                reverse=True,
            )
            if not selected_cards:
                return "未找到指定的新卡测试数据"
            selected_stamps: list[dict[str, Any]] = []
        else:
            selected_cards = sorted(
                cards,
                key=self._new_card_sort_key,
                reverse=True,
            )[:NEW_CARD_TEST_SCAN_LIMIT]
            selected_stamps = sorted(
                stamps,
                key=self._new_stamp_sort_key,
                reverse=True,
            )[:NEW_CARD_TEST_SCAN_LIMIT]

        base_key = "test"
        pending_keys = {
            self._new_card_summary_record_key(base_key),
            *(
                self._new_card_card_record_key(base_key, card.get("id", 0))
                for card in selected_cards
            ),
            *(
                self._new_card_stamp_record_key(base_key, stamp.get("id", 0))
                for stamp in selected_stamps
            ),
        }
        item_specs = self._build_new_card_item_specs(
            server=resolved_server,
            revision="test",
            base_key=base_key,
            cards=selected_cards,
            stamps=selected_stamps,
            pending_keys=pending_keys,
        )
        if not item_specs:
            return "当前暂无可用的新卡上线提醒测试样本，请稍后重试或显式指定 card_id"
        sent_content = await self._send_test_new_card_batches(
            bot=bot,
            group_id=group_id,
            server=resolved_server,
            revision="test",
            specs=item_specs,
        )
        if not sent_content:
            if card_ids:
                return "指定卡牌资源暂不可用，请稍后重试"
            return "当前暂无可用的新卡上线提醒测试样本，请稍后重试或显式指定 card_id"
        return None

    async def _send_test_new_card_batches(
        self,
        *,
        bot: Bot,
        group_id: str,
        server: str,
        revision: str | None,
        specs: list[_NewCardReminderItemSpec],
    ) -> bool:
        target_state = _NewCardReminderTargetState(
            platform="test",
            group_id=group_id,
            pending_keys={spec.key for spec in specs},
        )
        return await self._stream_new_card_plain_items(
            bot=bot,
            server=server,
            revision=revision,
            target_states=[target_state],
            prepared_batches=self._iter_new_card_batches(
                server=server,
                specs=specs,
            ),
            mark_sent=False,
            skip_summary_without_media=True,
        )

    async def build_auto_update_notifications(
        self,
        results: list[RegionUpdateResult],
    ) -> list[str]:
        messages: list[str] = []
        for result in results:
            if not result.updated or not result.download_success or result.error:
                continue
            if result.previous_version == result.current_version:
                continue
            messages.append(
                "\n".join(
                    [
                        "MoeSekai 主数据已自动更新",
                        f"区服：{server_label(result.server)}",
                        f"来源：{result.selected_source_name or '未知'}",
                        f"Revision：{result.previous_revision or '-'} -> {result.current_revision or '-'}",
                        f"版本：{result.previous_version or '-'} -> {result.current_version or '-'}",
                        f"时间：{result.checked_at or '-'}",
                    ]
                )
            )
        return messages

    async def dispatch_new_card_notifications(
        self,
        bot: Bot,
        results: list[RegionUpdateResult],
    ) -> None:
        filter_started_at = time.perf_counter()
        pending_state = self._load_new_card_pending_state()
        dispatch_results = self._build_new_card_dispatch_results(results, pending_state)
        self._save_new_card_pending_state(pending_state)
        if dispatch_results:
            self._log_new_card_stage(
                dispatch_results[0].server,
                "更新结果过滤",
                filter_started_at,
                extra=f"results={len(dispatch_results)}",
            )
        else:
            logger.debug("MoeSekai 新卡提醒阶段 无需处理的新卡更新结果", MODULE_NAME)
            return

        for result in dispatch_results:
            server_started_at = time.perf_counter()
            cards = result.added_records.get("cards", [])
            stamps = result.added_records.get("stamps", [])
            base_key = self._new_card_base_record_key(
                revision=result.current_revision,
                version=result.current_version,
            )
            if not cards and not stamps:
                self._delete_new_card_pending_snapshot(
                    pending_state,
                    server=result.server,
                    base_key=base_key,
                )
                continue

            toggle_started_at = time.perf_counter()
            toggles = await list_enabled_group_feature_toggles(
                feature_name=FEATURE_NEW_CARD_REMINDER,
                server=result.server,
            )
            self._log_new_card_stage(
                result.server,
                "群开关加载",
                toggle_started_at,
                extra=f"toggles={len(toggles)}",
            )
            if not toggles:
                self._delete_new_card_pending_snapshot(
                    pending_state,
                    server=result.server,
                    base_key=base_key,
                )
                continue

            record_prefix = f"{base_key}:"
            existing_started_at = time.perf_counter()
            sent_key_map = await list_notification_record_keys_by_groups(
                feature_name=FEATURE_NEW_CARD_REMINDER,
                server=result.server,
                record_key_prefix=record_prefix,
                groups=[(toggle.platform, toggle.group_id) for toggle in toggles],
            )
            self._log_new_card_stage(
                result.server,
                "已发送记录批量读取",
                existing_started_at,
                extra=f"groups={len(sent_key_map)}",
            )

            target_states: list[_NewCardReminderTargetState] = []
            required_keys: set[str] = set()
            for toggle in toggles:
                group_key = (toggle.platform, toggle.group_id)
                sent_keys = sent_key_map.get(group_key, set())
                pending_keys: set[str] = set()
                summary_key = self._new_card_summary_record_key(base_key)
                if summary_key not in sent_keys:
                    pending_keys.add(summary_key)
                for card in cards:
                    pending_key = self._new_card_card_record_key(
                        base_key,
                        card.get("id", 0),
                    )
                    if pending_key not in sent_keys:
                        pending_keys.add(pending_key)
                for stamp in stamps:
                    pending_key = self._new_card_stamp_record_key(
                        base_key,
                        stamp.get("id", 0),
                    )
                    if pending_key not in sent_keys:
                        pending_keys.add(pending_key)
                if not pending_keys:
                    continue
                target_states.append(
                    _NewCardReminderTargetState(
                        platform=toggle.platform,
                        group_id=toggle.group_id,
                        pending_keys=pending_keys,
                        started_at=time.perf_counter(),
                    )
                )
                # 这里先合并所有群的待发送逻辑项，只准备一次媒体；真正发消息时再按群过滤，避免重复抓图。
                required_keys.update(pending_keys)

            if not target_states or not required_keys:
                self._delete_new_card_pending_snapshot(
                    pending_state,
                    server=result.server,
                    base_key=base_key,
                )
                continue

            item_specs = self._build_new_card_item_specs(
                server=result.server,
                revision=result.current_revision,
                base_key=base_key,
                cards=cards,
                stamps=stamps,
                pending_keys=required_keys,
            )
            if not item_specs:
                self._delete_new_card_pending_snapshot(
                    pending_state,
                    server=result.server,
                    base_key=base_key,
                )
                continue

            await self._stream_new_card_plain_items(
                bot=bot,
                server=result.server,
                revision=result.current_revision,
                target_states=target_states,
                prepared_batches=self._iter_new_card_batches(
                    server=result.server,
                    specs=item_specs,
                ),
                mark_sent=True,
            )

            for target_state in target_states:
                self._log_new_card_stage(
                    result.server,
                    "单群总耗时",
                    target_state.started_at,
                    extra=(
                        f"group_id={target_state.group_id} pending={len(target_state.pending_keys)} "
                        f"aborted={target_state.aborted}"
                    ),
                )
            if any(target_state.pending_keys for target_state in target_states):
                self._store_new_card_pending_snapshot(
                    pending_state,
                    server=result.server,
                    base_key=base_key,
                    revision=result.current_revision,
                    version=result.current_version,
                    cards=cards,
                    stamps=stamps,
                )
            else:
                self._delete_new_card_pending_snapshot(
                    pending_state,
                    server=result.server,
                    base_key=base_key,
                )
            self._log_new_card_stage(
                result.server,
                "单区服总耗时",
                server_started_at,
                extra=(
                    f"cards={len(cards)} stamps={len(stamps)} groups={len(target_states)}"
                ),
            )
        self._save_new_card_pending_state(pending_state)

    async def dispatch_live_reminders(self, bot: Bot) -> None:
        now = datetime.now()
        toggles = await list_enabled_group_feature_toggles(
            feature_name=FEATURE_LIVE_REMINDER
        )
        for toggle in toggles:
            lives = await master_data_provider.get_virtual_lives(toggle.server)
            for live in lives:
                schedules = sorted(
                    [
                        datetime.fromtimestamp(item["startAt"] / 1000)
                        for item in live.get("virtualLiveSchedules", [])
                        if item.get("startAt")
                    ]
                )
                if not schedules:
                    continue
                checkpoints = [("first", schedules[0])]
                if len(schedules) >= 2:
                    checkpoints.append(("second_last", schedules[-2]))
                for stage, start_time in checkpoints:
                    remind_at = start_time - timedelta(minutes=5)
                    if not (timedelta(minutes=0) <= start_time - now <= timedelta(minutes=6)):
                        continue
                    record_key = f"{live.get('id')}:{stage}:{start_time.isoformat()}"
                    if await has_notification_record(
                        platform=toggle.platform,
                        group_id=toggle.group_id,
                        feature_name=FEATURE_LIVE_REMINDER,
                        server=toggle.server,
                        record_key=record_key,
                    ):
                        continue
                    subscribers = await list_user_feature_subscriptions(
                        platform=toggle.platform,
                        group_id=toggle.group_id,
                        feature_name=FEATURE_LIVE_REMINDER,
                        server=toggle.server,
                    )
                    reminder = await self._build_live_message(
                        toggle.server,
                        live,
                        remind_start=remind_at,
                        title="Live 即将开始",
                    )
                    if subscribers:
                        at_message = UniMessage()
                        for subscription in subscribers:
                            at_message += UniMessage([At(flag="user", target=subscription.user_id)])
                        at_message += UniMessage([Text("\n")])
                        reminder = at_message + reminder
                    await PlatformUtils.send_message(bot, None, toggle.group_id, reminder)
                    await create_notification_record(
                        platform=toggle.platform,
                        group_id=toggle.group_id,
                        feature_name=FEATURE_LIVE_REMINDER,
                        server=toggle.server,
                        record_key=record_key,
                    )


moesekai_app = MoeSekaiApplication()
