# AGENTS.md

## Repository Overview

- `PoleSudBot/Bot` 是基于 `zhenxun_bot` 二次开发的 NoneBot2 / OneBot V11 机器人项目，协议端主要采用napcat。
- 依赖与本地源码源由 `uv` 管理，插件清单由 `plugins.txt` 和 `nbm.py` 协同维护。
- 默认开发启动命令使用 `uv run nb run`。
- 运行时入口与插件加载编排集中在 `bot.py`。

## Code Ownership Boundaries

- `zhenxun/`：核心代码、共享服务、模型、配置、工具、内建插件与运行时集成。
- `zhenxun/plugins/`：本仓库维护的一方插件，以及针对真寻体系做的本地适配插件。
- `plugins/`：通过 `pyproject.toml` 的 `[tool.uv.sources]` 以 editable 方式接入的第三方 NoneBot 插件源码。
- 除非明确要求，否则将 `plugins/` 视为外部依赖代码，不主动做大范围重构或风格统一。

## Working Rules

- 开始实现前，先判断改动应该落在 `zhenxun/`、`zhenxun/plugins/` 还是 `plugins/`。
- 优先复用仓库已有抽象与封装，不直接绕开项目层写底层实现。
- Bugfix 默认保持行为兼容并尽量小改，不把核心逻辑迁入 `plugins/`。
- 若新能力会被多个插件复用，或涉及共享模型、配置、渲染、任务、权限、基础设施，优先落到 `zhenxun/`。
- 第三方插件问题优先确认能否在第一方代码层兼容或规避；只有问题明确属于 vendor 代码且用户希望修补时，才修改 `plugins/`。

## Commands

- 同步第三方插件源码并重写本地依赖：`python nbm.py init --install`
- 按当前锁文件和插件清单做完整同步：`python nbm.py prod-setup`
- 默认开发启动：`uv run nb run`
- 若已有对应测试，优先做最小范围验证；在测试依赖可用时可使用 `uv run pytest <path>` 做定向验证。
- 不臆造仓库里不存在的 lint / test 工作流；先查现有脚本和配置再执行。

## Task Mode

### Bugfix

- 先定位问题归属层：核心、一方插件，还是第三方插件。
- 优先修第一方代码，避免无意 patch vendor。
- 默认保持现有行为和配置兼容，除非用户明确要求调整行为。

### New Plugin / Feature

- 先判断需求属于核心能力、`zhenxun/plugins/` 插件，还是第三方插件接入 / 修补。
- 若改动落在 `zhenxun/plugins/`，先阅读 `docs/agent/plugin-development.md`，再确定结构、配置、权限和验证方案。
- 不额外发明与仓库现有模式冲突的新框架层。

## Extra References

- 涉及 `zhenxun/plugins/` 下插件的新建、重构或大改时，先看 `docs/agent/plugin-development.md`。
