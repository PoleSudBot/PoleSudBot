import os
import re
import subprocess
import sys


def get_dev_branch(root_dir):
    toml_path = os.path.join(root_dir, "manage.toml")
    if not os.path.exists(toml_path):
        print(f"  ⚠️ 未找到 {toml_path}，默认使用 L7dev")
        return "L7dev"

    try:
        if sys.version_info >= (3, 11):
            import tomllib

            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            return data.get("manager", {}).get("dev_branch", "L7dev")
        else:
            with open(toml_path, encoding="utf-8") as f:
                content = f.read()
                match = re.search(r'dev_branch\s*=\s*["\']([^"\']+)["\']', content)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"  ❌ 读取 {toml_path} 失败: {e}")

    return "L7dev"


def is_git_clean(cwd=None):
    # -uno 忽略未跟踪的文件，只检查已跟踪文件是否有修改
    res = subprocess.run(
        ["git", "status", "--porcelain", "-uno"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"  ❌ 检查 Git 状态失败:\n{res.stderr}")
        return False

    if res.stdout.strip() == "":
        return True

    print(f"  ⚠️ 工作区有未提交的修改，请先提交或撤销:\n{res.stdout}")
    return False


def update_repo(cwd=None, branch=None):
    if not is_git_clean(cwd):
        print("  ⏩ 跳过拉取更新")
        return False

    cmd = ["git", "pull"]
    if branch:
        cmd.extend(["origin", branch])

    print(f"  执行: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd)
    if res.returncode == 0:
        print("  ✅ 更新成功\n")
        return True
    else:
        print("  ❌ 更新失败\n")
        return False


def main():
    root_dir = os.path.abspath(os.path.dirname(__file__))

    print("=== 1. 更新主仓库 ===")
    dev_branch = get_dev_branch(root_dir)
    print(f"  目标分支: origin/{dev_branch}")
    update_repo(cwd=root_dir, branch=dev_branch)

    print("=== 2. 更新 resources ===")
    resources_dir = os.path.join(root_dir, "resources")
    if os.path.exists(resources_dir) and os.path.isdir(
        os.path.join(resources_dir, ".git")
    ):
        update_repo(cwd=resources_dir)
    else:
        print("  ⏩ resources 目录不存在或不是独立的 Git 仓库，跳过\n")

    print("=== 3. 拉取第三方插件更新 ===")
    nbm_path = os.path.join(root_dir, "nbm.py")
    if os.path.exists(nbm_path):
        # 如果你使用 uv run python update.py 执行本脚本，
        # sys.executable 会自动指向 uv 虚拟环境的 Python，无需重复调用 uv run
        cmd = [sys.executable, "nbm.py", "prod-setup"]
        print(f"  执行: {' '.join(cmd)}")
        subprocess.run(cmd, cwd=root_dir)
        print("\n  ✅ 插件更新执行完毕")
    else:
        print("  ❌ 未找到 nbm.py 脚本")


if __name__ == "__main__":
    main()
