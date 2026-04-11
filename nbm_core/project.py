# nbm_core/project.py (v1.2 - Type Safe)
"""
High-level functions for managing the NoneBot project and its plugins.
"""

from pathlib import Path
import shutil
from typing import Literal

import tomlkit
from tomlkit.items import Array, Item, Table

from . import config, git, process
from .exceptions import CommandError

SIDECAR_BASE_IMAGE = (
    "docker.cnb.cool/gscore-mirror/docker-sync/astral-uv:"
    "python3.12-bookworm-slim"
)
SIDECAR_PYTHON_INDEX = "https://pypi.org/simple"
SIDECAR_NO_PROXY = (
    "localhost,127.0.0.1,.local,cnb.cool,mirrors.aliyun.com,"
    "pypi.tuna.tsinghua.edu.cn,mirrors.volces.com"
)

MANAGE_TOML_TEMPLATE = """# nbm (NoneBot Manager) 配置文件
# 请根据你的实际情况修改此文件

[manager]
# [必填] 你的 GitHub组织/用户名，用于存放所有插件的 Fork
github_org = "my-bot-workspace"

# [可选] update 命令更新主仓库时使用的分支
project_branch = "dev"

# [可选] resources 仓库使用的分支
resources_branch = "dev"

# [可选] 你希望在插件 Fork 中使用的主要开发分支名
plugin_branch = "dev"

# [兼容旧配置] 未设置 plugin_branch 时将回退到 dev_branch
dev_branch = "dev"

# [可选] resources 仓库地址
resources_repo = "https://github.com/PoleSudBot/resources.git"

# [可选] 存放插件源码的本地目录名
plugins_src_dir = "plugins"

# [可选] 记录插件仓库地址列表的文件名
plugins_list_file = "plugins.txt"

# [可选] 真寻桥接依赖仓库地址
bridge_vendor_repo = "https://github.com/Genshin-bots/nonebot-plugin-genshinuid.git"

# [可选] 真寻桥接依赖固定版本；留空时跟随默认分支最新提交
bridge_vendor_ref = ""

# [可选] sidecar Core 仓库地址
sidecar_core_repo = "https://github.com/Genshin-bots/gsuid_core.git"

# [可选] sidecar Core 固定版本；留空时跟随默认分支最新提交
sidecar_core_ref = ""

# [可选] sidecar 运行时目录
sidecar_runtime_dir = "sidecar/.runtime"

# [可选] sidecar 插件清单文件
sidecar_plugins_file = "sidecar/plugins.toml"

# [可选] 生产部署时额外安装的 extras 列表
prod_sync_extras = []

# [可选] 并发执行任务时使用的最大线程数
max_workers = 8

# [可选] 执行外部命令（如 git, uv）的超时时间（秒）
command_timeout = 1200
"""
PLUGINS_TXT_TEMPLATE = "# 请在此处逐行输入插件的 GitHub 仓库地址\n"
SIDECAR_ENV_TEMPLATE = """# Sidecar 容器环境配置
SIDECAR_BIND_HOST=127.0.0.1
SIDECAR_PORT=8765

# 如需代理，可取消注释并填写
# GSCORE_HTTP_PROXY=http://127.0.0.1:7890
# GSCORE_HTTPS_PROXY=http://127.0.0.1:7890
"""
SIDECAR_PLUGINS_TEMPLATE = """# Sidecar 插件清单
# ref 可选；留空或删除时将跟随默认分支最新提交
# packages / playwright_browsers 可选；用于 sidecar 容器额外依赖

[[plugins]]
repo = "https://github.com/jiluoQAQ/RocomUID.git"

[[plugins]]
repo = "https://github.com/Loping151/XutheringWavesUID.git"
packages = ["playwright", "opencv-python", "fonttools"]
playwright_browsers = ["chromium"]
"""
SIDECAR_COMPOSE_TEMPLATE = """services:
  external-bot-sidecar:
    build:
      context: ./.runtime/gsuid_core
      target: ${GSCORE_BUILD_TARGET:-runtime}
      args:
        GSCORE_BASE_IMAGE: ${GSCORE_BASE_IMAGE:-""" + SIDECAR_BASE_IMAGE + """}
        GSCORE_PYTHON_INDEX: ${GSCORE_PYTHON_INDEX:-""" + SIDECAR_PYTHON_INDEX + """}
    image: external-bot-sidecar:${GSCORE_BUILD_TARGET:-runtime}
    container_name: external-bot-sidecar
    ports:
      - "${SIDECAR_BIND_HOST:-127.0.0.1}:${SIDECAR_PORT:-8765}:8765"
    volumes:
      - ./.runtime/gsuid_core:/gsuid_core
      - sidecar-venv:/venv
      - ./.runtime/sidecar_dependencies.json:/sidecar_dependencies.json:ro
      - ./start_sidecar.py:/start_sidecar.py:ro
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      PYTHONUNBUFFERED: "1"
      PLAYWRIGHT_BROWSERS_PATH: /venv/ms-playwright
      UV_INDEX: ${GSCORE_PYTHON_INDEX:-}
      UV_NO_CONFIG: ${UV_NO_CONFIG:-0}
      http_proxy: ${GSCORE_HTTP_PROXY:-}
      https_proxy: ${GSCORE_HTTPS_PROXY:-}
      no_proxy: ${GSCORE_NO_PROXY:-""" + SIDECAR_NO_PROXY + """}
    command:
      - /venv/bin/python
      - /start_sidecar.py

volumes:
  sidecar-venv:
"""
SIDECAR_README_TEMPLATE = """# Sidecar

该目录承载 gsuid_core sidecar 的部署文件与插件清单。

常用命令：

```bash
uv run --no-project nbm.py init --install
uv run --no-project nbm.py docker install
```
"""
SIDECAR_STARTER_TEMPLATE = """from __future__ import annotations

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


CONFIG_PATH = Path("/gsuid_core/data/config.json")
DEPENDENCIES_PATH = Path("/sidecar_dependencies.json")
DEFAULT_TRUSTED_IPS = ["localhost", "::1", "127.0.0.1"]
PLAYWRIGHT_BROWSERS_PATH = Path(
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/venv/ms-playwright")
)


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text("utf-8"))


def _resolve_default_gateway() -> str | None:
    route_path = Path("/proc/net/route")
    if not route_path.exists():
        return None

    for line in route_path.read_text("utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) <= 2 or fields[1] != "00000000":
            continue
        gateway = fields[2]
        return ".".join(str(int(gateway[i : i + 2], 16)) for i in range(6, -2, -2))
    return None


def _prepare_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = _load_json_dict(CONFIG_PATH)

    trusted_ips = config.get("TRUSTED_IPS")
    if not isinstance(trusted_ips, list):
        trusted_ips = []

    merged_ips = list(dict.fromkeys([*DEFAULT_TRUSTED_IPS, *trusted_ips]))
    if gateway_ip := _resolve_default_gateway():
        merged_ips = list(dict.fromkeys([*merged_ips, gateway_ip]))

    config["TRUSTED_IPS"] = merged_ips
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=4),
        "utf-8",
    )


def _load_dependency_manifest() -> dict[str, Any]:
    manifest = _load_json_dict(DEPENDENCIES_PATH)
    plugins = manifest.get("plugins", [])
    if not isinstance(plugins, list):
        raise RuntimeError("sidecar dependency manifest is malformed: plugins must be a list")
    return {"plugins": plugins}


def _collect_string_values(data: Any, field_name: str) -> list[str]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise RuntimeError(
            f"sidecar dependency manifest is malformed: {field_name} must be a list"
        )
    values: list[str] = []
    for item in data:
        if not isinstance(item, str):
            raise RuntimeError(
                f"sidecar dependency manifest is malformed: {field_name} items must be strings"
            )
        value = item.strip()
        if value:
            values.append(value)
    return list(dict.fromkeys(values))


def _collect_runtime_dependencies() -> tuple[list[str], list[str]]:
    manifest = _load_dependency_manifest()
    packages: list[str] = []
    browsers: list[str] = []
    for plugin in manifest["plugins"]:
        if not isinstance(plugin, dict):
            raise RuntimeError(
                "sidecar dependency manifest is malformed: plugin entries must be objects"
            )
        packages.extend(_collect_string_values(plugin.get("packages"), "packages"))
        browsers.extend(
            _collect_string_values(
                plugin.get("playwright_browsers"),
                "playwright_browsers",
            )
        )
    return list(dict.fromkeys(packages)), list(dict.fromkeys(browsers))


def _is_package_installed(package: str) -> bool:
    try:
        metadata.distribution(package)
    except metadata.PackageNotFoundError:
        return False
    return True


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True, env=os.environ.copy())


def _ensure_python_packages(packages: list[str]) -> None:
    missing = [package for package in packages if not _is_package_installed(package)]
    if not missing:
        return
    print(
        "[sidecar] installing python packages: " + ", ".join(missing),
        flush=True,
    )
    _run_command(["uv", "pip", "install", "--python", sys.executable, *missing])


def _is_playwright_browser_installed(browser: str) -> bool:
    if not PLAYWRIGHT_BROWSERS_PATH.exists():
        return False
    return any(
        path.is_dir() and path.name.startswith(f"{browser}-")
        for path in PLAYWRIGHT_BROWSERS_PATH.iterdir()
    )


def _ensure_playwright_browsers(browsers: list[str]) -> None:
    if not browsers:
        return
    if not _is_package_installed("playwright"):
        raise RuntimeError(
            "playwright browsers requested but the playwright package is not installed"
        )

    missing = [browser for browser in browsers if not _is_playwright_browser_installed(browser)]
    if not missing:
        return

    PLAYWRIGHT_BROWSERS_PATH.mkdir(parents=True, exist_ok=True)
    for browser in missing:
        print(f"[sidecar] installing playwright browser: {browser}", flush=True)
        _run_command([sys.executable, "-m", "playwright", "install", browser])


def _prepare_runtime_dependencies() -> None:
    packages, browsers = _collect_runtime_dependencies()
    _ensure_python_packages(packages)
    _ensure_playwright_browsers(browsers)


def main() -> None:
    _prepare_config()
    _prepare_runtime_dependencies()
    os.execvp(
        "uv",
        [
            "uv",
            "run",
            "--python",
            "/venv/bin/python",
            "core",
            "--host",
            "0.0.0.0",
        ],
    )


if __name__ == "__main__":
    main()
"""


