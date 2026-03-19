from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from packaging.version import InvalidVersion, Version

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .config import MasterSourceConfig, get_settings
from .constants import MASTER_DATA_DIR, MODULE_NAME, SERVER_SET, server_label


@dataclass
class SourceVersionInfo:
    source: MasterSourceConfig
    version: str | None = None
    success: bool = False
    error: str | None = None


@dataclass
class RegionUpdateResult:
    server: str
    selected_source_name: str | None = None
    previous_version: str | None = None
    current_version: str | None = None
    checked_at: str | None = None
    updated: bool = False
    download_success: bool = False
    error: str | None = None
    source_versions: list[SourceVersionInfo] = field(default_factory=list)

    @staticmethod
    def _display_source_name(source_name: str | None) -> str:
        if not source_name:
            return "未选中"
        name = source_name.removesuffix("-jp").removesuffix("-cn").removesuffix("-tw")
        if name == "sekai-viewer":
            return "sekai.best"
        return name

    def _source_versions_message(self) -> str:
        lines = [f"{server_label(self.server)}MasterData数据源"]
        for item in self.source_versions:
            version = item.version or item.error or "获取失败"
            lines.append(f"[{self._display_source_name(item.source.name)}] {version}")
        return "\n".join(lines)

    def to_message(self) -> str:
        if self.error:
            base = f"{self.server.upper()} 更新失败: {self.error}"
            if self.source_versions:
                return f"{base}\n\n{self._source_versions_message()}"
            return base
        changed = "已更新" if self.updated else "无变化"
        source_name = self._display_source_name(self.selected_source_name)
        base = (
            f"{self.server.upper()} {changed}\n"
            f"来源: {source_name}\n"
            f"版本: {self.previous_version or '-'} -> {self.current_version or '-'}"
        )
        if self.source_versions:
            return f"{base}\n\n{self._source_versions_message()}"
        return base


