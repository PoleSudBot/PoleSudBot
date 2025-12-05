#!/usr/bin/env python3
# nbm.py - v1.1 (Refactored & Patched)
"""
A professional-grade management script for fork-based NoneBot2 projects.
Powered by uv and a modular, robust core library.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from tqdm import tqdm

# --- Core Library Imports ---
from nbm_core import config, git, process, project
from nbm_core.exceptions import CommandError


# --- Helper Functions ---
def ensure_venv_exists():
    """Checks for a .venv directory and runs `uv venv` if not found."""
    venv_path = config.PROJECT_ROOT / ".venv"
    if not venv_path.is_dir():
        config.logger.info(
            " Virtual environment not found. Creating one with `uv venv`..."
        )
        try:
            # Use direct subprocess call for bootstrapping before full process setup
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


# def _overwrite_with_editable_installs():
#     """
#     使用可编辑模式强制重新安装所有本地插件。
#     这会覆盖掉 `uv sync` 创建的复制版安装。
#     """
#     config.logger.info("🛠️  Overwriting local plugins with editable mode...")

#     local_plugin_paths = [p for p in config.PLUGINS_SRC_DIR.iterdir() if p.is_dir()]

#     if not local_plugin_paths:
#         config.logger.info("  - No local plugins found to install in editable mode.")
#         return

#     # 构建 uv pip install 命令，一次性安装所有插件以提高效率
#     install_args = ["pip", "install", "--no-deps", "--no-cache-dir"]
#     for plugin_path in local_plugin_paths:
#         install_args.extend(["-e", str(plugin_path)])

#     try:
#         process.uv(install_args, config.PROJECT_ROOT)
#         config.logger.info("  - ✅ All local plugins are now in editable mode.")
#     except CommandError as e:
#         config.logger.error(f"❌ Failed to install local plugins in editable mode: {e}")
#         # 在实际命令中，错误会向上抛出，这里只在直接调用时记录日志
#         raise  # 重新抛出异常，让调用方处理


# --- Command Base Classes ---
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
        if self.scope in ["all", "root"]:
            if (config.PROJECT_ROOT / ".git").is_dir():
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


# --- Command Implementations ---
class SetupCommand(CommandBase):
    description = "【首次运行】初始化 nbm 的工作环境"

    def execute(self) -> None:
        project.check_and_setup_configs()
        config.logger.info("✅ Configuration files are ready.")


