from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from nonebot.adapters import Bot
from nonebot_plugin_alconna import At, Image, Text, UniMessage
from tortoise import Tortoise

from ..adapters.results import (
    MoeForwardMessage,
    MoeImageTextMessage,
    build_forward_message,
    build_image_message,
    build_native_image_text_message,
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
    SEEDS_DIR,
    SERVER_SET,
    SERVERS,
    SCOPE_GLOBAL,
    STATE_DIR,
    YCX_SUPPORTED_SERVERS,
    make_scope_key,
    normalize_alias,
    normalize_deck_difficulty,
    normalize_live_type,
    server_label,
)
from ..master_data import RegionUpdateResult
from ..providers import (
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
    get_alias_entry,
    get_blacklist_entry,
    get_group_feature_toggle,
    get_or_create_user_settings,
    get_user_binding,
    get_user_feature_subscription,
    get_user_settings,
    has_notification_record,
    list_alias_entries,
    list_blacklist_entries,
    list_enabled_group_feature_toggles,
    list_user_bindings,
    list_user_feature_subscriptions,
    query_bindings_by_uid,
    remove_alias_entry,
    remove_blacklist_entry,
    remove_user_feature_subscription,
    resolve_alias,
    search_alias_entries,
    set_allow_share_profile,
    set_default_server,
    set_group_feature_toggle,
    upsert_alias_entry,
    upsert_user_binding,
    upsert_user_feature_subscription,
)
from ..screenshot import ScreenshotError, screenshot_service
from ..storage.state import JsonStateStore

PREDICTION_RANKS = [50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000]
NEW_CARD_TEST_CARD_LIMIT = 3
NEW_CARD_TEST_STAMP_LIMIT = 3
NEW_CARD_TEST_SCAN_LIMIT = 12
NEW_CARD_TEST_ASSET_TIMEOUT_SECONDS = 6.0
_ALIAS_STATE = JsonStateStore(STATE_DIR / "alias_sync_state.json")


