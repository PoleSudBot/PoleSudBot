from __future__ import annotations

from enum import StrEnum
from typing import Any

from aiocache import SimpleMemoryCache
from aiocache.base import BaseCache
from aiocache.serializers import JsonSerializer
from aiocache.backends.redis import RedisCache

from zhenxun.services.cache import cache_config
from zhenxun.services.log import logger

from .config import get_settings
from .constants import MODULE_NAME


class CacheMode(StrEnum):
    REDIS = "REDIS"
    MEMORY = "MEMORY"
    NONE = "NONE"


class MoeSekaiCache:
    _backend: BaseCache | None = None
    _mode: CacheMode | None = None

    @classmethod
    def _resolve_mode(cls) -> CacheMode:
        value = get_settings().cache_mode.upper().strip()
        try:
            return CacheMode(value)
        except ValueError:
            logger.warning(f"未知的 MoeSekai 缓存模式 {value}，将回退为 MEMORY", MODULE_NAME)
            return CacheMode.MEMORY

    @classmethod
    def _build_backend(cls) -> tuple[CacheMode, BaseCache | None]:
        mode = cls._resolve_mode()
        if mode == CacheMode.NONE:
            return mode, None
        if mode == CacheMode.REDIS:
            if cache_config.redis_host and cache_config.redis_port:
                return (
                    mode,
                    RedisCache(
                        endpoint=cache_config.redis_host,
                        port=cache_config.redis_port,
                        password=cache_config.redis_password,
                        serializer=JsonSerializer(),
                        namespace=MODULE_NAME,
                    ),
                )
            logger.warning("MoeSekai 缓存模式为 REDIS，但未发现 Redis 配置，回退为 MEMORY", MODULE_NAME)
        return (
            CacheMode.MEMORY,
            SimpleMemoryCache(serializer=JsonSerializer(), namespace=MODULE_NAME),
        )

    @classmethod
    def get_backend(cls) -> BaseCache | None:
        mode = cls._resolve_mode()
        if cls._backend is None or cls._mode != mode:
            cls._mode, cls._backend = cls._build_backend()
        return cls._backend

    @classmethod
    async def get(cls, key: str) -> Any | None:
        backend = cls.get_backend()
        if backend is None:
            return None
        return await backend.get(key)

    @classmethod
    async def set(cls, key: str, value: Any, ttl: int | None = None) -> None:
        backend = cls.get_backend()
        if backend is None:
            return
        await backend.set(key, value, ttl=ttl or get_settings().cache_ttl_seconds)

    @classmethod
    async def delete(cls, key: str) -> None:
        backend = cls.get_backend()
        if backend is None:
            return
        await backend.delete(key)
