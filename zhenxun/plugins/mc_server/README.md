# MC 服务器助手

`mc_server` 是真寻第一方 MC 服务器配套插件。当前版本采用“每群单服”设计：一个 QQ 群绑定一个 Minecraft Java 服务器，避免多服别名和默认服带来的命令歧义。

插件提供状态查询、Paper 日志监听、进退服播报、断连/恢复播报、群服聊天互通、RCON 远程管理、白名单绑定、在线时长统计和在线人数图表。

## 功能概览

- 服务器状态：通过 `mcstatus` 查询 Java 服状态、版本、延迟、在线人数和可见玩家列表。
- BlueMap 增强：可选读取 BlueMap live players JSON，补充玩家坐标和维度。
- 日志监听：读取 Paper `latest.log`，解析玩家加入、离开和游戏聊天。
- 三类开关：进退服播报、连接播报、聊天互通默认关闭，需要在群内手动开启。
- RCON：通过 `mctools` 执行服务端命令，用于远程管理、聊天互通和白名单。
- 统计：按在线 session 累计玩家在线时长，并按状态轮询采样生成在线人数图。
- 图片渲染：状态卡、在线时长和人数图优先用 zhenxun htmlrender 渲染，失败时回退文字。

## 前置条件

### Bot 侧

依赖已写入项目依赖：

```text
mcstatus>=13.1.0,<14.0.0
mctools>=1.4.1,<2.0.0
```

同步依赖后重启 Bot：

```bash
uv sync
uv run nb run
```

如果只想跑最小验证：

```bash
uv run pytest -s tests/test_mc_server_*.py
```

### MC 服务器侧

基础状态查询只需要 Bot 能访问 MC 服务器地址和端口，例如 `mc.example.com:25565`。

日志监听需要 Bot 进程能读取 Paper 的 `latest.log`。如果 MC 服和 Bot 不在同一台机器，需要通过共享目录、挂载、同步文件等方式让 Bot 能看到日志文件。

RCON 功能需要在服务端 `server.properties` 开启：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=你的强密码
```

修改后重启 MC 服务器，并确保 Bot 所在机器能访问 RCON 端口。

## 快速配置

推荐使用绑定向导完成首次配置。它会一步步询问服务器地址、日志路径和 RCON，并在关键步骤做验证。

### 1. 启动绑定向导

在目标群内发送：

```text
mcbind
```

向导会先让你输入 MC 服务器地址。端口不写时默认 `25565`：

```text
mc.example.com
mc.example.com:25565
```

IPv6 地址需要用方括号：

```text
[::1]:25565
```

地址填写后插件会先用 Java status ping 探测服务器。探测成功才会保存绑定，并返回版本、延迟、在线人数和可见玩家；探测失败不会覆盖旧绑定。

### 2. 按提示填写日志路径

向导第二步会询问 Paper 日志。可以填写 `latest.log` 文件，也可以填写 `logs` 目录；如果是目录，插件会自动尝试 `<目录>/latest.log`。

```text
/path/to/server/logs/latest.log
/path/to/server/logs
```

日志路径必须是 Bot 进程能读取到的本地路径。第一次设置日志路径时，插件会从文件末尾开始读，不会把历史日志刷屏。后续只处理新增日志。

如果暂时不配置日志，输入：

```text
skip
```

`skip` 不会清空旧日志路径，只是跳过本次配置。

### 3. 按提示配置 RCON

向导第三步会询问 RCON 地址。端口不写时默认 `25575`：

```text
mc.example.com:25575
mc.example.com
```

如果暂时不使用 RCON，输入：

```text
skip
```

输入 RCON 地址后，向导会提示超级用户私聊 Bot 设置密码：

```text
mcrcon passwd <群号> <RCON密码>
```

例如：

```text
mcrcon passwd 123456789 my-rcon-password
```

密码不会在群内回显，但会按 MVP 方案明文存入数据库。数据库管理员、数据库备份或导出文件的读取者都可以看到该密码。建议为 Bot 单独设置低权限 RCON 密码，并通过防火墙或内网限制 RCON 访问来源。

私聊设置完成后，回到群里输入：

```text
done
```

向导会执行 `mcrcon list` 做 RCON 验证。验证失败时可以继续私聊修正密码，再回群输入 `done` 重试。

### 4. 中途退出

向导任意步骤都可以输入以下任一内容结束：

```text
q
quit
退出
取消
```

单步等待超时时间由 `MC_BIND_FLOW_TIMEOUT_SECONDS` 控制，默认 `120` 秒。

### 5. 单步/修正配置

如果你只想快速改服务器地址，也可以继续使用单步命令：

```text
mcbind mc.example.com:25565
```

单步 `mcbind <地址>` 同样会先探测服务器，探测成功后才保存。

日志、BlueMap、RCON 地址和 RCON 密码也保留单步命令，适合后续修正：

```text
mclog /path/to/server/logs
mcbluemap https://map.example.com world
mcrcon set mc.example.com:25575
mcrcon passwd <群号> <RCON密码>
```

### 6. 开启需要的播报或互通

三类开关默认都是关闭：

```text
mctoggle join on
mctoggle conn on
mctoggle chat on
```

含义：

- `join`：玩家加入/离开游戏时，在群内播报。
- `conn`：Bot 轮询检测到服务器断连/恢复时，在群内播报。
- `chat`：开启群服聊天互通。

查看当前群绑定和开关状态：

```text
mclist
```

### 7. 验证是否可用

查询状态：

```text
mcinfo
```

发送群消息到游戏内：

```text
mcsend 大家好
mcs 大家好
```

执行 RCON 测试命令：

```text
mcrcon list
mcr list
```

给自己加白名单并绑定 QQ：

```text
mcwhitelist PlayerName
mcw PlayerName
```

## BlueMap 配置

BlueMap 是可选增强源。配置后，`mcinfo` 会尝试补充玩家坐标与维度。维度使用 BlueMap 的 `map_id` 标识。

如果 BlueMap Web 中可读接口类似：

```text
https://map.example.com/maps/world/live/players.json
https://map.example.com/maps/world_nether/live/players.json
```

则在群内配置：

```text
mcbluemap https://map.example.com world world_nether
```

`base_url` 不带协议时会自动补 `http://`：

