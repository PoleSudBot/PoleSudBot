from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import nonebot
import pytest
from PIL import Image as PILImage
from PIL import ImageDraw

nonebot.init()

from zhenxun.plugins.moesekai.adapters.results import (
    MoeImageTextMessage,
    _build_alias_section_layout,
    _load_font,
    build_alias_profile_image,
)
from zhenxun.plugins.moesekai.application import service as service_module
from zhenxun.plugins.moesekai.deck import (
    CustomBonusSpec,
    CustomCharacterQuery,
    DeckCommandRequest,
)
from zhenxun.plugins.moesekai.providers.aliases import (
    AliasProfile,
    AliasResolveResult,
    AliasSearchHit,
)
from zhenxun.plugins.moesekai.providers.ranking import RankingItem, RankingSnapshot
from zhenxun.plugins.moesekai.screenshot import ScreenshotError


def _build_snapshot(
    *,
    server: str,
    event_id: int,
    status: str = "running",
    score: int | None = 1_000_000,
    prediction: int | None = 1_234_567,
    source_name: str = "rk.exmeaning.com",
) -> RankingSnapshot:
    return RankingSnapshot(
        server=server,
        event_id=event_id,
        status=status,
        updated_at="2026-03-29T05:00:00+08:00",
        source_name=source_name,
        items=[
            RankingItem(
                rank=100,
                score=score,
                prediction=prediction,
                collect_time="2026-03-29T04:55:00+08:00",
                is_final=prediction is None,
            )
        ],
    )


@pytest.mark.asyncio
async def test_handle_ycx_tw_returns_unsupported(monkeypatch: pytest.MonkeyPatch):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "tw", None

    async def fail_capture(*_args, **_kwargs):
        raise AssertionError("台服 ycx 不应进入截图逻辑")

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.screenshot_service, "capture_ranking", fail_capture)

    result = await service_module.moesekai_app.handle_ycx(
        "qq",
        "123456",
        None,
        is_superuser=False,
    )
    assert result == "台服暂不支持ycx榜线查询"


@pytest.mark.asyncio
async def test_handle_update_uses_default_server(monkeypatch: pytest.MonkeyPatch):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    class FakeResult:
        def to_message(self) -> str:
            return "JP 已更新"

    async def fake_update_region(server: str, *, force: bool):
        assert server == "jp"
        assert force is True
        return FakeResult()

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.master_data_provider, "update_region", fake_update_region)

    result = await service_module.moesekai_app.handle_update(
        "qq",
        "123456",
        None,
        update_all=False,
        is_superuser=False,
    )
    assert result == "JP 已更新"


@pytest.mark.asyncio
async def test_handle_update_prefers_explicit_server_over_jp_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] == "cn"
        assert kwargs["fallback_jp"] is True
        return "cn", None

    class FakeResult:
        def to_message(self) -> str:
            return "CN 已更新"

    async def fake_update_region(server: str, *, force: bool):
        assert server == "cn"
        assert force is True
        return FakeResult()

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.master_data_provider, "update_region", fake_update_region)

    result = await service_module.moesekai_app.handle_update(
        "qq",
        "123456",
        "cn",
        update_all=False,
        is_superuser=False,
    )

    assert result == "CN 已更新"


@pytest.mark.asyncio
async def test_handle_update_all_requires_superuser(monkeypatch: pytest.MonkeyPatch):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )

    result = await service_module.moesekai_app.handle_update(
        "qq",
        "123456",
        None,
        update_all=True,
        is_superuser=False,
    )
    assert result == "pjsk update all 仅超级用户可用"


@pytest.mark.asyncio
async def test_handle_live_toggle_status_defaults_to_jp_when_unbound(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    async def fake_get_group_feature_toggle(*, platform: str, group_id: str, feature_name: str, server: str):
        assert platform == "qq"
        assert group_id == "654321"
        assert server == "jp"
        return SimpleNamespace(enabled=True)

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module,
        "get_group_feature_toggle",
        fake_get_group_feature_toggle,
    )

    result = await service_module.moesekai_app.handle_live_toggle(
        platform="qq",
        user_id="123456",
        group_id="654321",
        server=None,
        action="status",
        is_superuser=False,
        can_manage_group=False,
    )

    assert result == "日服 live提醒当前为：开启"


@pytest.mark.asyncio
async def test_handle_new_card_toggle_status_defaults_to_jp_when_unbound(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    async def fake_get_group_feature_toggle(*, server: str, **_kwargs):
        assert server == "jp"
        return SimpleNamespace(enabled=False)

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module,
        "get_group_feature_toggle",
        fake_get_group_feature_toggle,
    )

    result = await service_module.moesekai_app.handle_new_card_toggle(
        platform="qq",
        user_id="123456",
        group_id="654321",
        server=None,
        action="status",
        is_superuser=False,
        can_manage_group=False,
    )

    assert result == "日服 新卡上线提醒当前为：关闭"


@pytest.mark.asyncio
async def test_handle_live_subscription_defaults_to_jp_when_unbound(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    async def fake_upsert_user_feature_subscription(*, server: str, **_kwargs):
        assert server == "jp"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module,
        "upsert_user_feature_subscription",
        fake_upsert_user_feature_subscription,
    )

    result = await service_module.moesekai_app.handle_live_subscription(
        platform="qq",
        user_id="123456",
        group_id="654321",
        server=None,
        subscribe=True,
    )

    assert result == "已订阅 日服 live提醒"


