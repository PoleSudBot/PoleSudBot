from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from zhenxun.utils.exception import AllURIsFailedError

from .asset_cache import AssetCacheProvider, asset_cache_provider
from .asset_fetcher import asset_fetcher
from .config import get_settings
from .constants import LEGACY_PROFILE_STATIC_ASSET_DIR, MODULE_NAME
from .runtime import logger
from .storage import JsonStateStore, PathBinaryFileStore


class ProfileStaticAssetProvider:
    def __init__(
        self,
        cache_provider: AssetCacheProvider | None = None,
        *,
        legacy_store: PathBinaryFileStore | None = None,
        store: PathBinaryFileStore | None = None,
        manifest_store: JsonStateStore | None = None,
        miss_store: JsonStateStore | None = None,
    ) -> None:
        if cache_provider is not None:
            self._cache_provider = cache_provider
        elif store is not None or manifest_store is not None or miss_store is not None:
            self._cache_provider = AssetCacheProvider(
                store=store,
                manifest_store=manifest_store,
                miss_store=miss_store,
            )
        else:
            self._cache_provider = asset_cache_provider
        self._legacy_store = legacy_store or PathBinaryFileStore(
            LEGACY_PROFILE_STATIC_ASSET_DIR
        )
        self._inflight_lock = asyncio.Lock()
        self._inflight_tasks: dict[
            tuple[tuple[str, ...], float],
            asyncio.Task[Path | None],
        ] = {}

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        normalized = PurePosixPath(str(relative_path).strip("/"))
        if not normalized.parts or any(
            part in {"..", "."} for part in normalized.parts
        ):
            raise ValueError(f"relative_path 非法: {relative_path}")
        return normalized.as_posix()

    @classmethod
    def _cache_relative_path(cls, relative_path: str) -> str:
        return f"profile_static/{cls._normalize_relative_path(relative_path)}"

    @staticmethod
    def _build_url(base_url: str, relative_path: str) -> str:
        return f"{base_url.rstrip('/')}/{relative_path.lstrip('/')}"

    @staticmethod
    def _source_fetch_limit(url_count: int) -> int:
        settings = get_settings()
        if settings.asset_source_fetch_all:
            return max(1, url_count)
        return max(1, min(int(settings.asset_source_fetch_concurrency), url_count))

    @staticmethod
    def _unwrap_all_uris_error(exc: AllURIsFailedError) -> Exception:
        if exc.exceptions:
            last_error = exc.exceptions[-1]
            if isinstance(last_error, Exception):
                return last_error
        return exc

    def get_local_path(self, relative_path: str) -> Path | None:
        normalized = self._normalize_relative_path(relative_path)
        cache_relative_path = self._cache_relative_path(normalized)
        if path := self._cache_provider.get_local_path_by_relative_path(
            cache_relative_path
        ):
            return path

        # 旧 MoeSekai 私有缓存不删除；首次命中时复制进 sekai_resource 镜像缓存。
        if payload := self._legacy_store.load(normalized):
            self._cache_provider.set_by_relative_path(
                cache_relative_path,
                payload,
                server="global",
                kind="profile_static",
                assetbundle=normalized,
                source_url="legacy:moesekai/profile_static",
            )
            return self._cache_provider.get_local_path_by_relative_path(
                cache_relative_path
            )
        return None

    def _record_missing(self, url: str) -> None:
        self._cache_provider.record_missing(url)

    def _clear_missing(self, url: str) -> None:
        self._cache_provider.clear_missing(url)

    async def _fetch_candidate(
        self,
        candidate: str,
        *,
        timeout: float,
    ) -> tuple[str, bytes | None, Exception | None]:
        try:
            content = await asset_fetcher.fetch_content(candidate, timeout=timeout)
        except AllURIsFailedError as exc:
            error = self._unwrap_all_uris_error(exc)
            if (
                isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code == 404
            ):
                self._record_missing(candidate)
            return candidate, None, error
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self._record_missing(candidate)
            return candidate, None, exc
        except httpx.RequestError as exc:
            return candidate, None, exc
        self._clear_missing(candidate)
        return candidate, content, None

    async def _fetch_first_from_candidates(
        self,
        urls: list[str],
        *,
        timeout: float,
    ) -> tuple[str | None, bytes | None, Exception | None]:
        last_error: Exception | None = None
        remaining = iter(urls)
        pending: set[asyncio.Task[tuple[str, bytes | None, Exception | None]]] = set()

        def start_next() -> None:
            try:
                candidate = next(remaining)
            except StopIteration:
                return
            pending.add(
                asyncio.create_task(self._fetch_candidate(candidate, timeout=timeout))
            )

        # 单个素材可能有多个 profile 静态源；有限竞速避免一个慢源拖住整张档案图。
        for _ in range(self._source_fetch_limit(len(urls))):
            start_next()

        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                try:
                    candidate, content, error = task.result()
                except Exception:
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise
                if content:
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return candidate, content, None
                if error is not None:
                    last_error = error
                start_next()

        return None, None, last_error

    async def _ensure_one_local_path_uncached(
        self,
        relative_path: str,
        *,
        timeout: float,
    ) -> Path | None:
        normalized = self._normalize_relative_path(relative_path)
        if local_path := self.get_local_path(normalized):
            return local_path

        urls = [
            self._build_url(base_url, normalized)
            for base_url in get_settings().profile_static_asset_bases
        ]
        urls = [url for url in urls if not self._cache_provider.is_known_missing(url)]
        if not urls:
            return None

        candidate, content, last_error = await self._fetch_first_from_candidates(
            urls,
            timeout=timeout,
        )
        if content is None or candidate is None:
            if last_error:
                logger.warning(
                    "SekaiResource profile 静态资源抓取失败",
                    MODULE_NAME,
                    e=last_error,
                )
            return None

        self._cache_provider.set_by_relative_path(
            self._cache_relative_path(normalized),
            content,
            server="global",
            kind="profile_static",
            assetbundle=normalized,
            source_url=candidate,
        )
        return self.get_local_path(normalized)

    async def _ensure_one_local_path(
        self,
        relative_path: str,
        *,
        timeout: float,
    ) -> Path | None:
        normalized = self._normalize_relative_path(relative_path)
        if local_path := self.get_local_path(normalized):
            return local_path
        key = ((normalized,), float(timeout))
        async with self._inflight_lock:
            task = self._inflight_tasks.get(key)
            if task is None or task.done():
                # 同一进程内相同素材共用下载任务，避免预取和实际渲染重复打源站。
                task = asyncio.create_task(
                    self._ensure_one_local_path_uncached(
                        normalized,
                        timeout=timeout,
                    )
                )
                self._inflight_tasks[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._inflight_lock:
                    if self._inflight_tasks.get(key) is task:
                        self._inflight_tasks.pop(key, None)

    async def ensure_local_path(
        self,
        relative_candidates: Sequence[str],
        *,
        timeout: float = 20,
    ) -> Path | None:
        for relative_path in relative_candidates:
            if local_path := await self._ensure_one_local_path(
                relative_path,
                timeout=timeout,
            ):
                return local_path
        return None

    async def ensure_many_local_paths(
        self,
        candidate_groups: Sequence[Sequence[str]],
        *,
        timeout: float = 20,
        concurrency: int | None = None,
    ) -> list[Path | None]:
        if not candidate_groups:
            return []
        limit = max(1, int(concurrency or get_settings().asset_batch_fetch_concurrency))
        semaphore = asyncio.Semaphore(limit)

        async def fetch_one(candidates: Sequence[str]) -> Path | None:
            async with semaphore:
                return await self.ensure_local_path(candidates, timeout=timeout)

        return list(
            await asyncio.gather(*(fetch_one(group) for group in candidate_groups))
        )

    async def get_json(self, relative_path: str) -> Any:
        path = await self.ensure_local_path([relative_path])
        if path is None:
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                f"SekaiResource profile 静态 JSON 解析失败: {relative_path}",
                MODULE_NAME,
                e=exc,
            )
            return {}

    async def get_text(self, relative_path: str) -> str | None:
        path = await self.ensure_local_path([relative_path])
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                f"SekaiResource profile 静态文本读取失败: {relative_path}",
                MODULE_NAME,
                e=exc,
            )
            return None

    async def get_costume_icon(self, card_id: int) -> Path | None:
        return await self.ensure_local_path([f"costume_icons/{card_id}.png"])

    async def get_base_chibi(self, base_name: str) -> Path | None:
        return await self.ensure_local_path(
            [
                f"base_chibis/{base_name}.png",
                f"base_chibis/{base_name}.webp",
            ]
        )

    async def get_honor_asset_svg(self, filename: str) -> str | None:
        return await self.get_text(f"honor_assets/{filename}")

    async def get_credits(self) -> dict[str, Any]:
        payload = await self.get_json("credits.json")
        return payload if isinstance(payload, dict) else {}


profile_static_provider = ProfileStaticAssetProvider()


__all__ = ["ProfileStaticAssetProvider", "profile_static_provider"]