class InitCommand(CommandBase):
    description = "【项目初始化】根据 plugins.txt 构建整个项目"

    def execute(self) -> None:
        ensure_venv_exists()  # Ensure foundation is laid first!

        if not config.PLUGINS_LIST_FILE.exists():
            config.logger.warning(
                f"🤷‍♀️ '{config.PLUGINS_LIST_FILE.name}' not found. Aborting."
            )
            return

        urls = [
            ln.strip()
            for ln in config.PLUGINS_LIST_FILE.read_text("utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

        successful_paths: list[Path] = []
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
            f_map = {ex.submit(project.setup_plugin_repo, url): url for url in urls}
            for future in tqdm(
                as_completed(f_map), total=len(urls), desc="Setting up repositories"
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
            # CORRECTED: Use process.uv, not config.uv
            process.uv_streamed(["lock"], config.PROJECT_ROOT)
        except CommandError as e:
            config.logger.error(
                f"\n❌ Dependency resolution failed! There might be conflicts.\n{e}"
            )
            config.logger.info("👉 Run 'nbm diagnose' to analyze conflicts.")
            sys.exit(1)

        if getattr(self.args, "install", False):
            config.logger.info("\n🔧 Syncing virtual environment...")
            # CORRECTED: Use process.uv, not config.uv
            process.uv_streamed(["sync", "--all-extras"], config.PROJECT_ROOT)

        # _overwrite_with_editable_installs()

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
        # --- 捕获新的、更具体的错误 ---
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
                config.logger.info("  - Step 1/3: Locking dependencies...")
                process.uv_streamed(["lock"], config.PROJECT_ROOT)

                config.logger.info("  - Step 2/3: Syncing environment...")
                process.uv_streamed(["sync", "--all-extras"], config.PROJECT_ROOT)

                config.logger.info("  - Step 3/3: Applying editable mode...")
                # _overwrite_with_editable_installs()

                config.logger.info(
                    f"\n🎉 Plugin '{path.name}' added and installed successfully!"
                )
            except CommandError as e:
                config.logger.error(f"\n❌ Environment update failed: {e}")
                config.logger.info(
                    "💡 Please try running 'nbm init --install' to fix potential issues."
                )
        else:
            # --- 原有的提示信息 ---
            config.logger.info(
                f"\n🎉 Plugin '{path.name}' added successfully! Run 'nbm init --install' or 'uv sync' to update your environment."
            )


class RemoveCommand(CommandBase):
    description = "【移除插件】从项目中安全地移除一个插件"

    def execute(self) -> None:
        ensure_venv_exists()
        plugin_name = self.args.plugin_name

        if not project.remove_dependency(plugin_name):
            return  # Error already logged

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
        branch = self.args.branch or config.DEV_BRANCH
        project.create_production_package(branch)


class StatusCommand(ConcurrentCommand):
    description = "【状态检查】快速概览所有仓库的 Git 状态"

    def worker(self, path: Path) -> dict:
        try:
            branch = git.get_current_branch(path)
            dirty_str = "⚠️  Dirty" if git.is_workspace_dirty(path) else "✅ Clean"
            # CORRECTED: Use process.git
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

    # --- FIX 4: Correct method signature and return value ---
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
        try:
            current_branch = git.get_current_branch(path)
            if current_branch != config.DEV_BRANCH:
                config.logger.info(
                    f"  - 🔄 Skipping {repo_name} (not on '{config.DEV_BRANCH}' branch)"
                )
                return {"status": "skipped", "reason": "wrong branch"}

            # 修正：使用 process.git 而不是 config.git
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
                    f"  - 🚀 Pushing {repo_name} to origin/{config.DEV_BRANCH}..."
                )
                process.git(
                    ["push", "--force-with-lease", "origin", config.DEV_BRANCH], path
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
            else:
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

        # --- FINAL FIX: Use isinstance for definitive type narrowing ---
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
            # This branch safely handles other non-CompletedProcess results
            config.logger.error(
                f"The command failed with an unexpected result: {result}"
            )
        else:
            config.logger.error(
                "Could not get output from uv. Is it installed correctly?"
            )


class ProdSetupCommand(CommandBase):
    description = "【生产部署】克隆所有插件的源代码并同步环境"

    def execute(self) -> None:
        ensure_venv_exists()

        if not config.PLUGINS_LIST_FILE.exists():
            config.logger.warning(
                f"🤷‍♀️ '{config.PLUGINS_LIST_FILE.name}' not found. Aborting clone phase."
            )
            return

        urls = [
            ln.strip()
            for ln in config.PLUGINS_LIST_FILE.read_text("utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

        config.logger.info(
            f"🚀 Starting to clone/update {len(urls)} plugin repositories..."
        )
        config.PLUGINS_SRC_DIR.mkdir(parents=True, exist_ok=True)

        for url in tqdm(urls, desc="Cloning plugins"):
            repo_name_with_owner = git.get_repo_name_from_url(url)
            if not repo_name_with_owner:
                config.logger.warning(f"  - Invalid URL, skipping: {url}")
                continue

            plugin_name = repo_name_with_owner.split("/")[-1]
            local_path = config.PLUGINS_SRC_DIR / plugin_name
            fork_repo = f"{config.YOUR_GITHUB_ORG}/{plugin_name}"
            clone_url = f"https://github.com/{fork_repo}.git"

            if local_path.exists():
                # 如果已存在，则更新
                config.logger.info(f"  - Updating existing repo: {plugin_name}")
                try:
                    process.git(
                        ["-C", str(local_path), "pull", "origin", config.DEV_BRANCH],
                        config.PROJECT_ROOT,
                    )
                except CommandError as e:
                    config.logger.error(
                        f"  - ❌ Failed to pull updates for {plugin_name}: {e}"
                    )
            else:
                # 如果不存在，则克隆
                config.logger.info(f"  - Cloning new repo: {plugin_name}")
                try:
                    process.git(
                        [
                            "clone",
                            "--depth=1",
                            "-b",
                            config.DEV_BRANCH,
                            clone_url,
                            str(local_path),
                        ],
                        config.PROJECT_ROOT,
                    )
                except CommandError as e:
                    config.logger.error(f"  - ❌ Failed to clone {plugin_name}: {e}")

        config.logger.info("\n✅ All plugin sources are ready.")
        config.logger.info("🔧 Syncing environment from lock file...")

        try:
            # 直接使用 lock 文件进行同步
            process.uv(["sync", "--all-extras"], config.PROJECT_ROOT)
            config.logger.info("\n🎉 Production setup complete! Environment is ready.")
        except CommandError as e:
            config.logger.error(f"\n❌ Failed to sync environment: {e}")
            sys.exit(1)


# --- Main Application ---
def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--scope",
        choices=["all", "plugins", "root"],
        default=config.DEFAULT_SCOPE,  # 使用 argparse 的 default 机制
        help="Command scope (default: %(default)s)",
    )
    # -------------------------

    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase output verbosity"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    command_map = {
        # 项目生命周期
        "setup": SetupCommand,
        "init": InitCommand,
        "add": AddCommand,
        "remove": RemoveCommand,
        "package": PackageCommand,
        "prod-setup": ProdSetupCommand,
        # 日常开发
        "status": StatusCommand,
        "commit": CommitCommand,
        "sync": SyncCommand,
        # 批量维护与工具
        "push": PushCommand,
        "checkout": CheckoutCommand,
        "cleanup-branches": CleanupBranchesCommand,
        "diagnose": DiagnoseCommand,
    }

    for name, cmd_class in command_map.items():
        p = subparsers.add_parser(name, help=cmd_class.description)
        if name == "init":
            p.add_argument(
                "--install",
                action="store_true",
                help="Sync environment after initialization",
            )
        elif name == "add":
            p.add_argument("plugin_url", help="The GitHub URL of the plugin to add")
            p.add_argument(
                "--install",
                action="store_true",
                help="Lock, sync, and install the new plugin immediately.",
            )
        elif name == "remove":
            p.add_argument(
                "plugin_name", help="The name of the plugin folder to remove"
            )
            p.add_argument(
                "-f",
                "--force",
                action="store_true",
                help="Force delete the plugin source folder",
            )
        elif name == "package":
            p.add_argument(
                "--branch",
                help=f"Git branch to use for dependencies (default: {config.DEV_BRANCH})",
            )
        elif name == "commit":
            p.add_argument("-m", "--message", required=True, help="Commit message")
        elif name == "sync":
            p.add_argument(
                "--push", action="store_true", help="Push after successful rebase"
            )
        elif name == "push":
            p.add_argument(
                "-f", "--force", action="store_true", help="Use --force-with-lease"
            )
        elif name == "checkout":
            p.add_argument("branch_name", help="Branch to switch to or create")
            p.add_argument(
                "-b", "--create-new", action="store_true", help="Create if not exists"
            )
        elif name == "cleanup-branches":
            p.add_argument("branch_name", help="Remote branch to delete from forks")

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