```text
mcbluemap map.example.com world
```

BlueMap 读取失败不会让服务器状态变成离线，只会缺少坐标信息，并在日志里记录警告。

## 命令总览

### 查询类

```text
mcinfo
mcstatus
mci
```

查询当前群绑定服务器的状态。显示在线状态、延迟、版本、在线人数、可见玩家、天气占位和 BlueMap 坐标。

天气当前固定显示“未知/未配置数据源”。`mcstatus` 和 BlueMap live players 不提供通用天气字段，后续可以接 RCON 查询命令或服务端插件桥。

```text
mclist
```

显示当前群绑定状态、日志路径、BlueMap、RCON 和开关状态。

```text
mctime [范围]
mctime @用户 [范围]
mctime me|我|自己 [范围]
mct [范围]
mct @用户 [范围]
```

不指定用户时查询在线时长排行；指定 `@用户`、`me`、`我` 或 `自己` 时生成个人在线情况图，并显示该范围内总在线时长。

范围支持：

- 不写：本周目
- `今日` / `今天` / `today`
- `本周` / `week`
- `本月` / `month`
- `本周目` / `season`
- `YYYY-MM-DD`
- `YYYY-MM-DD..YYYY-MM-DD`

示例：

```text
mctime
mctime 今日
mctime 2026-05-01..2026-05-18
mctime @某人 本周
mctime 我 今日
mct 本周
```

```text
mcchart [范围]
mcc [范围]
```

生成在线人数变化图，范围语法同 `mctime`。

示例：

```text
mcchart 今日
mcchart 本月
mcc 今日
```

### 群管理配置

以下命令需要超级用户、群主/群管理员，或达到 `MC_ADMIN_LEVEL`。

```text
mcbind
mcbind <地址[:端口]>
```

不带参数时进入绑定向导，依次配置 MC 地址、日志路径和 RCON。向导中可用 `q` / `quit` / `退出` / `取消` 结束；日志和 RCON 步骤可用 `skip` 跳过；RCON 密码私聊设置后回群输入 `done` 验证。