def _get_package_name_from_plugin_dir(plugin_path: Path) -> str | None:
    """
    Reads a plugin's pyproject.toml to find its actual package name.
    Handles both [project] and [tool.poetry] sections.
    """
    pyproject_file = plugin_path / "pyproject.toml"
    if not pyproject_file.exists():
        return None
    try:
        data = tomlkit.parse(pyproject_file.read_text("utf-8"))
        # 兼容 [project] 和 [tool.poetry]
        project_section = data.get("project", data.get("tool", {}).get("poetry", {}))
        if name := project_section.get("name"):
            return str(name)
    except Exception as e:
        config.logger.warning(
            f"Could not parse package name from '{pyproject_file}': {e}"
        )
    return None


def check_and_setup_configs() -> None:
    # ... (This function is correct, no changes needed) ...
    is_first_run = not (config.PROJECT_ROOT / "manage.toml").exists()
    files_to_check = {
        "manage.toml": MANAGE_TOML_TEMPLATE,
        "plugins.txt": PLUGINS_TXT_TEMPLATE,
    }
    for filename, content in files_to_check.items():
        path = config.PROJECT_ROOT / filename
        if not path.exists():
            path.write_text(content.strip() + "\n", "utf-8")

    sidecar_files = {
        config.SIDECAR_ENV_EXAMPLE_FILE: SIDECAR_ENV_TEMPLATE,
        config.SIDECAR_PLUGINS_FILE: SIDECAR_PLUGINS_TEMPLATE,
        config.SIDECAR_COMPOSE_FILE: SIDECAR_COMPOSE_TEMPLATE,
        config.SIDECAR_DIR / "start_sidecar.py": SIDECAR_STARTER_TEMPLATE,
        config.SIDECAR_DIR / "README.md": SIDECAR_README_TEMPLATE,
    }
    for path, content in sidecar_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content.strip() + "\n", "utf-8")

    if is_first_run:
        config.logger.info("⚠️  检测到首次运行，已为您创建模板配置文件。")
        config.logger.info("👉 请打开并修改 'manage.toml'（特别是 'github_org'），")
        config.logger.info(
            "   并编辑 'plugins.txt' / 'sidecar/plugins.toml'，然后重新运行。"
        )


