from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..adapters.runtime import AsyncHttpx
from ..cache import MoeSekaiCache
from ..config import get_settings
from ..constants import PREDICTION_SUPPORTED_SERVERS, RANK_SOURCE_NAME

from .masterdata import master_data_provider


@dataclass
class RankingItem:
    rank: int
    score: int | None = None
    prediction: int | None = None
    collect_time: str | None = None
    is_final: bool = False


@dataclass
class RankingSnapshot:
    server: str
    event_id: int
    status: str
    updated_at: str | None
    items: list[RankingItem]
    source_name: str = RANK_SOURCE_NAME

    @property
    def has_prediction(self) -> bool:
        return any(item.prediction is not None for item in self.items)

    @property
    def has_score(self) -> bool:
        return any(item.score is not None for item in self.items)


class RankingProvider:
    async def list_events(self, server: str) -> list[dict[str, Any]]:
        if server not in PREDICTION_SUPPORTED_SERVERS:
            return []
        cache_key = f"rk:events:{server}"
        cached = await MoeSekaiCache.get(cache_key)
        if cached is not None:
            return cached
        base = get_settings().ranking_api_base.rstrip("/")
        payload = await AsyncHttpx.get_json(
            f"{base}/public/events?region={server}",
            raise_on_failure=True,
        )
        result = payload if isinstance(payload, list) else []
        await MoeSekaiCache.set(cache_key, result, ttl=180)
        return result

    async def get_event_meta(self, server: str, event_id: int) -> dict[str, Any] | None:
        events = await self.list_events(server)
        for event in events:
            try:
                if int(event.get("event_id")) == event_id:
                    return event
            except (TypeError, ValueError):
                continue
        return None

    async def get_latest_snapshot(
        self,
        server: str,
        event_id: int,
    ) -> RankingSnapshot | None:
        if server not in PREDICTION_SUPPORTED_SERVERS:
            return None
        cache_key = f"rk:latest:{server}:{event_id}"
        cached = await MoeSekaiCache.get(cache_key)
        if cached is not None:
            return self._build_snapshot(server, cached)
        base = get_settings().ranking_api_base.rstrip("/")
        payload = await AsyncHttpx.get_json(
            f"{base}/public/event/{event_id}/latest?region={server}",
            raise_on_failure=False,
            default=None,
        )
        if not isinstance(payload, dict) or payload.get("error"):
            return None
        await MoeSekaiCache.set(cache_key, payload, ttl=180)
        return self._build_snapshot(server, payload)

    def _build_snapshot(self, server: str, payload: dict[str, Any]) -> RankingSnapshot | None:
        items: list[RankingItem] = []
        for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
            try:
                items.append(
                    RankingItem(
                        rank=int(item["rank"]),
                        score=int(item["score"]) if item.get("score") is not None else None,
                        prediction=(
                            int(item["prediction"])
                            if item.get("prediction") is not None
                            else None
                        ),
                        collect_time=item.get("collect_time"),
                        is_final=bool(item.get("is_final")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if not items:
            return None
        return RankingSnapshot(
            server=server,
            event_id=int(payload["event_id"]),
            status=str(payload.get("status", "unknown")),
            updated_at=payload.get("updated_at"),
            items=items,
        )

    async def resolve_default_event_id(
        self, server: str, *, fallback: str = "prev"
    ) -> int | None:
        event = await master_data_provider.get_current_event(server, fallback=fallback)
        if not event:
            return None
        try:
            return int(event["id"])
        except (KeyError, TypeError, ValueError):
            return None

    async def get_snapshot(
        self,
        server: str,
        *,
        event_id: int | None = None,
        fallback: str = "prev",
    ) -> tuple[RankingSnapshot | None, dict[str, Any] | None, bool]:
        used_previous_event = False
        resolved_event_id = event_id
        event_meta = None
        if resolved_event_id is None:
            current_event = await master_data_provider.get_current_event(server, fallback=fallback)
            if not current_event:
                return None, None, False
            resolved_event_id = int(current_event["id"])
            try:
                start = datetime.fromtimestamp(current_event["startAt"] / 1000)
                end = datetime.fromtimestamp(current_event["aggregateAt"] / 1000 + 1)
                now = datetime.now()
                used_previous_event = not (start <= now <= end)
            except (KeyError, TypeError, ValueError):
                used_previous_event = False
        event_meta = await self.get_event_meta(server, resolved_event_id)
        snapshot = await self.get_latest_snapshot(server, resolved_event_id)
        if snapshot:
            return snapshot, event_meta, used_previous_event
        return None, event_meta, used_previous_event


ranking_provider = RankingProvider()
