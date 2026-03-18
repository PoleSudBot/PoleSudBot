# zhenxun/plugins Plugin Development

## Scope
- 本文仅适用于 `zhenxun/plugins/` 下的一方插件与真寻适配插件。
- `plugins/` 下第三方nonebot2插件默认不套用本文规范；第三方插件优先遵循原项目结构和用户意图。
- 如果改动本质上是共享框架能力、公共模型、配置系统或运行时基础设施，应优先放在 `zhenxun/`，而不是强行塞进单个插件。

## Placement Decision
- 放到 `zhenxun/`：多插件共享服务、公共模型、配置 / 权限 / 加载器 / 渲染 / LLM / 基础设施调整。
- 放到 `zhenxun/plugins/`：与真寻运行时强耦合的一方功能，或需要深度复用本仓库服务层、配置层、任务体系的插件。
- 放到 `plugins/`：明确是在维护 `plugins.txt` / `[tool.uv.sources]` 接入的第三方 NoneBot 插件。
- 拿不准时，优先在第一方代码中做兼容、封装或桥接，不要直接改 vendor。

## Plugin Skeleton
- 先跟随相邻插件的真实结构，不强制统一模板。
- 简单插件可以集中在 `__init__.py`；复杂插件可拆分为 `commands.py`、`config.py`、`model.py`、`services/`、`utils/`、`templates/` 等。
- 入口模块通常使用 `__plugin_meta__ = PluginMetadata(...)`，`extra` 使用 `PluginExtraData(...).dict()` 或同目录已有的当前写法。
- 需要启动 / 关闭钩子、模板命名空间、后台任务时，优先参考 `zhenxun_plugin_quote`、`parse_bilibili` 等现有插件。
- 插件名、菜单分类、配置键名、帮助文本尽量延续仓库现有命名风格。

## Preferred Project Abstractions
- 配置与元数据：`PluginMetadata`、`PluginExtraData`、`RegisterConfig`、`Task` 以及现有 group config model。
- 日志：`from zhenxun.services.log import logger`
- 消息构建：`from zhenxun.utils.message import MessageUtils`
- 网络请求：`from zhenxun.utils.http_utils import AsyncHttpx`
- 数据模型：`from zhenxun.services.db_context import Model`
- 生命周期与优先级：`PriorityLifecycle`、driver hooks、现有初始化模式。
- 规则、依赖注入、渲染、缓存、LLM、调度等能力优先复用仓库已有封装，而不是重新下沉到底层库。

## Red Lines
- 不要硬编码可变配置；应优先通过 `RegisterConfig` 或现有配置层暴露。
- 新增网络逻辑优先使用 `AsyncHttpx` 或现有服务封装，不要随手新建长期使用的底层 HTTP client。
- 不要绕过 `MessageUtils` 或项目消息封装直接大面积构造底层消息，除非现有能力确实不支持。
- 不要使用标准 `logging` 替代 `zhenxun.services.log.logger`。
- 不要把第三方插件的实现风格原样照搬进 `zhenxun/plugins/`；优先贴合本仓库现有一方插件模式。
- 不要因为单个插件需求把通用逻辑复制到多个插件里；可复用逻辑应抽到 `zhenxun/`。

## New Plugin Checklist
- 先确认落点：这是核心能力、一方插件，还是第三方插件问题。
- 明确 `__plugin_meta__` 的名称、菜单分类、权限等级、配置项和必要命令说明。
- 明确是否需要模型、缓存、定时任务、启动初始化、模板命名空间或外部服务凭据。
- 明确应复用的项目抽象：日志、消息、HTTP、数据库、规则、渲染、LLM、任务。
- 明确最小验证方式：目标命令、关键路径、必要时的定向测试或运行时验证。