@pytest.mark.asyncio
async def test_handle_test_live_reminder_defaults_to_jp_when_unbound(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    async def fake_get_virtual_lives(server: str):
        assert server == "jp"
        return [{"id": 999, "name": "测试 Live"}]

    async def fake_build_live_message(server: str, live: dict, *, remind_start: datetime, title: str):
        assert server == "jp"
        assert live["id"] == 999
        assert title == "Live 提醒测试"
        assert remind_start > datetime.now()
        return "live-message"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module.master_data_provider,
        "get_virtual_lives",
        fake_get_virtual_lives,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_build_live_message",
        fake_build_live_message,
    )

    result = await service_module.moesekai_app.handle_test_live_reminder(
        platform="qq",
        user_id="123456",
        server=None,
    )

    assert result == "live-message"


@pytest.mark.asyncio
async def test_handle_test_new_card_reminder_defaults_to_jp_when_unbound(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    async def fake_get_cards(server: str):
        assert server == "jp"
        return [{"id": 100, "assetbundleName": "res001_no001", "releaseAt": 1}]

    async def fake_get_stamps(server: str):
        assert server == "jp"
        return [{"id": 200, "assetbundleName": "stamp001", "seq": 1}]

    async def fake_collect_new_card_nodes(*, server: str, **_kwargs):
        assert server == "jp"
        return ["preview-node"], 1, 0

    async def fake_build_new_card_forward(*, server: str, revision: str, prepared_nodes, prepared_card_count, prepared_stamp_count, **_kwargs):
        assert server == "jp"
        assert revision == "test"
        assert prepared_nodes == ["preview-node"]
        assert prepared_card_count == 1
        assert prepared_stamp_count == 0
        return "new-card-forward"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.master_data_provider, "get_cards", fake_get_cards)
    monkeypatch.setattr(service_module.master_data_provider, "get_stamps", fake_get_stamps)
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_collect_new_card_nodes",
        fake_collect_new_card_nodes,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_build_new_card_forward",
        fake_build_new_card_forward,
    )

    result = await service_module.moesekai_app.handle_test_new_card_reminder(
        platform="qq",
        user_id="123456",
        server=None,
    )

    assert result == "new-card-forward"


@pytest.mark.asyncio
async def test_handle_personal_archive_still_requires_binding(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is False
        return None, "未绑定"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )

    result = await service_module.moesekai_app.handle_personal_archive(
        "qq",
        "123456",
        None,
        is_superuser=False,
    )

    assert result == "你还没有绑定任何账号，请先使用“绑定 [区服] <游戏ID>”"


@pytest.mark.asyncio
async def test_handle_default_server_still_requires_binding(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_get_or_create_user_settings(*_args, **_kwargs):
        return SimpleNamespace(default_server=None)

    async def fake_list_user_bindings(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module,
        "get_or_create_user_settings",
        fake_get_or_create_user_settings,
    )
    monkeypatch.setattr(
        service_module,
        "list_user_bindings",
        fake_list_user_bindings,
    )

    result = await service_module.moesekai_app.handle_default_server(
        "qq",
        "123456",
        None,
        is_superuser=False,
    )

    assert result == "你还没有绑定任何账号"


@pytest.mark.asyncio
async def test_handle_deck_still_requires_binding(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_binding_for_user(*_args, **_kwargs):
        return None, "未绑定"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_binding_for_user",
        fake_resolve_binding_for_user,
    )

    result = await service_module.moesekai_app.handle_deck(
        platform="qq",
        requester_user_id="123456",
        request=DeckCommandRequest(mode="event", server=None),
        is_superuser=False,
    )

    assert result == "未绑定"


@pytest.mark.asyncio
async def test_resolve_event_deck_without_current_event_requires_manual_event_id(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_current_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        service_module.master_data_provider,
        "get_current_event",
        fake_get_current_event,
    )

    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(mode="event", server=None),
        server="jp",
        game_id="1234567890123",
    )

    assert resolved is None
    assert error == "当前和下一期活动都不可用，请手动指定活动ID"


@pytest.mark.asyncio
async def test_resolve_custom_deck_uses_default_song_and_difficulty():
    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(
            mode="custom",
            server=None,
            custom_bonus=CustomBonusSpec(
                kind="unit",
                attr="pure",
                unit="vivid_bad_squad",
            ),
        ),
        server="jp",
        game_id="1234567890123",
    )

    assert error is None
    assert resolved is not None
    assert resolved.music_id == 74
    assert resolved.difficulty == "expert"
    assert resolved.live_type == "multi"
    assert resolved.custom_attr == "pure"
    assert resolved.custom_unit == "vivid_bad_squad"


@pytest.mark.asyncio
async def test_resolve_custom_deck_explicit_song_defaults_to_master(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_music_query(*_args, **_kwargs):
        return 277, None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_music_query",
        fake_resolve_music_query,
    )

    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(
            mode="custom",
            server=None,
            music_query="phony",
            custom_bonus=CustomBonusSpec(
                kind="unit",
                attr="pure",
                unit="vivid_bad_squad",
            ),
            explicit_music=True,
        ),
        server="jp",
        game_id="1234567890123",
    )

    assert error is None
    assert resolved is not None
    assert resolved.music_id == 277
    assert resolved.difficulty == "master"


@pytest.mark.asyncio
async def test_resolve_strongest_deck_uses_strongest_defaults():
    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(mode="strongest", server=None),
        server="jp",
        game_id="1234567890123",
    )

    assert error is None
    assert resolved is not None
    assert resolved.music_id == 141
    assert resolved.difficulty == "append"
    assert resolved.live_type == "multi"
    assert resolved.strongest_target == "power"


@pytest.mark.asyncio
async def test_resolve_strongest_deck_explicit_song_defaults_to_master_and_skill(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_music_query(*_args, **_kwargs):
        return 1, None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_music_query",
        fake_resolve_music_query,
    )

    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(
            mode="strongest",
            server=None,
            music_query="tell your world",
            strongest_target="skill",
            explicit_music=True,
        ),
        server="jp",
        game_id="1234567890123",
    )

    assert error is None
    assert resolved is not None
    assert resolved.music_id == 1
    assert resolved.difficulty == "master"
    assert resolved.strongest_target == "skill"
    assert resolved.live_type == "multi"


