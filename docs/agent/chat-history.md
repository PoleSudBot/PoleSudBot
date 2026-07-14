# ChatHistory 开发接口

`zhenxun.services.chat_history` 是项目统一的轻量聊天历史服务。插件应通过服务层查询消息，不要直接依赖平台原始消息、CQ 码或自行解析数据库 JSON。

## 数据职责

| 字段 | 内容 | 推荐用途 |
| --- | --- | --- |
| `text` | 安全可读文本，如 `@123 看看[图片]` | LLM 总结、导出、人工展示 |
| `plain_text` | 只包含文本消息段 | 词云、关键词、全文搜索 |
| `segments` | 跨平台结构化消息，是内容 source of truth | 媒体、艾特、引用等精确处理 |
| `segment_types` | 去重后的消息段类型 | 图片、语音、引用等分类统计 |
| `direction` | `in` 或 `out` | 区分用户消息和 Bot 消息 |
| `bot_id` | 收发消息的 Bot ID | 多 Bot 筛选和出站发送者识别 |
| `platform` | 标准化平台名称 | 跨平台筛选和消息 ID 隔离 |
| `message_id` | 平台消息 ID | 消息定位和引用解析 |
| `message_type` | `private`、`group` 或 `channel` | 会话场景筛选 |
| `reply_to_message_id` | 被引用的平台消息 ID | 引用关系查询 |
| `create_time` | 消息实际发生时间 | 时间范围查询和排序 |

默认查询方向是 `in`。只有确实需要 Bot 回复时才传入 `direction="out"` 或 `direction="all"`。

## 消息段协议

`segments` 不保存 OneBot CQ 码，也不直接保存 Alconna 的内部序列化格式。当前稳定类型包括：

- `text`: `{"text": "消息文本"}`
- `mention`: `{"target": "用户ID", "kind": "user"}`
- `image`、`audio`、`video`、`file`: 仅保存文件名、媒体 ID、大小、摘要等有限元数据
- `emoji`: `{"id": "表情ID", "name": "可选名称"}`
- `reply`: `{"message_id": "被引用消息ID"}`
- `reference`: 合并转发或引用容器的轻量 ID/名称
- `card`: 只保存卡片格式，不保存大型原始正文
- `unknown`: 保存 `raw_type` 和经过长度限制的简单字段

示例：

```json
[
  {"type": "mention", "data": {"target": "123", "kind": "user"}},
  {"type": "text", "data": {"text": "看看这个"}},
  {"type": "image", "data": {"id": "abc", "name": "image.png"}},
  {"type": "reply", "data": {"message_id": "999"}}
]
```

所有平台、所有消息段的 `url` 字段都不会入库，包括图片、音频、视频、文件、表情和未知扩展段。媒体 Base64、二进制正文和超大卡片 payload 同样不会入库。普通元数据最多保留 512 字符，单个文本段最多保留 4096 字符。

因此结构化历史只用于理解、筛选和统计消息，不能依赖其中的数据重新下载、恢复或重放媒体。后续如需媒体归档，应由独立服务明确处理生命周期与存储成本，不应重新把临时 URL 混入 ChatHistory。

## 查询入口

```python
from zhenxun.services.chat_history import ChatHistoryQuery
```

### 最近消息

```python
rows = await ChatHistoryQuery.recent(
    group_id="123456",
    direction="in",
    limit=100,
)
```

返回完整 `ChatHistory` 模型，按 `create_time`、`id` 倒序排列。单次最多返回 10000 条。

只需字典投影时，使用与 `recent()` 相同筛选参数的轻量接口：

```python
text_rows = await ChatHistoryQuery.text_recent(
    group_id="123456",
    direction="all",
    limit=1000,
)
structured_rows = await ChatHistoryQuery.structured_recent(
    group_id="123456",
    direction="all",
    limit=1000,
)
```

两者同样按 `create_time`、`id` 倒序排列，默认返回 1000 条且最多 10000 条。`text_recent()` 适合直接读取近期总结文本；`structured_recent()` 适合同时处理用户与 Bot 的完整消息段。

### 时间范围完整记录

```python
rows = await ChatHistoryQuery.time_range(
    start=start_time,
    end=end_time,
    group_id="123456",
    limit=1000,
)
```

返回完整模型并按时间正序排列。即使省略 `limit`，也最多返回 10000 条。

### LLM 与导出文本

```python
rows = await ChatHistoryQuery.text_range(
    start=start_time,
    end=end_time,
    group_id="123456",
    limit=1000,
)
```

只查询：

```text
id, user_id, create_time, text, message_id, reply_to_message_id,
direction, bot_id, platform, message_type
```

该接口不会加载 `segments` JSON，适合大量消息总结、导出和分页处理。返回值是字典列表。

### 结构化消息

```python
rows = await ChatHistoryQuery.structured_range(
    start=start_time,
    end=end_time,
    group_id="123456",
    direction="all",
    descending=True,
    limit=500,
)
```

额外返回 `plain_text`、`segments` 和 `segment_types`，适合处理图片、艾特和引用关系。返回值是字典列表。默认按时间正序；需要先截取范围内最新 N 条时传入 `descending=True`，避免正序查询先截断成最早 N 条。

## `summary_group` 全数据库模式

`summary_group` 设置 `USE_DB_HISTORY=True` 后，最近消息使用 `structured_recent()`，时间范围使用 `structured_range()`。这只替换历史消息列表的来源；后续仍会进行消息段渲染、用户名称解析和引用正文补取。

默认 `EXCLUDE_BOT_MESSAGES=False`，查询方向为 `direction="all"`，因此用户入站消息和 Bot 出站消息都会进入上下文。设置为 `True` 后，查询阶段即改为 `direction="in"`，并保留处理阶段的 Bot 排除检查作为防御。

