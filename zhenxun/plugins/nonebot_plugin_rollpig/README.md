# nonebot_plugin_rollpig

本目录是仓库内维护的一方版本，基于上游 `Felis2026/nonebot-plugin-rollpig` 做了本地账本、群内日报、猪王排行、PSB 面板和真寻配置层适配。

## 功能

- `今日小猪 / 今天是什么小猪 / jrxz`：每天抽取一次命运小猪，重复发送只查看当天结果。
- `我的猪圈 / 我的小猪`：查看图鉴进度、EX 等级成长摘要、当前群排名与本地总排名。
- `猪王争霸榜 / 猪猪总榜`：按图鉴数量排行，并用首次达到当前图鉴数的时间处理并列。
- `今日烤猪 / 烤群友`：围绕当天小猪形态做烧烤、反噬、保护和日报事件记录。
- `随机小猪 / 找猪`：从 PigHub 获取或搜索外部猪图。

## 成长系统

- 每只猪都有累计抽到次数 `copies`，`1` 次为 `EX Lv.0`，之后每多一次提升一级，默认最高 `EX Lv.5`。
- 重复抽到已解锁小猪会增加 `duplicate_streak`，下一次未解锁小猪的抽取权重会随连续重复次数提高。
- 抽到新猪后 `duplicate_streak` 清零，并写入图鉴；同一天重复查看不会刷等级或连续重复次数。
- 旧账本迁移时只能确认“曾经拥有过”，因此历史图鉴统一回填为 `copies=1`，不会伪造历史重复次数。

## 配置

配置统一走真寻插件配置层，模块名为 `nonebot_plugin_rollpig`。

| Key | 默认值 | 说明 |
| --- | --- | --- |
| `AI_ENABLED` | `False` | 是否启用 AI 烤猪文案 |
| `LLM_MODEL_NAME` | `None` | 烤猪文案使用的 LLM 模型名 |
| `ROAST_COOLDOWN_HOURS` | `8.0` | 普通烤群友冷却时间 |
| `STORAGE_BACKEND` | `local` | 存储后端，支持 `local` / `cloud` |
| `CLOUD_API_URL` | `None` | cloud 存储服务地址 |
| `CLOUD_TOKEN` | `None` | cloud 存储鉴权 token |
| `CLOUD_TIMEOUT` | `3.0` | cloud 请求超时时间 |
| `CLOUD_STRICT_MODE` | `True` | cloud 是否启用严格模式 |
| `PROXY` | `None` | PigHub / cloud 外部请求代理 |
| `GROWTH_MAX_EXPERT_LEVEL` | `5` | EX 等级上限 |
| `GROWTH_PITY_WEIGHT_STEP` | `0.5` | 连续重复后未解锁小猪的单次权重加成 |
| `GROWTH_PITY_WEIGHT_CAP` | `4.0` | 连续重复后未解锁小猪的最大权重加成 |

## 数据

本地模式使用 `nonebot_plugin_localstore` 下的 `pig_data.json`：

- `history`：每日抽猪结果，保留短期历史。
- `group_rolls`：群维度的今日显形记录，用于日报和随机烤群友候选。
- `collection`：永久图鉴。
- `collection_progress`：达到当前图鉴数量的时间，用于排行并列排序。
- `pig_progress`：每只猪的累计抽到次数和首次获得时间。
- `draw_state`：连续重复次数。
- `daily_events`：烧烤事件，用于日报。
- `protected`：群维度保护名单。

## 验证

优先运行最小验证：

```bash
PYTHONPYCACHEPREFIX=/tmp/rollpig-pycache uv run --no-sync pytest -s tests/test_rollpig_ranking.py
PYTHONPYCACHEPREFIX=/tmp/rollpig-pycache uv run --no-sync python -m py_compile zhenxun/plugins/nonebot_plugin_rollpig/*.py zhenxun/plugins/nonebot_plugin_rollpig/store/*.py
```
