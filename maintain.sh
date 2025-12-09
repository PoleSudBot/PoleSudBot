#!/bin/bash
set -e

# ================= 配置区 =================
VENV_DIR=".venv"
PLAYWRIGHT_EXEC="$VENV_DIR/bin/playwright"
PROJECT_ROOT=$(pwd)
TARGET_BROWSER_PATH="$PROJECT_ROOT/data/nonebot_plugin_htmlrender"
# =========================================

export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright/"

echo "🚀 [1/5] 拉取最新代码..."
git pull

echo "🐍 [2/5] 检查虚拟环境..."
if [ ! -d "$VENV_DIR" ]; then
    uv venv
fi

echo "📦 [3/5] 安装依赖..."

uv pip install tomlkit tqdm portalocker pydantic loguru

echo "⚙️ [4/5] 同步生产环境..."
"$VENV_DIR/bin/python" nbm.py prod-setup

echo "🌍 [5/5] 强制安装浏览器内核到指定目录..."
echo "   - 目标路径: $TARGET_BROWSER_PATH"

export PLAYWRIGHT_BROWSERS_PATH="$TARGET_BROWSER_PATH"

# 执行安装
$PLAYWRIGHT_EXEC install chromium

echo "✅ 维护完成！"
echo "💡 如果bot当前运行中，请重启以应用更新"