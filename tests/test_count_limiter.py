import sys
import types

import pytest

from zhenxun.utils.limiters import CountLimiter


class FakeCacheRoot:
    def __init__(self, get_values: dict[str, int] | None = None):
        self.enabled = True
        self.get_values = dict(get_values or {})
        self.set_results: list[bool] = []
        self.delete_results: list[bool] = []
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, int, int | None]] = []
        self.delete_calls: list[str] = []

    def register(self, _name: str, expire: int = 0):
        return None

    async def get(self, _cache_type: str, key: str, default=None):
        self.get_calls.append(key)
        return self.get_values.get(key, default)

    async def set(
        self, _cache_type: str, key: str, value: int, expire: int | None = None
    ):
        self.set_calls.append((key, value, expire))
        if self.set_results:
            return self.set_results.pop(0)
        self.get_values[key] = value
        return True

    async def delete(self, _cache_type: str, key: str):
        self.delete_calls.append(key)
        if self.delete_results:
            return self.delete_results.pop(0)
        self.get_values.pop(key, None)
        return True


@pytest.fixture
def limiter_env(monkeypatch, tmp_path):
    fake_cache = FakeCacheRoot()
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.services.cache",
        types.SimpleNamespace(CacheRoot=fake_cache),
    )
    monkeypatch.setattr(CountLimiter, "_ensure_init", classmethod(lambda cls: None))

    def reset_state(today: str = "20260317"):
        CountLimiter._mem_store = {}
        CountLimiter._store_date = today
        CountLimiter._save_path = tmp_path / "plugin_count_limits.json"
        CountLimiter._initialized = True
        CountLimiter._cache_registered = True
        CountLimiter._last_file_save = 0

    reset_state()
    monkeypatch.setattr(
        CountLimiter, "_save_to_file", classmethod(lambda cls, force=False: None)
    )
    monkeypatch.setattr(CountLimiter, "_today_str", classmethod(lambda cls: "20260317"))
    return fake_cache, reset_state


@pytest.mark.asyncio
async def test_redis_old_value_does_not_override_mem_after_failed_set(limiter_env):
    fake_cache, _reset_state = limiter_env
    store_key = "memes:20260317:group_42_user_24"
    fake_cache.get_values[store_key] = 2
    fake_cache.set_results = [False, False]

    limiter = CountLimiter(4, "memes")

    assert await limiter.check("group_42_user_24") is True
    await limiter.increase("group_42_user_24")
    assert CountLimiter._mem_store[store_key] == 3

    assert await limiter.check("group_42_user_24") is True
    await limiter.increase("group_42_user_24")
    assert CountLimiter._mem_store[store_key] == 4

    assert await limiter.check("group_42_user_24") is False
    assert fake_cache.get_calls == [store_key]


@pytest.mark.asyncio
async def test_mem_store_keeps_counting_when_cache_has_no_value_and_set_fails(
    limiter_env,
):
    fake_cache, _reset_state = limiter_env
    fake_cache.set_results = [False, False, False]
    limiter = CountLimiter(3, "tarot")

    assert await limiter.check("group_1_user_1") is True
    await limiter.increase("group_1_user_1")
    assert await limiter.check("group_1_user_1") is True
    await limiter.increase("group_1_user_1")
    assert await limiter.check("group_1_user_1") is True
    await limiter.increase("group_1_user_1")

    assert await limiter.check("group_1_user_1") is False
    assert await limiter.get_num("group_1_user_1") == 3


@pytest.mark.asyncio
async def test_cold_restart_recovers_count_from_cache(limiter_env, monkeypatch):
    fake_cache, reset_state = limiter_env
    limiter = CountLimiter(5, "memes")

    await limiter.increase("group_7_user_9", num=2)
    assert fake_cache.set_calls[-1][1] == 2

    reset_state()
    monkeypatch.setattr(CountLimiter, "_today_str", classmethod(lambda cls: "20260317"))
    recovered = CountLimiter(5, "memes")

    assert await recovered.get_num("group_7_user_9") == 2
    await recovered.increase("group_7_user_9")
    assert await recovered.get_num("group_7_user_9") == 3


@pytest.mark.asyncio
async def test_cross_day_uses_new_date_key_and_ignores_old_value(
    limiter_env, monkeypatch
):
    fake_cache, _reset_state = limiter_env
    limiter = CountLimiter(5, "memes")

    await limiter.increase("group_8_user_3", num=2)
    old_key = "memes:20260317:group_8_user_3"
    new_key = "memes:20260318:group_8_user_3"
    fake_cache.get_values[old_key] = 2

    monkeypatch.setattr(CountLimiter, "_today_str", classmethod(lambda cls: "20260318"))

    assert await limiter.check("group_8_user_3") is True
    assert await limiter.get_num("group_8_user_3") == 0
    assert new_key not in CountLimiter._mem_store
