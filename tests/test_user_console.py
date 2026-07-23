from __future__ import annotations

import asyncio

import nonebot
import pytest
from tortoise import Tortoise

nonebot.init()

from zhenxun.models.user_console import UserConsole


@pytest.fixture
async def user_console_db(tmp_path):
    db_path = tmp_path / "user_console.sqlite3"
    await Tortoise.init(
        db_url=f"sqlite://{db_path}",
        modules={
            "models": [
                "zhenxun.models.user_console",
                "zhenxun.models.user_gold_log",
            ]
        },
        timezone="Asia/Shanghai",
    )
    await Tortoise.generate_schemas()
    UserConsole._uid_counter = None
    UserConsole._uid_lock = asyncio.Lock()
    yield
    UserConsole._uid_counter = None
    UserConsole._uid_lock = asyncio.Lock()
    await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_existing_user_does_not_consume_uid(user_console_db):
    first, created = await UserConsole.get_or_create_user("first", "qq")
    assert created is True

    for _ in range(10):
        existing, created = await UserConsole.get_or_create_user("first", "qq")
        assert created is False
        assert existing.uid == first.uid

    second, created = await UserConsole.get_or_create_user("second", "qq")
    assert created is True
    assert second.uid == first.uid + 1


@pytest.mark.asyncio
async def test_concurrent_same_user_creates_one_record(user_console_db):
    results = await asyncio.gather(
        *(UserConsole.get_or_create_user("same", "qq") for _ in range(10))
    )

    assert sum(created for _, created in results) == 1
    assert len({user.uid for user, _ in results}) == 1
    assert await UserConsole.filter(user_id="same").count() == 1


@pytest.mark.asyncio
async def test_concurrent_users_receive_unique_contiguous_uids(user_console_db):
    users = await asyncio.gather(
        *(UserConsole.get_user(f"user_{index}", "qq") for index in range(10))
    )

    assert sorted(user.uid for user in users) == list(range(1, 11))
    assert await UserConsole.all().count() == 10


@pytest.mark.asyncio
async def test_uid_collision_refreshes_and_retries(monkeypatch, user_console_db):
    await UserConsole.create(user_id="existing", platform="qq", uid=1)
    allocated_uids = iter([1, 2])
    allocation_count = 0

    async def get_new_uid(cls) -> int:
        nonlocal allocation_count
        allocation_count += 1
        return next(allocated_uids)

    monkeypatch.setattr(UserConsole, "get_new_uid", classmethod(get_new_uid))

    user, created = await UserConsole.get_or_create_user("new", "qq")

    assert created is True
    assert user.uid == 2
    assert allocation_count == 2
