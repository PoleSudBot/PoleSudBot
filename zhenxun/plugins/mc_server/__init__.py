from __future__ import annotations

import nonebot
from nonebot.plugin import PluginMetadata

from .constants import PLUGIN_NAME

_USAGE = """
## MC 服务器助手

每个群可以绑定一个 MC 服务器。普通用户主要使用查询、统计、白名单和群服互通功能。
服务器管理者可发送 `mcbind` 进入绑定向导，或发送 `mcbind <预设名>` 使用配置里的服务器预设；完整配置教程见插件 README。

### 常用查询

- `mcinfo` / `mcstatus` / `mci`
  查看服务器状态、延迟、版本、在线人数、可见玩家和在线时长。
- `mclist`
  查看本群绑定状态、日志、BlueMap、RCON 和开关状态。

### 在线时长与图表

- `mctime` / `mct`
  查看本周目玩家在线时长排行，包含每位玩家日平均在线。
- `mctime [范围]` / `mct [范围]`
  查看指定范围的玩家在线时长排行。
- `mctime 我` / `mctime me` / `mctime 自己`
  查看自己本周的个人在线情况图。
- `mctime @用户 [范围]` / `mct @用户 [范围]`
  查看指定 QQ 绑定玩家的个人在线情况图。
- `mctime <玩家名> [范围]` / `mct <玩家名> [范围]`
  按 MC 玩家名查看个人在线情况图，不要求先绑定 QQ。
- `mctime 我 今日` / `mctime me 本周` / `mctime 自己 本月`
  查看自己指定范围的个人在线情况图。
- `mcchart [范围]` / `mcc [范围]`
  查看服务器在线人数变化图；直接使用 `mcc` 时默认查询 06:00 分界的本日。

### 群服消息互通

- `mcsend <消息>` / `mcs <消息>`
  将群消息同步到游戏内；成功静默，失败才提示。

聊天互通需要管理员先开启 `mctoggle chat on`，否则不会同步。

### 白名单

- `mcwhitelist <玩家名>` / `mcwhite <玩家名>` / `mcw <玩家名>`
  给自己加白名单。
- `mcwhitelist <玩家名> @用户`
  管理员代别人添加白名单并绑定 QQ。

绑定后，个人在线图和部分展示会优先使用该 QQ 与玩家名的关系。

### 管理者入口

- `mcbind`
  进入服务器绑定向导。
- `mcbind <预设名>`
  使用配置里的服务器预设绑定当前群。
- `mcbind <地址> <服务器端口> <RCON端口>`
  用同一个地址快速配置 MC 与 RCON 端口。
- `mctoggle all on` / `mctoggle all off`
  一键开启或关闭进退服、连接、聊天互通开关。
- `mcrcon <命令>` / `mcr <命令>`
  执行 RCON 命令，需要对应权限。

### 时间范围

统计类指令的 `[范围]` 支持：

- `mctime/mct` 排行不写范围时默认 `本周目`
- `mctime/mct` 个人查询不写范围时默认 `本周`
- `mcchart/mcc` 不写范围时默认 06:00 分界的 `本日`
- `今日` / `本周` / `本月` / `本周目`
- `YYYY-MM-DD`
- `YYYY-MM-DD..YYYY-MM-DD`
""".strip()


def _nonebot_ready() -> bool:
    try:
        nonebot.get_driver()
    except ValueError:
        return False
    return True


if _nonebot_ready():
    from zhenxun.configs.utils import Command, PluginExtraData, Task

    from . import commands as _commands
    from . import models as _models
    from . import scheduler as _scheduler
    from .config import REGISTER_CONFIGS

    _ = (_commands, _models, _scheduler)

    __plugin_meta__ = PluginMetadata(
        name=PLUGIN_NAME,
        description="MC服务器状态查询、日志监听、聊天互通、RCON与白名单管理。",
        usage=_USAGE,
        extra=PluginExtraData(
            author="k1yuyu",
            version="0.3.0",
            menu_type="游戏相关",
            configs=REGISTER_CONFIGS,
            commands=[
                Command(
                    command="mcbind",
                    params=["预设名|地址?"],
                    description="进入向导、使用预设或验证绑定当前群MC服务器",
                ),
                Command(command="mcinfo/mci", description="查询当前群MC服务器状态"),
                Command(command="mclist", description="查看当前群MC绑定与开关状态"),
                Command(
                    command="mctoggle/mctg",
                    params=["join|conn|chat|all", "on|off"],
                    description="切换MC播报/互通",
                ),
                Command(
                    command="mctime/mct",
                    params=["玩家名|@用户|me|范围"],
                    description="查询在线时长排行或个人在线情况图",
                ),
                Command(
                    command="mcchart/mcc",
                    params=["范围"],
                    description="生成在线人数变化图",
                ),
                Command(
                    command="mcsend/mcs",
                    params=["消息"],
                    description="同步群消息到游戏内，成功时静默",
                ),
                Command(
                    command="mcrcon/mcr",
                    params=["命令"],
                    description="执行RCON命令",
                ),
                Command(
                    command="mcwhitelist/mcw",
                    params=["玩家名"],
                    description="添加白名单并绑定QQ",
                ),
            ],
            tasks=[Task(module="mc_server", name="MC服务器状态轮询")],
            superuser_help="私聊 mcrcon passwd <群号> <密码> 设置RCON密码。",
        ).to_dict(),
    )
else:
    __plugin_meta__ = PluginMetadata(
        name=PLUGIN_NAME,
        description="MC服务器状态查询、日志监听、聊天互通、RCON与白名单管理。",
        usage=_USAGE,
    )
