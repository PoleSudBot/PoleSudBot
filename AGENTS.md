# AGENTS.md

## Repository Overview
- **Stack**: 基于 `zhenxun_bot` 二次开发的 NoneBot2 / OneBot V11 机器人项目，协议端主要采用 `napcat`,当前开发/维护者是k1yuyu（1163272259@qq.com）。
- **Package Manager**: 依赖、虚拟环境与本地源码由 `uv` 管理。插件清单由 `plugins.txt` 和 `nbm.py` 协同维护。
- **Entrypoint**: 运行时入口与插件加载编排集中在 `bot.py`。
- 你当前所处的是开发环境。

## Code Ownership Boundaries
严格遵守以下文件层级与归属边界：
- `zhenxun/`：**核心层**。包含共享服务、模型、配置、工具、内建插件与运行时集成。会被复用的能力或基建变动必须落于此。
- `zhenxun/plugins/`：**一方插件层**。本仓库维护的第一方插件及针对真寻体系的本地适配插件。
- `plugins/`：**外部依赖层 (Vendor)**。通过 `pyproject.toml` 的 `[tool.uv.sources]` 以 editable 方式接入的第三方插件源码（fork而来,每个都有单独仓库维护）。**禁止主动对该目录做大范围重构或风格统一。**
- `resources/`：主要针对真寻体系的资源文件（有单独仓库维护），我正在使用的是resources/themes/psb主题。
- `docs/`：项目相关文档。

## Architecture & Task Rules
- **归属判断**：开始任何任务前，首先明确改动应落在 `zhenxun/`、`zhenxun/plugins/` 还是 `plugins/`。
- **Bugfix 路由**：第三方插件 (`plugins/`) 的 Bug，优先在第一方核心层 (`zhenxun/`) 做兼容或规避。只有当问题属于 vendor 代码且用户明确希望修补时，才修改 `plugins/`。
- **新功能路由**：若开发 `zhenxun/plugins/` 下的插件，必须先阅读 `docs/agent/plugin-development.md`，确定结构与权限后再动手。
- **抽象复用**：禁止绕开现有项目层直接写底层实现，禁止引入与当前架构冲突的新框架。

## Commands Workflow
- **默认开发启动**：`uv run nb run`
- **同步插件源码并重写本地依赖**：`uv run python nbm.py init --install`
- **生产级完整同步**：`uv run python nbm.py prod-setup`
- **测试验证**：`uv run pytest <path>`（优先在测试依赖可用时做最小范围定向验证）。
- **注意**：不臆造仓库里不存在的 lint / test 工作流；先查现有脚本再执行。