@pytest.mark.asyncio
async def test_resolve_challenge_deck_uses_default_song_and_difficulty(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_split(*_args, **_kwargs):
        return "初音未来", None, None

    async def fake_resolve_character_query(*_args, **_kwargs):
        return 1, None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_split_challenge_queries",
        fake_split,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_character_query",
        fake_resolve_character_query,
    )

    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(
            mode="challenge",
            server=None,
            free_text_query="初音未来",
        ),
        server="jp",
        game_id="1234567890123",
    )

    assert error is None
    assert resolved is not None
    assert resolved.character_id == 1
    assert resolved.music_id == 540
    assert resolved.difficulty == "master"


@pytest.mark.asyncio
async def test_resolve_custom_mixed_deck_character_units(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_character_query(query: str, **_kwargs):
        mapping = {"miku": (21, None), "rin": (22, None)}
        return mapping[query]

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_character_query",
        fake_resolve_character_query,
    )

    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(
            mode="custom",
            server=None,
            custom_bonus=CustomBonusSpec(
                kind="mixed",
                attr="pure",
                characters=(
                    CustomCharacterQuery(query="miku", support_unit="leo_need"),
                    CustomCharacterQuery(query="rin", support_unit=None),
                ),
            ),
        ),
        server="jp",
        game_id="1234567890123",
    )

    assert error is None
    assert resolved is not None
    assert resolved.custom_attr == "pure"
    assert resolved.custom_character_ids == (21, 22)
    assert resolved.custom_character_units == {21: "leo_need"}


@pytest.mark.asyncio
async def test_resolve_mysekai_deck_requires_event_when_no_current_event(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_current_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        service_module.master_data_provider,
        "get_current_event",
        fake_get_current_event,
    )

    resolved, error = await service_module.moesekai_app._resolve_deck_request(
        DeckCommandRequest(mode="mysekai", server=None),
        server="jp",
        game_id="1234567890123",
    )

    assert resolved is None
    assert error == "当前和下一期活动都不可用，请手动指定活动ID"


@pytest.mark.asyncio
async def test_handle_prediction_current_or_previous_event_note(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    previous_start = datetime.now() - timedelta(days=9)
    previous_end = datetime.now() - timedelta(days=1)
    previous_event = {
        "id": 194,
        "name": "上一期活动",
        "startAt": int(previous_start.timestamp() * 1000),
        "aggregateAt": int(previous_end.timestamp() * 1000),
    }

    async def fake_get_snapshot(*_args, **_kwargs):
        return _build_snapshot(server="jp", event_id=194, prediction=None), None, True

    async def fake_get_current_event(server: str, fallback: str = "prev"):
        assert server == "jp"
        assert fallback == "prev"
        return previous_event

    async def fake_get_banner(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.ranking_provider, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(
        service_module.master_data_provider,
        "get_current_event",
        fake_get_current_event,
    )
    monkeypatch.setattr(service_module.asset_provider, "get_event_banner", fake_get_banner)
    monkeypatch.setattr(
        service_module,
        "build_image_message",
        lambda image_bytes, text=None: ("image", image_bytes, text),
    )

    async def fake_render_reminder_card(view_model):
        captured["title"] = view_model.title
        captured["subtitle"] = view_model.subtitle
        captured["lines"] = list(view_model.lines)
        captured["banner"] = view_model.banner
        return b"rendered-card"

    monkeypatch.setattr(service_module, "render_reminder_card", fake_render_reminder_card)

    result = await service_module.moesekai_app.handle_prediction(
        "qq",
        "123456",
        None,
        is_superuser=False,
    )
    assert result == ("image", b"rendered-card", None)
    assert captured["title"] == "日服预测线"
    assert captured["subtitle"] == "第194期 上一期活动"
    assert captured["banner"] is None
    assert "<strong>注意</strong>：当前无进行中活动，已回退到上期活动结榜线" in captured["lines"]
    assert "<strong>T100</strong>: 结榜 1,000,000" in captured["lines"]
    assert "<strong>采集时间</strong>：2026-03-29 04:55:00" in captured["lines"]
    assert "<strong>数据来源</strong>：rk.exmeaning.com" in captured["lines"]


@pytest.mark.asyncio
async def test_handle_prediction_returns_banner_and_text(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    event = {
        "id": 178,
        "name": "Deep Dark For Light",
        "assetbundleName": "event_wavering_2026",
    }

    async def fake_get_snapshot(*_args, **_kwargs):
        return _build_snapshot(server="jp", event_id=178), None, False

    async def fake_get_event_by_id(server: str, event_id: int):
        assert server == "jp"
        assert event_id == 178
        return event

    async def fake_get_banner(server: str, assetbundle: str):
        assert server == "jp"
        assert assetbundle == "event_wavering_2026"
        return b"banner-image"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.ranking_provider, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(service_module.asset_provider, "get_event_banner", fake_get_banner)
    monkeypatch.setattr(
        service_module,
        "build_image_message",
        lambda image_bytes, text=None: ("image", image_bytes, text),
    )

    async def fake_render_reminder_card(view_model):
        captured["title"] = view_model.title
        captured["subtitle"] = view_model.subtitle
        captured["lines"] = list(view_model.lines)
        captured["banner"] = view_model.banner
        return b"rendered-card"

    monkeypatch.setattr(service_module, "render_reminder_card", fake_render_reminder_card)

    result = await service_module.moesekai_app.handle_prediction(
        "qq",
        "123456",
        None,
        178,
        is_superuser=False,
    )
    assert result == ("image", b"rendered-card", None)
    assert captured["title"] == "日服预测线"
    assert captured["subtitle"] == "第178期 Deep Dark For Light"
    assert captured["banner"] == b"banner-image"
    assert "<strong>T100</strong>: 当前 1,000,000 / 预测 1,234,567" in captured["lines"]
    assert "<strong>采集时间</strong>：2026-03-29 04:55:00" in captured["lines"]


@pytest.mark.asyncio
async def test_handle_prediction_with_explicit_event_id_no_data(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "jp", None

    async def fake_get_snapshot(*_args, **_kwargs):
        return None, None, False

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 178, "name": "Deep Dark For Light"}

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.ranking_provider, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )

    result = await service_module.moesekai_app.handle_prediction(
        "qq",
        "123456",
        None,
        178,
        is_superuser=False,
    )
    assert result == "第178期活动暂无可用预测线数据"


@pytest.mark.asyncio
async def test_handle_ycx_with_explicit_event_id_uses_historical_screenshot(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "cn", None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 166, "name": "历史活动"}

    async def fake_capture_ranking(server: str, event_id: int | None = None):
        assert server == "cn"
        assert event_id == 166
        return b"image-bytes"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_ranking",
        fake_capture_ranking,
    )

    result = await service_module.moesekai_app.handle_ycx(
        "qq",
        "123456",
        None,
        166,
        is_superuser=False,
    )
    assert result == b"image-bytes"


@pytest.mark.asyncio
async def test_handle_ycx_with_explicit_event_id_falls_back_to_text(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "cn", None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 166, "name": "历史活动"}

    async def fake_capture_ranking(*_args, **_kwargs):
        raise ScreenshotError("榜线", ["历史页面无可见数据"])

    async def fake_get_latest_snapshot(*_args, **_kwargs):
        return _build_snapshot(
            server="cn",
            event_id=166,
            status="finished",
            score=1_111_111,
            prediction=None,
        )

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_ranking",
        fake_capture_ranking,
    )
    monkeypatch.setattr(
        service_module.ranking_provider,
        "get_latest_snapshot",
        fake_get_latest_snapshot,
    )

    result = await service_module.moesekai_app.handle_ycx(
        "qq",
        "123456",
        None,
        166,
        is_superuser=False,
    )
    assert "历史活动页面暂不可用，已回退为文字数据" in result
    assert "状态：finished" in result
    assert "T100: 结榜 1,111,111" in result


