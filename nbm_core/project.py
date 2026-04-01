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

# [可选] 生产部署时额外安装的 extras 列表
prod_sync_extras = []

# [可选] 并发执行任务时使用的最大线程数
max_workers = 8

# [可选] 执行外部命令（如 git, uv）的超时时间（秒）
command_timeout = 1200
"""
PLUGINS_TXT_TEMPLATE = "# 请在此处逐行输入插件的 GitHub 仓库地址\n"


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
    if is_first_run:
        config.logger.info("⚠️  检测到首次运行，已为您创建模板配置文件。")
        config.logger.info("👉 请打开并修改 'manage.toml'（特别是 'github_org'），")
        config.logger.info("   并编辑 'plugins.txt'，然后重新运行。")


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
                f"Detected package name '{pkg_name_to_remove}' from plugin's pyproject.toml."
            )
        except Exception:
            pkg_name_to_remove = pkg_name.replace("_", "-")
            config.logger.warning(
                f"Could not parse package name. Falling back to folder name '{pkg_name}'."
            )
    else:
        pkg_name_to_remove = pkg_name.replace("_", "-")

    # --- Step 2: Use `uv` to remove the dependency ---
    try:
        process.uv(["remove", pkg_name_to_remove], config.PROJECT_ROOT, check=True)
    except CommandError as e:
        if "not found in dependencies" in str(e):
            config.logger.warning(
                f"⚠️ Dependency '{pkg_name_to_remove}' not found in project dependencies."
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
                f"✅ Removed entry for '{plugin_name}' from {config.PLUGINS_LIST_FILE.name}."
            )
        else:
            config.logger.warning(
                f"⚠️ Could not find an entry for '{plugin_name}' in {config.PLUGINS_LIST_FILE.name}."
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
                        f"Could not find repo for local package '{pkg_name_from_str}'. Skipping."
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
                    # 简化处理：假设 poetry 的 array 里是简单的 "pkg-name==version" 字符串
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
        sorted_deps = sorted(list(prod_deps_set))
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
