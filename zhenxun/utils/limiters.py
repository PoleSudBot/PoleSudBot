import asyncio
from collections import defaultdict, deque
from pathlib import Path
import time
from typing import Any, ClassVar


class FreqLimiter:
    """
    命令冷却，检测用户是否处于冷却状态
    """

    def __init__(self, default_cd_seconds: int):
        self.next_time: dict[Any, float] = defaultdict(float)
        self.default_cd = default_cd_seconds

    def check(self, key: Any) -> bool:
        return time.time() >= self.next_time[key]

    def start_cd(self, key: Any, cd_time: int = 0):
        self.next_time[key] = time.time() + (
            cd_time if cd_time > 0 else self.default_cd
        )

    def left_time(self, key: Any) -> float:
        return max(0.0, self.next_time[key] - time.time())


class CountLimiter:
    """
    每日调用命令次数限制 (持久化版)

    - 当 CacheManager 可用时 (CACHE_MODE=REDIS 或 MEMORY)，数据通过 CacheRoot 存取
      → 配置 Redis 后自动持久化到 Redis，重启不丢失
    - 当 CacheManager 不可用时 (CACHE_MODE=NONE)，回退到本地 JSON 文件持久化
    - 始终维持内存字典作为高速缓存，确保检测性能
    - 使用类级别共享存储，避免 update_limits() 重建实例时丢失计数
    """

    # ── 类级别共享状态 ──
    _mem_store: ClassVar[dict[str, int]] = {}
    _store_date: ClassVar[str] = ""
    _save_path: ClassVar[Path | None] = None
    _initialized: ClassVar[bool] = False
    _last_file_save: ClassVar[float] = 0
    _FILE_SAVE_INTERVAL: float = 3.0  # JSON 文件写盘最短间隔（秒）
    _CACHE_TYPE: ClassVar[str] = "PLUGIN_COUNT_LIMIT"
    _cache_registered: ClassVar[bool] = False

    # ── 初始化与持久化 ──

    @classmethod
    def _ensure_init(cls):
        """首次使用时初始化：注册缓存类型、从持久层加载数据"""
        if cls._initialized:
            return
        from zhenxun.configs.path_config import DATA_PATH

        cls._save_path = DATA_PATH / "plugin_count_limits.json"
        # 注册到 CacheManager（如果可用）
        cls._try_register_cache()
        # 从 JSON 文件加载（作为降级数据源）
        cls._load_from_file()
        cls._initialized = True

    @classmethod
    def _try_register_cache(cls):
        """向 CacheRoot 注册缓存类型"""
        if cls._cache_registered:
            return
        try:
            from zhenxun.services.cache import CacheRoot

            CacheRoot.register(cls._CACHE_TYPE, expire=172800)  # 48h TTL
            cls._cache_registered = True
        except Exception:
            pass

    @classmethod
    def _cache_available(cls) -> bool:
        """检查 CacheManager 是否启用（REDIS 或 MEMORY 模式）"""
        try:
            from zhenxun.services.cache import CacheRoot

            return CacheRoot.enabled
        except Exception:
            return False

    @classmethod
    def _load_from_file(cls):
        """从 JSON 文件恢复计数（降级数据源）"""
        import json

        if cls._save_path and cls._save_path.exists():
            try:
                with open(cls._save_path, encoding="utf-8") as f:
                    data = json.load(f)
                cls._store_date = data.get("date", "")
                cls._mem_store = data.get("counts", {})
            except Exception:
                cls._mem_store = {}
                cls._store_date = ""

    @classmethod
    def _save_to_file(cls, force: bool = False):
        """将计数保存到 JSON 文件（带防抖）"""
        import json

        now = time.time()
        if not force and now - cls._last_file_save < cls._FILE_SAVE_INTERVAL:
            return
        cls._last_file_save = now
        if cls._save_path:
            try:
                cls._save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cls._save_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"date": cls._store_date, "counts": cls._mem_store},
                        f,
                        ensure_ascii=False,
                    )
            except Exception:
                pass

    @classmethod
    def _check_day(cls):
        """跨天自动清零"""
        import datetime

        today = datetime.datetime.now().strftime("%Y%m%d")
        if today != cls._store_date:
            cls._store_date = today
            cls._mem_store.clear()
            cls._save_to_file(force=True)

    # ── 实例方法 ──

    def __init__(
        self,
        max_num: int,
        module_name: str = "",
        group_max_count: dict[str, int] | None = None,
    ):
        self.max = max_num
        self.module_name = module_name
        self.group_max_count: dict[str, int] = group_max_count or {}
        self._ensure_init()

    def _make_key(self, key: Any) -> str:
        return f"{self.module_name}:{key}"

    def _resolve_max(self, group_id: str | None) -> int:
        """根据群号解析实际生效的 max_count

        优先级: group_max_count[群号] > max（全局默认）
        返回值 -1 表示不限制
        """
        if group_id and self.group_max_count:
            if group_id in self.group_max_count:
                return self.group_max_count[group_id]
        return self.max

    async def check(self, key: Any, group_id: str | None = None) -> bool:
        self._check_day()
        effective_max = self._resolve_max(group_id)
        if effective_max < 0:
            return True  # -1 = 不限制
        store_key = self._make_key(key)
        # 优先从 CacheManager 读取（Redis 持久化数据源）
        if self._cache_available():
            try:
                from zhenxun.services.cache import CacheRoot

                val = await CacheRoot.get(self._CACHE_TYPE, store_key, default=None)
                if val is not None:
                    self._mem_store[store_key] = int(val)
                    return int(val) < effective_max
            except Exception:
                pass
        # 回退到内存
        return self._mem_store.get(store_key, 0) < effective_max

    async def get_num(self, key: Any) -> int:
        self._check_day()
        store_key = self._make_key(key)
        if self._cache_available():
            try:
                from zhenxun.services.cache import CacheRoot

                val = await CacheRoot.get(self._CACHE_TYPE, store_key, default=None)
                if val is not None:
                    return int(val)
            except Exception:
                pass
        return self._mem_store.get(store_key, 0)

    async def increase(self, key: Any, num: int = 1):
        self._check_day()
        store_key = self._make_key(key)
        new_val = self._mem_store.get(store_key, 0) + num
        self._mem_store[store_key] = new_val
        # 写入 CacheManager（→ Redis）
        if self._cache_available():
            try:
                from zhenxun.services.cache import CacheRoot

                await CacheRoot.set(self._CACHE_TYPE, store_key, new_val, expire=172800)
            except Exception:
                pass
        # 同时写入 JSON 文件（降级保障）
        self._save_to_file()

    async def reset(self, key: Any):
        self._check_day()
        store_key = self._make_key(key)
        self._mem_store.pop(store_key, None)
        if self._cache_available():
            try:
                from zhenxun.services.cache import CacheRoot

                await CacheRoot.delete(self._CACHE_TYPE, store_key)
            except Exception:
                pass
        self._save_to_file()