@pytest.mark.asyncio
async def test_handle_ycx_with_explicit_event_id_returns_no_data(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **kwargs):
        assert kwargs["explicit_server"] is None
        assert kwargs["fallback_jp"] is True
        return "cn", None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 166, "name": "历史活动"}

    async def fake_capture_ranking(*_args, **_kwargs):
        raise ScreenshotError("榜线", ["历史页面无可见数据"])

    async def fake_get_latest_snapshot(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_ranking",
        fake_capture_ranking,
    )
    monkeypatch.setattr(
        service_module.ranking_provider,
        "get_latest_snapshot",
        fake_get_latest_snapshot,
    )

    result = await service_module.moesekai_app.handle_ycx(
        "qq",
        "123456",
        None,
        166,
        is_superuser=False,
    )
    assert result == "第166期活动暂无可用榜线数据"


@pytest.mark.asyncio
async def test_handle_story_appends_bilibili_url(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 199, "name": "剧情活动"}

    async def fake_capture_story(event_id: int):
        assert event_id == 199
        return b"story-image"

    async def fake_get_bvid(event_id: int):
        assert event_id == 199
        return "https://www.bilibili.com/video/BV1xx411c7mD"

    cache_set_calls: list[tuple[int, bytes]] = []

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        service_module.story_cache_provider,
        "get",
        lambda event_id: None,
    )
    monkeypatch.setattr(
        service_module.story_cache_provider,
        "set",
        lambda event_id, payload: cache_set_calls.append((event_id, payload)),
    )
    monkeypatch.setattr(service_module.screenshot_service, "capture_story", fake_capture_story)
    monkeypatch.setattr(service_module.hub_provider, "get_event_bilibili_url", fake_get_bvid)

    result = await service_module.moesekai_app.handle_story(199)
    assert isinstance(result, MoeImageTextMessage)
    assert result.image_bytes == b"story-image"
    assert result.text == "B站链接：https://www.bilibili.com/video/BV1xx411c7mD"
    assert cache_set_calls == [(199, b"story-image")]


@pytest.mark.asyncio
async def test_handle_story_uses_cache_when_available(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 199, "name": "剧情活动"}

    async def fail_capture_story(_event_id: int):
        raise AssertionError("命中剧情缓存时不应再次截图")

    async def fake_get_bvid(event_id: int):
        assert event_id == 199
        return "https://www.bilibili.com/video/BV1xx411c7mD"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        service_module.story_cache_provider,
        "get",
        lambda event_id: b"cached-story" if event_id == 199 else None,
    )
    monkeypatch.setattr(service_module.screenshot_service, "capture_story", fail_capture_story)
    monkeypatch.setattr(service_module.hub_provider, "get_event_bilibili_url", fake_get_bvid)

    result = await service_module.moesekai_app.handle_story(199)
    assert isinstance(result, MoeImageTextMessage)
    assert result.image_bytes == b"cached-story"
    assert result.text == "B站链接：https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.mark.asyncio
