from __future__ import annotations

from pathlib import Path
import shutil

import tomlkit

from .git import AsyncCommandRunner
from .models import AddPluginRequest, AddPluginResult, ManagerSettings
from .repository import (
    normalize_repo_url,
    package_key,
    parse_github_repo,
    parse_package_name,
    resolve_remote_default_branch,
)


def update_plugins_txt(path: Path, repo_url: str) -> bool:
    """将插件上游 URL 追加到 plugins.txt，已存在时保持原文件不变。"""
    normalized = repo_url.strip()
    existing = path.read_text("utf-8") if path.exists() else ""
    for raw_line in existing.splitlines():
        if raw_line.strip() == normalized:
            return False
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{suffix}{normalized}\n", "utf-8")
    return True


def update_pyproject_dependency(
    pyproject_path: Path,
    package_name: str,
    plugin_path: Path,
) -> bool:
    """把本地插件写入主项目依赖和 uv editable source。"""
    doc = tomlkit.parse(pyproject_path.read_text("utf-8"))
    normalized_name = package_key(package_name)
    changed = False

    project = doc.setdefault("project", tomlkit.table())
    dependencies = project.setdefault("dependencies", tomlkit.array())
    exists = any(
        str(item).strip().split("[", 1)[0] == normalized_name
        for item in dependencies
    )
    if not exists:
        dependencies.add_line(normalized_name)
        dependencies.multiline(True)
        changed = True

    tool = doc.setdefault("tool", tomlkit.table())
    uv = tool.setdefault("uv", tomlkit.table())
    sources = uv.setdefault("sources", tomlkit.table())
    relative_path = plugin_path.relative_to(pyproject_path.parent).as_posix()
    source_entry = tomlkit.inline_table()
    source_entry.update({"path": relative_path, "editable": True})
    if sources.get(normalized_name) != source_entry:
        sources[normalized_name] = source_entry
        changed = True

    if changed:
        pyproject_path.write_text(tomlkit.dumps(doc), "utf-8")
    return changed


async def add_plugin(
    runner: AsyncCommandRunner,
    settings: ManagerSettings,
    request: AddPluginRequest,
) -> AddPluginResult:
    """按 upstream clone -> origin 组织仓库 -> 本地依赖的顺序新增插件。"""
    upstream_repo = parse_github_repo(request.repo_url)
    if not upstream_repo:
        raise ValueError(f"无法解析 GitHub 仓库地址: {request.repo_url}")

    plugin_name = upstream_repo.rsplit("/", 1)[1]
    fork_repo = f"{settings.github_org}/{plugin_name}"
    plugin_path = settings.project_root / settings.plugins_src_dir / plugin_name
    if plugin_path.exists():
        raise FileExistsError(f"插件目录已存在: {plugin_path}")

    try:
        upstream_url = normalize_repo_url(upstream_repo)
        await runner.git(
            settings.project_root,
            "clone",
            upstream_url,
            str(plugin_path),
            timeout=settings.clone_timeout,
            check=True,
        )
        await runner.git(
            plugin_path, "remote", "rename", "origin", "upstream", check=True
        )

        # 先在本地保留原作者为 upstream，再创建或连接组织仓库作为 origin。
        view_result = await runner.gh(
            settings.project_root, "repo", "view", fork_repo, check=False
        )
        if not view_result.ok:
            await runner.gh(
                settings.project_root,
                "repo",
                "create",
                fork_repo,
                "--public",
                "--confirm",
                check=True,
            )

        origin_url = normalize_repo_url(fork_repo)
        await runner.git(plugin_path, "remote", "add", "origin", origin_url, check=True)
        await _checkout_plugin_branch(runner, plugin_path, settings.plugin_branch)
    except Exception:
        # 只清理本次 clone 出来的新目录，避免半完成目录阻塞用户修正后重试。
        if plugin_path.exists():
            shutil.rmtree(plugin_path)
        raise

    package_name = parse_package_name(plugin_path) or plugin_name
    update_pyproject_dependency(
        settings.project_root / "pyproject.toml",
        package_name,
        plugin_path,
    )
    update_plugins_txt(
        settings.project_root / settings.plugins_list_file,
        request.repo_url,
    )
    return AddPluginResult(
        plugin_name=plugin_name,
        plugin_path=plugin_path,
        package_name=package_key(package_name),
        fork_repo=fork_repo,
        upstream_repo=upstream_repo,
        branch=settings.plugin_branch,
    )


async def _checkout_plugin_branch(
    runner: AsyncCommandRunner, plugin_path: Path, branch: str
) -> None:
    """切到 fork 的管理分支；缺失时从 upstream 默认分支创建并推送。"""
    remote_branch = await runner.git(
        plugin_path,
        "ls-remote",
        "--heads",
        "origin",
        f"refs/heads/{branch}",
        check=False,
    )
    if remote_branch.ok and remote_branch.stdout.strip():
        # 已有 fork 分支必须显式拉取 origin，避免误 checkout 到 upstream 同名分支。
        await runner.git(
            plugin_path,
            "fetch",
            "origin",
            f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            check=True,
        )
        await runner.git(
            plugin_path,
            "checkout",
            "-B",
            branch,
            f"origin/{branch}",
            check=True,
        )
        await runner.git(
            plugin_path,
            "branch",
            "--set-upstream-to",
            f"origin/{branch}",
            branch,
            check=True,
        )
        return

    upstream_branch = await resolve_remote_default_branch(
        runner, plugin_path, "upstream"
    )
    await runner.git(plugin_path, "fetch", "upstream", upstream_branch, check=True)
    await runner.git(
        plugin_path,
        "checkout",
        "-b",
        branch,
        f"upstream/{upstream_branch}",
        check=True,
    )
    await runner.git(plugin_path, "push", "-u", "origin", branch, check=True)
