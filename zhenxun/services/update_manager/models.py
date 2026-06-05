from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

RepoKind = Literal["root", "resources", "plugin"]
JobKind = Literal["update", "checkout", "upstream_check", "add_plugin"]
JobState = Literal["pending", "running", "success", "failed"]


class ManagerSettings(BaseModel):
    """更新管理读取 nbm 配置语义后的运行时设置。"""

    project_root: Path
    github_org: str = "PoleSudBot"
    project_branch: str = "dev"
    resources_branch: str = "dev"
    plugin_branch: str = "L7dev"
    resources_repo: str = "https://github.com/PoleSudBot/resources.git"
    plugins_src_dir: str = "plugins"
    plugins_list_file: str = "plugins.txt"
    git_timeout: int = 120
    clone_timeout: int = 600
    max_parallel: int = 4
    auto_enabled: bool = False
    auto_scope: list[RepoKind] = Field(
        default_factory=lambda: ["root", "resources", "plugin"]
    )
    auto_time: str = "04:30"
    auto_restart: bool = False
    auto_run_uv_sync: bool = False


class ManagedRepo(BaseModel):
    """一个可被更新管理器处理的本地 Git 仓库。"""

    id: str
    name: str
    kind: RepoKind
    path: Path
    target_branch: str
    source_url: str | None = None


class CommitSummary(BaseModel):
    """前端展示用的精简 commit 信息。"""

    hash: str
    short_hash: str
    subject: str


class CommitInfo(BaseModel):
    """前端版本列表展示用的 commit 信息。"""

    hash: str
    short_hash: str
    author: str
    date: str
    message: str


class RepoCommitList(BaseModel):
    """单仓库目标分支的 commit 历史。"""

    repo: ManagedRepo
    branch: str | None = None
    current_hash: str | None = None
    running_hash: str | None = None
    latest_hash: str | None = None
    commits: list[CommitInfo] = Field(default_factory=list)


class RepoStatus(BaseModel):
    """仓库状态，区分 origin 更新与 upstream 提示。"""

    repo: ManagedRepo
    exists: bool
    is_git_repo: bool
    branch: str | None = None
    head: str | None = None
    origin_url: str | None = None
    upstream_url: str | None = None
    origin_head: str | None = None
    ahead: int = 0
    behind: int = 0
    tracked_dirty: bool = False
    untracked_count: int = 0
    can_update: bool = False
    update_block_reason: str | None = None
    error: str | None = None


class UpstreamStatus(BaseModel):
    """第三方插件相对原作者仓库的只读更新提示。"""

    repo: ManagedRepo
    has_upstream: bool
    upstream_branch: str | None = None
    behind: int = 0
    commits: list[CommitSummary] = Field(default_factory=list)
    error: str | None = None


class RepoUpdateResult(BaseModel):
    """单仓库更新结果，失败不影响同批其他仓库。"""

    repo: ManagedRepo
    status: Literal["updated", "skipped", "failed", "unchanged"]
    message: str
    old_head: str | None = None
    new_head: str | None = None
    needs_restart: bool = False
    needs_uv_sync: bool = False
    commits: list[CommitSummary] = Field(default_factory=list)
    omitted_commit_count: int = 0


class AddPluginRequest(BaseModel):
    """新增第三方插件所需输入。"""

    repo_url: str


class AddPluginResult(BaseModel):
    """新增插件后的本地接入结果。"""

    plugin_name: str
    plugin_path: Path
    package_name: str
    fork_repo: str
    upstream_repo: str
    branch: str
    needs_uv_sync: bool = True


class JobRecord(BaseModel):
    """异步任务的最近状态快照。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: JobKind
    state: JobState = "pending"
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    title: str = ""
    logs: list[str] = Field(default_factory=list)
    result: object | None = None
    error: str | None = None