async def test_handle_story_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 199, "name": "剧情活动"}

    async def fake_capture_story(event_id: int):
        assert event_id == 199
        return b"fresh-story"

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        service_module.story_cache_provider,
        "get",
        lambda event_id: b"cached-story" if event_id == 199 else None,
    )
    monkeypatch.setattr(
        service_module.story_cache_provider,
        "set",
        lambda _event_id, _payload: None,
    )
    monkeypatch.setattr(service_module.screenshot_service, "capture_story", fake_capture_story)

    async def fake_get_bvid_none(_event_id: int):
        return None

    monkeypatch.setattr(
        service_module.hub_provider,
        "get_event_bilibili_url",
        fake_get_bvid_none,
    )

    result = await service_module.moesekai_app.handle_story(199, force_refresh=True)
    assert isinstance(result, MoeImageTextMessage)
    assert result.image_bytes == b"fresh-story"
    assert result.text is None


@pytest.mark.asyncio
async def test_handle_story_deduplicates_inflight_capture(monkeypatch: pytest.MonkeyPatch):
    app = service_module.MoeSekaiApplication()
    release = asyncio.Event()
    capture_calls = 0

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 199, "name": "剧情活动"}

    async def fake_capture_story(event_id: int):
        nonlocal capture_calls
        assert event_id == 199
        capture_calls += 1
        await release.wait()
        return b"shared-story"

    monkeypatch.setattr(app, "_get_event_by_id", fake_get_event_by_id)
    monkeypatch.setattr(service_module.story_cache_provider, "get", lambda _event_id: None)
    monkeypatch.setattr(service_module.story_cache_provider, "set", lambda *_args: None)
    monkeypatch.setattr(service_module.screenshot_service, "capture_story", fake_capture_story)

    async def fake_get_bvid_none(_event_id: int):
        return None

    monkeypatch.setattr(service_module.hub_provider, "get_event_bilibili_url", fake_get_bvid_none)

    first = asyncio.create_task(app.handle_story(199))
    second = asyncio.create_task(app.handle_story(199))
    await asyncio.sleep(0.05)
    release.set()
    left, right = await asyncio.gather(first, second)

    assert isinstance(left, MoeImageTextMessage)
    assert isinstance(right, MoeImageTextMessage)
    assert left.image_bytes == b"shared-story"
    assert right.image_bytes == b"shared-story"
    assert capture_calls == 1


@pytest.mark.asyncio
async def test_handle_random_manga_downloads_image(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_random_manga():
        return {
            "id": 351,
            "image_url": "https://moe.exmeaning.com/mangas/351.png",
            "url": "https://www.bilibili.com/opus/1183974551994761216",
        }

    async def fake_get_content(url: str):
        assert url.endswith("/351.png")
        return b"manga-image"

    monkeypatch.setattr(service_module.hub_provider, "get_random_manga", fake_get_random_manga)
    monkeypatch.setattr(service_module.AsyncHttpx, "get_content", fake_get_content)

    result = await service_module.moesekai_app.handle_random_manga()
    assert isinstance(result, MoeImageTextMessage)
    assert result.image_bytes == b"manga-image"
    assert "B站链接：" in (result.text or "")
    assert "1183974551994761216" in (result.text or "")


@pytest.mark.asyncio
async def test_handle_manga_by_id_returns_not_found(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_manga_by_id(_manga_id: int):
        return None

    monkeypatch.setattr(service_module.hub_provider, "get_manga_by_id", fake_get_manga_by_id)

    result = await service_module.moesekai_app.handle_manga_by_id(999999)
    assert result == "未找到第 999999 话四格漫画"


@pytest.mark.asyncio
async def test_handle_manga_by_id_returns_image_and_link(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_manga_by_id(manga_id: int):
        assert manga_id == 351
        return {
            "id": 351,
            "image_url": "https://moe.exmeaning.com/mangas/351.png",
            "url": "https://www.bilibili.com/opus/1183974551994761216",
        }

    async def fake_get_content(url: str):
        assert url.endswith("/351.png")
        return b"manga-image"

    monkeypatch.setattr(service_module.hub_provider, "get_manga_by_id", fake_get_manga_by_id)
    monkeypatch.setattr(service_module.AsyncHttpx, "get_content", fake_get_content)

    result = await service_module.moesekai_app.handle_manga_by_id(351)
    assert isinstance(result, MoeImageTextMessage)
    assert result.image_bytes == b"manga-image"
    assert "B站链接：" in (result.text or "")


@pytest.mark.asyncio
async def test_handle_character_uses_cache_when_available(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve_character_id(*_args, **_kwargs):
        return "21"

    async def fake_capture_character(character_id: int):
        raise AssertionError(f"命中缓存后不应再次截图: {character_id}")

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_character_id",
        fake_resolve_character_id,
    )
    monkeypatch.setattr(
        service_module.character_cache_provider,
        "get",
        lambda character_id: b"cached-character" if character_id == 21 else None,
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_character",
        fake_capture_character,
    )

    result = await service_module.moesekai_app.handle_character("初音未来")
    assert result == b"cached-character"


@pytest.mark.asyncio
async def test_handle_character_returns_screenshot_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_character_id(*_args, **_kwargs):
        return "21"

    async def fake_capture_character(character_id: int):
        assert character_id == 21
        return b"character-image"

    set_calls: list[tuple[int, bytes]] = []

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_character_id",
        fake_resolve_character_id,
    )
    monkeypatch.setattr(service_module.character_cache_provider, "get", lambda _character_id: None)
    monkeypatch.setattr(
        service_module.character_cache_provider,
        "set",
        lambda character_id, payload: set_calls.append((character_id, payload)),
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_character",
        fake_capture_character,
    )

    result = await service_module.moesekai_app.handle_character("初音未来")
    assert result == b"character-image"
    assert set_calls == [(21, b"character-image")]


@pytest.mark.asyncio
async def test_handle_character_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve_character_id(*_args, **_kwargs):
        return "21"

    async def fake_capture_character(character_id: int):
        assert character_id == 21
        return b"fresh-character"

    set_calls: list[tuple[int, bytes]] = []

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_character_id",
        fake_resolve_character_id,
    )
    monkeypatch.setattr(
        service_module.character_cache_provider,
        "get",
        lambda _character_id: b"stale-character",
    )
    monkeypatch.setattr(
        service_module.character_cache_provider,
        "set",
        lambda character_id, payload: set_calls.append((character_id, payload)),
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_character",
        fake_capture_character,
    )

    result = await service_module.moesekai_app.handle_character(
        "初音未来",
        force_refresh=True,
    )
    assert result == b"fresh-character"
    assert set_calls == [(21, b"fresh-character")]


@pytest.mark.asyncio
async def test_handle_character_deduplicates_inflight_capture(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()
    release = asyncio.Event()
    capture_calls = 0

    async def fake_resolve_character_id(*_args, **_kwargs):
        return "21"

    async def fake_capture_character(character_id: int):
        nonlocal capture_calls
        assert character_id == 21
        capture_calls += 1
        await release.wait()
        return b"shared-character"

    monkeypatch.setattr(app, "_resolve_character_id", fake_resolve_character_id)
    monkeypatch.setattr(service_module.character_cache_provider, "get", lambda _character_id: None)
    monkeypatch.setattr(service_module.character_cache_provider, "set", lambda *_args: None)
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_character",
        fake_capture_character,
    )

    first = asyncio.create_task(app.handle_character("初音未来"))
    second = asyncio.create_task(app.handle_character("初音未来"))
    await asyncio.sleep(0.05)
    release.set()
    left, right = await asyncio.gather(first, second)

    assert left == b"shared-character"
    assert right == b"shared-character"
    assert capture_calls == 1


@pytest.mark.asyncio
async def test_handle_character_does_not_overwrite_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_character_id(*_args, **_kwargs):
        return "21"

    async def fake_capture_character(_character_id: int):
        raise ScreenshotError("查角色", ["页面加载失败"])

    set_calls: list[tuple[int, bytes]] = []

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_character_id",
        fake_resolve_character_id,
    )
    monkeypatch.setattr(service_module.character_cache_provider, "get", lambda _character_id: None)
    monkeypatch.setattr(
        service_module.character_cache_provider,
        "set",
        lambda character_id, payload: set_calls.append((character_id, payload)),
    )
    monkeypatch.setattr(
        service_module.screenshot_service,
        "capture_character",
        fake_capture_character,
    )

    result = await service_module.moesekai_app.handle_character("初音未来")
    assert result == "查角色截图失败，请稍后重试"
    assert set_calls == []


