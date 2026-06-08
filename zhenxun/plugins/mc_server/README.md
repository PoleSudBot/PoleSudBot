# MC 服务器助手

`mc_server` 是真寻第一方 MC 服务器配套插件。当前版本采用“每群一个绑定入口、同服数据共享”设计：一个 QQ 群一次只绑定一个 Minecraft Java 服务器；多个群绑定同一预设或同一地址端口时，共享服务器统计、日志、RCON 和 BlueMap 配置。

插件提供状态查询、Paper 日志监听、进退服播报、断连/恢复播报、群服聊天互通、RCON 远程管理、白名单绑定、在线时长统计和在线人数图表。

## 功能概览

- 服务器状态：通过 `mcstatus` 查询 Java 服状态、版本、延迟、在线人数和可见玩家列表。
- BlueMap 增强：可选读取 BlueMap live players JSON，补充玩家坐标和维度。
- 日志监听：读取 Paper `latest.log`，解析玩家加入、离开和游戏聊天。
- 三类开关：进退服播报、连接播报、聊天互通默认关闭，并且按群独立控制。
- RCON：通过 `mctools` 执行服务端命令，用于远程管理、聊天互通和白名单。
- 统计：按共享服务器的在线 session 累计玩家在线时长，并按唯一服务器轮询采样生成在线人数图。
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

如果大多数群都绑定同一批服务器，推荐先在配置文件中写好服务器预设，再让群管理员用 `mcbind <预设名>` 绑定。同名预设会指向同一个服务器实体，后续任一群通过 `mclog`、`mcbluemap`、`mcrcon set/passwd` 修改的是共享服务器配置；`mctoggle` 的三类开关仍只影响当前群。

### 0. 可选：配置服务器预设

配置项位于 `mc_server.MC_SERVER_PRESETS`，键名就是绑定时使用的预设名：

```yaml
mc_server:
  MC_SERVER_PRESETS:
    "1":
      host: "mc.example.com"
      port: 25565
      rcon_host: ""
      rcon_port: 25575
      rcon_password: "your-rcon-password"
      log_path: "/path/to/server/logs"
      bluemap_base_url: "https://map.example.com"
      bluemap_map_ids:
        - "world"
        - "world_nether"
```

`rcon_host` 为空时默认使用 `host`，因此同一台机器只需要写 `host`、`port` 和 `rcon_port`。`bluemap_base_url` 不带协议时会自动补 `http://`。

在目标群内发送：

```text
mcbind 1
```

插件会先探测预设中的 MC 地址，探测成功后把当前群绑定到该预设对应的共享服务器，并写入 RCON、日志和 BlueMap 配置。预设中的 RCON 密码会明文进入配置文件和数据库，请只使用 Bot 专用低权限密码，并限制 RCON 端口访问来源。

也可以使用绑定向导完成首次配置。群内只填写 MC/RCON 地址，RCON 密码和日志路径会转到私聊中继续配置。

### 1. 启动绑定向导

在目标群内发送：

```text
mcbind
```

向导会让你一次输入 MC 地址、MC 端口和 RCON 端口：

```text
mc.example.com 25565 25575
```

IPv6 地址需要用方括号：

```text
[::1] 25565 25575
```

地址填写后插件会先用 Java status ping 探测服务器。探测成功才会保存 MC 地址和同 host 的 RCON 地址；探测失败不会覆盖旧绑定。

### 2. 私聊配置 RCON 密码和日志

地址和端口保存后，Bot 会私聊发起人。按两行回复：

```text
RCON密码
latest.log路径或logs目录
```

如果暂时不配置日志，第二行写 `skip` 或省略：

```text
my-rcon-password
skip
```

密码不会在群内回显，但会按 MVP 方案明文存入数据库。数据库管理员、数据库备份或导出文件的读取者都可以看到该密码。建议为 Bot 单独设置低权限 RCON 密码，并通过防火墙或内网限制 RCON 访问来源。私聊保存后会自动执行 `mcrcon list` 验证，失败时按私聊提示重发即可。

### 3. 中途退出

向导任意步骤都可以输入以下任一内容结束：

```text
q
quit
退出
取消
```

单步等待超时时间由 `MC_BIND_FLOW_TIMEOUT_SECONDS` 控制，默认 `120` 秒。

### 4. 单步/修正配置

如果你只想快速改服务器地址，也可以继续使用单步命令：

```text
mcbind mc.example.com:25565
mcbind mc.example.com 25565 25575
```

单步 `mcbind <地址>` 同样会先探测服务器，探测成功后才保存。同一规范化地址端口会复用同一个服务器实体，例如多个群都绑定 `127.0.0.1:25565` 时，在线时长、人数采样、日志游标和玩家绑定都会共享。`mcbind <地址> <服务器端口> <RCON端口>` 会把 MC 地址和同 host 的 RCON 地址一起保存，适合 MC 端口和 RCON 端口不同但 IP 相同的服务器。

