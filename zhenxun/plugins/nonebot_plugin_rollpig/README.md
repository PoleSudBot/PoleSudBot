# nonebot_plugin_rollpig

本目录是仓库内维护的一方版本，基于上游 `Felis2026/nonebot-plugin-rollpig` 做了本地账本、群内日报、猪王排行、PSB 面板和真寻配置层适配。

## 功能

- `今日小猪 / 今天是什么小猪 / jrxz`：每天抽取一次命运小猪，重复发送只查看当天结果。
- `我的猪圈 / 我的小猪`：查看图鉴进度、EX 等级成长摘要、当前群排名与本地总排名。
- `猪王争霸榜 / 猪猪总榜`：按图鉴数量排行，并用首次达到当前图鉴数的时间处理并列。
- `今日烤猪 / 烤群友`：围绕当天小猪形态做烧烤、反噬、保护和日报事件记录。
- `随机小猪 / 找猪`：从 PigHub 获取或搜索外部猪图。
- `同步小猪资源 / 刷新小猪资源`：superuser 手动刷新静态小猪图鉴资源。

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
| `RESOURCE_SYNC_ENABLED` | `True` | 是否启用静态小猪资源同步，默认下载上游公有资源包 |
| `RESOURCE_MANIFEST_URL` | `https://pig.felislab.cc/resources/rollpig/manifest.json` | 静态资源 manifest URL，设为空字符串可关闭公有同步 URL |
| `RESOURCE_SYNC_ON_STARTUP` | `True` | 启动后是否后台同步资源 |
| `RESOURCE_SYNC_INTERVAL_HOURS` | `24` | 定时资源同步间隔 |
| `RESOURCE_SYNC_TIMEOUT` | `10.0` | 资源同步请求超时时间 |
| `RESOURCE_MAX_FILE_SIZE` | `10485760` | 单个资源文件下载大小上限 |
| `PRIVATE_RESOURCE_MANIFEST_URL` | `https://pig.felislab.cc/resources/rollpig-pjsk/manifest.json` | 私有资源 overlay manifest URL，设为空字符串可关闭 |
| `PRIVATE_RESOURCE_TOKEN` | `None` | 私有资源 Bearer Token |

## 资源同步

资源同步只处理静态资源，不接入上游 rollpig 云端账本，也不会读写本地 `pig_data.json`。

- manifest 指向一组静态文件：`pig.json`、可选 `pig_rules.json`、以及小猪图片。
- 同步成功前写入 localstore 缓存目录，完整校验后再替换 active 快照；失败会继续使用旧缓存或内置元数据。
- `pig.json` 按 ID 合并：远端新增或更新的 ID 覆盖本地旧条目，远端缺失的旧 ID 会保留，避免历史本地账本引用失效。
- `pig_rules.json` 按规则类型取并集，用于继续识别人类、熟食、吃掉、卖掉等特殊形态。
- 仓库不再携带小猪图片，生产环境部署后会自动下载到 localstore 的 `resources/active/images/`。
- 小猪图片优先从私有 overlay 缓存读取，其次读取公有 active 缓存；首次同步完成前可能暂时无图。
- 私有 overlay 默认指向上游 PJSK 包；启用资源同步后会缓存到 localstore 的 `resources/private_active/`，优先级高于公有资源。
- 私有 `pig.json` 默认只追加新 ID；如需覆盖公有小猪文案，必须通过 `pig_overrides.json` 显式声明。
- 本地特色的 `new.png` 贴纸位于 `resource/assets/new.png`，不属于小猪图片同步范围。

manifest 示例：

```json
{
  "resource_version": "2026-06-21",
  "pig_json": {"path": "pig.json", "sha256": "..."},
  "optional_files": {
    "pig_rules": {"path": "pig_rules.json", "sha256": "..."}
  },
  "images": [
    {"path": "images/pig.png", "filename": "pig.png", "sha256": "..."}
  ]
}
```

私有 overlay manifest 示例：

```json
{
  "resource_version": "private-2026-06-21",
  "overlay": true,
  "pig_json": {"path": "pig.json", "sha256": "..."},
  "optional_files": {
    "pig_rules": {"path": "pig_rules.json", "sha256": "..."},
    "pig_overrides": {"path": "pig_overrides.json", "sha256": "..."}
  },
  "images": [
    {"path": "images/mining-pig.png", "filename": "mining-pig.png", "sha256": "..."}
  ]
}
```

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
