from __future__ import annotations

from datetime import datetime, timedelta

import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.adapters.results import MoeImageTextMessage
from zhenxun.plugins.moesekai.application import service as service_module
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

    async def fake_resolve_default_server(*_args, **_kwargs):
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
async def test_handle_prediction_current_or_previous_event_note(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
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
    assert "注意：当前无进行中活动，已回退到上期活动结榜线" in captured["lines"]
    assert "T100: 结榜 1,000,000" in captured["lines"]
    assert "采集时间：2026-03-29 04:55:00" in captured["lines"]
    assert "数据来源：rk.exmeaning.com" in captured["lines"]


@pytest.mark.asyncio
async def test_handle_prediction_returns_banner_and_text(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
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
    assert "T100: 当前 1,000,000 / 预测 1,234,567" in captured["lines"]
    assert "采集时间：2026-03-29 04:55:00" in captured["lines"]


@pytest.mark.asyncio
async def test_handle_prediction_with_explicit_event_id_no_data(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
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

    async def fake_resolve_default_server(*_args, **_kwargs):
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

    async def fake_resolve_default_server(*_args, **_kwargs):
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
    assert "国服结榜榜线" in result
    assert "T100: 结榜 1,111,111" in result


@pytest.mark.asyncio
async def test_handle_ycx_with_explicit_event_id_returns_no_data(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
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
    upsert_calls: list[dict[str, object]] = []

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

    async def fake_upsert_alias_entry(**kwargs):
        upsert_calls.append(kwargs)
        return None

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
    monkeypatch.setattr(service_module, "upsert_alias_entry", fake_upsert_alias_entry)

    await service_module.moesekai_app.sync_music_aliases(force=True)

    assert upsert_calls == [
        {
            "target_type": "music",
            "target_value": "1",
            "alias": "tyw",
            "scope": "global",
            "created_by": "system",
        },
        {
            "target_type": "music",
            "target_value": "1",
            "alias": "告诉你的世界",
            "scope": "global",
            "created_by": "system",
        },
    ]
    assert saved_state["music_alias_source"] == "MoeSekai-Hub/music_aliases.json"
    assert saved_state["music_alias_sync_count"] == 2
    assert "music_alias_sync_at" in saved_state


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
