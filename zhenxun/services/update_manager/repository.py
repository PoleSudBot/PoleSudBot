from __future__ import annotations

import hashlib
from pathlib import Path
import re

import tomlkit

from .git import AsyncCommandRunner, CommandError
from .models import (
    CommitInfo,
    CommitSummary,
    ManagedRepo,
    RepoCommitList,
    RepoStatus,
    UpstreamStatus,
)

GITHUB_RE = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def parse_github_repo(url: str) -> str | None:
    """从 GitHub URL 中解析 owner/repo。"""
    match = GITHUB_RE.search(url.strip())
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def normalize_repo_url(repo_full_name: str) -> str:
    """生成标准 HTTPS GitHub 仓库地址。"""
    return f"https://github.com/{repo_full_name}.git"


def package_key(name: str) -> str:
    """按 Python 包名规范生成依赖键，兼容下划线和中划线。"""
    return name.replace("_", "-").lower()


def read_plugin_urls(file: Path) -> list[str]:
    """读取 plugins.txt 中启用的第三方插件 URL。"""
    if not file.exists():
        return []
    urls: list[str] = []
    for raw_line in file.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def read_editable_plugin_sources(pyproject_path: Path, plugins_dir: str) -> list[Path]:
    """从 uv editable sources 读取真实纳入依赖的第三方插件目录。"""
    if not pyproject_path.exists():
        return []
    data = tomlkit.parse(pyproject_path.read_text("utf-8"))
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    if not isinstance(sources, dict):
        return []

    plugin_paths: list[Path] = []
    project_root = pyproject_path.parent.resolve(strict=False)
    plugin_root = (project_root / plugins_dir).resolve(strict=False)
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        path_value = source.get("path")
        if not path_value or not source.get("editable", False):
            continue
        source_path = Path(str(path_value))
        absolute_path = (
            source_path
            if source_path.is_absolute()
            else project_root / source_path
        ).resolve(strict=False)
        try:
            # 归一化后再判断归属，避免 plugins/.. 逃逸到插件目录外。
            absolute_path.relative_to(plugin_root)
        except ValueError:
            continue
        try:
            plugin_paths.append(absolute_path.relative_to(project_root))
        except ValueError:
            continue
    return sorted(plugin_paths, key=lambda path: path.as_posix())


def _plugin_url_map(plugin_urls: list[str]) -> dict[str, str]:
    """按仓库名索引 plugins.txt 中的 upstream URL，兼容本地目录名不同。"""
    urls: dict[str, str] = {}
    for url in plugin_urls:
        repo_full_name = parse_github_repo(url)
        if not repo_full_name:
            continue
        urls[repo_full_name.rsplit("/", 1)[1]] = url
    return urls


def build_managed_repos(settings) -> list[ManagedRepo]:
    """根据当前配置生成管理对象，明确排除 sidecar 与第一方插件。"""
    root = settings.project_root
    repos = [
        ManagedRepo(
            id="root",
            name="Bot",
            kind="root",
            path=root,
            target_branch=settings.project_branch,
        ),
        ManagedRepo(
            id="resources",
            name="resources",
            kind="resources",
            path=root / "resources",
            target_branch=settings.resources_branch,
            source_url=settings.resources_repo,
        ),
    ]
    url_by_name = _plugin_url_map(read_plugin_urls(root / settings.plugins_list_file))
    for relative_path in read_editable_plugin_sources(
        root / "pyproject.toml",
        settings.plugins_src_dir,
    ):
        plugin_name = relative_path.name
        repos.append(
            ManagedRepo(
                id=f"plugin:{plugin_name}",
                name=plugin_name,
                kind="plugin",
                path=root / relative_path,
                target_branch=settings.plugin_branch,
                source_url=url_by_name.get(plugin_name),
            )
        )
    return repos


def file_sha256(path: Path) -> str | None:
    """计算文件哈希；文件不存在时用于变化检测的值为空。"""
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _git_output(
    runner: AsyncCommandRunner,
    cwd: Path,
    *args: str,
    check: bool = True,
) -> str:
    """执行 git 并返回 stdout，减少状态采集里的重复样板。"""
    result = await runner.git(cwd, *args, check=check)
    return result.stdout.strip()