class MasterDataService:
    _lock = asyncio.Lock()
    _events_cache: dict[str, list[dict[str, Any]]] = {}
    _state_path = MASTER_DATA_DIR / "state.json"

    @classmethod
    def _server_dir(cls, server: str) -> Path:
        path = MASTER_DATA_DIR / server
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _events_path(cls, server: str) -> Path:
        return cls._server_dir(server) / "events.json"

    @classmethod
    def _load_state(cls) -> dict[str, dict[str, Any]]:
        if not cls._state_path.exists():
            return {}
        try:
            return json.loads(cls._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("MoeSekai 主数据状态文件损坏，将重新创建", MODULE_NAME)
            return {}

    @classmethod
    def _save_state(cls, state: dict[str, dict[str, Any]]) -> None:
        cls._state_path.parent.mkdir(parents=True, exist_ok=True)
        cls._state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _normalize_version(cls, version: str | None) -> tuple[Any, ...]:
        if not version:
            return tuple()
        try:
            return (Version(version),)
        except InvalidVersion:
            return tuple(
                int(part) if part.isdigit() else part
                for part in version.replace("-", ".").split(".")
            )

    @classmethod
    async def _fetch_source_version(
        cls, source: MasterSourceConfig
    ) -> SourceVersionInfo:
        try:
            payload = await AsyncHttpx.get_json(
                source.version_url,
                raise_on_failure=True,
            )
            if not isinstance(payload, dict):
                raise TypeError("版本接口返回的不是 JSON 对象")
            version = payload.get(source.version_field)
            if not version:
                raise ValueError(f"版本字段 {source.version_field} 不存在")
            return SourceVersionInfo(source=source, version=str(version), success=True)
        except Exception as exc:
            logger.warning(
                f"获取 MoeSekai 主数据源版本失败: {source.name}",
                MODULE_NAME,
                e=exc,
            )
            return SourceVersionInfo(
                source=source,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    @classmethod
    async def _select_source(
        cls, server: str
    ) -> tuple[MasterSourceConfig | None, list[SourceVersionInfo]]:
        sources = [item for item in get_settings().master_sources if item.region == server]
        if not sources:
            return None, []

        results = await asyncio.gather(
            *(cls._fetch_source_version(source) for source in sources)
        )
        available = [item for item in results if item.success and item.version]
        if not available:
            return None, results
        available.sort(
            key=lambda item: cls._normalize_version(item.version),
            reverse=True,
        )
        return available[0].source, results

    @classmethod
    async def update_region(
        cls,
        server: str,
        *,
        force: bool = False,
    ) -> RegionUpdateResult:
        server = server.lower()
        if server not in SERVER_SET:
            return RegionUpdateResult(server=server, error="不支持的区服")

        async with cls._lock:
            state = cls._load_state()
            region_state = state.get(server, {})
            result = RegionUpdateResult(
                server=server,
                previous_version=region_state.get("version"),
            )
            selected_source, source_versions = await cls._select_source(server)
            result.source_versions = source_versions
            result.checked_at = datetime.now().isoformat(timespec="seconds")
            if not selected_source:
                result.error = "没有可用的主数据源"
                return result

            selected_version = next(
                (
                    item.version
                    for item in source_versions
                    if item.source.name == selected_source.name and item.success
                ),
                None,
            )
            result.selected_source_name = selected_source.name
            result.current_version = selected_version

            events_path = cls._events_path(server)
            needs_update = force or not events_path.exists()
            if selected_version and result.previous_version != selected_version:
                needs_update = True

            if not needs_update:
                result.download_success = True
                return result

            try:
                events = await AsyncHttpx.get_json(
                    selected_source.events_url,
                    raise_on_failure=True,
                )
                if not isinstance(events, list):
                    raise TypeError("events.json 格式无效")
                events_path.write_text(
                    json.dumps(events, ensure_ascii=False),
                    encoding="utf-8",
                )
                cls._events_cache.pop(server, None)
                state[server] = {
                    "version": selected_version,
                    "source_name": selected_source.name,
                    "events_url": selected_source.events_url,
                    "version_url": selected_source.version_url,
                    "checked_at": result.checked_at,
                    "updated_at": result.checked_at,
                }
                cls._save_state(state)
                result.updated = result.previous_version != selected_version or force
                result.download_success = True
                return result
            except Exception as exc:
                logger.error(
                    f"MoeSekai 主数据下载失败: {server}",
                    MODULE_NAME,
                    e=exc,
                )
                result.error = f"{type(exc).__name__}: {exc}"
                return result

    @classmethod
    async def update_all(cls, *, force: bool = False) -> list[RegionUpdateResult]:
        results = []
        for server in ("jp", "cn", "tw"):
            results.append(await cls.update_region(server, force=force))
        return results

    @classmethod
    async def get_events(cls, server: str) -> list[dict[str, Any]]:
        server = server.lower()
        if server in cls._events_cache:
            return cls._events_cache[server]

        events_path = cls._events_path(server)
        if not events_path.exists():
            await cls.update_region(server)

        if not events_path.exists():
            return []

        try:
            events = json.loads(events_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("MoeSekai events.json 解析失败", MODULE_NAME, e=exc)
            return []

        cls._events_cache[server] = events
        return events

    @classmethod
    async def get_current_event(
        cls,
        server: str,
        fallback: Literal[None, "prev", "next", "prev_first", "next_first"] = None,
    ) -> dict[str, Any] | None:
        events = sorted(
            await cls.get_events(server),
            key=lambda item: item.get("aggregateAt", 0),
        )
        if not events:
            return None

        now = datetime.now()
        prev_event = None
        cur_event = None
        next_event = None
        for event in events:
            try:
                start_time = datetime.fromtimestamp(event["startAt"] / 1000)
                end_time = datetime.fromtimestamp(event["aggregateAt"] / 1000 + 1)
            except (KeyError, TypeError, ValueError):
                continue
            if start_time <= now <= end_time:
                cur_event = event
            if end_time < now:
                prev_event = event
            if next_event is None and start_time > now:
                next_event = event

        if cur_event or fallback is None:
            return cur_event
        if fallback == "prev":
            return prev_event
        if fallback == "next":
            return next_event
        if fallback == "prev_first":
            return prev_event or next_event
        if fallback == "next_first":
            return next_event or prev_event
        return None


master_data_service = MasterDataService()
