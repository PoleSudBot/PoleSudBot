from pathlib import Path
import subprocess
import sys


def main():
    root_dir = Path(__file__).resolve().parent
    cmd = ["uv", "run", "--no-project", "nbm.py", "update", *sys.argv[1:]]
    print(
        "`update.py` 已合并到 `nbm.py`，正在转调到 `uv run --no-project nbm.py update`...",
        flush=True,
    )
    try:
        result = subprocess.run(cmd, cwd=root_dir)
    except FileNotFoundError:
        print("❌ 未找到 `uv`，请先安装后再执行更新。")
        raise SystemExit(1) from None
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
