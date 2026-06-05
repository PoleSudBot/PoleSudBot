from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import json
from pathlib import Path
from typing import Any, TypeVar

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump_json

from .git import redact
from .models import JobKind, JobRecord

T = TypeVar("T")


class JobStore:
    """内存任务队列，同时持久化最近任务摘要。"""

    def __init__(self, file: Path | None = None):
        self.file = file or DATA_PATH / "update_manager" / "jobs.json"
        self.jobs: dict[str, JobRecord] = {}
        self._tasks: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        self._load()

    def get(self, job_id: str) -> JobRecord | None:
        """读取单个任务状态。"""
        return self.jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[JobRecord]:
        """按创建时间倒序返回最近任务。"""
        return sorted(
            self.jobs.values(),
            key=lambda job: job.created_at,
            reverse=True,
        )[:limit]

    def start(
        self,
        *,
        kind: JobKind,
        title: str,
        write: bool,
        coro_factory: Callable[[JobRecord], Awaitable[Any]],
    ) -> JobRecord:
        """创建后台任务；写任务通过锁串行化以保护工作树。"""
        job = JobRecord(kind=kind, title=title)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, write, coro_factory))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._persist()
        return job

    async def run_now(
        self,
        *,
        kind: JobKind,
        title: str,
        write: bool,
        coro_factory: Callable[[JobRecord], Awaitable[Any]],
    ) -> JobRecord:
        """同步执行并记录任务，供定时任务复用与手动任务相同的互斥边界。"""
        job = JobRecord(kind=kind, title=title)
        self.jobs[job.id] = job
        self._persist()
        await self._run(job, write, coro_factory)
        return job

    async def run_exclusive(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        """复用写任务锁执行会更新 Git refs 的即时操作。"""
        async with self._write_lock:
            return await coro_factory()

    async def _run(
        self,
        job: JobRecord,
        write: bool,
        coro_factory: Callable[[JobRecord], Awaitable[Any]],
    ) -> None:
        """执行任务并统一保存成功/失败状态。"""
        lock = self._write_lock if write else _NullAsyncLock()
        async with lock:
            job.state = "running"
            job.started_at = datetime.now()
            self._persist()
            try:
                job.result = await coro_factory(job)
                job.state = "success"
            except Exception as exc:
                job.state = "failed"
                job.error = redact(str(exc))
                job.logs.append(job.error)
            finally:
                job.finished_at = datetime.now()
                self._persist()

    def _load(self) -> None:
        """加载最近任务摘要；解析失败时丢弃旧摘要不影响启动。"""
        if not self.file.exists():
            return
        try:
            raw = self.file.read_text("utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            logger.warning(f"更新管理任务文件读取/解析失败: {self.file}")
            return
        try:
            for item in data[-50:]:
                job = JobRecord.model_validate(item)
                self.jobs[job.id] = job
        except Exception:
            logger.warning(f"更新管理任务数据校验失败，丢弃旧摘要: {self.file}")
            self.jobs = {}

    def _persist(self) -> None:
        """落盘最近任务，避免文件无限增长。"""
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            payload = [json.loads(model_dump_json(job)) for job in self.recent(50)]
            self.file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception:
            logger.error(f"更新管理任务持久化失败: {self.file}")


class _NullAsyncLock:
    """给只读任务提供与 asyncio.Lock 相同的上下文接口。"""

    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


job_store = JobStore()
