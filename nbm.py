#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pydantic>=2,<3",
#   "tomlkit>=0.13,<1",
#   "tqdm>=4.67,<5",
# ]
# ///
"""
A professional-grade management script for fork-based NoneBot2 projects.
Powered by uv and a modular, robust core library.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

try:
    import tomlkit
    from tqdm import tqdm

    from nbm_core import config, git, process, project
    from nbm_core.exceptions import CommandError
    from nbm_core.state import StateManager
except ModuleNotFoundError as exc:
    missing = exc.name or "unknown dependency"
    sys.stderr.write(f"❌ 缺少运行 nbm 所需依赖：{missing}\n")
    sys.stderr.write("👉 请改用 `uv run --no-project nbm.py <command>` 运行该脚本。\n")
    raise SystemExit(1) from None


SYNC_STATE_MANAGER = StateManager(config.SYNC_STATE_FILE)
PROD_SETUP_STATE_KEY = "prod_setup"
RESOURCES_DIR = config.PROJECT_ROOT / "resources"


@dataclass(frozen=True)
class SidecarPluginSpec:
    name: str
    repo: str
    ref: str | None = None
    packages: tuple[str, ...] = ()
    playwright_browsers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoSyncResult:
    label: str
    status: str
    stage: str
    head: str | None = None
    error: str | None = None


class RepoSyncFailure(CommandError):
    """Adds stage metadata so prod-setup can summarize repo failures precisely."""

    def __init__(self, label: str, stage: str, error: str | CommandError):
        detail = str(error).strip()
        result = error.result if isinstance(error, CommandError) else None
        super().__init__(f"{label} 在 {stage} 失败：\n{detail}", result)
        self.label = label
        self.stage = stage
        self.detail = detail


def ensure_venv_exists() -> None:
    """Checks for a .venv directory and runs `uv venv` if not found."""
    venv_path = config.PROJECT_ROOT / ".venv"
    if not venv_path.is_dir():
        config.logger.info(
            " Virtual environment not found. Creating one with `uv venv`..."
        )
        try:
            subprocess.run(
                ["uv", "venv"],
                check=True,
                cwd=config.PROJECT_ROOT,
                capture_output=True,
            )
            config.logger.info(f"✅ Virtual environment created at: {venv_path}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            config.logger.error(
                "❌ Failed to create virtual environment. "
                "Is `uv` installed and in your PATH?"
            )
            sys.exit(1)


def _read_plugin_urls() -> list[str]:
    if not config.PLUGINS_LIST_FILE.exists():
        raise CommandError(
            f"'{config.PLUGINS_LIST_FILE.name}' not found. Aborting deployment."
        )
    return [
        line.strip()
        for line in config.PLUGINS_LIST_FILE.read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _capture_repo_head(local_path: Path) -> str | None:
    if not (local_path / ".git").is_dir():
        return None
    try:
        return git.get_head_commit(local_path)
    except CommandError:
        return None


def _build_repo_sync_failure(
    *,
    label: str,
    stage: str,
    local_path: Path,
    error: CommandError,
) -> RepoSyncResult:
    detail = str(error).strip()
    if isinstance(error, RepoSyncFailure):
        stage = error.stage
        detail = error.detail

    # 这里保留失败仓库当前 HEAD，避免一次失败就让后续部署完全丢失本地状态指纹。
    head = _capture_repo_head(local_path)
    summary = detail.splitlines()[0] if detail else "未知错误"
    config.logger.error(f"  - ❌ {label} 在 {stage} 失败: {summary}")
    return RepoSyncResult(
        label=label,
        status="failed",
        stage=stage,
        head=head,
        error=detail,
    )


def _collect_repo_sync_failures(results: list[RepoSyncResult]) -> list[RepoSyncResult]:
    return [result for result in results if result.status == "failed"]


def _can_persist_repo_sync_state(results: list[RepoSyncResult]) -> bool:
    # 只有每个仓库都还能解析出 HEAD 时才覆盖缓存状态，避免失败克隆把上次有效快照清掉。
    return all(result.head for result in results)


def _log_repo_sync_summary(results: list[RepoSyncResult]) -> None:
    failures = _collect_repo_sync_failures(results)
    config.logger.info("\n📋 Repository sync summary:")
    config.logger.info(f"  - ✅ Success: {len(results) - len(failures)}")
    config.logger.info(f"  - ❌ Failed: {len(failures)}")
    for result in failures:
        detail = result.error or "未知错误"
        config.logger.error(f"  - {result.label} [{result.stage}]: {detail}")


def _parse_sidecar_string_list_field(entry: Any, field_name: str) -> tuple[str, ...]:
    raw = entry.get(field_name)
    if raw is None:
        return ()
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, dict):
        raise CommandError(
            "Invalid sidecar plugin entry in "
            f"{config.SIDECAR_PLUGINS_FILE}: {field_name} must be a list of strings"
        )
    else:
        try:
            values = list(raw)
        except TypeError as exc:
            raise CommandError(
                "Invalid sidecar plugin entry in "
                f"{config.SIDECAR_PLUGINS_FILE}: {field_name} must be a list of strings"
            ) from exc

    normalized: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        normalized.append(item)
    return tuple(dict.fromkeys(normalized))


def _read_sidecar_plugin_specs() -> list[SidecarPluginSpec]:
    if not config.SIDECAR_PLUGINS_FILE.exists():
        raise CommandError(
            f"'{config.SIDECAR_PLUGINS_FILE.as_posix()}' not found. "
            "Aborting deployment."
        )

    try:
        data = tomlkit.parse(config.SIDECAR_PLUGINS_FILE.read_text("utf-8"))
    except Exception as exc:
        raise CommandError(
            f"Failed to parse sidecar plugin manifest: {config.SIDECAR_PLUGINS_FILE}"
        ) from exc

    specs: list[SidecarPluginSpec] = []
    plugin_entries = data.get("plugins", [])
    for entry in plugin_entries:
        repo = str(entry.get("repo", "")).strip()
        if not repo:
            raise CommandError(
                "Invalid sidecar plugin entry in "
                f"{config.SIDECAR_PLUGINS_FILE}: missing repo"
            )
        repo_name = git.get_repo_name_from_url(repo)
        if not repo_name:
            raise CommandError(f"Invalid GitHub URL in sidecar plugin manifest: {repo}")
        ref = str(entry.get("ref", "")).strip() or None
        specs.append(
            SidecarPluginSpec(
                name=repo_name.split("/")[-1],
                repo=repo,
                ref=ref,
                packages=_parse_sidecar_string_list_field(entry, "packages"),
                playwright_browsers=_parse_sidecar_string_list_field(
                    entry, "playwright_browsers"
                ),
            )
        )
    return specs


def _get_origin_default_branch(cwd: Path) -> str:
    ref = process.git(
        ["symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd,
        check=False,
        quiet=True,
    )
    prefix = "refs/remotes/origin/"
    if ref.startswith(prefix):
        return ref[len(prefix) :]

    remote_show = process.git(
        ["remote", "show", "origin"],
        cwd,
        check=False,
        quiet=True,
    )
    for line in remote_show.splitlines():
        stripped = line.strip()
        if stripped.startswith("HEAD branch:"):
            return stripped.split(":", 1)[1].strip()
    return "main"


def _sync_repo_ref(
    local_path: Path,
    repo_ref: str | None,
    label: str,
    *,
    check_tracked_clean: bool = True,
) -> str:
    if not repo_ref:
        branch = _get_origin_default_branch(local_path)
        return _update_existing_repo(
            local_path,
            branch,
            label,
            check_tracked_clean=check_tracked_clean,
        )

    try:
        process.git(["fetch", "origin", "--prune", "--tags"], local_path, quiet=True)
    except CommandError as e:
        raise RepoSyncFailure(label, "获取 origin tags", e) from e
    if git.remote_branch_exists(local_path, "origin", repo_ref):
        return _update_existing_repo(
            local_path,
            repo_ref,
            label,
            check_tracked_clean=check_tracked_clean,
        )

    process.git(["fetch", "origin", repo_ref], local_path, check=False, quiet=True)
    try:
        process.git(["checkout", "--detach", repo_ref], local_path)
    except CommandError as e:
        raise RepoSyncFailure(label, f"切换到指定引用 {repo_ref}", e) from e
    try:
        return git.get_head_commit(local_path)
    except CommandError as e:
        raise RepoSyncFailure(label, "读取 HEAD 提交", e) from e


def _sync_managed_repo_ref(
    *,
    local_path: Path,
    clone_url: str,
    repo_ref: str | None,
    label: str,
    check_tracked_clean: bool = True,
) -> str:
    if local_path.exists():
        config.logger.info(f"  - Updating {label}: {local_path.name}")
        if not (local_path / ".git").is_dir():
            raise RepoSyncFailure(
                label,
                "检查仓库",
                f"{label} 路径存在，但不是独立 Git 仓库：{local_path}",
            )
        if check_tracked_clean:
            try:
                _ensure_tracked_clean(local_path, label)
            except CommandError as e:
                raise RepoSyncFailure(label, "检查工作区", e) from e
        return _sync_repo_ref(
            local_path,
            repo_ref,
            label,
            check_tracked_clean=check_tracked_clean,
        )

    config.logger.info(f"  - Cloning {label}: {local_path.name}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        process.git(["clone", clone_url, str(local_path)], config.PROJECT_ROOT)
    except CommandError as e:
        raise RepoSyncFailure(label, "克隆仓库", e) from e
    return _sync_repo_ref(
        local_path,
        repo_ref,
        label,
        check_tracked_clean=check_tracked_clean,
    )


def _sync_sidecar_plugin_repositories(
    specs: list[SidecarPluginSpec],
) -> dict[str, str]:
    plugin_dir = config.SIDECAR_CORE_DIR / "gsuid_core" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin_heads: dict[str, str] = {}

    for spec in specs:
        plugin_heads[spec.name] = _sync_managed_repo_ref(
            local_path=plugin_dir / spec.name,
            clone_url=spec.repo,
            repo_ref=spec.ref,
            label=f"sidecar 插件 {spec.name}",
        )
    return plugin_heads


def _write_sidecar_dependency_manifest(specs: list[SidecarPluginSpec]) -> None:
    config.SIDECAR_PLUGIN_DEPENDENCIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "plugins": [
            {
                "name": spec.name,
                "packages": list(spec.packages),
                "playwright_browsers": list(spec.playwright_browsers),
            }
            for spec in specs
        ]
    }
    config.SIDECAR_PLUGIN_DEPENDENCIES_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )


def _sync_sidecar_runtime() -> tuple[str, str, dict[str, str]]:
    config.SIDECAR_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    config.logger.info("🔌 Preparing bridge vendor...")
    bridge_vendor_head = _sync_managed_repo_ref(
        local_path=config.BRIDGE_VENDOR_DIR,
        clone_url=config.BRIDGE_VENDOR_REPO,
        repo_ref=config.BRIDGE_VENDOR_REF or None,
        label="桥接依赖 nonebot-plugin-genshinuid",
    )

    config.logger.info("🛰️ Preparing sidecar core...")
    sidecar_core_head = _sync_managed_repo_ref(
        local_path=config.SIDECAR_CORE_DIR,
        clone_url=config.SIDECAR_CORE_REPO,
        repo_ref=config.SIDECAR_CORE_REF or None,
        label="sidecar Core",
    )

    specs = _read_sidecar_plugin_specs()
    if specs:
        config.logger.info(f"🎮 Syncing {len(specs)} sidecar plugins...")
    sidecar_plugin_heads = _sync_sidecar_plugin_repositories(specs)
    _write_sidecar_dependency_manifest(specs)
    return bridge_vendor_head, sidecar_core_head, sidecar_plugin_heads


def _sync_managed_repo_ref_with_report(
    *,
    local_path: Path,
    clone_url: str,
    repo_ref: str | None,
    label: str,
) -> RepoSyncResult:
    try:
        head = _sync_managed_repo_ref(
            local_path=local_path,
            clone_url=clone_url,
            repo_ref=repo_ref,
            label=label,
            check_tracked_clean=False,
        )
        return RepoSyncResult(
            label=label,
            status="success",
            stage="同步完成",
            head=head,
        )
    except CommandError as e:
        return _build_repo_sync_failure(
            label=label,
            stage="同步仓库",
            local_path=local_path,
            error=e,
        )


def _sync_sidecar_runtime_with_report() -> (
    tuple[str, str, dict[str, str], list[RepoSyncResult]]
):
    config.SIDECAR_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    results: list[RepoSyncResult] = []

    config.logger.info("🔌 Preparing bridge vendor...")
    bridge_result = _sync_managed_repo_ref_with_report(
        local_path=config.BRIDGE_VENDOR_DIR,
        clone_url=config.BRIDGE_VENDOR_REPO,
        repo_ref=config.BRIDGE_VENDOR_REF or None,
        label="桥接依赖 nonebot-plugin-genshinuid",
    )
    results.append(bridge_result)

    config.logger.info("🛰️ Preparing sidecar core...")
    sidecar_core_result = _sync_managed_repo_ref_with_report(
        local_path=config.SIDECAR_CORE_DIR,
        clone_url=config.SIDECAR_CORE_REPO,
        repo_ref=config.SIDECAR_CORE_REF or None,
        label="sidecar Core",
    )
    results.append(sidecar_core_result)

    specs = _read_sidecar_plugin_specs()
    if specs:
        config.logger.info(f"🎮 Syncing {len(specs)} sidecar plugins...")

    sidecar_plugin_heads: dict[str, str] = {}
    plugin_dir = config.SIDECAR_CORE_DIR / "gsuid_core" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        result = _sync_managed_repo_ref_with_report(
            local_path=plugin_dir / spec.name,
            clone_url=spec.repo,
            repo_ref=spec.ref,
            label=f"sidecar 插件 {spec.name}",
        )
        results.append(result)
        if result.head:
            sidecar_plugin_heads[spec.name] = result.head

    _write_sidecar_dependency_manifest(specs)
    return (
        bridge_result.head or "",
        sidecar_core_result.head or "",
        sidecar_plugin_heads,
        results,
    )


def _ensure_sidecar_env_file() -> None:
    if config.SIDECAR_ENV_FILE.exists():
        return
    if not config.SIDECAR_ENV_EXAMPLE_FILE.exists():
        raise CommandError(
            f"Missing sidecar env template: {config.SIDECAR_ENV_EXAMPLE_FILE}"
        )
    config.SIDECAR_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SIDECAR_ENV_FILE.write_text(
        config.SIDECAR_ENV_EXAMPLE_FILE.read_text("utf-8"),
        "utf-8",
    )
    config.logger.info(f"📝 Created sidecar env file: {config.SIDECAR_ENV_FILE}")


def _tracked_status_output(cwd: Path) -> str:
    return process.git(["status", "--short", "-uno"], cwd, check=False, quiet=True)


def _parse_tracked_status(cwd: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if not (status_output := _tracked_status_output(cwd)):
        return entries

    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path_text = line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip()
        entries.append((line[:2], path_text))
    return entries


def _discard_tracked_paths(cwd: Path, paths: list[str]) -> None:
    if not paths:
        return
    process.git(
        ["restore", "--source=HEAD", "--staged", "--worktree", "--", *paths],
        cwd,
    )


def _ensure_tracked_clean(
    cwd: Path,
    label: str,
    *,
    ignored_paths: set[str] | None = None,
) -> None:
    ignored_paths = ignored_paths or set()
    entries = _parse_tracked_status(cwd)
    if not entries:
        return

    ignored_entries = [path for _, path in entries if path in ignored_paths]
    blocking_entries = [
        f"{status} {path}" for status, path in entries if path not in ignored_paths
    ]

    if blocking_entries:
        raise CommandError(
            f"{label} 存在未提交的已跟踪修改，已停止更新：\n"
            + "\n".join(blocking_entries)
        )

    if ignored_entries:
        ignored_list = ", ".join(sorted(ignored_entries))
        config.logger.warning(
            f"⚠️ {label} 检测到可自动忽略的部署产物改动：{ignored_list}。"
            "将恢复到当前 HEAD 后继续更新。"
        )
        _discard_tracked_paths(cwd, sorted(ignored_entries))


def _discard_ignored_tracked_paths(
    cwd: Path,
    label: str,
    *,
    ignored_paths: set[str] | None = None,
) -> None:
    ignored_paths = ignored_paths or set()
    if not ignored_paths:
        return

    ignored_entries = [
        path for _, path in _parse_tracked_status(cwd) if path in ignored_paths
    ]
    if not ignored_entries:
        return

    ignored_list = ", ".join(sorted(ignored_entries))
    config.logger.warning(
        f"⚠️ {label} 检测到可自动忽略的部署产物改动：{ignored_list}。"
        "将恢复到当前 HEAD 后继续更新。"
    )
    _discard_tracked_paths(cwd, sorted(ignored_entries))


def _ensure_upstream_remote(cwd: Path, upstream_url: str | None) -> None:
    if not upstream_url:
        return
    current_url = process.git(
        ["remote", "get-url", "upstream"], cwd, check=False, quiet=True
    )
    if not current_url:
        process.git(["remote", "add", "upstream", upstream_url], cwd, check=False)


def _checkout_target_branch(cwd: Path, branch: str, label: str) -> None:
    if not git.remote_branch_exists(cwd, "origin", branch):
        raise RepoSyncFailure(
            label,
            "校验目标分支",
            f"{label} 的远程分支 origin/{branch} 不存在。",
        )
    try:
        if git.local_branch_exists(cwd, branch):
            process.git(["checkout", branch], cwd)
        else:
            process.git(["checkout", "-b", branch, f"origin/{branch}"], cwd)
    except CommandError as e:
        raise RepoSyncFailure(label, f"切换到分支 {branch}", e) from e


def _update_existing_repo(
    cwd: Path,
    branch: str,
    label: str,
    *,
    ignored_paths: set[str] | None = None,
    check_tracked_clean: bool = True,
) -> str:
    if not (cwd / ".git").is_dir():
        raise RepoSyncFailure(
            label,
            "检查仓库",
            f"{label} 路径存在，但不是独立 Git 仓库：{cwd}",
        )
    if check_tracked_clean:
        try:
            _ensure_tracked_clean(cwd, label, ignored_paths=ignored_paths)
        except CommandError as e:
            raise RepoSyncFailure(label, "检查工作区", e) from e
    else:
        # prod-setup 需要按仓库汇总失败原因，因此这里让 git 自己暴露真实冲突，
        # 而不是在预检查阶段提前打断整批仓库同步。
        _discard_ignored_tracked_paths(cwd, label, ignored_paths=ignored_paths)
    try:
        process.git(["fetch", "origin", "--prune"], cwd, quiet=True)
    except CommandError as e:
        raise RepoSyncFailure(label, "获取 origin 更新", e) from e
    _checkout_target_branch(cwd, branch, label)
    try:
        process.git(["pull", "--ff-only", "origin", branch], cwd)
    except CommandError as e:
        raise RepoSyncFailure(label, f"快进拉取 origin/{branch}", e) from e
    try:
        return git.get_head_commit(cwd)
    except CommandError as e:
        raise RepoSyncFailure(label, "读取 HEAD 提交", e) from e


def _sync_managed_repo(
    *,
    local_path: Path,
    clone_url: str,
    branch: str,
    label: str,
    upstream_url: str | None = None,
    check_tracked_clean: bool = True,
) -> str:
    if local_path.exists():
        config.logger.info(f"  - Updating {label}: {local_path.name}")
        _ensure_upstream_remote(local_path, upstream_url)
        return _update_existing_repo(
            local_path,
            branch,
            label,
            check_tracked_clean=check_tracked_clean,
        )

    config.logger.info(f"  - Cloning {label}: {local_path.name}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        process.git(
            ["clone", "--depth=1", "-b", branch, clone_url, str(local_path)],
            config.PROJECT_ROOT,
        )
    except CommandError as e:
        raise RepoSyncFailure(label, "克隆仓库", e) from e
    _ensure_upstream_remote(local_path, upstream_url)
    try:
        return git.get_head_commit(local_path)
    except CommandError as e:
        raise RepoSyncFailure(label, "读取 HEAD 提交", e) from e


def _sync_resources_repo() -> str:
    return _sync_managed_repo(
        local_path=RESOURCES_DIR,
        clone_url=config.RESOURCES_REPO,
        branch=config.RESOURCES_BRANCH,
        label="resources 仓库",
    )


def _sync_managed_repo_with_report(
    *,
    local_path: Path,
    clone_url: str,
    branch: str,
    label: str,
    upstream_url: str | None = None,
) -> RepoSyncResult:
    try:
        head = _sync_managed_repo(
            local_path=local_path,
            clone_url=clone_url,
            branch=branch,
            label=label,
            upstream_url=upstream_url,
            check_tracked_clean=False,
        )
        return RepoSyncResult(
            label=label,
            status="success",
            stage="同步完成",
            head=head,
        )
    except CommandError as e:
        return _build_repo_sync_failure(
            label=label,
            stage="同步仓库",
            local_path=local_path,
            error=e,
        )


def _sync_resources_repo_with_report() -> RepoSyncResult:
    return _sync_managed_repo_with_report(
        local_path=RESOURCES_DIR,
        clone_url=config.RESOURCES_REPO,
        branch=config.RESOURCES_BRANCH,
        label="resources 仓库",
    )


def _sync_plugin_repositories(urls: list[str]) -> dict[str, str]:
    config.PLUGINS_SRC_DIR.mkdir(parents=True, exist_ok=True)
    plugin_heads: dict[str, str] = {}
    for url in tqdm(urls, desc="Syncing plugins"):
        repo_name_with_owner = git.get_repo_name_from_url(url)
        if not repo_name_with_owner:
            raise CommandError(f"Invalid GitHub URL in plugins.txt: {url}")
        plugin_name = repo_name_with_owner.split("/")[-1]
        clone_url = f"https://github.com/{config.YOUR_GITHUB_ORG}/{plugin_name}.git"
        local_path = config.PLUGINS_SRC_DIR / plugin_name
        plugin_heads[plugin_name] = _sync_managed_repo(
            local_path=local_path,
            clone_url=clone_url,
            branch=config.PLUGIN_BRANCH,
            label=f"插件仓库 {plugin_name}",
            upstream_url=url,
        )
    return plugin_heads


def _sync_plugin_repositories_with_report(
    urls: list[str],
) -> tuple[dict[str, str], list[RepoSyncResult]]:
    config.PLUGINS_SRC_DIR.mkdir(parents=True, exist_ok=True)
    plugin_heads: dict[str, str] = {}
    results: list[RepoSyncResult] = []
    for url in tqdm(urls, desc="Syncing plugins"):
        repo_name_with_owner = git.get_repo_name_from_url(url)
        if not repo_name_with_owner:
            label = f"插件仓库 {url}"
            result = RepoSyncResult(
                label=label,
                status="failed",
                stage="解析 plugins.txt",
                error=f"Invalid GitHub URL in plugins.txt: {url}",
            )
            config.logger.error(
                f"  - ❌ {label} 在 解析 plugins.txt 失败: Invalid GitHub URL"
            )
            results.append(result)
            continue

        plugin_name = repo_name_with_owner.split("/")[-1]
        clone_url = f"https://github.com/{config.YOUR_GITHUB_ORG}/{plugin_name}.git"
        local_path = config.PLUGINS_SRC_DIR / plugin_name
        result = _sync_managed_repo_with_report(
            local_path=local_path,
            clone_url=clone_url,
            branch=config.PLUGIN_BRANCH,
            label=f"插件仓库 {plugin_name}",
            upstream_url=url,
        )
        results.append(result)
        if result.head:
            plugin_heads[plugin_name] = result.head
    return plugin_heads, results


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_lock_fingerprint(
    *,
    plugin_heads: dict[str, str],
    resources_head: str,
    bridge_vendor_head: str,
) -> tuple[str, dict[str, Any]]:
    material = {
        "pyproject_sha256": _file_sha256(config.PROJECT_ROOT / "pyproject.toml"),
        "plugins_txt_sha256": _file_sha256(config.PLUGINS_LIST_FILE),
        "manage": {
            "project_branch": config.PROJECT_BRANCH,
            "resources_branch": config.RESOURCES_BRANCH,
            "plugin_branch": config.PLUGIN_BRANCH,
            "resources_repo": config.RESOURCES_REPO,
            "bridge_vendor_repo": config.BRIDGE_VENDOR_REPO,
            "bridge_vendor_ref": config.BRIDGE_VENDOR_REF,
            "prod_sync_extras": list(config.PROD_SYNC_EXTRAS),
        },
        "resources": {
            "path": RESOURCES_DIR.name,
            "head": resources_head,
        },
        "bridge_vendor": {
            "path": config.BRIDGE_VENDOR_DIR.as_posix(),
            "head": bridge_vendor_head,
        },
        "plugins": dict(sorted(plugin_heads.items())),
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return fingerprint, material


def _get_prod_setup_state() -> dict[str, Any]:
    state = SYNC_STATE_MANAGER.read()
    section = state.get(PROD_SETUP_STATE_KEY)
    return section if isinstance(section, dict) else {}


def _save_prod_setup_state(
    *,
    lock_fingerprint: str,
    fingerprint_material: dict[str, Any],
    plugin_heads: dict[str, str],
    resources_head: str,
    bridge_vendor_head: str,
) -> None:
    state = SYNC_STATE_MANAGER.read()
    state[PROD_SETUP_STATE_KEY] = {
        "lock_fingerprint": lock_fingerprint,
        "fingerprint_material": fingerprint_material,
        "plugin_heads": dict(sorted(plugin_heads.items())),
        "resources_head": resources_head,
        "bridge_vendor_head": bridge_vendor_head,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    SYNC_STATE_MANAGER.write(state)


def _should_regenerate_lock(lock_fingerprint: str) -> tuple[bool, str]:
    current_state = _get_prod_setup_state()
    if not config.LOCK_FILE.exists():
        return True, f"'{config.LOCK_FILE.name}' 不存在"
    if current_state.get("lock_fingerprint") != lock_fingerprint:
        return True, (
            "检测到 pyproject / manage.toml / resources / bridge vendor 发生变化"
        )
    return False, "部署输入未变化，直接复用现有锁文件"


def _build_prod_sync_args() -> list[str]:
    args = ["sync", "--frozen"]
    for extra in config.PROD_SYNC_EXTRAS:
        args.extend(["--extra", extra])
    return args


def _managed_branch_for_path(path: Path) -> str:
    if path == config.PROJECT_ROOT:
        return config.PROJECT_BRANCH
    return config.PLUGIN_BRANCH


def _reexec_latest_nbm(
    *,
    command: str,
    extra_args: list[str] | None = None,
    verbose: int = 0,
    scope: str = config.DEFAULT_SCOPE,
) -> None:
    cmd = ["uv", "run", "--no-project", "nbm.py"]
    if verbose > 0:
        cmd.extend(["-v"] * verbose)
    if scope != config.DEFAULT_SCOPE:
        cmd.extend(["--scope", scope])
    cmd.append(command)
    if extra_args:
        cmd.extend(extra_args)

    config.logger.info(f"♻️ Re-executing latest nbm.py: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=config.PROJECT_ROOT)
    except FileNotFoundError as exc:
        raise CommandError(
            "Unable to re-execute nbm.py because `uv` is not available in PATH."
        ) from exc
    raise SystemExit(result.returncode)


class CommandBase:
    """Base class for all commands, handling argument parsing and target selection."""

    description: str = "No description provided."

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.scope = getattr(args, "scope", config.DEFAULT_SCOPE)
        self.target_paths = self._get_target_paths()

    def _get_target_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.scope in ["all", "plugins"]:
            config.PLUGINS_SRC_DIR.mkdir(parents=True, exist_ok=True)
            paths.extend(
                p for p in config.PLUGINS_SRC_DIR.iterdir() if (p / ".git").is_dir()
            )
        if self.scope in ["all", "root"] and (config.PROJECT_ROOT / ".git").is_dir():
            paths.insert(0, config.PROJECT_ROOT)
        return sorted(paths)

    def execute(self) -> Any:
        raise NotImplementedError


class ConcurrentCommand(CommandBase):
    """Base class for commands that run tasks concurrently across multiple repos."""

    def worker(self, path: Path) -> dict:
        raise NotImplementedError

    def execute(self) -> dict:
        config.logger.info(
            "🚀 "
            f"{self.description} "
            f"(Scope: {self.scope}, Concurrency: {config.MAX_WORKERS})"
        )
        if not self.target_paths:
            config.logger.info("🤷‍♀️ No Git repositories found in the specified scope.")
            return {}
        results = {}
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
            future_map = {executor.submit(self.worker, p): p for p in self.target_paths}
            p_bar = tqdm(
                as_completed(future_map), total=len(future_map), desc=self.description
            )
            for future in p_bar:
                path = future_map[future]
                p_bar.set_postfix_str(f"Processing {path.name}...")
                try:
                    if res := future.result():
                        results[path.name] = res
                except Exception as exc:
                    config.logger.error(
                        f"CRITICAL: Worker for {path.name} failed: {exc}",
                        exc_info=True,
                    )
        return results


class SetupCommand(CommandBase):
    description = "【首次运行】初始化 nbm 的工作环境"

    def execute(self) -> None:
        project.check_and_setup_configs()
        config.logger.info("✅ Configuration files are ready.")


class InitCommand(CommandBase):
    description = "【项目初始化】根据 plugins.txt 构建整个项目"

    def execute(self) -> None:
        project.check_and_setup_configs()
        ensure_venv_exists()

        urls = _read_plugin_urls()

        successful_paths: list[Path] = []
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
            future_map = {
                ex.submit(project.setup_plugin_repo, url): url for url in urls
            }
            for future in tqdm(
                as_completed(future_map),
                total=len(urls),
                desc="Setting up repositories",
            ):
                status, path = future.result()
                if path and status in ["success", "exists"]:
                    successful_paths.append(path)

        if not successful_paths:
            config.logger.warning(
                "⚠️ No plugin repositories were successfully set up. "
                "Continuing with sidecar/runtime initialization only."
            )
        else:
            config.logger.info(
                "\n📦 Adding downloaded plugins as editable dependencies..."
            )
            for path in tqdm(successful_paths, desc="Adding dependencies"):
                try:
                    project.add_local_dependency(path)
                except CommandError as e:
                    config.logger.error(
                        f"\n❌ Failed to add dependency for '{path.name}': {e}"
                    )
                    config.logger.info(
                        "Aborting. Please fix the issue and run 'init' again."
                    )
                    sys.exit(1)

        _sync_sidecar_runtime()

        config.logger.info(
            "\n⚡️ All dependencies added. "
            f"Generating lock file '{config.LOCK_FILE.name}'..."
        )
        try:
            process.uv_streamed(["lock"], config.PROJECT_ROOT)
        except CommandError as e:
            config.logger.error(
                f"\n❌ Dependency resolution failed! There might be conflicts.\n{e}"
            )
            config.logger.info("👉 Run 'nbm diagnose' to analyze conflicts.")
            sys.exit(1)

        if getattr(self.args, "install", False):
            config.logger.info("\n🔧 Syncing virtual environment...")
            process.uv_streamed(["sync", "--all-extras"], config.PROJECT_ROOT)

        config.logger.info("\n🎉 Project initialization complete!")


class AddCommand(CommandBase):
    description = "【添加新插件】向项目中添加一个全新的插件"

    def execute(self) -> None:
        url = self.args.plugin_url
        status, path = project.setup_plugin_repo(url)
        if not path or status == "failed":
            config.logger.error(f"❌ Failed to add plugin from {url}.")
            return

        try:
            project.add_local_dependency(path)
        except FileNotFoundError as e:
            config.logger.error(f"❌ Validation failed for '{path.name}':\n{e}")
            config.logger.info(
                "💡 Tip: The plugin folder was cloned, "
                "but you need to fix the packaging issue "
                "then run 'nbm init' to add it as a dependency."
            )
            return
        except CommandError as e:
            config.logger.error(f"❌ Failed to add dependency for '{path.name}': {e}")
            return

        project.update_plugins_list(url=url, action="add")
        if self.args.install:
            config.logger.info(
                "\n--install flag detected. Performing full environment update..."
            )
            try:
                config.logger.info("  - Step 1/2: Locking dependencies...")
                process.uv_streamed(["lock"], config.PROJECT_ROOT)

                config.logger.info("  - Step 2/2: Syncing environment...")
                process.uv_streamed(["sync", "--all-extras"], config.PROJECT_ROOT)

                config.logger.info(
                    f"\n🎉 Plugin '{path.name}' added and installed successfully!"
                )
            except CommandError as e:
                config.logger.error(f"\n❌ Environment update failed: {e}")
                config.logger.info(
                    "💡 Please try running 'nbm init --install' "
                    "to fix potential issues."
                )
        else:
            config.logger.info(
                f"\n🎉 Plugin '{path.name}' added successfully! "
                "Run 'nbm init --install' or 'uv sync' "
                "to update your environment."
            )


class RemoveCommand(CommandBase):
    description = "【移除插件】从项目中安全地移除一个插件"

    def execute(self) -> None:
        ensure_venv_exists()
        plugin_name = self.args.plugin_name

        if not project.remove_dependency(plugin_name):
            return

        project.update_plugins_list(plugin_name=plugin_name, action="remove")

        plugin_path = config.PLUGINS_SRC_DIR / plugin_name
        if plugin_path.exists():
            if self.args.force:
                config.logger.warning(f"🚨 Force removing directory: {plugin_path}")
                shutil.rmtree(plugin_path)
            else:
                rel_path = plugin_path.relative_to(config.PROJECT_ROOT).as_posix()
                config.logger.warning(
                    f"🔔 Please manually remove the plugin folder: rm -rf {rel_path}"
                )

        config.logger.info("\n🎉 Plugin removal process finished.")
        config.logger.info(
            "💡 IMPORTANT: Run 'uv sync' to update your virtual environment."
        )


class PackageCommand(CommandBase):
    description = "【生产打包】生成用于生产环境的部署文件"

    def execute(self) -> None:
        branch = self.args.branch or config.PLUGIN_BRANCH
        project.create_production_package(branch)


class StatusCommand(ConcurrentCommand):
    description = "【状态检查】快速概览所有仓库的 Git 状态"

    def worker(self, path: Path) -> dict:
        try:
            branch = git.get_current_branch(path)
            dirty_str = "⚠️  Dirty" if git.is_workspace_dirty(path) else "✅ Clean"
            process.git(["fetch", "origin", branch], path, check=False, quiet=True)
            ahead = process.git(
                ["rev-list", "--count", f"origin/{branch}..HEAD"], path, quiet=True
            )
            behind = process.git(
                ["rev-list", "--count", f"HEAD..origin/{branch}"], path, quiet=True
            )
            status = f"({branch}) | {dirty_str} | Ahead: {ahead}, Behind: {behind}"
            config.logger.info(f"  - {path.name:<30} {status}")
        except CommandError as e:
            config.logger.warning(f"  - {path.name:<30} ERROR: {e}")
        return {}


class CommitCommand(ConcurrentCommand):
    description = "【一键提交】将所有仓库中的修改以一个统一的消息和ID进行提交"

    def execute(self) -> dict:
        timestamp = str(time.time()).encode()
        msg_bytes = self.args.message.encode()
        self.change_id = f"I{hashlib.sha1(timestamp + msg_bytes).hexdigest()[:10]}"
        results = super().execute()
        config.logger.info(
            f"\n✨ Successfully committed with Change-ID: {self.change_id}"
        )
        return results

    def worker(self, path: Path) -> dict:
        if not git.is_workspace_dirty(path):
            return {}
        full_message = f"{self.args.message}\n\nChange-ID: {self.change_id}"
        try:
            process.git(["add", "."], path)
            process.git(["commit", "-m", full_message], path)
            config.logger.info(f"  - ✅ Committed changes in {path.name}")
            return {"committed": True}
        except CommandError as e:
            config.logger.error(f"  - ❌ Failed to commit in {path.name}: {e}")
            return {"committed": False}


class SyncCommand(ConcurrentCommand):
    description = "【核心同步】与上游仓库保持同步，并管理你的本地修改"

    def worker(self, path: Path) -> dict[str, Any]:
        repo_name = path.name if path != config.PROJECT_ROOT else "root project"
        target_branch = _managed_branch_for_path(path)
        try:
            current_branch = git.get_current_branch(path)
            if current_branch != target_branch:
                config.logger.info(
                    f"  - 🔄 Skipping {repo_name} (not on '{target_branch}' branch)"
                )
                return {"status": "skipped", "reason": "wrong branch"}

            process.git(["fetch", "upstream", "--prune"], path, quiet=True)
            process.git(["fetch", "origin", "--prune"], path, quiet=True)

            url = process.git(["remote", "get-url", "upstream"], path)
            repo_full_name = git.get_repo_name_from_url(url)
            main_branch = (
                git.get_default_branch(repo_full_name) if repo_full_name else "main"
            )

            config.logger.info(
                f"  - 🧬 Rebasing {repo_name} onto upstream/{main_branch}..."
            )
            process.git(["rebase", f"upstream/{main_branch}"], path)

            if getattr(self.args, "push", False):
                config.logger.info(
                    f"  - 🚀 Pushing {repo_name} to origin/{target_branch}..."
                )
                process.git(
                    ["push", "--force-with-lease", "origin", target_branch], path
                )

            return {"status": "success"}

        except CommandError as e:
            config.logger.error(f"  - ❌ Failed to sync {repo_name}:\n{e}")
            config.logger.info(
                f"  💡 To resolve, go to '{path}' and manually run "
                "'git rebase --abort' or fix conflicts."
            )
            return {"status": "failed"}


class PushCommand(ConcurrentCommand):
    description = "【手动推送】将所有仓库的当前分支推送到各自的 origin"

    def worker(self, path: Path) -> dict[str, Any]:
        repo_name = path.name
        try:
            current_branch = git.get_current_branch(path)
            config.logger.info(f"  - 🚀 Pushing {repo_name} ({current_branch})...")
            if self.args.force:
                process.git(
                    ["push", "--force-with-lease", "origin", current_branch], path
                )
            else:
                process.git(["push", "origin", current_branch], path)
            return {"status": "success"}
        except CommandError as e:
            config.logger.error(f"  - ❌ Failed to push {repo_name}:\n{e}")
            return {"status": "failed"}


class CheckoutCommand(ConcurrentCommand):
    description = "【批量切换分支】在所有仓库中切换到指定分支"

    def worker(self, path: Path) -> dict[str, Any]:
        repo_name = path.name
        branch = self.args.branch_name
        try:
            if self.args.create_new:
                config.logger.info(
                    f"  - ✨ Creating branch '{branch}' in {repo_name}..."
                )
                process.git(["checkout", "-b", branch], path)
            else:
                config.logger.info(
                    f"  - 🔄 Switching to branch '{branch}' in {repo_name}..."
                )
                process.git(["checkout", branch], path)
            return {"status": "success"}
        except CommandError as e:
            config.logger.error(f"  - ❌ Failed to checkout in {repo_name}:\n{e}")
            return {"status": "failed"}


class CleanupBranchesCommand(ConcurrentCommand):
    description = "【仓库维护】批量删除所有插件 Fork 仓库中的指定远程分支"

    def worker(self, path: Path) -> dict[str, Any]:
        if path == config.PROJECT_ROOT:
            return {"status": "skipped", "reason": "root project"}

        repo_name = path.name
        branch = self.args.branch_name
        try:
            remote_branch_exists = process.git(
                ["ls-remote", "--heads", "origin", branch],
                path,
                check=False,
                quiet=True,
            )
            if remote_branch_exists:
                config.logger.info(
                    f"  - 🗑️ Deleting remote branch 'origin/{branch}' in {repo_name}..."
                )
                process.git(["push", "origin", "--delete", branch], path)
                return {"status": "deleted"}
            return {"status": "not_found"}
        except CommandError as e:
            config.logger.error(f"  - ❌ Failed to delete branch in {repo_name}:\n{e}")
            return {"status": "failed"}


class DiagnoseCommand(CommandBase):
    description = "【依赖诊断】分析并报告依赖冲突"

    def execute(self) -> None:
        ensure_venv_exists()
        config.logger.info("🩺 Running dependency resolver to diagnose conflicts...")
        result = process.uv(["lock"], config.PROJECT_ROOT, check=False)

        if isinstance(result, subprocess.CompletedProcess) and result.returncode == 0:
            config.logger.info("✅ No dependency conflicts detected!")
            return

        config.logger.error("\n--- ❗ Dependency Conflict Report ---")
        if isinstance(result, subprocess.CompletedProcess) and result.stderr:
            config.logger.error(result.stderr.strip())
        elif isinstance(result, subprocess.TimeoutExpired):
            config.logger.error(
                f"The command timed out after {result.timeout} seconds."
            )
        elif result:
            config.logger.error(
                f"The command failed with an unexpected result: {result}"
            )
        else:
            config.logger.error(
                "Could not get output from uv. Is it installed correctly?"
            )


class ProdSetupCommand(CommandBase):
    description = "【生产部署】同步 resources / 插件源码并准备环境"

    def execute(self) -> None:
        project.check_and_setup_configs()
        ensure_venv_exists()

        urls = _read_plugin_urls()

        config.logger.info(
            f"🧱 Preparing resources repository on '{config.RESOURCES_BRANCH}'..."
        )
        resources_result = _sync_resources_repo_with_report()

        config.logger.info(
            f"🚀 Syncing {len(urls)} plugin repositories on '{config.PLUGIN_BRANCH}'..."
        )
        plugin_heads, plugin_results = _sync_plugin_repositories_with_report(urls)
        bridge_vendor_head, _, _, sidecar_results = _sync_sidecar_runtime_with_report()

        sync_results = [resources_result, *plugin_results, *sidecar_results]
        resources_head = resources_result.head or ""
        repo_failures = _collect_repo_sync_failures(sync_results)

        try:
            lock_fingerprint, fingerprint_material = _build_lock_fingerprint(
                plugin_heads=plugin_heads,
                resources_head=resources_head,
                bridge_vendor_head=bridge_vendor_head,
            )
            should_relock, reason = _should_regenerate_lock(lock_fingerprint)

            if should_relock:
                config.logger.info(
                    f"\n🔁 Regenerating '{config.LOCK_FILE.name}': {reason}"
                )
                process.uv_streamed(["lock"], config.PROJECT_ROOT)
            else:
                config.logger.info(f"\n✅ Reusing '{config.LOCK_FILE.name}': {reason}")

            sync_args = _build_prod_sync_args()
            config.logger.info(f"🔧 Syncing environment with: uv {' '.join(sync_args)}")
            process.uv_streamed(sync_args, config.PROJECT_ROOT)

            if _can_persist_repo_sync_state(sync_results):
                _save_prod_setup_state(
                    lock_fingerprint=lock_fingerprint,
                    fingerprint_material=fingerprint_material,
                    plugin_heads=plugin_heads,
                    resources_head=resources_head,
                    bridge_vendor_head=bridge_vendor_head,
                )
            else:
                config.logger.warning(
                    "⚠️ 部分仓库无法解析当前 HEAD，本次不会覆盖部署缓存状态。"
                )
        finally:
            _log_repo_sync_summary(sync_results)

        if repo_failures:
            raise SystemExit(1)

        config.logger.info("\n🎉 Production setup complete! Environment is ready.")


class DockerInstallCommand(CommandBase):
    description = "【Sidecar部署】同步 sidecar 源码并启动 Docker 容器"

    def execute(self) -> None:
        project.check_and_setup_configs()
        _sync_sidecar_runtime()
        _ensure_sidecar_env_file()

        compose_args = [
            "docker",
            "compose",
            "--env-file",
            config.SIDECAR_ENV_FILE.relative_to(config.PROJECT_ROOT).as_posix(),
            "-f",
            config.SIDECAR_COMPOSE_FILE.relative_to(config.PROJECT_ROOT).as_posix(),
            "up",
            "-d",
            "--build",
        ]
        config.logger.info(f"🐳 Starting sidecar with: {' '.join(compose_args)}")
        process.run_and_stream(compose_args, config.PROJECT_ROOT)
        config.logger.info("\n🎉 Sidecar is ready.")


class UpdateCommand(CommandBase):
    description = "【更新部署】先更新主仓库，再使用最新 nbm 继续部署"

    def execute(self) -> None:
        if getattr(self.args, "resume_after_root", False):
            config.logger.info("♻️ Root project updated. Continuing with prod-setup...")
            ProdSetupCommand(self.args).execute()
            return

        config.logger.info(
            f"🔄 Updating root project from origin/{config.PROJECT_BRANCH}..."
        )
        _update_existing_repo(
            config.PROJECT_ROOT,
            config.PROJECT_BRANCH,
            "主仓库",
            ignored_paths={"uv.lock"},
        )

        _reexec_latest_nbm(
            command="update",
            extra_args=["--resume-after-root"],
            verbose=getattr(self.args, "verbose", 0),
            scope=getattr(self.args, "scope", config.DEFAULT_SCOPE),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--scope",
        choices=["all", "plugins", "root"],
        default=config.DEFAULT_SCOPE,
        help="Command scope (default: %(default)s)",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase output verbosity"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    command_map = {
        "setup": SetupCommand,
        "init": InitCommand,
        "add": AddCommand,
        "remove": RemoveCommand,
        "package": PackageCommand,
        "prod-setup": ProdSetupCommand,
        "update": UpdateCommand,
        "status": StatusCommand,
        "commit": CommitCommand,
        "sync": SyncCommand,
        "push": PushCommand,
        "checkout": CheckoutCommand,
        "cleanup-branches": CleanupBranchesCommand,
        "diagnose": DiagnoseCommand,
    }

    for name, cmd_class in command_map.items():
        subparser = subparsers.add_parser(name, help=cmd_class.description)
        if name == "init":
            subparser.add_argument(
                "--install",
                action="store_true",
                help="Sync environment after initialization",
            )
        elif name == "add":
            subparser.add_argument(
                "plugin_url", help="The GitHub URL of the plugin to add"
            )
            subparser.add_argument(
                "--install",
                action="store_true",
                help="Lock, sync, and install the new plugin immediately.",
            )
        elif name == "remove":
            subparser.add_argument(
                "plugin_name", help="The name of the plugin folder to remove"
            )
            subparser.add_argument(
                "-f",
                "--force",
                action="store_true",
                help="Force delete the plugin source folder",
            )
        elif name == "package":
            subparser.add_argument(
                "--branch",
                help=(
                    "Git branch to use for dependencies "
                    f"(default: {config.PLUGIN_BRANCH})"
                ),
            )
        elif name == "update":
            subparser.add_argument(
                "--resume-after-root",
                action="store_true",
                help=argparse.SUPPRESS,
            )
        elif name == "commit":
            subparser.add_argument(
                "-m",
                "--message",
                required=True,
                help="Commit message",
            )
        elif name == "sync":
            subparser.add_argument(
                "--push", action="store_true", help="Push after successful rebase"
            )
        elif name == "push":
            subparser.add_argument(
                "-f", "--force", action="store_true", help="Use --force-with-lease"
            )
        elif name == "checkout":
            subparser.add_argument("branch_name", help="Branch to switch to or create")
            subparser.add_argument(
                "-b", "--create-new", action="store_true", help="Create if not exists"
            )
        elif name == "cleanup-branches":
            subparser.add_argument(
                "branch_name",
                help="Remote branch to delete from forks",
            )

    docker_parser = subparsers.add_parser(
        "docker", help="Manage sidecar Docker lifecycle"
    )
    docker_subparsers = docker_parser.add_subparsers(
        dest="docker_command",
        required=True,
        help="Docker commands",
    )
    docker_subparsers.add_parser(
        "install",
        help=DockerInstallCommand.description,
    )

    args = parser.parse_args()
    command_key = args.command
    display_command = args.command
    if args.command == "docker":
        command_key = f"docker:{args.docker_command}"
        display_command = f"docker {args.docker_command}"
        command_map[command_key] = DockerInstallCommand

    if args.verbose >= 1:
        for handler in config.logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    try:
        command_instance = command_map[command_key](args)
        line = "=" * 20
        config.logger.info(f"\n{line} Executing: {display_command} {line}")
        command_instance.execute()
        config.logger.info(f"\n{line} Finished: {display_command} {line}")
    except CommandError as e:
        config.logger.error(f"\n💥 A command failed to execute:\n{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        config.logger.warning("\nℹ️ User interrupted the operation.")
        sys.exit(130)
    except Exception:
        config.logger.error(
            "\n💥 An unexpected critical error occurred:", exc_info=True
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
