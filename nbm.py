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
    from tqdm import tqdm

    from nbm_core import config, git, process, project
    from nbm_core.exceptions import CommandError
    from nbm_core.state import StateManager
except ModuleNotFoundError as exc:
    missing = exc.name or "unknown dependency"
    print(f"❌ 缺少运行 nbm 所需依赖：{missing}", file=sys.stderr)
    print(
        "👉 请改用 `uv run --no-project nbm.py <command>` 运行该脚本。",
        file=sys.stderr,
    )
    raise SystemExit(1) from None


SYNC_STATE_MANAGER = StateManager(config.SYNC_STATE_FILE)
PROD_SETUP_STATE_KEY = "prod_setup"
RESOURCES_DIR = config.PROJECT_ROOT / "resources"


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


def _tracked_status_output(cwd: Path) -> str:
    return process.git(["status", "--short", "-uno"], cwd, check=False, quiet=True)


def _ensure_tracked_clean(cwd: Path, label: str) -> None:
    if status_output := _tracked_status_output(cwd):
        raise CommandError(
            f"{label} 存在未提交的已跟踪修改，已停止更新：\n{status_output}"
        )


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
        raise CommandError(f"{label} 的远程分支 origin/{branch} 不存在。")
    if git.local_branch_exists(cwd, branch):
        process.git(["checkout", branch], cwd)
    else:
        process.git(["checkout", "-b", branch, f"origin/{branch}"], cwd)


def _update_existing_repo(cwd: Path, branch: str, label: str) -> str:
    if not (cwd / ".git").is_dir():
        raise CommandError(f"{label} 路径存在，但不是独立 Git 仓库：{cwd}")
    _ensure_tracked_clean(cwd, label)
    process.git(["fetch", "origin", "--prune"], cwd, quiet=True)
    _checkout_target_branch(cwd, branch, label)
    process.git(["pull", "--ff-only", "origin", branch], cwd)
    return git.get_head_commit(cwd)


def _sync_managed_repo(
    *,
    local_path: Path,
    clone_url: str,
    branch: str,
    label: str,
    upstream_url: str | None = None,
) -> str:
    if local_path.exists():
        config.logger.info(f"  - Updating {label}: {local_path.name}")
        _ensure_upstream_remote(local_path, upstream_url)
        return _update_existing_repo(local_path, branch, label)

    config.logger.info(f"  - Cloning {label}: {local_path.name}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    process.git(
        ["clone", "--depth=1", "-b", branch, clone_url, str(local_path)],
        config.PROJECT_ROOT,
    )
    _ensure_upstream_remote(local_path, upstream_url)
    return git.get_head_commit(local_path)


