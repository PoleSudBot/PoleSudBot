from __future__ import annotations

from collections.abc import Sequence

import httpx

from zhenxun.utils.exception import AllURIsFailedError

from .runtime import AsyncHttpx


class AssetFetcher:
    @staticmethod
    def _unwrap_http_error(exc: AllURIsFailedError) -> Exception:
        if exc.exceptions:
            last_error = exc.exceptions[-1]
            if isinstance(last_error, Exception):
                return last_error
        return exc

    async def fetch_content(
        self,
        url: str,
        *,
        timeout: float = 20,
    ) -> bytes:
        try:
            return await AsyncHttpx.get_content(
                url,
                timeout=timeout,
            )
        except AllURIsFailedError as exc:
            raise self._unwrap_http_error(exc) from exc

    async def fetch_first_content(
        self,
        urls: Sequence[str],
        *,
        timeout: float = 20,
    ) -> bytes | None:
        candidates = [url for url in urls if url]
        if not candidates:
            return None
        for candidate in candidates:
            try:
                return await self.fetch_content(candidate, timeout=timeout)
            except httpx.HTTPError:
                continue
        return None


asset_fetcher = AssetFetcher()