def setup_plugin_repo(
    url: str,
) -> tuple[Literal["success", "exists", "failed"], Path | None]:
    # ... (This function is correct, no changes needed) ...
    repo_name = git.get_repo_name_from_url(url)
    if not repo_name:
        config.logger.error(f"  - ❌ Invalid GitHub URL: {url}")
        return "failed", None
    plugin_name = repo_name.split("/")[-1]
    local_path = config.PLUGINS_SRC_DIR / plugin_name
    if local_path.exists():
        config.logger.info(f"  - ✅ Plugin '{plugin_name}' already exists locally.")
        return "exists", local_path
    config.logger.info(f"--- Setting up new plugin: {plugin_name} ---")
    try:
        fork_repo = f"{config.YOUR_GITHUB_ORG}/{plugin_name}"
        if not process.gh(
            ["repo", "view", fork_repo], config.PROJECT_ROOT, check=False, quiet=True
        ):
            config.logger.info(f"  - Forking {repo_name}...")
            process.gh(
                ["repo", "fork", repo_name, f"--org={config.YOUR_GITHUB_ORG}"],
                config.PROJECT_ROOT,
            )
        config.logger.info(f"  - Cloning {fork_repo}...")
        clone_url = f"https://github.com/{fork_repo}.git"
        config.PLUGINS_SRC_DIR.mkdir(parents=True, exist_ok=True)
        process.git(["clone", clone_url, str(local_path)], config.PROJECT_ROOT)
        process.git(["remote", "add", "upstream", url], local_path, check=False)
        config.logger.info(f"  - Setting up '{config.PLUGIN_BRANCH}' branch...")
        remote_dev_exists = process.git(
            ["ls-remote", "--heads", "origin", f"refs/heads/{config.PLUGIN_BRANCH}"],
            local_path,
            check=False,
            quiet=True,
        )
        if remote_dev_exists:
            process.git(["checkout", config.PLUGIN_BRANCH], local_path)
        else:
            main_branch = git.get_default_branch(repo_name)
            process.git(["fetch", "upstream", main_branch], local_path, quiet=True)
            process.git(
                ["checkout", "-b", config.PLUGIN_BRANCH, f"upstream/{main_branch}"],
                local_path,
            )
            process.git(["push", "-u", "origin", config.PLUGIN_BRANCH], local_path)
        return "success", local_path
    except CommandError as e:
        config.logger.error(f"  - 💥 Failed to set up {plugin_name}: {e}")
        if local_path.exists():
            shutil.rmtree(local_path)
        return "failed", None