def _sync_resources_repo() -> str:
    return _sync_managed_repo(
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


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_lock_fingerprint(
    *, plugin_heads: dict[str, str], resources_head: str
) -> tuple[str, dict[str, Any]]:
    material = {
        "pyproject_sha256": _file_sha256(config.PROJECT_ROOT / "pyproject.toml"),
        "plugins_txt_sha256": _file_sha256(config.PLUGINS_LIST_FILE),
        "manage": {
            "project_branch": config.PROJECT_BRANCH,
            "resources_branch": config.RESOURCES_BRANCH,
            "plugin_branch": config.PLUGIN_BRANCH,
            "resources_repo": config.RESOURCES_REPO,
            "prod_sync_extras": list(config.PROD_SYNC_EXTRAS),
        },
        "resources": {
            "path": RESOURCES_DIR.name,
            "head": resources_head,
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
) -> None:
    state = SYNC_STATE_MANAGER.read()
    state[PROD_SETUP_STATE_KEY] = {
        "lock_fingerprint": lock_fingerprint,
        "fingerprint_material": fingerprint_material,
        "plugin_heads": dict(sorted(plugin_heads.items())),
        "resources_head": resources_head,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    SYNC_STATE_MANAGER.write(state)


def _should_regenerate_lock(lock_fingerprint: str) -> tuple[bool, str]:
    current_state = _get_prod_setup_state()
    if not config.LOCK_FILE.exists():
        return True, f"'{config.LOCK_FILE.name}' 不存在"
    if current_state.get("lock_fingerprint") != lock_fingerprint:
        return True, "检测到 pyproject / manage.toml / resources / 插件提交发生变化"
    return False, "部署输入未变化，直接复用现有锁文件"


def _build_prod_sync_args() -> list[str]:
    args = ["sync", "--frozen"]
    for extra in config.PROD_SYNC_EXTRAS:
        args.extend(["--extra", extra])
    return args


def _managed_branch_for_path(path: Path) -> str:
    return config.PROJECT_BRANCH if path == config.PROJECT_ROOT else config.PLUGIN_BRANCH


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
            f"🚀 {self.description} (Scope: {self.scope}, Concurrency: {config.MAX_WORKERS})"
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
        ensure_venv_exists()

        urls = _read_plugin_urls()

        successful_paths: list[Path] = []
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
            future_map = {ex.submit(project.setup_plugin_repo, url): url for url in urls}
            for future in tqdm(
                as_completed(future_map), total=len(urls), desc="Setting up repositories"
            ):
                status, path = future.result()
                if path and status in ["success", "exists"]:
                    successful_paths.append(path)

        if not successful_paths:
            config.logger.error(
                "❌ No plugin repositories were successfully set up. Aborting dependency phase."
            )
            return

        config.logger.info("\n📦 Adding downloaded plugins as editable dependencies...")
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

        config.logger.info(
            f"\n⚡️ All dependencies added. Generating lock file '{config.LOCK_FILE.name}'..."
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
                "💡 Tip: The plugin folder was cloned, but you need to fix the packaging issue then run 'nbm init' to add it as a dependency."
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
                    "💡 Please try running 'nbm init --install' to fix potential issues."
                )
        else:
            config.logger.info(
                f"\n🎉 Plugin '{path.name}' added successfully! Run 'nbm init --install' or 'uv sync' to update your environment."
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
                f"  💡 To resolve, go to '{path}' and manually run 'git rebase --abort' or fix conflicts."
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
        ensure_venv_exists()

        urls = _read_plugin_urls()

        config.logger.info(
            f"🧱 Preparing resources repository on '{config.RESOURCES_BRANCH}'..."
        )
        resources_head = _sync_resources_repo()

        config.logger.info(
            f"🚀 Syncing {len(urls)} plugin repositories on '{config.PLUGIN_BRANCH}'..."
        )
        plugin_heads = _sync_plugin_repositories(urls)

        lock_fingerprint, fingerprint_material = _build_lock_fingerprint(
            plugin_heads=plugin_heads,
            resources_head=resources_head,
        )
        should_relock, reason = _should_regenerate_lock(lock_fingerprint)

        if should_relock:
            config.logger.info(f"\n🔁 Regenerating '{config.LOCK_FILE.name}': {reason}")
            process.uv_streamed(["lock"], config.PROJECT_ROOT)
        else:
            config.logger.info(f"\n✅ Reusing '{config.LOCK_FILE.name}': {reason}")

        sync_args = _build_prod_sync_args()
        config.logger.info(f"🔧 Syncing environment with: uv {' '.join(sync_args)}")
        process.uv_streamed(sync_args, config.PROJECT_ROOT)

        _save_prod_setup_state(
            lock_fingerprint=lock_fingerprint,
            fingerprint_material=fingerprint_material,
            plugin_heads=plugin_heads,
            resources_head=resources_head,
        )

        config.logger.info("\n🎉 Production setup complete! Environment is ready.")


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
        _update_existing_repo(config.PROJECT_ROOT, config.PROJECT_BRANCH, "主仓库")

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
                help=f"Git branch to use for dependencies (default: {config.PLUGIN_BRANCH})",
            )
        elif name == "update":
            subparser.add_argument(
                "--resume-after-root",
                action="store_true",
                help=argparse.SUPPRESS,
            )
        elif name == "commit":
            subparser.add_argument("-m", "--message", required=True, help="Commit message")
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
            subparser.add_argument("branch_name", help="Remote branch to delete from forks")

    args = parser.parse_args()

    if args.verbose >= 1:
        for handler in config.logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    try:
        command_instance = command_map[args.command](args)
        line = "=" * 20
        config.logger.info(f"\n{line} Executing: {args.command} {line}")
        command_instance.execute()
        config.logger.info(f"\n{line} Finished: {args.command} {line}")
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
