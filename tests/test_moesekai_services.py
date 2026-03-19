from __future__ import annotations

from datetime import datetime, timedelta

import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai import services as moesekai_services
from zhenxun.plugins.moesekai.screenshot import ScreenshotError


@pytest.mark.asyncio
async def test_handle_ycx_tw_returns_unsupported(monkeypatch: pytest.MonkeyPatch):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
        return "tw", None

    async def fail_capture(*_args, **_kwargs):
        raise AssertionError("台服 ycx 不应进入截图逻辑")

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services.screenshot_service,
        "capture_ranking",
        fail_capture,
    )

    result = await moesekai_services.handle_ycx(
        "qq",
        "123456",
        "tw",
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
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services.master_data_service,
        "update_region",
        fake_update_region,
    )

    result = await moesekai_services.handle_update(
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
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )

    result = await moesekai_services.handle_update(
        "qq",
        "123456",
        None,
        update_all=True,
        is_superuser=False,
    )
    assert result == "pjsk update all 仅超级用户可用"


@pytest.mark.asyncio
async def test_handle_prediction_falls_back_to_previous_event(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
        return "jp", None

    async def fake_get_user_binding(*_args, **_kwargs):
        return None

    previous_start = datetime.now() - timedelta(days=9)
    previous_end = datetime.now() - timedelta(days=1)
    previous_event = {
        "id": 194,
        "name": "上一期活动",
        "startAt": int(previous_start.timestamp() * 1000),
        "aggregateAt": int(previous_end.timestamp() * 1000),
    }

    async def fake_get_current_event(server: str, fallback=None):
        assert server == "jp"
        assert fallback == "prev"
        return previous_event

    async def fake_fetch_prediction_payload(server: str, event_id: int):
        assert server == "jp"
        assert event_id == 194
        return {
            "timestamp": 1_773_851_332_000,
            "data": {
                "charts": [
                    {"Rank": 100, "PredictedScore": 1234567},
                    {"Rank": 1000, "PredictedScore": 7654321},
                ]
            },
        }

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services,
        "get_user_binding",
        fake_get_user_binding,
    )
    monkeypatch.setattr(
        moesekai_services.master_data_service,
        "get_current_event",
        fake_get_current_event,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_fetch_prediction_payload",
        fake_fetch_prediction_payload,
    )

    result = await moesekai_services.handle_prediction(
        "qq",
        "123456",
        None,
        is_superuser=False,
    )
    assert "当前无进行中活动，已回退到上期活动结榜线" in result
    assert "当前活动：第194期 上一期活动" in result
    assert "T100: 1,234,567" in result


@pytest.mark.asyncio
async def test_handle_prediction_with_explicit_event_id_skips_current_event(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
        return "jp", None

    async def fake_get_user_binding(*_args, **_kwargs):
        return None

    async def fail_get_current_event(*_args, **_kwargs):
        raise AssertionError("显式活动ID时不应查询当前活动")

    explicit_event = {"id": 178, "name": "Deep Dark For Light"}

    async def fake_get_event_by_id(server: str, event_id: int):
        assert server == "jp"
        assert event_id == 178
        return explicit_event

    async def fake_fetch_prediction_payload(server: str, event_id: int):
        assert server == "jp"
        assert event_id == 178
        return {
            "timestamp": 1_773_851_332_000,
            "data": {
                "charts": [
                    {"Rank": 100, "PredictedScore": 1234567},
                ]
            },
        }

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services,
        "get_user_binding",
        fake_get_user_binding,
    )
    monkeypatch.setattr(
        moesekai_services.master_data_service,
        "get_current_event",
        fail_get_current_event,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_fetch_prediction_payload",
        fake_fetch_prediction_payload,
    )

    result = await moesekai_services.handle_prediction(
        "qq",
        "123456",
        None,
        178,
        is_superuser=False,
    )
    assert "活动：第178期 Deep Dark For Light" in result
    assert "T100: 1,234,567" in result


@pytest.mark.asyncio
async def test_handle_prediction_with_explicit_event_id_no_data(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
        return "jp", None

    async def fake_get_user_binding(*_args, **_kwargs):
        return None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 178, "name": "Deep Dark For Light"}

    async def fake_fetch_prediction_payload(*_args, **_kwargs):
        return {"timestamp": 1_773_851_332_000, "data": {"charts": None}}

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services,
        "get_user_binding",
        fake_get_user_binding,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_fetch_prediction_payload",
        fake_fetch_prediction_payload,
    )

    result = await moesekai_services.handle_prediction(
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

    async def fake_get_user_binding(*_args, **_kwargs):
        return None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 166, "name": "历史活动"}

    async def fake_capture_ranking(server: str, event_id: int | None = None):
        assert server == "cn"
        assert event_id == 166
        return b"image-bytes"

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services,
        "get_user_binding",
        fake_get_user_binding,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        moesekai_services.screenshot_service,
        "capture_ranking",
        fake_capture_ranking,
    )

    result = await moesekai_services.handle_ycx(
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

    async def fake_get_user_binding(*_args, **_kwargs):
        return None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 166, "name": "历史活动"}

    async def fake_capture_ranking(*_args, **_kwargs):
        raise ScreenshotError("榜线", ["历史页面无可见数据"])

    async def fake_fetch_prediction_payload(*_args, **_kwargs):
        return {
            "timestamp": 1_773_851_332_000,
            "data": {
                "charts": [
                    {"Rank": 100, "PredictedScore": 1111111},
                ]
            },
        }

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services,
        "get_user_binding",
        fake_get_user_binding,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        moesekai_services.screenshot_service,
        "capture_ranking",
        fake_capture_ranking,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_fetch_prediction_payload",
        fake_fetch_prediction_payload,
    )

    result = await moesekai_services.handle_ycx(
        "qq",
        "123456",
        None,
        166,
        is_superuser=False,
    )
    assert "历史活动页面暂不可用，已回退为文字数据" in result
    assert "国服结榜榜线" in result
    assert "T100: 1,111,111" in result


@pytest.mark.asyncio
async def test_handle_ycx_with_explicit_event_id_returns_no_data(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_is_qq_blacklisted(*_args, **_kwargs):
        return None

    async def fake_resolve_default_server(*_args, **_kwargs):
        return "cn", None

    async def fake_get_user_binding(*_args, **_kwargs):
        return None

    async def fake_get_event_by_id(*_args, **_kwargs):
        return {"id": 166, "name": "历史活动"}

    async def fake_capture_ranking(*_args, **_kwargs):
        raise ScreenshotError("榜线", ["历史页面无可见数据"])

    async def fake_fetch_prediction_payload(*_args, **_kwargs):
        return {"timestamp": 1_773_851_332_000, "data": {"charts": None}}

    monkeypatch.setattr(
        moesekai_services,
        "_is_qq_blacklisted",
        fake_is_qq_blacklisted,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_resolve_default_server",
        fake_resolve_default_server,
    )
    monkeypatch.setattr(
        moesekai_services,
        "get_user_binding",
        fake_get_user_binding,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_get_event_by_id",
        fake_get_event_by_id,
    )
    monkeypatch.setattr(
        moesekai_services.screenshot_service,
        "capture_ranking",
        fake_capture_ranking,
    )
    monkeypatch.setattr(
        moesekai_services,
        "_fetch_prediction_payload",
        fake_fetch_prediction_payload,
    )

    result = await moesekai_services.handle_ycx(
        "qq",
        "123456",
        None,
        166,
        is_superuser=False,
    )
    assert result == "第166期活动暂无可用榜线数据"