@pytest.mark.asyncio
async def test_sync_music_aliases_uses_bulk_index(monkeypatch: pytest.MonkeyPatch):
    saved_state: dict[str, object] = {}
    refresh_calls: list[list[dict[str, object]]] = []

    monkeypatch.setattr(service_module._ALIAS_STATE, "load", lambda default: {})
    monkeypatch.setattr(
        service_module._ALIAS_STATE,
        "save",
        lambda payload: saved_state.update(payload),
    )

    async def fake_get_music_alias_index():
        return [
            {
                "music_id": 1,
                "title": "Tell Your World",
                "aliases": ["tyw", "告诉你的世界"],
            },
            {
                "music_id": 2,
                "title": "ロキ",
                "aliases": [],
            },
        ]

    def fake_refresh_music_alias_snapshot(items):
        refresh_calls.append(items)
        return items

    async def fail_get_music_aliases(*_args, **_kwargs):
        raise AssertionError("同步歌曲别名时不应再逐曲请求 API")

    monkeypatch.setattr(
        service_module.hub_provider,
        "get_music_alias_index",
        fake_get_music_alias_index,
    )
    monkeypatch.setattr(
        service_module.hub_provider,
        "get_music_aliases",
        fail_get_music_aliases,
    )
    monkeypatch.setattr(
        service_module.alias_provider,
        "refresh_music_alias_snapshot",
        fake_refresh_music_alias_snapshot,
    )

    await service_module.moesekai_app.sync_music_aliases(force=True)

    assert refresh_calls == [
        [
            {
                "music_id": 1,
                "title": "Tell Your World",
                "aliases": ["tyw", "告诉你的世界"],
            },
            {
                "music_id": 2,
                "title": "ロキ",
                "aliases": [],
            },
        ]
    ]
    assert saved_state["music_alias_source"] == "MoeSekai-Hub/music_aliases.json"
    assert saved_state["music_alias_sync_count"] == 2
    assert "music_alias_sync_at" in saved_state


@pytest.mark.asyncio
async def test_seed_default_aliases_is_noop(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        service_module.hub_provider,
        "read_character_alias_seed",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("seed_default_aliases 不应再读取角色 seed 并写库")
        ),
    )

    async def fail_get_game_characters(*_args, **_kwargs):
        raise AssertionError("seed_default_aliases 不应读取角色 masterdata")

    async def fail_get_musics(*_args, **_kwargs):
        raise AssertionError("seed_default_aliases 不应读取歌曲 masterdata")

    monkeypatch.setattr(
        service_module.master_data_provider,
        "get_game_characters",
        fail_get_game_characters,
    )
    monkeypatch.setattr(
        service_module.master_data_provider,
        "get_musics",
        fail_get_musics,
    )

    await service_module.MoeSekaiApplication().seed_default_aliases()