### 数据库投影示例

一条包含艾特、文本和图片的入站记录，服务层返回的结构大致如下：

```json
{
  "id": 8421,
  "user_id": "10001",
  "bot_id": "99999",
  "direction": "in",
  "platform": "qq",
  "message_type": "group",
  "message_id": "183746",
  "reply_to_message_id": null,
  "create_time": "2026-07-14T20:30:00+08:00",
  "text": "@10002看看这个[图片]",
  "plain_text": "看看这个",
  "segments": [
    {"type": "mention", "data": {"target": "10002", "kind": "user"}},
    {"type": "text", "data": {"text": "看看这个"}},
    {"type": "image", "data": {"id": "ABC.jpg", "name": "image.png"}}
  ],
  "segment_types": ["mention", "text", "image"]
}
```

图片没有 URL，也不会被下载或识别。若适配器提供了轻量 `summary`，图片段可以额外保存该字段；否则总结时只生成图片占位符。

### `summary_group` 内部消息示例

上述记录进入消息处理器后，会被转换成与 API 历史兼容的字典：

```json
{
  "message_id": "183746",
  "user_id": 10001,
  "time": 1784032200,
  "message_type": "group",
  "message": [
    {"type": "mention", "data": {"target": "10002", "kind": "user"}},
    {"type": "text", "data": {"text": "看看这个"}},
    {"type": "image", "data": {"id": "ABC.jpg", "name": "image.png"}}
  ],
  "raw_message": "看看这个",
  "sender": {"user_id": 10001},
  "_summary_source": "db"
}
```

`message` 中的 canonical segments 是富内容处理依据，`raw_message` 只是旧数据和纯文本场景的回退。出站记录的发送者取 `bot_id`，不会把数据库自增 `id` 当成平台 `message_id`。

### 最终发送给 LLM 的文本示例

用户名称解析成功时，常见消息最终会序列化成：

```text
Alice(10001): 大家晚上八点开会
Alice(10001): @Bob 看看这个 [img]
Alice(10001): @Bob 看看这个 [img]猫咪截图
Alice(10001): [voice]
Alice(10001): [emoji] [card]
真寻(99999): 收到，我来提醒大家
```

引用消息的数据库记录只保存平台消息 ID。当前处理器会调用 `bot.get_msg()` 补取被引用消息正文；成功和失败时分别类似：

```text
Alice(10001): > Bob(10002): 原消息内容 | 我同意
Alice(10001): > unknown: reply unavailable | 我同意
```

艾特名称和发送者名称也会通过平台用户信息接口补取；接口失败时使用 `user_0001` 一类回退名称。因此，全数据库模式不是完全离线模式。

### 生产切换建议

- 可以把 `USE_DB_HISTORY=True` 作为生产主路径：当前总结只消费文本、占位符和已有 `summary`，不需要媒体 URL。
- 不建议删除 API 模式或回退开关：数据库查询失败不会自动切换到 API，保留开关便于故障时快速回退。
- 切换前应确认目标群已有足够长的 ChatHistory 覆盖期，并抽样核对入站、出站、艾特、图片和引用段；旧记录缺少 `segments` 时只能回退 `plain_text` 或 `text`。
- 如果目标是完全不调用历史消息相关 API，还需要让引用正文优先通过 `by_message_id()` 从 ChatHistory 查询，再以 `bot.get_msg()` 兜底。当前实现尚未完成这一层。
- 如果目标是完全离线运行，还需要另外替换用户名称查询；当前 `PlatformUtils.get_user()` 仍依赖平台能力。

### 统计与排行

```python
count = await ChatHistoryQuery.count(
    group_id="123456",
    direction="in",
    start=start_time,
    end=end_time,
)

rank = await ChatHistoryQuery.rank(
    group_id="123456",
    direction="in",
    limit=10,
)
```

`count()` 只有在 `start` 和 `end` 同时传入时才应用时间范围。

### 消息 ID 与引用

```python
target = await ChatHistoryQuery.by_message_id(
    row["reply_to_message_id"],
    platform="qq",
    bot_id="机器人ID",
)
```

同时传入 `platform` 和 `bot_id` 可以避免不同账号、平台之间的消息 ID 冲突。不要把被引用消息全文复制进当前消息；通过 `reply_to_message_id` 按需查询。

### 按消息段类型查询

```python
images = await ChatHistoryQuery.by_segment_type(
    "image",
    group_id="123456",
    direction="in",
    limit=100,
)
```

当前实现会分页扫描最多 50000 条候选记录，优先使用 `segment_types`，旧数据缺失时回退读取 `segments`。该接口适合低频查询，不应在高频事件处理链中反复调用。

## 文本转换

```python
from zhenxun.services.chat_history import segments_to_readable_text

readable = segments_to_readable_text(row.segments)
```

该函数适合旧数据兜底或需要重新生成展示文本的场景。新记录已经在 `text` 中缓存了相同语义的可读文本，大批量 LLM 总结应优先使用 `text_range()`，避免无意义地加载完整 JSON。

## 兼容边界

- 旧记录可能没有 `segments` 或 `segment_types`，插件需要允许字段为空。
- 旧记录的 `segments` 可能仍含历史 URL；统一清洗规则只影响新写入记录，不自动清理存量 JSON。
- 旧 `text` 可能仍包含 CQ 码；本次升级不会自动重写历史数据。
- `segments` 用于查询和理解，不保存媒体 URL，也不保证能够完整重放为原平台消息。
- 大于 10000 条的任务应按时间和 `id` 分批处理，并在调用侧进行分段总结或聚合。
- 新增公共查询能力应继续放在 `zhenxun/services/chat_history/`，不要让插件直接扩展 ORM 模型查询。
