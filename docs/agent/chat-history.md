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
| `message_id` | 平台消息 ID | 消息定位和引用解析 |
| `message_type` | `private`、`group` 或 `channel` | 会话场景筛选 |
| `reply_to_message_id` | 被引用的平台消息 ID | 引用关系查询 |
| `create_time` | 消息实际发生时间 | 时间范围查询和排序 |

默认查询方向是 `in`。只有确实需要 Bot 回复时才传入 `direction="out"` 或 `direction="all"`。

## 消息段协议

`segments` 不保存 OneBot CQ 码，也不直接保存 Alconna 的内部序列化格式。当前稳定类型包括：

- `text`: `{"text": "消息文本"}`
- `mention`: `{"target": "用户ID", "kind": "user"}`
- `image`、`audio`、`video`、`file`: 仅保存 URL、文件名、媒体 ID、大小等有限元数据
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
  {"type": "image", "data": {"id": "abc", "url": "https://example.com/a.jpg"}},
  {"type": "reply", "data": {"message_id": "999"}}
]
```

媒体 Base64、二进制正文和超大卡片 payload 不会入库。普通元数据最多保留 512 字符，单个文本段最多保留 4096 字符。

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
id, user_id, create_time, text, message_id, reply_to_message_id
```

该接口不会加载 `segments` JSON，适合大量消息总结、导出和分页处理。返回值是字典列表。

### 结构化消息

```python
rows = await ChatHistoryQuery.structured_range(
    start=start_time,
    end=end_time,
    group_id="123456",
    limit=500,
)
```

额外返回 `plain_text`、`segments` 和 `segment_types`，适合处理图片、艾特和引用关系。返回值是字典列表。

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
- 旧 `text` 可能仍包含 CQ 码；本次升级不会自动重写历史数据。
- `segments` 用于查询和理解，不保证能够完整重放为原平台消息。
- 大于 10000 条的任务应按时间和 `id` 分批处理，并在调用侧进行分段总结或聚合。
- 新增公共查询能力应继续放在 `zhenxun/services/chat_history/`，不要让插件直接扩展 ORM 模型查询。
