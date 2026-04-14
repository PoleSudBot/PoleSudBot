from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from zhenxun.builtin_plugins.shop._data_source import SHOP_TRANSITION_NOTICE, ShopManage
from zhenxun.builtin_plugins.sign_in import _data_source as sign_data_source
from zhenxun.builtin_plugins.sign_in._random_event import random_event
from zhenxun.models.sign_user import SignUser


@pytest.fixture(scope="session", autouse=True)
async def nonebug_init(_nonebot_init: None, after_nonebot_init: None):
    # 这组用例只覆盖纯逻辑分支，不需要启动整套插件生命周期；
    # 在模块内覆盖 fixture，避免把会话级 nonebug 配置泄漏到其他测试。
    return None


def test_random_event_only_returns_gold(monkeypatch: pytest.MonkeyPatch):
    def fake_get_config(module: str, key: str):
        assert module == "sign_in"
        assert key == "MAX_SIGN_GOLD"
        return 200

    monkeypatch.setattr(
        "zhenxun.builtin_plugins.sign_in._random_event.Config.get_config",
        fake_get_config,
    )
    monkeypatch.setattr(
        "zhenxun.builtin_plugins.sign_in._random_event.random.randint",
        lambda _start, end: end,
    )

    reward = random_event(6.8)

    assert isinstance(reward, int)
    assert reward == 6


@pytest.mark.asyncio
async def test_handle_sign_in_adds_gold_once_and_never_adds_props(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    user = SimpleNamespace(
        impression=5,
        add_probability=0,
        specify_probability=0,
        user_id="user_1",
    )
    session = SimpleNamespace(self_id="bot_1")

    async def fake_sign(_user, impression: float, bot_id: str, platform: str):
        calls["sign"] = (impression, bot_id, platform)

    async def fake_add_gold(
        user_id: str, gold: int, handle: str, platform: str | None = None
    ):
        calls["add_gold"] = (user_id, gold, handle, platform)

    async def fail_add_props(*_args, **_kwargs):
        raise AssertionError("过渡期签到不应再发放道具")

    async def fake_get_card(
        _user,
        _session,
        nickname: str,
        add_impression: float,
        gold: int,
        gift: str,
        is_double: bool = False,
        continuous_sign_count: int = 0,
    ):
        calls["card"] = {
            "nickname": nickname,
            "add_impression": add_impression,
            "gold": gold,
            "gift": gift,
            "is_double": is_double,
            "continuous_sign_count": continuous_sign_count,
        }
        return "card.png"

    monkeypatch.setattr(sign_data_source.PlatformUtils, "get_platform", lambda _s: "qq")
    monkeypatch.setattr(sign_data_source.SignUser, "sign", fake_sign)
    monkeypatch.setattr(sign_data_source.UserConsole, "add_gold", fake_add_gold)
    monkeypatch.setattr(
        sign_data_source.UserConsole,
        "add_props_by_name",
        fail_add_props,
        raising=False,
    )
    monkeypatch.setattr(sign_data_source, "get_card", fake_get_card)
    monkeypatch.setattr(sign_data_source, "random_event", lambda _impression: 7)
    monkeypatch.setattr(sign_data_source.random, "randint", lambda _start, _end: 10)
    monkeypatch.setattr(sign_data_source.random, "random", lambda: 0.5)
    monkeypatch.setattr(sign_data_source.secrets, "randbelow", lambda _limit: 24)

    result = await sign_data_source.SignManage._handle_sign_in(
        user,
        "测试用户",
        session,
        continuous_sign_count=3,
    )

    assert result == "card.png"
    assert calls["sign"] == (0.25, "bot_1", "qq")
    assert calls["add_gold"] == ("user_1", 17, "sign_in", "qq")
    assert calls["card"] == {
        "nickname": "测试用户",
        "add_impression": 0.25,
        "gold": 10,
        "gift": "额外金币 +7",
        "is_double": False,
        "continuous_sign_count": 3,
    }


@pytest.mark.asyncio
async def test_handle_sign_in_existing_card_still_affects_next_sign(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    user = SimpleNamespace(
        impression=5,
        add_probability=0.3,
        specify_probability=0,
        user_id="user_2",
    )
    session = SimpleNamespace(self_id="bot_2")

    async def fake_sign(_user, impression: float, bot_id: str, platform: str):
        calls["sign"] = (impression, bot_id, platform)

    async def fake_add_gold(*_args, **_kwargs):
        return None

    async def fake_get_card(
        _user,
        _session,
        _nickname: str,
        add_impression: float,
        gold: int,
        gift: str,
        is_double: bool = False,
        continuous_sign_count: int = 0,
    ):
        calls["card"] = (add_impression, gold, gift, is_double, continuous_sign_count)
        return "double-card.png"

    monkeypatch.setattr(sign_data_source.PlatformUtils, "get_platform", lambda _s: "qq")
    monkeypatch.setattr(sign_data_source.SignUser, "sign", fake_sign)
    monkeypatch.setattr(sign_data_source.UserConsole, "add_gold", fake_add_gold)
    monkeypatch.setattr(sign_data_source, "get_card", fake_get_card)
    monkeypatch.setattr(sign_data_source, "random_event", lambda _impression: 3)
    monkeypatch.setattr(sign_data_source.random, "randint", lambda _start, _end: 8)
    monkeypatch.setattr(sign_data_source.random, "random", lambda: 0.8)
    monkeypatch.setattr(sign_data_source.secrets, "randbelow", lambda _limit: 49)

    result = await sign_data_source.SignManage._handle_sign_in(
        user,
        "测试用户",
        session,
        continuous_sign_count=1,
    )

    assert result == "double-card.png"
    assert calls["sign"] == (1.0, "bot_2", "qq")
    assert calls["card"] == (1.0, 8, "额外金币 +3", True, 1)


@pytest.mark.asyncio
async def test_sign_user_sign_resets_temporary_probabilities(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeUser:
        def __init__(self):
            self.user_id = "user_3"
            self.impression = 1.5
            self.add_probability = 0.2
            self.specify_probability = 0.4
            self.sign_count = 2
            self.save_called = False

        async def save(self):
            self.save_called = True

    user = FakeUser()
    sign_log_calls: dict[str, object] = {}

    async def fake_get_or_create(*_args, **_kwargs):
        return user, False

    async def fake_create(**kwargs):
        sign_log_calls.update(kwargs)

    monkeypatch.setattr(SignUser, "get_or_create", fake_get_or_create)
    monkeypatch.setattr("zhenxun.models.sign_user.SignLog.create", fake_create)

    result = await SignUser.sign("user_3", 0.5, "bot_3", "qq")

    assert result is user
    assert user.impression == 2.0
    assert user.add_probability == 0
    assert user.specify_probability == 0
    assert user.sign_count == 3
    assert user.save_called is True
    assert sign_log_calls == {
        "user_id": "user_3",
        "impression": 0.5,
        "bot_id": "bot_3",
        "platform": "qq",
    }


@pytest.mark.asyncio
async def test_shop_buy_prop_returns_transition_notice():
    assert ShopManage.get_transition_notice() == SHOP_TRANSITION_NOTICE
    assert (
        await ShopManage.buy_prop("user_4", "神秘药水", 2, "qq")
        == SHOP_TRANSITION_NOTICE
    )