def add_local_dependency(plugin_path: Path) -> None:
    """
    将本地插件作为可编辑依赖添加到 pyproject.toml。
    此函数会同时更新 [project.dependencies] 和 [tool.uv.sources]。
    """
    main_pyproject_path = config.PROJECT_ROOT / "pyproject.toml"
    if not main_pyproject_path.exists():
        raise FileNotFoundError("主项目的 pyproject.toml 未找到！")

    # 1. 智能解析包名，并规范化为小写+中划线
    pkg_name = _get_package_name_from_plugin_dir(plugin_path)
    if not pkg_name:
        pkg_name = plugin_path.name  # 回退到文件夹名
        config.logger.warning(
            f"⚠️  无法从 '{plugin_path.name}' 的 pyproject.toml 解析包名。"
            f"将回退使用文件夹名: '{pkg_name}'"
        )

    # 规范化包名 (PEP 规范推荐小写)
    normalized_pkg_name = pkg_name.replace("_", "-").lower()

    # 2. 使用 tomlkit 原子化地写入
    doc = tomlkit.parse(main_pyproject_path.read_text("utf-8"))

    # 3. 更新 [project.dependencies]
    project_table = doc.setdefault("project", tomlkit.table())
    dependencies = project_table.setdefault("dependencies", tomlkit.array())

    # 检查依赖是否已存在，避免重复
    if not any(str(d).strip().startswith(normalized_pkg_name) for d in dependencies):
        dependencies.add_line(normalized_pkg_name)
        dependencies.multiline(True)
        config.logger.info(
            f"  - ✅ 已将 '{normalized_pkg_name}' 添加到 [project.dependencies]"
        )
    else:
        config.logger.debug(
            f"  - 依赖 '{normalized_pkg_name}' 已存在于 [project.dependencies]，跳过。"
        )

    # 4. 更新 [tool.uv.sources]
    tool_table = doc.setdefault("tool", tomlkit.table())
    uv_table = tool_table.setdefault("uv", tomlkit.table())
    sources_table = uv_table.setdefault("sources", tomlkit.table())

    relative_path = plugin_path.relative_to(config.PROJECT_ROOT).as_posix()

    # 创建一个新的 Table 来表示 {path = "...", editable = true}
    source_entry = tomlkit.inline_table()
    source_entry.update({"path": relative_path, "editable": True})

    # 检查是否已存在
    if sources_table.get(normalized_pkg_name) != source_entry:
        sources_table[normalized_pkg_name] = source_entry
        config.logger.info(
            f"  - ✅ 已将 '{normalized_pkg_name}' 的可编辑路径添加到 [tool.uv.sources]"
        )

    # 5. 写回文件
    main_pyproject_path.write_text(tomlkit.dumps(doc), "utf-8")


