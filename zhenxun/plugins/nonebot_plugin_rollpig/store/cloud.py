from __future__ import annotations

import datetime
from typing import Optional

import httpx
from nonebot import get_plugin_config
from nonebot.log import logger

from ..config import Config
from .base import RollpigStore
from .models import CooldownConsumeResult, RoastEvent


class CloudStoreError(RuntimeError):
    pass


class CloudStore(RollpigStore):
    def __init__(self):
        config = get_plugin_config(Config)
        if not config.rollpig_cloud_api_url:
            raise ValueError("启用 cloud 存储时必须配置 rollpig_cloud_api_url")
        if not config.rollpig_cloud_token:
            raise ValueError("启用 cloud 存储时必须配置 rollpig_cloud_token")

        self.base_url = config.rollpig_cloud_api_url.rstrip("/")
        self.timeout = max(0.5, float(config.rollpig_cloud_timeout or 3.0))
        self.strict_mode = bool(config.rollpig_cloud_strict_mode)
        self.headers = {
            "Authorization": f"Bearer {config.rollpig_cloud_token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        fallback=None,
    ):
        url = f"{self.base_url}{path}"
        normalized_params = {key: value for key, value in (params or {}).items() if value is not None}
        normalized_json = {key: value for key, value in (json_body or {}).items() if value is not None}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self.headers,
                    params=normalized_params,
                    json=normalized_json,
                )
            response.raise_for_status()
            if response.content:
                return response.json()
            return None
        except Exception as error:
            logger.error(f"rollpig cloud 请求失败: {method} {url} error={error}")
            if not self.strict_mode and fallback is not None:
                return fallback
            raise CloudStoreError(str(error)) from error

    async def get_daily_roll(self, user_id: str, date_str: Optional[str] = None) -> Optional[str]:
        payload = await self._request(
            "GET",
            "/v1/daily-rolls/by-date",
            params={"user_id": user_id, "date_str": date_str or datetime.date.today().isoformat()},
            fallback={"pig_id": None},
        )
        return payload.get("pig_id") if payload else None

    async def get_daily_rolls(self, date_str: Optional[str] = None) -> dict[str, str]:
        payload = await self._request(
            "GET",
            "/v1/daily-rolls/all",
            params={"date_str": date_str or datetime.date.today().isoformat()},
            fallback={"items": []},
        )
        items = payload.get("items", []) if payload else []
        return {
            str(item.get("user_id")): str(item.get("pig_id"))
            for item in items
            if item.get("user_id") and item.get("pig_id")
        }

    async def get_or_create_daily_roll(
        self,
        user_id: str,
        proposed_pig_id: str,
        date_str: Optional[str] = None,
        group_id: str = "",
    ) -> tuple[str, bool]:
        payload = await self._request(
            "POST",
            "/v1/daily-rolls/get-or-create",
            json_body={
                "user_id": user_id,
                "proposed_pig_id": proposed_pig_id,
                "date_str": date_str or datetime.date.today().isoformat(),
                "group_id": group_id,
            },
        )
        return str(payload["pig_id"]), bool(payload.get("created"))

    async def mark_group_roll_seen(
        self,
        user_id: str,
        pig_id: str,
        group_id: str,
        date_str: Optional[str] = None,
    ) -> None:
        await self._request(
            "POST",
            "/v1/group-rolls/mark-seen",
            json_body={
                "group_id": group_id,
                "user_id": user_id,
                "pig_id": pig_id,
                "date_str": date_str or datetime.date.today().isoformat(),
            },
        )

    async def get_group_rolls(self, group_id: str, date_str: Optional[str] = None) -> dict[str, str]:
        payload = await self._request(
            "GET",
            "/v1/group-rolls",
            params={"group_id": group_id, "date_str": date_str or datetime.date.today().isoformat()},
            fallback={"items": []},
        )
        items = payload.get("items", []) if payload else []
        return {
            str(item.get("user_id")): str(item.get("pig_id"))
            for item in items
            if item.get("user_id") and item.get("pig_id")
        }

    async def get_user_collection(self, user_id: str) -> list[str]:
        payload = await self._request(
            "GET",
            "/v1/collections",
            params={"user_id": user_id},
            fallback={"pig_ids": []},
        )
        return [str(item) for item in payload.get("pig_ids", [])] if payload else []

    async def get_pig_by_date(self, user_id: str, date_str: str) -> Optional[str]:
        payload = await self._request(
            "GET",
            "/v1/daily-rolls/by-date",
            params={"user_id": user_id, "date_str": date_str},
            fallback={"pig_id": None},
        )
        return payload.get("pig_id") if payload else None

    async def consume_roast_cooldown(
        self,
        user_id: str,
        now_ts: Optional[float] = None,
        cooldown_seconds: Optional[int] = None,
    ) -> CooldownConsumeResult:
        payload = await self._request(
            "POST",
            "/v1/cooldowns/consume-roast",
            json_body={
                "user_id": user_id,
                "now_ts": now_ts,
                "cooldown_seconds": cooldown_seconds,
            },
        )
        return CooldownConsumeResult(
            allowed=bool(payload.get("allowed")),
            remaining_seconds=int(payload.get("remaining_seconds", 0)),
        )

    async def consume_force_usage(self, user_id: str, date_str: Optional[str] = None) -> bool:
        payload = await self._request(
            "POST",
            "/v1/cooldowns/consume-force",
            json_body={"user_id": user_id, "date_str": date_str or datetime.date.today().isoformat()},
        )
        return bool(payload.get("allowed"))

    async def append_roast_event(self, event: RoastEvent) -> None:
        await self._request(
            "POST",
            "/v1/events",
            json_body={
                "event_type": event.event_type,
                "attacker_id": event.attacker_id,
                "target_id": event.target_id,
                "attacker_name": event.attacker_name,
                "target_name": event.target_name,
                "food": event.food,
                "group_id": event.group_id,
                "date_str": datetime.date.today().isoformat(),
            },
        )

    async def list_daily_events(self, date_str: Optional[str] = None, group_id: Optional[str] = None) -> list[dict]:
        payload = await self._request(
            "GET",
            "/v1/events",
            params={"date_str": date_str or datetime.date.today().isoformat(), "group_id": group_id},
            fallback={"items": []},
        )
        return payload.get("items", []) if payload else []

    async def get_active_group_ids(self, date_str: Optional[str] = None) -> set[str]:
        payload = await self._request(
            "GET",
            "/v1/groups/active",
            params={"date_str": date_str or datetime.date.today().isoformat()},
            fallback={"group_ids": []},
        )
        return {str(group_id) for group_id in payload.get("group_ids", [])} if payload else set()

    async def replace_group_protections(
        self,
        group_id: str,
        user_ids: list[str],
        protect_date: Optional[str] = None,
    ) -> None:
        await self._request(
            "POST",
            "/v1/protections/replace-group",
            json_body={
                "group_id": group_id,
                "user_ids": user_ids,
                "protect_date": protect_date or datetime.date.today().isoformat(),
            },
        )

    async def is_protected(self, group_id: str, user_id: str, date_str: Optional[str] = None) -> bool:
        payload = await self._request(
            "GET",
            "/v1/protections/check",
            params={
                "group_id": group_id,
                "user_id": user_id,
                "protect_date": date_str or datetime.date.today().isoformat(),
            },
            fallback={"protected": False},
        )
        return bool(payload.get("protected")) if payload else False

    async def prune_history(self, days_to_keep: int = 14) -> None:
        return None

    async def prune_events(self, days_to_keep: int = 7) -> None:
        return None