带地址时走单步绑定。每群只保留一个服务器，重复执行会覆盖绑定地址。无论向导还是单步模式，都会先用 Java status ping 探测成功后才保存。

```text
mclog <latest.log路径或logs目录>
```

设置 Paper 日志路径。可以填 `latest.log` 文件，也可以填日志目录；如果是目录会自动尝试 `<目录>/latest.log`。路径必须是 Bot 进程能访问到的本地路径。

```text
mcbluemap <base_url> <map_id...>
```

设置 BlueMap Web 地址和地图 ID。

```text
mcrcon set <host:port>
```

设置 RCON 地址。设置地址只需要 MC 管理权限；执行任意 RCON 命令需要 RCON 权限。

### 开关

以下命令需要超级用户、群主/群管理员，或达到 `MC_ADMIN_LEVEL`。

```text
mctoggle join on
mctoggle join off
mctoggle conn on
mctoggle conn off
mctoggle chat on
mctoggle chat off
mctoggle all on
mctoggle all off
mctg all on
```

默认值全部为 `off`，避免刚配置时刷屏。`all` 会同时操作 `join`、`conn`、`chat` 三类开关。

### 聊天互通

```text
mcsend <消息>
mcs <消息>
```

把群内消息通过 RCON `tellraw` 发送到游戏内。需要先开启：

```text
mctoggle chat on
```

游戏内聊天同步到群内来自日志监听，因此还需要配置：

```text
mclog <latest.log路径或logs目录>
```

### RCON 管理

```text
mcrcon <命令>
mcr <命令>
```

在当前群绑定服务器上执行 RCON 命令。

示例：

```text
mcrcon list
mcrcon time set day
mcrcon say Hello
mcr list
```

执行 RCON 命令默认要求超级用户或达到 `MC_RCON_LEVEL`。群管理员不会自动拥有任意 RCON 执行权限。

### 白名单与 QQ 绑定

```text
mcwhitelist <玩家名>
mcwhite <玩家名>
mcw <玩家名>
```

普通用户只能给自己绑定 QQ，并执行：

```text
whitelist add <玩家名>
```

管理员可以代绑：

```text
mcwhitelist PlayerName @用户
mcw PlayerName @用户
```

绑定后，`mctime @用户` 可以查询该 QQ 绑定玩家的在线时长。

## 配置项

配置项通过真寻配置层管理，模块名为 `mc_server`。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `MC_POLL_INTERVAL_SECONDS` | `60` | 状态轮询间隔，最小按 15 秒处理。 |
| `MC_REQUEST_TIMEOUT_SECONDS` | `8` | MC 状态、BlueMap、RCON 请求超时，最小按 2 秒处理。 |
| `MC_DISCONNECT_NOTIFY_THRESHOLD` | `3` | 连续失败多少次后标记断连并触发 `conn` 播报。 |
| `MC_SAMPLE_INTERVAL_SECONDS` | `300` | 在线人数采样最小间隔，最小按 60 秒处理。 |
| `MC_BIND_FLOW_TIMEOUT_SECONDS` | `120` | 绑定向导每一步等待群内回复的超时时间，最小按 30 秒处理。 |
| `MC_ADMIN_LEVEL` | `5` | 群内 MC 配置管理所需真寻权限等级。 |
| `MC_RCON_LEVEL` | `9` | 群内执行任意 RCON 命令所需真寻权限等级。 |
| `MC_CHAT_FORMAT` | `[{sender}] {message}` | 群消息同步到游戏内的显示格式。 |
| `MC_RENDER_ENABLED` | `True` | 是否优先使用 htmlrender 渲染图片。 |
| `MC_MAX_CHART_POINTS` | `96` | 人数图最多渲染的数据点数量。 |

`MC_CHAT_FORMAT` 支持两个占位符：

```text
{sender}
{message}
```

例如：

```text
[QQ/{sender}] {message}
```

## 数据口径

### 在线时长

在线时长使用 session 段累计：

- 日志里出现 `joined the game` 时开启在线段。
- 日志里出现 `left the game` 时关闭在线段。
- Bot 重启时会先截断旧的 active session。
- 如果状态查询能看到玩家在线，插件会从重新观测到的时间点开启在线段。