def remove_dependency(pkg_name: str) -> bool:
    """
    Removes a dependency and its corresponding workspace entry from pyproject.toml.

    This function performs a complete removal:
    1. Reads the plugin's real package name from its own pyproject.toml.
    2. Uses `uv remove` to delete the dependency from [project].dependencies.
    3. Manually cleans up the entry from [tool.uv.workspace].members.
    """
    plugin_path = config.PLUGINS_SRC_DIR / pkg_name
    pyproject_file = plugin_path / "pyproject.toml"

    # --- Step 1: Determine the real package name to remove ---
    if pyproject_file.exists():
        try:
            data = tomlkit.parse(pyproject_file.read_text("utf-8"))
            project_section = data.get(
                "project", data.get("tool", {}).get("poetry", {})
            )
            real_pkg_name = project_section.get("name", pkg_name)
            pkg_name_to_remove = str(real_pkg_name).replace("_", "-")
            config.logger.info(
                "Detected package name "
                f"'{pkg_name_to_remove}' from plugin's pyproject.toml."
            )
        except Exception:
            pkg_name_to_remove = pkg_name.replace("_", "-")
            config.logger.warning(
                "Could not parse package name. "
                f"Falling back to folder name '{pkg_name}'."
            )
    else:
        pkg_name_to_remove = pkg_name.replace("_", "-")

    # --- Step 2: Use `uv` to remove the dependency ---
    try:
        process.uv(["remove", pkg_name_to_remove], config.PROJECT_ROOT, check=True)
    except CommandError as e:
        if "not found in dependencies" in str(e):
            config.logger.warning(
                f"⚠️ Dependency '{pkg_name_to_remove}' "
                "not found in project dependencies."
            )
            # We continue, as we still might need to clean up the workspace.
        else:
            config.logger.error(
                f"❌ Failed to remove dependency '{pkg_name_to_remove}':\n{e}"
            )
            return False

    # --- Step 3: Manually remove from [tool.uv.workspace] ---
    try:
        pyproject_path = config.PROJECT_ROOT / "pyproject.toml"
        doc = tomlkit.parse(pyproject_path.read_text("utf-8"))

        workspace = doc.get("tool", {}).get("uv", {}).get("workspace", {})
        members = workspace.get("members")

        if members and isinstance(members, Array):
            entry_to_remove = f"{config.settings.plugins_src_dir}/{pkg_name}"

            new_members = tomlkit.array()
            removed = False
            for member in members:
                if str(member).strip() != entry_to_remove:
                    new_members.add_line(member)
                else:
                    removed = True

            if removed:
                # This complex access is needed for tomlkit to preserve structure
                doc["tool"]["uv"]["workspace"]["members"] = new_members  # type: ignore
                pyproject_path.write_text(tomlkit.dumps(doc), "utf-8")
                config.logger.info(
                    f"✅ Cleaned up '{entry_to_remove}' from workspace members."
                )

    except Exception as e:
        config.logger.warning(
            f"⚠️ Could not automatically clean up workspace members: {e}"
        )
        config.logger.warning("   Please check your pyproject.toml manually.")

    return True


