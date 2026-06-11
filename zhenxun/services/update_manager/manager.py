from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path

from .config import load_settings
from .git import AsyncCommandRunner, CommandError
from .models import (
    AddPluginRequest,
    AddPluginResult,
    CommitSummary,
    ManagedRepo,
    ManagerSettings,
    RepoCommitList,
    RepoKind,
    RepoStatus,
    RepoUpdateResult,
    UpstreamStatus,
)
from .plugin_ops import add_plugin
from .repository import (
    build_managed_repos,
    detect_upstream_status,
    file_sha256,
    get_repo_status,
    is_ancestor,
    list_repo_commits,
)


class UpdateManagerService:
    """更新管理核心服务，WebUI 和自动任务只调用这一层。"""

    def __init__(
        self,
        settings: ManagerSettings | None = None,
        runner: AsyncCommandRunner | None = None,
    ):
        self.settings = settings or load_settings()
        self.runner = runner or AsyncCommandRunner(
            git_timeout=self.settings.git_timeout,
            clone_timeout=self.settings.clone_timeout,
        )
        self._running_heads: dict[str, str | None] = {}

    def refresh_settings(self) -> ManagerSettings:
        """重新读取配置，供 WebUI 修改开关后立即生效。"""
        self.settings = load_settings(self.settings.project_root)
        self.runner.git_timeout = self.settings.git_timeout
        self.runner.clone_timeout = self.settings.clone_timeout
        return self.settings

    def list_managed_repos(self) -> list[ManagedRepo]:
        """列出当前配置纳入管理的仓库。"""
        return build_managed_repos(self.settings)

    async def scan_repositories(self, *, fetch: bool = False) -> list[RepoStatus]:
        """并发采集仓库状态；fetch 参数用于手动刷新远程信息。"""
        repos = self.list_managed_repos()
        semaphore = asyncio.Semaphore(self.settings.max_parallel)

        async def scan(repo: ManagedRepo) -> RepoStatus:
            async with semaphore:
                return await get_repo_status(self.runner, repo, fetch=fetch)

        return await asyncio.gather(*(scan(repo) for repo in repos))

    async def scan_repository(self, repo_id: str, *, fetch: bool = False) -> RepoStatus:
        """采集单个仓库状态，供 WebUI 按行独立刷新。"""
        repo = self._repo_by_id(repo_id)
        return await get_repo_status(self.runner, repo, fetch=fetch)

    async def update_repositories(
        self,
        *,
        repo_ids: Iterable[str] | None = None,
        kinds: Iterable[RepoKind] | None = None,
        force: bool = False,
    ) -> list[RepoUpdateResult]:
        """按 id 或类型批量执行 origin ff-only 更新。"""
        repos = self._filter_repos(repo_ids=repo_ids, kinds=kinds)
        semaphore = asyncio.Semaphore(self.settings.max_parallel)

        async def update(repo: ManagedRepo) -> RepoUpdateResult:
            async with semaphore:
                return await self.update_one(repo, force=force)

        return await asyncio.gather(*(update(repo) for repo in repos))

    async def update_one(
        self,
        repo: ManagedRepo,
        *,
        force: bool = False,
    ) -> RepoUpdateResult:
        """更新单仓库，任何阻断都转换为结构化结果。"""
        before_files = self._dependency_fingerprints(repo)
        status = await get_repo_status(self.runner, repo, fetch=True)
        if not status.exists or not status.is_git_repo:
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message=status.update_block_reason or "仓库不可用",
            )
        structural_reason = self._structural_block_reason(status)
        if structural_reason:
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message=structural_reason,
                old_head=status.head,
                new_head=status.head,
            )
        if status.update_block_reason and not force:
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message=status.update_block_reason,
                old_head=status.head,
                new_head=status.head,
            )
        if status.behind <= 0:
            if not force or (not status.tracked_dirty and status.ahead <= 0):
                return RepoUpdateResult(
                    repo=repo,
                    status="unchanged",
                    message="已是 origin 目标分支最新版本",
                    old_head=status.head,
                    new_head=status.head,
                )
        try:
            if force:
                # 强制更新只重置已跟踪文件，不执行 git clean，避免误删缓存和本地生成物。
                await self.runner.git(
                    repo.path,
                    "reset",
                    "--hard",
                    f"origin/{repo.target_branch}",
                    check=True,
                )
                new_status = await get_repo_status(self.runner, repo, fetch=False)
                needs_uv_sync = before_files != self._dependency_fingerprints(repo)
                commits, omitted = await self._updated_commit_summaries(
                    repo,
                    status.head,
                    new_status.head,
                )
                return RepoUpdateResult(
                    repo=repo,
                    status="updated",
                    message="强制更新完成",
                    old_head=status.head,
                    new_head=new_status.head,
                    needs_restart=True,
                    needs_uv_sync=needs_uv_sync,
                    commits=commits,
                    omitted_commit_count=omitted,
                )
            await self.runner.git(
                repo.path, "pull", "--ff-only", "origin", repo.target_branch, check=True
            )
            new_status = await get_repo_status(self.runner, repo, fetch=False)
            needs_uv_sync = before_files != self._dependency_fingerprints(repo)
            commits, omitted = await self._updated_commit_summaries(
                repo,
                status.head,
                new_status.head,
            )
            return RepoUpdateResult(
                repo=repo,
                status="updated",
                message="快进更新完成",
                old_head=status.head,
                new_head=new_status.head,
                needs_restart=True,
                needs_uv_sync=needs_uv_sync,
                commits=commits,
                omitted_commit_count=omitted,
            )
        except CommandError as exc:
            return RepoUpdateResult(
                repo=repo,
                status="failed",
                message=str(exc),
                old_head=status.head,
                new_head=status.head,
            )

    async def list_commits(
        self,
        repo_id: str,
        *,
        fetch: bool = False,
        limit: int = 30,
    ) -> RepoCommitList:
        """按需读取单仓库 commit 历史。"""
        repo = self._repo_by_id(repo_id)
        await self.capture_running_heads()
        commits = await list_repo_commits(self.runner, repo, fetch=fetch, limit=limit)
        commits.running_hash = self._running_heads.get(repo.id)
        return commits

    async def capture_running_heads(self, *, overwrite: bool = False) -> None:
        """记录 Bot 进程正在运行时看到的仓库 HEAD，用于区分更新后未重启状态。"""
        repos = self.list_managed_repos()
        semaphore = asyncio.Semaphore(self.settings.max_parallel)

        async def capture(repo: ManagedRepo) -> None:
            async with semaphore:
                if not overwrite and repo.id in self._running_heads:
                    return
                self._running_heads[repo.id] = await self._read_repo_head(repo)

        await asyncio.gather(*(capture(repo) for repo in repos))

    async def checkout_commit(
        self,
        *,
        repo_id: str,
        commit_hash: str,
        force: bool = False,
    ) -> RepoUpdateResult:
        """将仓库工作树切到目标分支历史内的指定 commit。"""
        repo = self._repo_by_id(repo_id)
        before_files = self._dependency_fingerprints(repo)
        status = await get_repo_status(self.runner, repo, fetch=True)
        if not status.exists or not status.is_git_repo:
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message=status.update_block_reason or "仓库不可用",
            )
        structural_reason = self._structural_block_reason(status)
        if structural_reason:
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message=structural_reason,
                old_head=status.head,
                new_head=status.head,
            )
        if status.tracked_dirty and not force:
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message="存在已跟踪文件的未提交修改",
                old_head=status.head,
                new_head=status.head,
            )
        if status.head == commit_hash:
            return RepoUpdateResult(
                repo=repo,
                status="unchanged",
                message="已在目标版本",
                old_head=status.head,
                new_head=status.head,
            )
        if not await is_ancestor(
            self.runner,
            repo.path,
            commit_hash,
            f"origin/{repo.target_branch}",
        ):
            return RepoUpdateResult(
                repo=repo,
                status="skipped",
                message="目标 commit 不在 origin 目标分支历史内",
                old_head=status.head,
                new_head=status.head,
            )

        try:
            target_is_future = await is_ancestor(
                self.runner,
                repo.path,
                status.head or "HEAD",
                commit_hash,
            )
            if not force and target_is_future:
                await self.runner.git(repo.path, "merge", "--ff-only", commit_hash)
                message = "更新至指定版本完成"
            else:
                # 回退或强制更新都需要重置已跟踪文件；不清理未跟踪文件以保留缓存。
                await self.runner.git(repo.path, "reset", "--hard", commit_hash)
                message = (
                    "强制更新至指定版本完成"
                    if force and target_is_future
                    else "回退至指定版本完成"
                )
            new_status = await get_repo_status(self.runner, repo, fetch=False)
            needs_uv_sync = before_files != self._dependency_fingerprints(repo)
            commits, omitted = await self._updated_commit_summaries(
                repo,
                status.head,
                new_status.head,
            )
            return RepoUpdateResult(
                repo=repo,
                status="updated",
                message=message,
                old_head=status.head,
                new_head=new_status.head,
                needs_restart=True,
                needs_uv_sync=needs_uv_sync,
                commits=commits,
                omitted_commit_count=omitted,
            )
        except CommandError as exc:
            return RepoUpdateResult(
                repo=repo,
                status="failed",
                message=str(exc),
                old_head=status.head,
                new_head=status.head,
            )

    async def check_upstream(
        self,
        *,
        repo_ids: Iterable[str] | None = None,
    ) -> list[UpstreamStatus]:
        """只读检测第三方插件是否落后 upstream。"""
        repos = [
            repo
            for repo in self._filter_repos(repo_ids=repo_ids, kinds=["plugin"])
            if repo.kind == "plugin"
        ]
        semaphore = asyncio.Semaphore(self.settings.max_parallel)

        async def check(repo: ManagedRepo) -> UpstreamStatus:
            async with semaphore:
                return await detect_upstream_status(self.runner, repo)

        return await asyncio.gather(*(check(repo) for repo in repos))

    async def add_plugin(self, request: AddPluginRequest) -> AddPluginResult:
        """新增第三方插件并接入 pyproject/plugins.txt。"""
        return await add_plugin(self.runner, self.settings, request)

    def _repo_by_id(self, repo_id: str) -> ManagedRepo:
        """按 id 获取仓库；API 层传错 id 时直接返回明确错误。"""
        for repo in self.list_managed_repos():
            if repo.id == repo_id:
                return repo
        raise ValueError(f"未知仓库：{repo_id}")

    def validate_selection(
        self,
        *,
        repo_ids: Iterable[str] | None = None,
        kinds: Iterable[str] | None = None,
    ) -> None:
        """提前校验 API 选择范围，避免无效输入变成空操作。"""
        repo_id_set = set(repo_ids or [])
        if repo_id_set:
            known_ids = {repo.id for repo in self.list_managed_repos()}
            unknown_ids = sorted(repo_id_set - known_ids)
            if unknown_ids:
                raise ValueError(f"未知仓库：{', '.join(unknown_ids)}")

        kind_set = set(kinds or [])
        if kind_set:
            allowed_kinds = {"root", "resources", "plugin"}
            unknown_kinds = sorted(kind_set - allowed_kinds)
            if unknown_kinds:
                raise ValueError(f"未知仓库类型：{', '.join(unknown_kinds)}")

    def _structural_block_reason(self, status: RepoStatus) -> str | None:
        """保留结构性阻断，强制更新只绕过 dirty/ahead 类本地状态。"""
        if not status.exists:
            return "本地目录不存在"
        if not status.is_git_repo:
            return "不是 Git 仓库"
        if status.error:
            return status.update_block_reason or "状态采集失败"
        if status.branch == "HEAD":
            return "当前处于 detached HEAD"
        if status.branch != status.repo.target_branch:
            return f"当前分支 {status.branch} 不是目标分支 {status.repo.target_branch}"
        if not status.origin_head:
            return f"origin/{status.repo.target_branch} 不存在"
        return None

    def _filter_repos(
        self,
        *,
        repo_ids: Iterable[str] | None = None,
        kinds: Iterable[RepoKind] | None = None,
    ) -> list[ManagedRepo]:
        """按前端传入范围筛选仓库；空范围代表全部。"""
        self.validate_selection(repo_ids=repo_ids, kinds=kinds)
        repo_id_set = set(repo_ids or [])
        kind_set = set(kinds or [])
        repos = self.list_managed_repos()
        if repo_id_set:
            repos = [repo for repo in repos if repo.id in repo_id_set]
        if kind_set:
            repos = [repo for repo in repos if repo.kind in kind_set]
        return repos

    async def _read_repo_head(self, repo: ManagedRepo) -> str | None:
        """读取本地 HEAD；仓库缺失或 Git 状态异常时不阻断更新管理页面。"""
        if not repo.path.exists() or not (repo.path / ".git").exists():
            return None
        result = await self.runner.git(repo.path, "rev-parse", "HEAD", check=False)
        return result.stdout.strip() if result.ok and result.stdout.strip() else None

    def _dependency_fingerprints(self, repo: ManagedRepo) -> dict[Path, str | None]:
        """记录可能影响依赖安装的文件指纹，用于提示 uv sync。"""
        files = {
            self.settings.project_root / "pyproject.toml",
            self.settings.project_root / "uv.lock",
            self.settings.project_root / self.settings.plugins_list_file,
        }
        if repo.kind == "plugin":
            files.add(repo.path / "pyproject.toml")
        return {path: file_sha256(path) for path in files}

    async def _updated_commit_summaries(
        self,
        repo: ManagedRepo,
        old_head: str | None,
        new_head: str | None,
        *,
        limit: int = 50,
    ) -> tuple[list[CommitSummary], int]:
        """读取本次更新新增的 commit；失败时只影响报告明细，不反转更新结果。"""
        if not old_head or not new_head or old_head == new_head:
            return [], 0
        commit_range = f"{old_head}..{new_head}"
        try:
            count_result = await self.runner.git(
                repo.path,
                "rev-list",
                "--count",
                commit_range,
                check=True,
            )
            total = int(count_result.stdout.strip() or "0")
            log_result = await self.runner.git(
                repo.path,
                "log",
                "--format=%H%x00%s",
                f"--max-count={limit}",
                commit_range,
                check=True,
            )
        except (CommandError, ValueError):
            return [], 0

        commits: list[CommitSummary] = []
        for line in log_result.stdout.splitlines():
            if not line.strip():
                continue
            full_hash, subject = line.split("\x00", 1)
            commits.append(
                CommitSummary(
                    hash=full_hash,
                    short_hash=full_hash[:7],
                    subject=subject.strip(),
                )
            )
        return commits, max(0, total - len(commits))
