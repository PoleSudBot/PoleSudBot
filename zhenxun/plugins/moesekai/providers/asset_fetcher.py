from __future__ import annotations

from collections.abc import Sequence

import httpx


class AssetFetcher:
    async def _fetch_single_content(self, client: httpx.AsyncClient, url: str) -> bytes:
        response = await client.get(url)
        response.raise_for_status()
        return response.content

    async def fetch_content(
        self,
        url: str,
        *,
        timeout: float = 20,
    ) -> bytes:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            return await self._fetch_single_content(client, url)

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