日志、BlueMap、RCON 地址和 RCON 密码也保留单步命令，适合后续修正。这些属于共享服务器配置，修改后所有绑定到同一服务器的群都会使用新配置。日志路径必须是 Bot 进程能读取到的本地路径；第一次设置日志路径时，插件会从文件末尾开始读，不会把历史日志刷屏。

```text
mclog /path/to/server/logs
mcbluemap https://map.example.com world
mcrcon set mc.example.com:25575
mcrcon passwd <群号> <RCON密码>
```

### 5. 开启需要的播报或互通

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

这些开关只影响当前群。同一服务器绑定到多个群时，某个群开启 `join` 不会让其他群一起收到进退服播报。

查看当前群绑定和开关状态：

```text
mclist
```

### 6. 验证是否可用

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

查询当前群绑定服务器的实时状态。显示在线状态、延迟、版本、在线人数、可见玩家、玩家当前在线时长、累计在线时长和 BlueMap 坐标。

```text
mclist
```

显示当前群绑定状态、日志路径、BlueMap、RCON 和开关状态。

```text
mctime [范围]
mctime @用户 [范围]
mctime 玩家名 [范围]
mctime me|我|自己 [范围]
mct [范围]
mct @用户 [范围]
mct 玩家名 [范围]
```

不指定用户时查询在线时长排行，并显示每位玩家的总在线与日平均在线。日平均只统计查询范围内该玩家从首次可观测在线日到最后登录日之间的业务日，不会把新玩家除以整个周目天数，也不会让不再上线的玩家被后续日期持续稀释。指定 `@用户`、玩家名、`me`、`我` 或 `自己` 时生成个人在线情况图，并显示该范围内总在线时长和日平均在线。

当查询范围超过 1 个业务日且包含今日时，`mctime/mct` 排行和个人图会附加 `本日在线`，表示该玩家从今日 06:00 到当前查询时刻的在线时长。纯历史范围（如 `昨日`、`上周`、`上月`）不会显示这一栏。

默认范围：

- `mctime` / `mct` 直接使用时：本周目排行。
- `mctime @用户` / `mctime 玩家名` / `mctime 我`：本周个人在线图。
- `mcchart` / `mcc` 直接使用时：今日在线人数图。

所有统计范围都以 06:00 为日期分界。

所有涉及时间范围的图片和文字 fallback 都会显示实际起止时间，格式为 `yy-mm-dd hh:mm ~ yy-mm-dd hh:mm`；如果范围截止到当前时刻，结束时间留空，例如 `26-06-09 06:00 ~`。

范围支持：

- `今日` / `今天` / `today`
- `昨日` / `昨天` / `yesterday`
- `本周` / `week`
- `上周` / `last_week`
- `本月` / `month`
- `上月` / `last_month`
- `本周目` / `season`
- `YYYY-MM-DD`
- `YYYY-MM-DD..YYYY-MM-DD`

示例：

```text
mctime
mctime 今日
mctime 上周
mctime 2026-05-01..2026-05-18
mctime @某人 本周
mctime Letemps
mctime 我 今日
mct 本周
```

```text
mcchart [范围]
mcc [范围]
```

生成在线人数变化图，范围语法同 `mctime`。不写范围时默认查询今日。

示例：

```text
mcchart 今日
mcchart 昨日
mcchart 本月
mcc
mcc 今日
mcc 上周
```

### 群管理配置

以下命令需要超级用户、群主/群管理员，或达到 `MC_ADMIN_LEVEL`。

```text
mcbind
mcbind <预设名>
mcbind <地址[:端口]>
mcbind <地址> <服务器端口> <RCON端口>
```

不带参数时进入绑定向导：群内一次填写 MC 地址、MC 端口和 RCON 端口，RCON 密码和日志路径转到私聊保存并自动验证。向导中可用 `q` / `quit` / `退出` / `取消` 结束。

带预设名时会把当前群绑定到 `MC_SERVER_PRESETS` 中同名预设对应的共享服务器。带地址时走单步绑定；三参数模式会同时保存 MC 地址和同 host 的 RCON 地址。每群只保留一个服务器绑定，重复执行会切换当前群绑定；若目标服务器已经被其他群绑定，历史统计会继续复用。无论向导、预设还是单步模式，都会先用 Java status ping 探测成功后才保存。

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