def update_plugins_list(
    *,
    url: str | None = None,
    plugin_name: str | None = None,  # 这里的 plugin_name 是文件夹名
    action: Literal["add", "remove"],
) -> None:
    if not config.PLUGINS_LIST_FILE.exists() and action == "remove":
        return

    lines = (
        config.PLUGINS_LIST_FILE.read_text("utf-8").splitlines()
        if config.PLUGINS_LIST_FILE.exists()
        else []
    )

    if action == "add" and url:
        if url not in lines:
            lines.append(url)
            config.logger.info(f"✅ Added '{url}' to {config.PLUGINS_LIST_FILE.name}.")

    elif action == "remove" and plugin_name:
        original_count = len(lines)

        lines = [
            line
            for line in lines
            if not (
                line.strip().lower().endswith(f"/{plugin_name.lower()}")
                or line.strip().lower().endswith(f"/{plugin_name.lower()}.git")
            )
        ]
        if len(lines) < original_count:
            config.logger.info(
                f"✅ Removed entry for '{plugin_name}' "
                f"from {config.PLUGINS_LIST_FILE.name}."
            )
        else:
            config.logger.warning(
                f"⚠️ Could not find an entry for '{plugin_name}' "
                f"in {config.PLUGINS_LIST_FILE.name}."
            )

    content = "\n".join(line for line in lines if line.strip()) + "\n"
    config.PLUGINS_LIST_FILE.write_text(content, "utf-8")