@pytest.mark.asyncio
async def test_handle_alias_query_returns_formatted_profile(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()

    async def fake_resolve_music(*_args, **_kwargs):
        return AliasResolveResult(
            target_id="277",
            canonical_name="フォニイ",
            matched_text="277",
            matched_source="id",
        )

    async def fake_get_music_profile(*_args, **_kwargs):
        return AliasProfile(
            target_type="music",
            target_id="277",
            canonical_name="フォニイ",
            display_title="277. フォニイ",
            merged_aliases=["phony", "伪物"],
            group_aliases=["凤梨"],
        )

    monkeypatch.setattr(service_module.alias_provider, "resolve_music", fake_resolve_music)
    monkeypatch.setattr(
        service_module.alias_provider,
        "get_music_profile",
        fake_get_music_profile,
    )

    result = await app.handle_alias_command(
        target_type="music",
        operation="query",
        query="277",
        group_id="123",
        platform="qq",
    )

    assert result == "277. フォニイ\n别名：phony，伪物\n本群别名：凤梨"


@pytest.mark.asyncio
async def test_handle_alias_query_returns_header_only_when_no_aliases(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()

    async def fake_resolve_music(*_args, **_kwargs):
        return AliasResolveResult(
            target_id="21",
            canonical_name="初音未来的消失",
            matched_text="21",
            matched_source="id",
        )

    async def fake_get_music_profile(*_args, **_kwargs):
        return AliasProfile(
            target_type="music",
            target_id="21",
            canonical_name="初音未来的消失",
            display_title="21. 初音未来的消失",
            merged_aliases=[],
            group_aliases=[],
        )

    monkeypatch.setattr(service_module.alias_provider, "resolve_music", fake_resolve_music)
    monkeypatch.setattr(
        service_module.alias_provider,
        "get_music_profile",
        fake_get_music_profile,
    )

    result = await app.handle_alias_command(
        target_type="music",
        operation="query",
        query="21",
        group_id="123",
        platform="qq",
    )

    assert result == "21. 初音未来的消失"


@pytest.mark.asyncio
async def test_handle_alias_query_returns_not_found_when_numeric_id_has_no_profile(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()

    async def fake_resolve_music(*_args, **_kwargs):
        return AliasResolveResult(
            target_id="999999",
            canonical_name="999999",
            matched_text="999999",
            matched_source="id",
        )

    async def fake_get_music_profile(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service_module.alias_provider, "resolve_music", fake_resolve_music)
    monkeypatch.setattr(
        service_module.alias_provider,
        "get_music_profile",
        fake_get_music_profile,
    )

    result = await app.handle_alias_command(
        target_type="music",
        operation="query",
        query="999999",
        group_id="123",
        platform="qq",
    )

    assert result == "未找到相关别名"


@pytest.mark.asyncio
async def test_handle_alias_query_long_result_returns_image(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()
    captured: dict[str, object] = {}

    async def fake_resolve_music(*_args, **_kwargs):
        return AliasResolveResult(
            target_id="277",
            canonical_name="フォニイ",
            matched_text="277",
            matched_source="id",
        )

    async def fake_get_music_profile(*_args, **_kwargs):
        return AliasProfile(
            target_type="music",
            target_id="277",
            canonical_name="フォニイ",
            display_title="277. フォニイ",
            merged_aliases=[f"别名{i}" for i in range(21)],
            group_aliases=[],
        )

    def fake_build_alias_profile_image(
        *,
        title: str,
        aliases: list[str],
        group_aliases=None,
        **_kwargs,
    ):
        captured["title"] = title
        captured["aliases"] = list(aliases)
        captured["group_aliases"] = list(group_aliases or [])
        return b"alias-image"

    monkeypatch.setattr(service_module.alias_provider, "resolve_music", fake_resolve_music)
    monkeypatch.setattr(
        service_module.alias_provider,
        "get_music_profile",
        fake_get_music_profile,
    )
    monkeypatch.setattr(
        service_module,
        "build_alias_profile_image",
        fake_build_alias_profile_image,
    )

    result = await app.handle_alias_command(
        target_type="music",
        operation="query",
        query="277",
        group_id="123",
        platform="qq",
    )

    assert isinstance(result, MoeImageTextMessage)
    assert result.image_bytes == b"alias-image"
    assert captured["title"] == "277. フォニイ"
    assert captured["aliases"] == [f"别名{i}" for i in range(21)]
    assert captured["group_aliases"] == []


def test_build_alias_profile_image_returns_png():
    image_bytes = build_alias_profile_image(
        title="277. フォニイ",
        aliases=[
            "phony",
            "伪物",
            "2周年25",
            "火泥",
            "佛你",
            "呱呱呱呱呱呱呱呱呱",
            "安踢怕西哇",
            "佛泥↗佛泥↘",
        ],
        group_aliases=["凤梨", "简单"],
    )

    with PILImage.open(BytesIO(image_bytes)) as image:
        assert image.format == "PNG"
        assert image.width == 1120
        assert image.height > 220


def test_build_alias_section_layout_prefers_four_column_grid_for_dense_aliases():
    canvas = PILImage.new("RGB", (1120, 64), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    layout = _build_alias_section_layout(
        draw,
        [f"别名{i}" for i in range(24)],
        font=_load_font(23),
        content_width=1024,
        column_gap=18,
        item_padding_x=16,
        item_padding_y=12,
        bullet_size=8,
        bullet_gap=12,
    )

    assert layout.base_column_count == 4
    assert max(len(row.items) for row in layout.rows) >= 4
    assert all(
        placement.block.column_span == 1
        for row in layout.rows
        for placement in row.items
    )


def test_build_alias_section_layout_uses_mixed_spans_for_long_alias():
    canvas = PILImage.new("RGB", (1120, 64), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    layout = _build_alias_section_layout(
        draw,
        [
            "普通别名",
            "立定跳远三点水单人旁",
            "另一个别名",
            "第四个别名",
            "第五个别名",
        ],
        font=_load_font(23),
        content_width=1024,
        column_gap=18,
        item_padding_x=16,
        item_padding_y=12,
        bullet_size=8,
        bullet_gap=12,
    )

    spans = [placement.block.column_span for row in layout.rows for placement in row.items]
    assert layout.base_column_count == 4
    assert max(spans) > 1
    assert min(spans) == 1


def test_build_alias_section_layout_wraps_only_extreme_long_alias():
    canvas = PILImage.new("RGB", (1120, 64), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    extreme_alias = "超" * 120 + "长别名"

    layout = _build_alias_section_layout(
        draw,
        [
            "普通别名",
            extreme_alias,
            "另一个别名",
            "第四个别名",
        ],
        font=_load_font(23),
        content_width=1024,
        column_gap=18,
        item_padding_x=16,
        item_padding_y=12,
        bullet_size=8,
        bullet_gap=12,
    )

    multiline_placements = [
        placement
        for row in layout.rows
        for placement in row.items
        if len(placement.block.lines) > 1
    ]
    single_column_placements = [
        placement
        for row in layout.rows
        for placement in row.items
        if placement.block.column_span == 1
    ]

    assert len(multiline_placements) == 1
    assert multiline_placements[0].block.column_span == layout.base_column_count
    assert single_column_placements


def test_build_alias_profile_image_grows_beyond_legacy_height_limit():
    image_bytes = build_alias_profile_image(
        title="277. フォニイ",
        aliases=[f"别名{i}" for i in range(200)],
    )

    with PILImage.open(BytesIO(image_bytes)) as image:
        assert image.height > 2400


def test_build_alias_profile_image_respects_explicit_width():
    image_bytes = build_alias_profile_image(
        title="277. フォニイ",
        aliases=["phony", "伪物", "立定跳远三点水单人旁", "人造花"],
        width=980,
    )

    with PILImage.open(BytesIO(image_bytes)) as image:
        assert image.width == 980


@pytest.mark.asyncio
async def test_handle_alias_query_search_results_include_titles(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()

    async def fake_resolve_music(*_args, **_kwargs):
        return None

    async def fake_search_music(*_args, **_kwargs):
        return [
            AliasSearchHit(
                target_id="277",
                canonical_name="フォニイ",
                matched_text="phony",
                matched_source="system",
            ),
            AliasSearchHit(
                target_id="278",
                canonical_name="ロウワー",
                matched_text="lower",
                matched_source="official",
            ),
        ]

    monkeypatch.setattr(service_module.alias_provider, "resolve_music", fake_resolve_music)
    monkeypatch.setattr(service_module.alias_provider, "search_music", fake_search_music)

    result = await app.handle_alias_command(
        target_type="music",
        operation="query",
        query="pho",
        group_id="123",
        platform="qq",
    )

    assert result == "phony -> 277. フォニイ\nlower -> 278. ロウワー"


@pytest.mark.asyncio
async def test_handle_alias_remove_rejects_system_alias(
    monkeypatch: pytest.MonkeyPatch,
):
    app = service_module.MoeSekaiApplication()

    async def fake_remove_managed_alias(**_kwargs):
        return "system"

    monkeypatch.setattr(
        service_module.alias_provider,
        "remove_managed_alias",
        fake_remove_managed_alias,
    )

    result = await app.handle_alias_command(
        target_type="music",
        operation="remove",
        query="phony",
        alias="phony",
        group_id="123",
        platform="qq",
        is_superuser=True,
        can_manage_group=True,
        global_scope=True,
    )

    assert result == "该别名为系统别名，不可删除"


@pytest.mark.asyncio
async def test_handle_test_new_card_reminder_returns_clear_message_when_no_media(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **_kwargs):
        return "jp", None

    async def fake_get_cards(_server: str):
        return [
            {
                "id": 100,
                "assetbundleName": "res001_no001",
                "cardRarityType": "rarity_4",
                "specialTrainingCosts": [{"resourceId": 1}],
                "releaseAt": 100,
            },
            {
                "id": 101,
                "assetbundleName": "res001_no002",
                "cardRarityType": "rarity_3",
                "specialTrainingCosts": [{"resourceId": 1}],
                "releaseAt": 200,
            },
        ]

    async def fake_get_stamps(_server: str):
        return [
            {
                "id": 200,
                "assetbundleName": "stamp200",
                "seq": 200,
            }
        ]

    async def fake_get_card_image(*_args, **_kwargs):
        return None

    async def fake_get_stamp_image(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.master_data_provider, "get_cards", fake_get_cards)
    monkeypatch.setattr(service_module.master_data_provider, "get_stamps", fake_get_stamps)
    monkeypatch.setattr(service_module.asset_provider, "get_card_image", fake_get_card_image)
    monkeypatch.setattr(service_module.asset_provider, "get_stamp_image", fake_get_stamp_image)

    result = await service_module.moesekai_app.handle_test_new_card_reminder(
        platform="qq",
        user_id="123456",
        server="jp",
    )

    assert result == "当前暂无可用的新卡上线提醒测试样本，请稍后重试或显式指定 card_id"


@pytest.mark.asyncio
async def test_handle_test_new_card_reminder_reports_missing_explicit_card_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_resolve_default_server(*_args, **_kwargs):
        return "jp", None

    async def fake_get_cards(_server: str):
        return [{"id": 100, "assetbundleName": "res001_no001"}]

    async def fake_get_stamps(_server: str):
        return []

    monkeypatch.setattr(
        service_module.moesekai_app,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(service_module.master_data_provider, "get_cards", fake_get_cards)
    monkeypatch.setattr(service_module.master_data_provider, "get_stamps", fake_get_stamps)

    result = await service_module.moesekai_app.handle_test_new_card_reminder(
        platform="qq",
        user_id="123456",
        server="jp",
        card_ids=[947, 948],
    )

    assert result == "未找到指定的新卡测试数据"