这意味着 Bot 离线期间的真实在线时间无法反推，统计会以 Bot 重新观测时间为起点。

`mctime @用户` / `mctime 我` 的个人在线情况图也使用同一批 session 数据。图中的在线段会按查询时间范围裁剪，仍在进行中的在线段会临时截断到查询结束时间；如果 QQ 是后来才绑定的，插件会同时按当前绑定玩家查找历史 session。

### 人数图

人数图来自状态轮询采样表：

- 只有服务器状态查询成功时才会采样。
- 采样间隔由 `MC_SAMPLE_INTERVAL_SECONDS` 控制。
- 图表点数超过 `MC_MAX_CHART_POINTS` 时会降采样显示。

### 本周目

首次绑定服务器时会自动创建默认周目。当前版本没有提供群内切换周目的命令；如果后续要做换周目管理，可以基于已保留的 `server_id` 和 `McSeason` 模型扩展。

## 权限说明

### MC 管理权限

以下任一条件满足即可：

- Bot 超级用户
- QQ 群主或群管理员
- 真寻群权限等级大于等于 `MC_ADMIN_LEVEL`

可执行：

- `mcbind`
- `mclog`
- `mcbluemap`
- `mctoggle`
- `mcrcon set`
- `mcwhitelist <玩家名> @用户`

### RCON 权限

以下任一条件满足即可：

- Bot 超级用户
- 真寻群权限等级大于等于 `MC_RCON_LEVEL`

可执行：

- `mcrcon <命令>`

RCON 密码只能由超级用户私聊设置。

## 排障

### `mcinfo` 显示离线

检查：

- `mcbind` 地址和端口是否正确。
- Bot 所在机器是否能访问 MC 端口。
- 域名是否能解析。
- 服务器是否开启 query 不影响基础状态查询；这里使用的是 Java status ping。

### `mclog` 提示日志文件不存在

检查：

- 路径必须是 Bot 机器上的路径，不是 MC 服务器内部相对路径。
- Docker/WSL/远程服务器场景需要把日志目录挂载给 Bot。
- Bot 运行用户需要有读取权限。

### 开了 `join` 但没有进退服播报

检查：

- 已执行 `mctoggle join on`。
- 已正确配置 `mclog <latest.log路径或logs目录>`。
- Paper 日志格式是否是常见格式，例如 `Player joined the game`。
- 第一次配置日志时不会回放历史日志，只会处理之后新增的日志行。

### 开了 `chat` 但群服互通不工作

群到游戏需要：

- `mctoggle chat on`
- RCON 地址已设置
- RCON 密码已私聊设置
- RCON 端口可访问

游戏到群需要：

- `mctoggle chat on`
- `mclog` 已配置且日志可读
- 游戏聊天确实写入 `latest.log`

### `mcrcon` 提示权限不足

`mcrcon <命令>` 不按群管理员放行，需要超级用户或达到 `MC_RCON_LEVEL`。这是为了避免群管理员直接获得任意服务端命令执行能力。

### `mcwhitelist` 失败

`mcwhitelist` 会调用 RCON 执行 `whitelist add <玩家名>`，因此需要：

- RCON 地址和密码配置正确。
- 服务端开启 RCON。
- 玩家名符合 MC 正版/离线服实际使用的名字。

### BlueMap 没有坐标

检查：

- `mcbluemap` 的 `base_url` 是否是 BlueMap Web 根地址。
- `map_id` 是否和 URL 中 `/maps/<map-id>/...` 一致。
- Bot 是否能访问 `https://你的地图域名/maps/<map-id>/live/players.json`。
- 玩家是否被 BlueMap live players 接口返回。

BlueMap 只是增强源；失败时 `mcinfo` 仍会显示基础状态。

## 当前限制

- 每群当前只绑定一个服务器。
- 天气暂不查询，显示“未知/未配置数据源”。
- RCON 密码明文存数据库；请使用专用低权限密码，并限制 RCON 端口访问来源。
- 日志解析覆盖 Paper 常见英文日志格式；如果安装了改日志格式的插件，可能需要扩展解析器。
- Bot 离线期间无法准确统计在线时长，只能从重新观测时刻继续记录。