def create_production_package(target_branch: str) -> None:
    """
    Creates a production-ready lock file by intelligently converting local
    dependencies to Git URLs based on the uv workspace configuration.
    """
    pyproject_path = config.PROJECT_ROOT / "pyproject.toml"
    prod_input_path = config.PROJECT_ROOT / "requirements.prod.in"
    prod_lock_path = config.PROJECT_ROOT / "requirements.prod.txt"

    try:
        data = tomlkit.parse(pyproject_path.read_text("utf-8"))

        # 步骤 1: 构建 'Python包名 -> 仓库名' 的映射
        pkg_name_to_repo_name_map: dict[str, str] = {}
        workspace_members = (
            data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
        )

        config.logger.info("Building package name to repository name map...")
        for member_pattern in workspace_members:
            # 假设 members 都是类似 "plugins/*" 的模式或直接的路径
            # 我们这里简化处理，直接匹配 "plugins/" 前缀
            if isinstance(member_pattern, str) and member_pattern.startswith(
                config.settings.plugins_src_dir
            ):
                # 遍历所有本地插件目录
                for plugin_dir in config.PLUGINS_SRC_DIR.iterdir():
                    if not plugin_dir.is_dir():
                        continue
                    repo_name = plugin_dir.name
                    pkg_name = _get_package_name_from_plugin_dir(plugin_dir)
                    if pkg_name:
                        normalized_pkg_name = pkg_name.replace("_", "-")
                        pkg_name_to_repo_name_map[normalized_pkg_name] = repo_name
                        config.logger.debug(
                            f"  - Mapped '{normalized_pkg_name}' -> '{repo_name}'"
                        )

        # 步骤 2: 处理依赖，并使用映射进行转换
        prod_deps_set = set()
        project_section = data.get("project", data.get("tool", {}).get("poetry", {}))

        def process_dependency(dep_name: str, dep_info: Item) -> str | None:
            """
            Helper to convert a dependency into a production-ready string.
            dep_name is the key, dep_info is the value from the toml dict.
            """
            # Case 1: 本地路径依赖 (e.g., { path = "...", editable = true })
            if isinstance(dep_info, Table) and "path" in dep_info:
                # 即使是字典形式，我们也用映射表来查找，因为更可靠
                normalized_dep_name = dep_name.replace("_", "-")
                if repo_name := pkg_name_to_repo_name_map.get(normalized_dep_name):
                    git_url = (
                        f"https://github.com/{config.YOUR_GITHUB_ORG}/{repo_name}.git"
                    )
                    return f"{dep_name} @ git+{git_url}@{target_branch}"
                else:
                    config.logger.warning(
                        f"Could not find repo for local package '{dep_name}'. Skipping."
                    )
                    return None

            # Case 2: 标准字符串依赖 (包括 `pip install -e .` 产生的 "my-pkg @ file://...")
            dep_str = str(dep_info)
            if " @ file://" in dep_str:
                pkg_name_from_str = dep_str.split(" @ ")[0].strip()
                normalized_pkg_name = pkg_name_from_str.replace("_", "-")
                if repo_name := pkg_name_to_repo_name_map.get(normalized_pkg_name):
                    git_url = (
                        f"https://github.com/{config.YOUR_GITHUB_ORG}/{repo_name}.git"
                    )
                    return f"{pkg_name_from_str} @ git+{git_url}@{target_branch}"
                else:
                    config.logger.warning(
                        "Could not find repo for local package "
                        f"'{pkg_name_from_str}'. Skipping."
                    )
                    return None

            # Case 3: 普通依赖 (e.g., "fastapi" or "fastapi>=0.10.0")
            # 清理可能的尾部逗号
            cleaned_dep = dep_str.strip().rstrip(",")
            return cleaned_dep if cleaned_dep else None

        # --- 开始处理 ---
        # 处理主要依赖
        if "dependencies" in project_section:
            dependencies = project_section["dependencies"]
            # tomlkit 对 [project].dependencies 的解析可能是 Table
            if isinstance(dependencies, Table):
                for name, info in dependencies.items():
                    if processed := process_dependency(name, info):
                        prod_deps_set.add(processed)
            # poetry 的 [tool.poetry].dependencies 可能是 Array
            elif isinstance(dependencies, Array):
                for item in dependencies:
                    # 简化处理：假设 poetry 的 array 里是简单的
                    # "pkg-name==version" 字符串
                    if processed := process_dependency(str(item).split(" @ ")[0], item):
                        prod_deps_set.add(processed)

        # 处理可选依赖
        optional_deps_section = project_section.get("optional-dependencies")
        if isinstance(optional_deps_section, Table):
            for group in optional_deps_section.values():
                if isinstance(group, Array):
                    for item in group:
                        # 简化处理
                        if processed := process_dependency(
                            str(item).split(" @ ")[0], item
                        ):
                            prod_deps_set.add(processed)

        if not prod_deps_set:
            config.logger.warning("No dependencies found to generate production file.")
            return

        # 步骤 3: 写入文件并编译
        sorted_deps = sorted(prod_deps_set)
        config.logger.info(
            f"Generated {len(sorted_deps)} unique production dependency entries."
        )
        prod_input_path.write_text("\n".join(sorted_deps) + "\n", "utf-8")
        config.logger.info(
            f"✅ Production input file '{prod_input_path.name}' created."
        )

        config.logger.info(
            f"⚡️ Generating production lock file '{prod_lock_path.name}'..."
        )
        process.uv(
            ["pip", "compile", str(prod_input_path), "-o", str(prod_lock_path)],
            config.PROJECT_ROOT,
        )
        config.logger.info("✅ Production lock file generated successfully.")

    except Exception as e:
        prod_input_path.unlink(missing_ok=True)  # 出错时清理临时文件
        config.logger.error(
            f"❌ Failed to create production package: {e}", exc_info=True
        )