class UserBlockLimiter:
    """
    检测用户是否正在调用命令 (简单阻塞锁)
    """

    def __init__(self):
        self.flag_data: dict[Any, bool] = defaultdict(bool)
        self.time: dict[Any, float] = defaultdict(float)

    def set_true(self, key: Any):
        self.time[key] = time.time()
        self.flag_data[key] = True

    def set_false(self, key: Any):
        self.flag_data[key] = False

    def check(self, key: Any) -> bool:
        if self.flag_data[key] and time.time() - self.time[key] > 30:
            self.set_false(key)
        return not self.flag_data[key]


class RateLimiter:
    """
    一个简单的基于时间窗口的速率限制器。
    """

    def __init__(self, max_calls: int, time_window: int):
        self.requests: dict[Any, deque[float]] = defaultdict(deque)
        self.max_calls = max_calls
        self.time_window = time_window

    def check(self, key: Any) -> bool:
        """检查是否超出速率限制。如果未超出，则记录本次调用。"""
        now = time.time()

        while self.requests[key] and self.requests[key][0] <= now - self.time_window:
            self.requests[key].popleft()

        if len(self.requests[key]) < self.max_calls:
            self.requests[key].append(now)
            return True
        return False

    def left_time(self, key: Any) -> float:
        """计算距离下次可调用还需等待的时间"""
        if self.requests[key]:
            return max(0.0, self.requests[key][0] + self.time_window - time.time())
        return 0.0


class ConcurrencyLimiter:
    """
    一个基于 asyncio.Semaphore 的并发限制器。
    """

    def __init__(self, max_concurrent: int):
        self._semaphores: dict[Any, asyncio.Semaphore] = {}
        self.max_concurrent = max_concurrent
        self._active_tasks: dict[Any, int] = defaultdict(int)

    def _get_semaphore(self, key: Any) -> asyncio.Semaphore:
        if key not in self._semaphores:
            self._semaphores[key] = asyncio.Semaphore(self.max_concurrent)
        return self._semaphores[key]

    async def acquire(self, key: Any):
        """获取一个信号量，如果达到并发上限则会阻塞等待。"""
        semaphore = self._get_semaphore(key)
        await semaphore.acquire()
        self._active_tasks[key] += 1

    def release(self, key: Any):
        """释放一个信号量。"""
        if key in self._semaphores:
            if self._active_tasks[key] > 0:
                self._semaphores[key].release()
                self._active_tasks[key] -= 1
            else:
                import logging

                logging.warning(f"尝试释放键 '{key}' 的信号量时，计数已经为零。")