默认值全部为 `off`，避免刚配置时刷屏。`all` 会同时操作当前群的 `join`、`conn`、`chat` 三类开关。

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
| `MC_REJOIN_SUPPRESS_SECONDS` | `30` | 玩家短时间重进时抑制进退服播报的窗口，最小按 5 秒处理。 |
| `MC_ADMIN_LEVEL` | `5` | 群内 MC 配置管理所需真寻权限等级。 |
| `MC_RCON_LEVEL` | `9` | 群内执行任意 RCON 命令所需真寻权限等级。 |
| `MC_CHAT_FORMAT` | `[{sender}] {message}` | 群消息同步到游戏内的显示格式。 |
| `MC_RENDER_ENABLED` | `True` | 是否优先使用 htmlrender 渲染图片。 |
| `MC_MAX_CHART_POINTS` | `96` | 人数图最多渲染的数据点数量。 |
| `MC_SERVER_PRESETS` | `{}` | 服务器预设表，供 `mcbind <预设名>` 绑定到共享服务器。 |

`MC_CHAT_FORMAT` 支持两个占位符：

```text
{sender}
{message}
```

例如：

```text
[QQ/{sender}] {message}
```

`MC_SERVER_PRESETS` 的每个预设支持以下字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | 必填 | MC Java 服务器地址。 |
| `port` | `25565` | MC Java 服务器端口。 |
| `rcon_host` | 同 `host` | RCON 地址；为空时使用 MC 地址。 |
| `rcon_port` | `25575` | RCON 端口。 |
| `rcon_password` | `""` | RCON 密码，会明文保存到配置和服务器数据库。 |
| `log_path` | `""` | `latest.log` 文件或 `logs` 目录；不可读时绑定成功但保留旧日志配置。 |
| `bluemap_base_url` | `""` | BlueMap Web 根地址。 |
| `bluemap_map_ids` | `[]` | BlueMap 地图 ID 列表。 |

## 数据口径

### 共享服务器

服务器数据按身份共享：

- `mcbind <预设名>` 优先使用预设名作为服务器身份。
- 手动绑定使用规范化 `host:port` 作为服务器身份。
- 同一服务器只轮询一次，在线时长、人数采样、玩家、QQ 绑定、日志游标、RCON、BlueMap 和日志路径都会共享。
- 群绑定和 `join` / `conn` / `chat` 开关按群独立保存。

旧版本按群保存的 `mc_server` 数据会在启动时自动迁移。迁移会按规范化 `host:port` 合并旧服务器行，例如两个群都手动绑定 `127.0.0.1:25565` 时，会保留旧在线 session 和人数采样，并为两个群建立独立绑定。`localhost` 和 `127.0.0.1` 不会自动视为同一个地址，避免误合并。

### 在线时长

在线时长使用 session 段累计：

- 日志里出现 `joined the game` 时开启在线段。
- 日志里出现 `left the game` 时会先等待 `MC_REJOIN_SUPPRESS_SECONDS`；如果玩家没有短时间重进，再按原始退出时间关闭在线段。
- 短时间重进会被视为同一次在线，不发送进退服播报，也不会切断本次在线时长。
- 服务器启动完成日志会截断重启前遗留的 active session。
- 如果状态查询拿到的玩家名单数量和在线人数一致，插件会把“不在名单里的 active 玩家”静默关掉；名单不完整时只补“看见的人在线”，不反推其他人下线。
- Bot 重启时会先截断旧的 active session。
- 如果状态查询能看到玩家在线，插件会从重新观测到的时间点开启在线段。

这意味着 Bot 离线期间的真实在线时间无法反推，统计会以 Bot 重新观测时间为起点。

`mctime @用户` / `mctime 玩家名` / `mctime 我` 的个人在线情况图也使用同一批 session 数据。5 个业务日及以内会按小时展示在线时长，超过 5 个业务日会按 06:00 业务日汇总；仍在进行中的在线段会临时截断到查询结束时间。如果 QQ 是后来才绑定的，插件会同时按当前绑定玩家查找历史 session。

### 人数图

人数图来自状态轮询采样表：

- 只有服务器状态查询成功时才会采样。
- 采样间隔由 `MC_SAMPLE_INTERVAL_SECONDS` 控制。
- 图表点数超过 `MC_MAX_CHART_POINTS` 时会降采样显示。
- 直接使用 `mcc` 时，默认范围是今日。
- `mcc` 支持 `今日`、`昨日`、`本周`、`上周`、`本月`、`上月`、`本周目` 和日期范围，全部按 06:00 分界。

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
- 手动绑定只按规范化 `host:port` 自动共享，不会自动合并 DNS 别名、`localhost` 与 `127.0.0.1` 这类不同写法。
- 天气暂不查询，因此 `mcinfo` 不展示天气字段。
- RCON 密码明文存数据库；请使用专用低权限密码，并限制 RCON 端口访问来源。
- 日志解析覆盖 Paper 常见英文日志格式；如果安装了改日志格式的插件，可能需要扩展解析器。
- Bot 离线期间无法准确统计在线时长，只能从重新观测时刻继续记录。