class MoeSekaiApplication:
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
        state = _ALIAS_STATE.load({})
        if not state.get("character_seed_loaded"):
            for alias, character_id in hub_provider.read_character_alias_seed(
                SEEDS_DIR / "character_alias.sql"
            ):
                await upsert_alias_entry(
                    target_type=ALIAS_TARGET_CHARACTER,
                    target_value=character_id,
                    alias=alias,
                    scope=SCOPE_GLOBAL,
                    created_by="system",
                )
            state["character_seed_loaded"] = True
            _ALIAS_STATE.save(state)

        characters = await master_data_provider.get_game_characters("jp")
        for character in characters:
            char_id = str(character.get("id"))
            aliases = [
                f"{character.get('firstName', '')}{character.get('givenName', '')}".strip(),
                f"{character.get('firstNameEnglish', '')} {character.get('givenNameEnglish', '')}".strip(),
                str(character.get("givenName", "")).strip(),
                str(character.get("givenNameEnglish", "")).strip(),
                char_id,
            ]
            for alias in aliases:
                if alias:
                    await upsert_alias_entry(
                        target_type=ALIAS_TARGET_CHARACTER,
                        target_value=char_id,
                        alias=alias,
                        scope=SCOPE_GLOBAL,
                        created_by="system",
                    )

        musics = await master_data_provider.get_musics("jp")
        for music in musics:
            music_id = str(music.get("id"))
            aliases = [
                str(music.get("title", "")).strip(),
                str(music.get("pronunciation", "")).strip(),
                music_id,
            ]
            for alias in aliases:
                if alias:
                    await upsert_alias_entry(
                        target_type=ALIAS_TARGET_MUSIC,
                        target_value=music_id,
                        alias=alias,
                        scope=SCOPE_GLOBAL,
                        created_by="system",
                    )

    async def sync_music_aliases(self, *, force: bool = False) -> None:
        state = _ALIAS_STATE.load({})
        interval = get_settings().alias_sync_interval_seconds
        last_sync = state.get("music_alias_sync_at")
        if not force and isinstance(last_sync, (int, float)):
            if datetime.now().timestamp() - float(last_sync) < interval:
                return
        alias_items = await hub_provider.get_music_alias_index()
        synced = False
        for item in alias_items:
            try:
                music_id = int(item.get("music_id", 0))
            except (TypeError, ValueError):
                continue
            if not music_id:
                continue
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                continue
            for alias in aliases:
                alias_text = str(alias).strip()
                if not alias_text:
                    continue
                await upsert_alias_entry(
                    target_type=ALIAS_TARGET_MUSIC,
                    target_value=str(music_id),
                    alias=alias_text,
                    scope=SCOPE_GLOBAL,
                    created_by="system",
                )
                synced = True
        if not synced and alias_items:
            synced = True
        if not synced:
            return
        state["music_alias_source"] = "MoeSekai-Hub/music_aliases.json"
        state["music_alias_sync_count"] = len(alias_items)
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
            fallback_jp=False,
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
            fallback_jp=False,
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
            fallback_jp=False,
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

    async def handle_activity_deck(
        self,
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
        if error := await self._is_qq_blacklisted(platform, requester_user_id, is_superuser):
            return error
        target_user_id = target_user_id or requester_user_id
        binding, error = await self._resolve_binding_for_user(
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
            current_event = await master_data_provider.get_current_event(
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
            image = await screenshot_service.capture_story(event_id)
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
        if query.isdigit():
            return query
        scopes = []
        if group_id:
            scopes.append(make_scope_key(group_id, platform=platform))
        scopes.append(SCOPE_GLOBAL)
        if entry := await resolve_alias(
            target_type=ALIAS_TARGET_CHARACTER,
            alias=query,
            scopes=scopes,
        ):
            return entry.target_value
        normalized = normalize_alias(query)
        for character in await master_data_provider.get_game_characters("jp"):
            character_id = str(character.get("id"))
            names = [
                f"{character.get('firstName', '')}{character.get('givenName', '')}".strip(),
                f"{character.get('firstNameEnglish', '')}{character.get('givenNameEnglish', '')}".strip(),
                str(character.get("givenName", "")).strip(),
                str(character.get("givenNameEnglish", "")).strip(),
                character_id,
            ]
            if any(normalized == normalize_alias(name) for name in names if name):
                return character_id
        return None

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
            image = await screenshot_service.capture_character(resolved_character_id)
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
        if query.isdigit():
            return query
        scopes = []
        if group_id:
            scopes.append(make_scope_key(group_id, platform=platform))
        scopes.append(SCOPE_GLOBAL)
        if entry := await resolve_alias(
            target_type=ALIAS_TARGET_MUSIC,
            alias=query,
            scopes=scopes,
        ):
            return entry.target_value
        normalized = normalize_alias(query)
        for music in await master_data_provider.get_musics("jp"):
            music_id = str(music.get("id"))
            names = [
                str(music.get("title", "")).strip(),
                str(music.get("pronunciation", "")).strip(),
                music_id,
            ]
            if any(normalized == normalize_alias(name) for name in names if name):
                return music_id
        return None

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
    ) -> str:
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
            scopes = [scope, SCOPE_GLOBAL] if scope != SCOPE_GLOBAL else [SCOPE_GLOBAL]
            if target_type == ALIAS_TARGET_CHARACTER:
                target_id = await self._resolve_character_id(
                    query,
                    platform=platform,
                    group_id=group_id,
                )
            else:
                target_id = await self._resolve_music_id(
                    query,
                    platform=platform,
                    group_id=group_id,
                )
            if target_id:
                aliases = await list_alias_entries(
                    target_type=target_type,
                    target_value=target_id,
                )
                if not aliases:
                    return f"未找到 {query} 的别名数据"
                grouped: dict[str, list[str]] = defaultdict(list)
                for entry in aliases:
                    grouped[entry.scope].append(entry.alias)
                lines = [f"{query} -> {target_id}"]
                for alias_scope, alias_list in grouped.items():
                    scope_name = "全局" if alias_scope == SCOPE_GLOBAL else "本群"
                    lines.append(f"{scope_name}别名：{' / '.join(sorted(set(alias_list))[:20])}")
                return "\n".join(lines)
            matches = await search_alias_entries(
                target_type=target_type,
                keyword=query,
                scopes=scopes,
            )
            if not matches:
                return "未找到相关别名"
            return "\n".join(
                [f"{entry.alias} -> {entry.target_value} ({'全局' if entry.scope == SCOPE_GLOBAL else '本群'})" for entry in matches]
            )

        if operation == "add":
            if not alias:
                return "请提供要添加的别名"
            if target_type == ALIAS_TARGET_CHARACTER:
                target_id = await self._resolve_character_id(
                    query, platform=platform, group_id=group_id
                )
            else:
                target_id = await self._resolve_music_id(
                    query, platform=platform, group_id=group_id
                )
            if not target_id:
                return f"未找到目标：{query}"
            await upsert_alias_entry(
                target_type=target_type,
                target_value=target_id,
                alias=alias,
                scope=scope,
                created_by=user_id or "system",
            )
            return f"已添加{'全局' if scope == SCOPE_GLOBAL else '本群'}别名：{alias} -> {target_id}"

        if not alias:
            return "请提供要删除的别名"
        removed = await remove_alias_entry(
            target_type=target_type,
            alias=alias,
            scope=scope,
        )
        return "已删除别名" if removed else "该别名不存在"

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
            fallback_jp=False,
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
            fallback_jp=False,
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
            fallback_jp=False,
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
            fallback_jp=False,
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

    async def _build_new_card_node(
        self,
        server: str,
        card: dict[str, Any],
        *,
        asset_timeout: float | None = None,
    ) -> tuple[UniMessage, bool]:
        lines = [
            f"卡牌ID：{card.get('id')}",
            f"角色ID：{card.get('characterId', '-')}",
            f"稀有度：{card.get('cardRarityType', '-')}",
        ]
        segments: list[Any] = [Text("\n".join(lines))]
        has_media = False
        assetbundle = str(card.get("assetbundleName", "")).strip()
        if assetbundle:
            if not asset_provider.only_has_after_training(card):
                normal = await asset_provider.get_card_image(
                    server,
                    assetbundle,
                    timeout=asset_timeout or 20,
                )
            else:
                normal = None
            if normal:
                segments.append(Image(raw=normal))
                has_media = True
            if asset_provider.has_after_training(card):
                trained = await asset_provider.get_card_image(
                    server,
                    assetbundle,
                    after_training=True,
                    timeout=asset_timeout or 20,
                )
            else:
                trained = None
            if trained:
                segments.append(Image(raw=trained))
                has_media = True
        return UniMessage(segments), has_media

    async def _build_new_stamp_node(
        self,
        server: str,
        stamp: dict[str, Any],
        *,
        asset_timeout: float | None = None,
    ) -> UniMessage | None:
        assetbundle = str(stamp.get("assetbundleName", "")).strip()
        if not assetbundle:
            return None
        stamp_image = await asset_provider.get_stamp_image(
            server,
            assetbundle,
            timeout=asset_timeout or 20,
        )
        if not stamp_image:
            return None
        return UniMessage(
            [
                Text(f"表情ID：{stamp.get('id')}\n名称：{stamp.get('name', '')}"),
                Image(raw=stamp_image),
            ]
        )

    async def _collect_new_card_nodes(
        self,
        *,
        server: str,
        cards: list[dict[str, Any]],
        stamps: list[dict[str, Any]],
        asset_timeout: float | None = None,
        max_cards: int | None = None,
        max_stamps: int | None = None,
        require_media: bool = False,
    ) -> tuple[list[UniMessage], int, int]:
        nodes: list[UniMessage] = []
        card_count = 0
        stamp_count = 0
        for card in cards:
            node, has_media = await self._build_new_card_node(
                server,
                card,
                asset_timeout=asset_timeout,
            )
            if require_media and not has_media:
                continue
            nodes.append(node)
            card_count += 1
            if max_cards and card_count >= max_cards:
                break
        for stamp in stamps:
            node = await self._build_new_stamp_node(
                server,
                stamp,
                asset_timeout=asset_timeout,
            )
            if node is None:
                if require_media:
                    continue
            else:
                nodes.append(node)
                stamp_count += 1
            if max_stamps and stamp_count >= max_stamps:
                break
        return nodes, card_count, stamp_count

    async def _build_new_card_forward(
        self,
        *,
        server: str,
        cards: list[dict[str, Any]],
        stamps: list[dict[str, Any]],
        revision: str | None,
        asset_timeout: float | None = None,
        max_cards: int | None = None,
        max_stamps: int | None = None,
        require_media: bool = False,
        prepared_nodes: list[UniMessage] | None = None,
        prepared_card_count: int | None = None,
        prepared_stamp_count: int | None = None,
    ) -> MoeForwardMessage:
        if (
            prepared_nodes is not None
            and prepared_card_count is not None
            and prepared_stamp_count is not None
        ):
            content_nodes = prepared_nodes
            selected_card_count = prepared_card_count
            selected_stamp_count = prepared_stamp_count
        else:
            content_nodes, selected_card_count, selected_stamp_count = (
                await self._collect_new_card_nodes(
                    server=server,
                    cards=cards,
                    stamps=stamps,
                    asset_timeout=asset_timeout,
                    max_cards=max_cards,
                    max_stamps=max_stamps,
                    require_media=require_media,
                )
            )
        nodes: list[UniMessage] = []
        summary = await render_reminder_card(
            ReminderCardViewModel(
                title=f"{server_label(server)} 新卡上线/表情更新",
                subtitle=f"发现时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                lines=[
                    f"Revision：{revision or '-'}",
                    f"新增卡牌：{selected_card_count}",
                    f"新增表情：{selected_stamp_count}",
                ],
                accent="New Cards",
            )
        )
        nodes.append(UniMessage([Image(raw=summary)]))
        nodes.extend(content_nodes)
        return build_forward_message(nodes, sender_id="10000", sender_name="南极萝卜")

    async def handle_test_new_card_reminder(
        self,
        *,
        platform: str,
        user_id: str,
        server: str | None,
        card_ids: list[int] | None = None,
    ) -> MoeForwardMessage | str:
        resolved_server, error = await self._resolve_default_server(
            platform,
            user_id,
            explicit_server=server,
            fallback_jp=False,
        )
        if error or not resolved_server:
            return "请先绑定账号或显式指定区服"
        cards = await master_data_provider.get_cards(resolved_server)
        stamps = await master_data_provider.get_stamps(resolved_server)
        selected_cards: list[dict[str, Any]]
        selected_stamps: list[dict[str, Any]]
        require_media = False
        max_cards = None
        max_stamps = None
        asset_timeout = NEW_CARD_TEST_ASSET_TIMEOUT_SECONDS
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
            selected_stamps = sorted(
                stamps,
                key=self._new_stamp_sort_key,
                reverse=True,
            )[:NEW_CARD_TEST_STAMP_LIMIT]
            max_cards = len(selected_cards)
            max_stamps = NEW_CARD_TEST_STAMP_LIMIT
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
            require_media = True
            max_cards = NEW_CARD_TEST_CARD_LIMIT
            max_stamps = NEW_CARD_TEST_STAMP_LIMIT
            preview_nodes, preview_card_count, preview_stamp_count = (
                await self._collect_new_card_nodes(
                    server=resolved_server,
                    cards=selected_cards,
                    stamps=selected_stamps,
                    asset_timeout=asset_timeout,
                    max_cards=max_cards,
                    max_stamps=max_stamps,
                    require_media=True,
                )
            )
            if not preview_nodes and preview_card_count == 0 and preview_stamp_count == 0:
                return "当前暂无可用的新卡上线提醒测试样本，请稍后重试或显式指定 card_id"
            return await self._build_new_card_forward(
                server=resolved_server,
                cards=selected_cards,
                stamps=selected_stamps,
                revision="test",
                asset_timeout=asset_timeout,
                max_cards=max_cards,
                max_stamps=max_stamps,
                require_media=require_media,
                prepared_nodes=preview_nodes,
                prepared_card_count=preview_card_count,
                prepared_stamp_count=preview_stamp_count,
            )
        return await self._build_new_card_forward(
            server=resolved_server,
            cards=selected_cards,
            stamps=selected_stamps,
            revision="test",
            asset_timeout=asset_timeout,
            max_cards=max_cards,
            max_stamps=max_stamps,
            require_media=require_media,
        )

    async def build_auto_update_notifications(
        self,
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
        for result in results:
            if not result.updated or not result.download_success or result.error:
                continue
            cards = result.added_records.get("cards", [])
            stamps = result.added_records.get("stamps", [])
            if not cards and not stamps:
                continue
            toggles = await list_enabled_group_feature_toggles(
                feature_name=FEATURE_NEW_CARD_REMINDER,
                server=result.server,
            )
            if not toggles:
                continue
            forward = await self._build_new_card_forward(
                server=result.server,
                cards=cards,
                stamps=stamps,
                revision=result.current_revision,
            )
            for toggle in toggles:
                record_key = f"{result.current_revision or result.current_version or 'unknown'}"
                if await has_notification_record(
                    platform=toggle.platform,
                    group_id=toggle.group_id,
                    feature_name=FEATURE_NEW_CARD_REMINDER,
                    server=result.server,
                    record_key=record_key,
                ):
                    continue
                for node in forward.nodes:
                    await PlatformUtils.send_message(bot, None, toggle.group_id, node)
                await create_notification_record(
                    platform=toggle.platform,
                    group_id=toggle.group_id,
                    feature_name=FEATURE_NEW_CARD_REMINDER,
                    server=result.server,
                    record_key=record_key,
                )

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
