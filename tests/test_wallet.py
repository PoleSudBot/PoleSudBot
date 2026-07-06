from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest
from tortoise import Tortoise

nonebot.init()

from zhenxun.builtin_plugins.wallet import data_source as wallet_data_source
from zhenxun.models.friend_user import FriendUser
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.user_console import UserConsole
from zhenxun.models.user_gold_log import UserGoldLog
from zhenxun.utils.enum import GoldHandle
from zhenxun.utils.exception import InsufficientGold


@pytest.fixture()
async def wallet_db(tmp_path):
    db_path = tmp_path / "wallet.sqlite3"
    await Tortoise.init(
        db_url=f"sqlite://{db_path}",
        modules={
            "models": [
                "zhenxun.models.user_console",
                "zhenxun.models.user_gold_log",
                "zhenxun.models.group_member_info",
                "zhenxun.models.friend_user",
            ]
        },
        timezone="Asia/Shanghai",
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_transfer_gold_updates_balances_and_logs(wallet_db):
    sender = await UserConsole.get_user("sender", "qq")
    receiver = await UserConsole.get_user("receiver", "qq")
    sender.gold = 150
    receiver.gold = 20
    await sender.save(update_fields=["gold"])
    await receiver.save(update_fields=["gold"])

    await UserConsole.transfer_gold("sender", "receiver", 30, "qq")

    sender = await UserConsole.get(user_id="sender")
    receiver = await UserConsole.get(user_id="receiver")
    logs = await UserGoldLog.all().order_by("id")
    assert sender.gold == 120
    assert receiver.gold == 50
    assert [(log.user_id, log.gold, log.handle, log.source) for log in logs] == [
        ("sender", 30, GoldHandle.TRANSFER_OUT, "wallet:receiver"),
        ("receiver", 30, GoldHandle.TRANSFER_IN, "wallet:sender"),
    ]


@pytest.mark.asyncio
async def test_transfer_gold_rejects_invalid_requests(wallet_db):
    sender = await UserConsole.get_user("sender", "qq")
    receiver = await UserConsole.get_user("receiver", "qq")
    sender.gold = 10
    receiver.gold = 20
    await sender.save(update_fields=["gold"])
    await receiver.save(update_fields=["gold"])

    with pytest.raises(ValueError):
        await UserConsole.transfer_gold("sender", "sender", 1, "qq")
    with pytest.raises(ValueError):
        await UserConsole.transfer_gold("sender", "receiver", 0, "qq")
    with pytest.raises(InsufficientGold):
        await UserConsole.transfer_gold("sender", "receiver", 99, "qq")

    sender = await UserConsole.get(user_id="sender")
    receiver = await UserConsole.get(user_id="receiver")
    assert sender.gold == 10
    assert receiver.gold == 20
    assert await UserGoldLog.all().count() == 0


@pytest.mark.asyncio
async def test_get_balance_creates_user_with_default_gold(wallet_db):
    assert await wallet_data_source.get_balance("new_user", "qq") == 100


@pytest.mark.asyncio
async def test_wallet_rank_handles_empty_group(wallet_db):
    session = SimpleNamespace(user=SimpleNamespace(id="sender"))

    result = await wallet_data_source.wallet_rank(session, "group_1", 10)

    assert result == "当前群还没有钱包数据哦..."


@pytest.mark.asyncio
async def test_wallet_rank_renders_group_members(monkeypatch, wallet_db):
    await UserConsole.get_user("sender", "qq")
    receiver = await UserConsole.get_user("receiver", "qq")
    receiver.gold = 250
    await receiver.save(update_fields=["gold"])
    await GroupInfoUser.create(user_id="sender", group_id="group_1", user_name="我")
    await GroupInfoUser.create(user_id="receiver", group_id="group_1", user_name="你")
    await FriendUser.create(user_id="receiver", user_name="好友")
    session = SimpleNamespace(user=SimpleNamespace(id="sender"))
    rendered: dict[str, object] = {}

    class _FakeTable:
        def __init__(self, title: str, tip: str):
            rendered["title"] = title
            rendered["tip"] = tip
            self.rows = []

        def set_headers(self, headers):
            rendered["headers"] = headers
            return self

        def add_rows(self, rows):
            self.rows = rows
            rendered["rows"] = rows
            return self

    async def fake_render(table):
        rendered["table"] = table
        return b"rank-image"

    async def fake_get_avatar_path(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        wallet_data_source.PlatformUtils, "get_platform", lambda _s: "qq"
    )
    monkeypatch.setattr(
        wallet_data_source.avatar_service,
        "get_avatar_path",
        fake_get_avatar_path,
    )
    monkeypatch.setattr(wallet_data_source.ui, "table", _FakeTable)
    monkeypatch.setattr(wallet_data_source.ui, "render", fake_render)

    result = await wallet_data_source.wallet_rank(session, "group_1", 10)

    assert result == b"rank-image"
    assert rendered["title"] == "钱包群组内排行"
    assert rendered["tip"] == "你的排名在本群第 2 位哦!"
    assert rendered["headers"] == ["排名", "-", "名称", "金币", "平台"]
    assert len(rendered["rows"]) == 2