async def _remote_url(
    runner: AsyncCommandRunner, path: Path, remote: str
) -> str | None:
    """读取 remote URL；remote 不存在时返回 None。"""
    result = await runner.git(path, "remote", "get-url", remote, check=False)
    return result.stdout.strip() if result.ok and result.stdout.strip() else None


async def _rev_parse(
    runner: AsyncCommandRunner, path: Path, ref: str
) -> str | None:
    """解析 ref 到 commit；不存在时返回 None。"""
    result = await runner.git(path, "rev-parse", "--verify", ref, check=False)
    return result.stdout.strip() if result.ok and result.stdout.strip() else None


async def _ahead_behind(
    runner: AsyncCommandRunner, path: Path, branch: str
) -> tuple[int, int]:
    """计算 HEAD 与 origin 目标分支的 ahead/behind。"""
    result = await runner.git(
        path,
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...origin/{branch}",
        check=False,
    )
    if not result.ok or not result.stdout.strip():
        return 0, 0
    ahead, behind = result.stdout.split()[:2]
    return int(ahead), int(behind)


async def is_ancestor(
    runner: AsyncCommandRunner,
    path: Path,
    ancestor: str,
    descendant: str,
) -> bool:
    """判断一个 commit 是否位于目标历史内，避免 checkout 任意 hash。"""
    result = await runner.git(
        path,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    return result.returncode == 0


def _tracked_dirty(status_output: str) -> bool:
    """判断是否存在已跟踪文件改动，忽略未跟踪文件对更新的阻断。"""
    return any(
        line and not line.startswith("??")
        for line in status_output.splitlines()
    )


async def get_repo_status(
    runner: AsyncCommandRunner,
    repo: ManagedRepo,
    *,
    fetch: bool = False,
) -> RepoStatus:
    """采集单个仓库状态，并给出是否可快进更新的原因。"""
    if not repo.path.exists():
        return RepoStatus(
            repo=repo,
            exists=False,
            is_git_repo=False,
            update_block_reason="本地目录不存在",
        )
    if not (repo.path / ".git").exists():
        return RepoStatus(
            repo=repo,
            exists=True,
            is_git_repo=False,
            update_block_reason="不是 Git 仓库",
        )

    try:
        if fetch:
            await runner.git(repo.path, "fetch", "origin", "--prune", check=True)
        branch = await _git_output(
            runner, repo.path, "rev-parse", "--abbrev-ref", "HEAD"
        )
        head = await _git_output(runner, repo.path, "rev-parse", "HEAD")
        origin_url = await _remote_url(runner, repo.path, "origin")
        upstream_url = await _remote_url(runner, repo.path, "upstream")
        origin_head = await _rev_parse(
            runner, repo.path, f"origin/{repo.target_branch}"
        )
        ahead, behind = await _ahead_behind(runner, repo.path, repo.target_branch)
        status_output = (
            await runner.git(repo.path, "status", "--porcelain", check=True)
        ).stdout
        tracked_dirty = _tracked_dirty(status_output)
        untracked = (
            await runner.git(
                repo.path, "ls-files", "--others", "--exclude-standard", check=True
            )
        ).stdout.splitlines()

        reason = None
        if branch == "HEAD":
            reason = "当前处于 detached HEAD"
        elif branch != repo.target_branch:
            reason = f"当前分支 {branch} 不是目标分支 {repo.target_branch}"
        elif tracked_dirty:
            reason = "存在已跟踪文件的未提交修改"
        elif not origin_head:
            reason = f"origin/{repo.target_branch} 不存在"

        return RepoStatus(
            repo=repo,
            exists=True,
            is_git_repo=True,
            branch=branch,
            head=head,
            origin_url=origin_url,
            upstream_url=upstream_url,
            origin_head=origin_head,
            ahead=ahead,
            behind=behind,
            tracked_dirty=tracked_dirty,
            untracked_count=len(untracked),
            can_update=reason is None and behind > 0,
            update_block_reason=reason,
        )
    except CommandError as exc:
        return RepoStatus(
            repo=repo,
            exists=True,
            is_git_repo=True,
            update_block_reason="状态采集失败",
            error=str(exc),
        )


async def list_repo_commits(
    runner: AsyncCommandRunner,
    repo: ManagedRepo,
    *,
    fetch: bool = False,
    limit: int = 30,
) -> RepoCommitList:
    """列出目标分支历史，用于 WebUI 展开版本列表。"""
    status = await get_repo_status(runner, repo, fetch=fetch)
    if not status.exists or not status.is_git_repo or status.error:
        return RepoCommitList(
            repo=repo,
            branch=status.branch,
            current_hash=status.head,
            latest_hash=status.origin_head,
        )
    if not status.origin_head:
        return RepoCommitList(
            repo=repo,
            branch=status.branch,
            current_hash=status.head,
            latest_hash=status.origin_head,
        )

    safe_limit = max(1, min(limit, 100))
    result = await runner.git(
        repo.path,
        "log",
        "--format=%H%x00%an%x00%ad%x00%s",
        "--date=short",
        f"-{safe_limit}",
        f"origin/{repo.target_branch}",
        check=True,
    )
    commits: list[CommitInfo] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        full_hash, author, date, message = line.split("\x00", 3)
        commits.append(
            CommitInfo(
                hash=full_hash,
                short_hash=full_hash[:7],
                author=author.strip(),
                date=date.strip(),
                message=message.strip(),
            )
        )
    return RepoCommitList(
        repo=repo,
        branch=status.branch,
        current_hash=status.head,
        latest_hash=status.origin_head,
        commits=commits,
    )


async def detect_upstream_status(
    runner: AsyncCommandRunner,
    repo: ManagedRepo,
) -> UpstreamStatus:
    """只读检测第三方插件落后 upstream 的提交数。"""
    if repo.kind != "plugin":
        return UpstreamStatus(
            repo=repo, has_upstream=False, error="仅插件支持 upstream"
        )
    if not repo.path.exists() or not (repo.path / ".git").exists():
        return UpstreamStatus(repo=repo, has_upstream=False, error="本地仓库不存在")
    upstream_url = await _remote_url(runner, repo.path, "upstream")
    if not upstream_url:
        return UpstreamStatus(repo=repo, has_upstream=False, error="未配置 upstream")

    try:
        await runner.git(repo.path, "fetch", "upstream", "--prune", check=True)
        branch = await resolve_remote_default_branch(runner, repo.path, "upstream")
        count_result = await runner.git(
            repo.path, "rev-list", "--count", f"HEAD..upstream/{branch}", check=True
        )
        log_result = await runner.git(
            repo.path,
            "log",
            "--format=%H%x00%s",
            "-10",
            f"HEAD..upstream/{branch}",
            check=True,
        )
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
        return UpstreamStatus(
            repo=repo,
            has_upstream=True,
            upstream_branch=branch,
            behind=int(count_result.stdout.strip() or "0"),
            commits=commits,
        )
    except CommandError as exc:
        return UpstreamStatus(repo=repo, has_upstream=True, error=str(exc))


async def resolve_remote_default_branch(
    runner: AsyncCommandRunner, path: Path, remote: str
) -> str:
    """解析 remote 默认分支；符号引用缺失时回退 remote show。"""
    symbolic = await runner.git(
        path, "symbolic-ref", f"refs/remotes/{remote}/HEAD", check=False
    )
    prefix = f"refs/remotes/{remote}/"
    if symbolic.ok and symbolic.stdout.strip().startswith(prefix):
        return symbolic.stdout.strip()[len(prefix) :]

    # 某些浅克隆或旧仓库没有 remote HEAD，本分支解析作为兼容兜底。
    remote_show = await runner.git(path, "remote", "show", remote, check=False)
    if remote_show.ok:
        for line in remote_show.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("HEAD branch:"):
                return stripped.split(":", 1)[1].strip()

    # 兜底：用 ls-remote 探测常见默认分支是否存在。
    for candidate in ("main", "master"):
        check_result = await runner.git(
            path,
            "ls-remote",
            "--heads",
            remote,
            f"refs/heads/{candidate}",
            check=False,
        )
        if check_result.ok and check_result.stdout.strip():
            return candidate
    return "main"


def parse_package_name(plugin_path: Path) -> str | None:
    """从插件 pyproject.toml 中解析项目包名。"""
    pyproject = plugin_path / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomlkit.parse(pyproject.read_text("utf-8"))
    project = data.get("project", {})
    name = project.get("name") if isinstance(project, dict) else None
    return str(name).strip() if name else None
