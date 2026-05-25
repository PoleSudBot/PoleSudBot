# ruff: noqa: E501,W291
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import nonebot
from nonebot import on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.typing import T_State

from zhenxun.configs.config import Config
from zhenxun.configs.utils import Command, PluginExtraData, PluginSetting
from zhenxun.services.external_onebot_gateway import (
    ExternalOneBotAppSpec,
    external_onebot_gateway,
)
from zhenxun.services.external_onebot_gateway_config import (
    DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS,
    DEFAULT_AUTO_SLASH_ENABLED_GROUP_IDS,
    DEFAULT_ENABLE_AUTO_SLASH,
    DEFAULT_HELP_IMAGE_PATH,
    DEFAULT_HELP_URLS,
    REGISTER_CONFIGS,
    get_pjsk_app_settings,
    parse_config_bool,
)
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

APP_NAME = "pjsk"
HELP_COMMANDS = {"skhelp", "pjskhelp", "pjsk帮助", "sk帮助"}
LOOSE_MODE_COMMANDS = ("pjsk宽松模式", "pjsk快捷模式", "pjsk免前缀")
LOOSE_MODE_USAGE = "用法：pjsk宽松模式 <开启|关闭|状态> [群号]"
LOOSE_MODE_ACTIONS = {
    "开启": "enable",
    "打开": "enable",
    "启用": "enable",
    "on": "enable",
    "enable": "enable",
    "关闭": "disable",
    "关": "disable",
    "禁用": "disable",
    "off": "disable",
    "disable": "disable",
    "状态": "status",
    "查看": "status",
    "status": "status",
}
LOOSE_MODE_RISK_NOTICE = (
    "宽松模式会允许不带 / 的 PJSK 指令，匹配范围更宽，偶尔可能误触发。"
    "如果本群还有其他分布式 HarukiBot，双方可能因为“无前缀指令”互相回响；"
    "目前已有熔断兜底，但仍建议谨慎开启。\n"
    "关闭方式：本群发送 `pjsk宽松模式 关闭`；超级用户也可以发送 "
    "`pjsk宽松模式 关闭 <群号>`。"
)
PLUGIN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PLUGIN_DIR.parents[2]
_LOOSE_MODE_CONFIG_LOCK = RLock()


@dataclass(frozen=True)
class LooseModeCommand:
    action: str | None
    group_id: str | None = None
    error: str | None = None


async def _pjsk_rule(event: Event) -> bool:
    if not isinstance(event, GroupMessageEvent | PrivateMessageEvent):
        return False
    if not str(event.get_plaintext() or "").strip():
        return False
    # PJSK 是黑箱转发入口，先过滤当前后端所有 bot，避免同进程多 bot 互相回响。
    connected_bot_ids = {str(bot_id) for bot_id in nonebot.get_bots()}
    return str(event.user_id) not in connected_bot_ids


async def _pjsk_help_rule(event: Event) -> bool:
    if not isinstance(event, GroupMessageEvent | PrivateMessageEvent):
        return False
    # 帮助指令同样跳过同后端 bot，防止 bot 自己的说明文本被二次响应。
    connected_bot_ids = {str(bot_id) for bot_id in nonebot.get_bots()}
    if str(event.user_id) in connected_bot_ids:
        return False
    plain_text = str(event.get_plaintext() or "").strip().lower()
    if plain_text.startswith("/"):
        plain_text = plain_text[1:].strip()
    return plain_text in HELP_COMMANDS


def _parse_loose_mode_command(raw_text: str) -> LooseModeCommand | None:
    # 解析宽松模式管理命令，未命中指令前缀时交给后续 matcher。
    plain_text = str(raw_text or "").strip()
    if plain_text.startswith("/"):
        plain_text = plain_text[1:].strip()
    for command in LOOSE_MODE_COMMANDS:
        if plain_text == command:
            args_text = ""
        elif plain_text.startswith(f"{command} "):
            args_text = plain_text[len(command) :].strip()
        else:
            continue

        # 管理命令只接受一个动作和一个可选群号，避免宽泛入口吞掉普通 PJSK 查询。
        tokens = args_text.split()
        if not tokens:
            return LooseModeCommand(None, error=LOOSE_MODE_USAGE)
        action = LOOSE_MODE_ACTIONS.get(tokens[0].lower())
        if action is None:
            return LooseModeCommand(None, error=LOOSE_MODE_USAGE)
        if len(tokens) > 2:
            return LooseModeCommand(action, error=LOOSE_MODE_USAGE)
        group_id = tokens[1] if len(tokens) == 2 else None
        if group_id is not None and not group_id.isdecimal():
            return LooseModeCommand(action, error="群号必须是纯数字。\n" + LOOSE_MODE_USAGE)
        return LooseModeCommand(action, group_id=group_id)
    return None


async def _pjsk_loose_mode_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    # 将解析结果放入 state，让 handler 统一返回错误或执行业务逻辑。
    parsed = _parse_loose_mode_command(event.get_plaintext())
    if parsed is None:
        return False
    state["pjsk_loose_mode_command"] = parsed
    return True


def _is_group_admin(event: MessageEvent) -> bool:
    # OneBot 群消息会携带 sender.role，用它判断群主/管理员权限。
    if not isinstance(event, GroupMessageEvent):
        return False
    sender = getattr(event, "sender", None)
    return getattr(sender, "role", None) in {"admin", "owner"}


def _resolve_loose_mode_target_group(
    event: MessageEvent,
    parsed: LooseModeCommand,
    *,
    is_superuser: bool,
) -> tuple[str | None, str | None]:
    # 超级用户可以跨群管理；私聊没有当前群上下文，因此必须显式给群号。
    current_group_id = (
        str(event.group_id) if isinstance(event, GroupMessageEvent) else None
    )
    if is_superuser:
        if parsed.group_id:
            return parsed.group_id, None
        if current_group_id:
            return current_group_id, None
        return None, "超级用户私聊操作时需要指定群号。\n" + LOOSE_MODE_USAGE

    # 群管理员只允许管理当前群，防止普通群管越权修改其他群配置。
    if not current_group_id:
        return None, "该指令仅群管理员或超级用户可用。"
    if parsed.group_id:
        return None, "只有超级用户可以指定群号；群管理员请在本群使用不带群号的指令。"
    if not _is_group_admin(event):
        return None, "该指令仅群管理员或超级用户可用。"
    return current_group_id, None


def _get_config_str_set(key: str, default: list[str]) -> set[str]:
    # 配置中心可能返回 list 或逗号字符串，这里统一成去空白后的字符串集合。
    value = Config.get(APP_NAME).get(key, default)
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, list | tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def _save_loose_mode_group_ids(
    enabled_group_ids: set[str],
    disabled_group_ids: set[str] | None = None,
) -> None:
    # 使用项目配置层持久化群列表，保持 WebUI 与运行时读取同一份配置。
    Config.set_config(
        APP_NAME,
        "AUTO_SLASH_ENABLED_GROUP_IDS",
        sorted(enabled_group_ids),
        auto_save=disabled_group_ids is None,
    )
    if disabled_group_ids is not None:
        Config.set_config(
            APP_NAME,
            "AUTO_SLASH_DISABLED_GROUP_IDS",
            sorted(disabled_group_ids),
            auto_save=True,
        )


def _is_loose_mode_globally_enabled() -> bool:
    # 全局总开关保留给部署侧兜底，关闭时群级启用列表不会实际生效。
    return parse_config_bool(
        Config.get(APP_NAME).get("ENABLE_AUTO_SLASH", DEFAULT_ENABLE_AUTO_SLASH),
        DEFAULT_ENABLE_AUTO_SLASH,
    )


def _build_loose_mode_status_message(group_id: str) -> str:
    # 状态展示区分“配置已开启”和“实际生效”，方便定位全局或兜底配置覆盖。
    with _LOOSE_MODE_CONFIG_LOCK:
        enabled_group_ids = _get_config_str_set(
            "AUTO_SLASH_ENABLED_GROUP_IDS",
            DEFAULT_AUTO_SLASH_ENABLED_GROUP_IDS,
        )
        disabled_group_ids = _get_config_str_set(
            "AUTO_SLASH_DISABLED_GROUP_IDS",
            DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS,
        )
        configured_enabled = group_id in enabled_group_ids
        globally_enabled = _is_loose_mode_globally_enabled()
        deny_listed = group_id in disabled_group_ids
        active = configured_enabled and globally_enabled and not deny_listed

    message = f"群 {group_id} 的 PJSK 宽松模式：{'开启' if active else '关闭'}。"
    if configured_enabled and not active:
        reasons = []
        if not globally_enabled:
            reasons.append("全局总开关 ENABLE_AUTO_SLASH 当前为关闭")
        if deny_listed:
            reasons.append("该群命中 AUTO_SLASH_DISABLED_GROUP_IDS 兜底禁用列表")
        if reasons:
            message += "\n配置已在启用列表中，但暂未实际生效：" + "；".join(reasons) + "。"
    return message


def _set_loose_mode_status(action: str, group_id: str) -> str:
    # 开关动作只维护群级启用列表，显式 / 指令与高级群硬拦截不受影响。
    if action == "status":
        return _build_loose_mode_status_message(group_id)

    with _LOOSE_MODE_CONFIG_LOCK:
        enabled_group_ids = _get_config_str_set(
            "AUTO_SLASH_ENABLED_GROUP_IDS",
            DEFAULT_AUTO_SLASH_ENABLED_GROUP_IDS,
        )
        disabled_group_ids = _get_config_str_set(
            "AUTO_SLASH_DISABLED_GROUP_IDS",
            DEFAULT_AUTO_SLASH_DISABLED_GROUP_IDS,
        )
        if action == "enable":
            enabled_group_ids.add(group_id)
            # 手动开启时移除旧禁用列表中的同群记录，避免历史配置让“开启成功”不生效。
            disabled_group_ids.discard(group_id)
            _save_loose_mode_group_ids(enabled_group_ids, disabled_group_ids)
            message = f"已为群 {group_id} 开启 PJSK 宽松模式。\n{LOOSE_MODE_RISK_NOTICE}"
            if not _is_loose_mode_globally_enabled():
                message += "\n注意：全局总开关 ENABLE_AUTO_SLASH 当前为关闭，本群配置已保存但暂不会生效。"
            return message

        enabled_group_ids.discard(group_id)
        _save_loose_mode_group_ids(enabled_group_ids)
    return f"已为群 {group_id} 关闭 PJSK 宽松模式。显式 / 指令仍可正常使用。"


def _resolve_help_image_path(value: str) -> Path:
    # 相对路径按仓库根目录解析，避免运行目录变化时找不到插件内默认截图。
    raw_path = value.strip() or DEFAULT_HELP_IMAGE_PATH
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _build_help_message_items() -> list[Path | str]:
    config_group = Config.get(APP_NAME)
    image_path = _resolve_help_image_path(
        str(config_group.get("HELP_IMAGE_PATH", DEFAULT_HELP_IMAGE_PATH) or "")
    )
    help_urls = str(config_group.get("HELP_URLS", DEFAULT_HELP_URLS) or "").strip()
    message_items: list[Path | str] = []

    # 帮助图是可选静态资源；缺失时只发网址，避免给普通用户暴露部署路径。
    if image_path.is_file():
        message_items.append(image_path)
    if help_urls:
        message_items.append(f"\n{help_urls}")
    return message_items


__plugin_meta__ = PluginMetadata(
    name="PJSK",
    description=(
        "由 HarukiBot NEO 提供的 Project SEKAI 查询功能，支持卡牌、"
        "歌曲、活动、榜线、个人资料等常用查询。"
    ),
    usage="""
> **⚠️ PJSK 宽松模式默认关闭**
>
> 默认只处理带 `/` 的 PJSK 指令，避免自然语言误触发。
> 如需允许本群使用不带 `/` 的快捷写法，群管理员或超级用户可发送：
>
> - `pjsk宽松模式 开启`
> - `pjsk宽松模式 关闭`
> - `pjsk宽松模式 状态`
>
> 超级用户也可在任意群聊或私聊指定群号：`pjsk宽松模式 开启 <群号>`。
> 开启后可能误触发，并且与其他分布式 HarukiBot 同群时存在互相回响风险，请谨慎使用。

---

> **📌 网页版帮助与工具箱指路**
>
> - **使用帮助**：https://neo.haruki.seiunx.com
> - **Haruki工具箱**：https://haruki.seiunx.com
>
> 💡 *提示：图片内的链接无法直接点击，您可以随时发送指令 **`sk帮助`** 直接获取可点击的网址链接！*

---

### 服务器支持与切换

+ HarukiBot NEO支持日服（jp）、台服（tw）、韩服（kr）、国际服（en）以及国服（cn）
  如果需要使用对应区服的功能需要在指令前加区服前缀（如 `/cn个人信息`）
+ HarukiBot NEO现在支持**全局默认绑定账号**和**区服默认绑定账号**，区服相关指令(如sk、sk线、时速等)如果不带区服前缀，会**默认采用**您的**全局默认绑定账号**的区服

> 如您的全局默认绑定为cn服账号，则使用 `/sk` 等效以前的 `/cnsk`

+ 部分功能不支持部分区服，会在功能内说明。

## 个人资料/账号

> **⚠️ 注意**
> 账号验证功能仅支持 `Haruki工具箱快速验证`
> 请确保您在工具箱中有已通过验证的绑定账号再使用此功能
> 上传个人信息背景需要验证账号，请确保你已验证账号再使用

| 指令 | 功能 |
| :--- | :--- |
| `/绑定` `/pjsk bind` `/pjsk id` `/pjsk 绑定` | 通过游戏uid绑定你的游戏账号，如"/绑定 114514"。 |
| `/个人信息` `/个人中心` `/profile` | 生成个人信息图片，可以输入uid查询指定账号的个人信息，没有uid时会查询自己的默认账号。 |
| `/绑定列表` `/pjsk bind list` `/pjsk绑定列表` | 列出所有当前qq号已绑定的账号 |
| `/交换绑定` | 交换已经绑定的两个账号的绑定顺序 |
| `/设置主账号` `/pjsk set main` `/pjsk主账号` `/设置默认绑定` | 通过游戏uid设置默认查询的账号，如/设置主账号 114514。 |
| `/清除默认绑定` `/取消默认绑定` `/取消主账号` `/清除主账号` | 无uid时会清除全局默认绑定，有uid时会清除对应账号的区服默认绑定。 |
| `/解绑` `/pjsk unbind` `/取消绑定` | 通过游戏uid解绑你的账号，如/解绑 114514。 |
| `/隐藏抓包` `/pjsk hide suite` `/pjsk隐藏抓包` | 隐藏suite抓包功能内的详细数据显示。 |
| `/展示抓包` `/pjsk show suite` | 展示suite抓包功能内的详细数据显示。 |
| `/隐藏烤森抓包` `/pjsk hide mysekai` | 隐藏烤森抓包功能内的详细数据显示。 |
| `/展示烤森抓包` `/pjsk show mysekai` | 展示烤森抓包功能内的详细数据显示。 |
| `/隐藏ID` `/隐藏id` `/pjsk hide id` | 在查询中隐藏自己的uid。 |
| `/显示ID` `/pjsk show id` `/显示id` | 在查询中显示自己的uid。 |
| `/抓包数据` `/pjsk check data` `/抓包状态` `/抓包信息` `/sud` | 查看自己的suite抓包数据上传时间。 |
| `/烤森抓包数据` `/msd` `/pjsk check mysekai data` `/烤森抓包` | 查看自己的烤森抓包数据上传时间。 |
| `/pjsk验证` `/pjsk verify` | 验证自己的游戏账号，**部分功能需要验证后使用**，无法快速验证请前往Haruki工具箱进行绑定 |
| `/pjsk验证列表` `/pjsk verify list` `/pjsk验证状态` | 查看已验证的账号列表。 |
| `/上传个人信息背景` `/pjsk upload profile bg` `/上传个人背景` | 上传自定义的个人信息背景图，需要消息内含有图片或回复上文中已有的某张图片。`请注意，需要先进行游戏账号验证才能使用!!!` |
| `/清空个人信息背景` | 清除自定义的个人信息背景图 |
| `/设置个人信息` `/pjsk adjust profile` `/调整个人信息背景` | 调整自定义个人信息背景图，可选横屏/竖屏、模糊以及透明度。模糊度范围为0~10，透明度范围为0~100。如：/调整个人信息 竖屏 模糊5 透明50 |
| `/查时间` `/注册时间` `/pjsk reg time` | 查询账号的注册时间 |

## 卡牌

| 指令 | 功能 |
| :--- | :--- |
| `/查卡` `/card-detail` `/查牌` `/查卡牌` `/pjsk card` | 按指定属性、人物或id查卡 |
| `/卡牌列表` `/cards` `/pjsk cards` `/card-list` | 按指定条件筛选卡牌列表 |
| `/卡牌一览` `/查箱` `/卡面一览` `/卡一览` `/box` `/card-box` `/pjsk box` | 按指定条件筛选卡牌，如果有抓包上传的suite数据，未拥有的卡牌会以灰色显示 |
| `/查卡面` `/pjsk card img` `/卡面原图` `/卡面` `/card` `/卡图` | 按id查询指定卡的卡图 |

> **💡 卡牌相关的可选参数及示例：**
>
> - **团名**：`ln` `vbs` `ws` `mmj` `25`
> - **对应团oc/纯vs**：`mmjoc` `25oc` `纯v`
> - **对应团vs**：`mmjv` `25v`
> - **角色昵称**：`miku` `mnr`
> - **卡牌稀有度**：`4` `四星` `生日` `4星`
> - **卡牌属性**：`cool` `蓝` `蓝星`
> - **限定类型**：`非限` `限定` `期间限定` `fes限定`
> - **卡牌技能类型**：`奶卡` `奶` `判` `分` `p分`
> - **年份**：`2025年` `今年` `去年`
> - **活动id或者箱活缩写**：`event123` `mnr1`
>
> **以上参数可以混合使用，用空格分隔。查询单张卡牌的可用参数格式：**
> - 卡牌id：`123`
> - 角色昵称+负数索引，表示角色的倒数第几张卡：`miku-1`

## 音乐/乐曲

| 指令 | 功能 |
| :--- | :--- |
| `/查曲` `/查歌` `/查乐` `/查音乐` `/查询乐曲` `/查歌曲` `/歌曲` `/乐曲` `/song` `/music` | 查询单曲信息 |
| `/难度排行` `/歌曲列表` `/歌曲一览` `/乐曲列表` `/乐曲一览` `/定数表` `/歌曲定数` `/查乐曲` `/music-list` `/pjsk music list` | 查询指定难度等级下的歌曲列表，如果有抓包上传的suite数据会显示clear/fc/ap进度 |
| `/谱面预览` `/pjsk chart` `/谱面查询` `/铺面查询` `/铺面预览` `/谱面` `/铺面` `/查谱面` `/查铺面` `/查谱` `/技能预览` | 查询指定歌曲的谱面预览 |
| `/谱面样式` `/谱面底色` `/设置谱面样式` `/设置谱面底色` `/pjsk chart style` | 指定查询谱面预览时对应的色调（可选white/black） |
| `/打歌奖励` `/曲目奖励` `/歌曲奖励` `/music rewards` `/music-rewards` `/pjsk music rewards` `/歌曲挖矿` `/打歌挖矿` | 查询指定账号剩余的 打歌奖励/挖矿奖励（⚠️需要上传suite数据） |
| `/pjsk进度` `/打歌进度` `/歌曲进度` `/打歌信息` `/progress` `/music-progress` `/pjsk music progress` `/pjsk progress` | 查询指定账号指定难度clear/fc/ap完成度（⚠️需要上传suite数据） |
| `/查物量` `/pjsk note num` `/pjsk note count` `/物量` | 查询指定物量下有哪些歌曲 |
| `/查bpm` `/pjsk bpm` `/查BPM` | 查询指定BPM下有哪些歌曲 |
| `/查曲绘` `/pjsk music cover` `/曲绘` | 查询指定歌曲的曲绘原图 |

> **💡 乐曲相关的可选参数及示例：**
>
> - **难度**：支持大部分写法和缩写，例如 `easy` `ma` `APD`，不指定则默认 MASTER 难度
> - **歌曲别名**：每个服务器单独一个别名库，但是在本服别名库未匹配到时会从其他服查找
>
> **查询单首歌曲的可用参数格式：**
> - 歌曲id，注意id和数字之间不能有空格：`id123`
> - 负数索引，表示倒数第几首歌曲：`-1`
> - 活动id查活动曲：`event123`
> - 箱活缩写，例如mnr一箱曲：`mnr1`
> - 添加的歌曲别名：`虾` `龙`
> - 歌曲名，任意语言均可，会进行模糊匹配：`六兆年零一夜物语`
>
> **查询多首歌曲的可用参数格式：**
> - 不加参数：默认查询MASTER难度的全部歌曲
> - 某个难度全部歌曲：`expert`
> - 某个难度单个等级歌曲：`expert 27`
> - 某个难度 闭区间等级范围 的歌曲：`expert 25 37`

## 活动

| 指令 | 功能 |
| :--- | :--- |
| `/活动列表` `/pjsk events` `/events` `/活动一览` `/event-list` | 活动列表 |
| `/查活动` `/pjsk event` `/活动` `/event` | 查询当前/指定活动信息 |
| `/冲榜记录` `/pjsk event record` `/活动记录` | 查询指定账号的冲榜记录（⚠️需要上传suite数据） |

> **💡 活动相关的可选参数及示例：**
>
> **查询单个活动的可用参数格式：**
> - 直接使用活动id：`123`
> - 负数索引，表示倒数第几个活动：`-1`
> - 箱活缩写，例如mnr一箱：`mnr1`
>
> **查询多个活动的筛选方式：**
> - 团名英文缩写：`mmj` `vs`
> - 查询某个角色有出场的活动，可以用空格分隔多个角色：`miku` `miku ick`
> - 查询角色箱活：`ick箱` `ickban`
> - 查询混活：`混活`
> - bonus属性：`cool` `蓝` `蓝星`
> - 年份：`2025年` `今年` `去年`
> - 活动类型：`普活` `5v5` `wl`
>
> *以上参数可以混合使用，用空格分隔*

## 榜线/SK

| 指令 | 功能 |
| :--- | :--- |
| `/sk线` `/sk-line` `/榜线` `/pjsk sk line` `/skl` | 查询榜线 |
| `/sk` `/sk-query` `/sk查询` `/sk查分` `/pjsk sk board` `/pjsk board` | 查指定分数榜位 |
| `/wlsk线` | 查wl单榜分数榜位 |
| `/时速` `/pjsk sk speed` `/sks` `/skv` `/sk时速` `/sk-speed` | 查询当前榜线时速 |
| `/日速` `/pjsk sk daily speed` `/skds` `/skdv` `/sk日速` | 查询榜线日均速度 |
| `/查房` `/sk-check-room` `/sk查房` `/cf` `/pjsk查房` | 查询当前房间周回时速等信息 |
| `/ptr` `/sk-player-trace` `/玩家轨迹` `/pjsk玩家追踪` | 查询账号在当前活动的冲榜统计数据 |
| `/档线轨迹` `/sk-rank-trace` `/rtr` `/skt` `/sklt` `/pjsk追踪` | 查询档线历史轨迹 |
| `/sk预测` `/pjsk sk predict` `/榜线预测` `/skp` | 查询榜线预测（33kit/moesekai/sekarun，暂不支持WL单榜） |
| `/5v5预测` `/pjsk winrate predict` `/胜率预测` `/胜率` `/预测胜率` | 查询5v5 胜率 |
| `/csb` | 查询指定排名的热力图数据 |

> **💡 榜线相关的可选参数及示例：**
>
> `/时速`、`/半日速`、`/日速` 指令后面可以跟数字，将特定时间范围内的PT增长转换为对应速度
> 如 `/时速10` = 10分钟内PT增长量转换的时速
> 可输入数字单位为分钟，最大不超过1440分钟(即一天)

## suite相关查询

+ 这部分功能需要抓包上传suite后才能使用，如果使用中遇到问题请先用 `/抓包数据` 检查自己的suite上传是否成功。

### 组卡

| 指令 | 功能 |
| :--- | :--- |
| `/活动组卡` `/活动组队` `/活动卡组` `/活动配队` `/组卡` `/组队` `/配队` `/指定属性组卡` `/模拟组卡` `/pjsk event card` `/pjsk event deck` `/pjsk deck` | 根据当前活动加成计算组队 |
| `/挑战组卡` `/挑战组队` `/挑战卡组` `/挑战配队` `/pjsk challenge card` `/pjsk challenge deck` | 根据对应角色计算每日挑战组队 |
| `/长草组卡` `/长草组队` `/最强卡组` `/最强组卡` `/pjsk no event deck` `/pjsk best deck` | 根据指定条件计算组队 |
| `/加成组卡` `/加成组队` `/控分组卡` `/控分配队` `/pjsk bonus deck` `/pjsk bonus card` | 根据指定加成计算组队 |
| `/烤森组卡` `/烤森组队` `/ms组卡` `/ms组队` `/mysekai deck` `/pjsk mysekai deck` | 根据当前活动计算最适合用于挖烤森获取pt的队伍（国服不开放） |

> **💡 组卡部分的可用参数及示例：**
>
> | 参数类型 | 可用参数 | 注意事项 |
> | :--- | :--- | :--- |
> | 歌曲名和难度 | 日服已实装的所有歌曲 所有难度 | |
> | live类型 | `协力` `单人` `solo` `自动` `auto` | |
> | 组卡目标 | 综合力 实效 | |
> | 指定体力 | `1火` `5火` `10火` 等 | （默认0火） |
> | 指定活动 | `114` `活动114` `event114` | 在加成组卡中不要使用仅数字格式！ |
> | 指定wl章节 | `140 wl1` `140 miku` | |
> | 指定团名+颜色加成 | 任意团 任意颜色 | |
> | 指定最低实效要求 | 任意实效 | |
> | 指定区域道具配置 | `顶配` `次顶配` | 使用与你卡组无关的顶配组卡，顶配区域道具为满级，次顶配为15级。 |
> | 指定某个团或颜色的卡牌 | 仅任意团 仅任意颜色 | |
> | 移除指定卡牌 | `-任意卡牌的id` | |
> | 包含指定卡牌 | 任意卡牌的id | ⚠️必须放在所有参数的最后，且无法固定卡牌顺序。 |
> | 指定强制包含角色 | 任意角色 | ⚠️必须放在所有参数最后，首个角色固定为队长，无法与指定卡牌同时使用 |
> | 不更换bfes卡牌技能 | `bf不变` | 添加该参数后固定为抓包时设置的技能 |
> | 指定卡牌养成状态 | `禁用` `满破` `满技能` `已读` `画布` `支援满破` `支援满技能` | 直接加上会作用于全部卡牌，前面跟着卡牌id/稀有度则仅作用于指定对象（如：四星满破满技能、114满破满技能、514禁用，注意不要用空格隔开） |
> | 指定区域道具能级 | 区域道具x级 | 将所有区域道具提升至指定等级 |
> | 指定技能顺序 | `技能顺序` | 自动计算: `技能顺序平均`/`最大`/`最小`。手动指定: `技能顺序12345` 只能当卡组完全固定时使用 |
> | 指定bfes花前吸取技能 | `技能吸取` | 可选：`技能吸取平均` / `技能吸取最大` / `技能吸取最小` |
>
> **⚠️请注意，参数之间一定要加空格，不然会识别失败**
>
> **【活动组卡示例】**
> - `/组卡` 当期活动组卡
> - `/组卡 龙 hd` 歌曲为龙hard
> - `/组卡 mmj 蓝` mmj+蓝色加成的模拟活动组卡
> - `/组卡 140 wl1 实效200` 140期WL第一章，实效至少为200
> - `/组卡 160 auto #mnr` 160期活动auto组卡，队长角色强制为mnr
> - `/组卡 歌曲比较 当前` 使用当前主队比较所有歌曲的活动pt
>
> **【挑战组卡示例】**
> - `/挑战组卡` 所有角色各组一队
> - `/挑战组卡 miku 群青`
> - `/挑战组卡 mnr 歌曲比较 10th 群青apd` 比较指定两首歌的mnr挑战分数 (apd指定角色和歌曲)
>
> **【最强组卡示例】**
> - `/最强组卡` 和活动无关的分数最大组卡
> - `/最强组卡 综合` 综合力最大组卡
> - `/最强组卡 实效` 实效最大组卡(支援队)
>
> **【加成组卡示例】**
> - `/加成组卡 100` 当期活动凑100加成
> - `/加成组卡 120 130` 凑120或130加成
> - `/加成组卡 event123 100` 指定活动
>
> **【烤森组卡示例】**
> - `/烤森组卡` 当期活动烤森组卡
> - `/烤森组卡 event123` 指定活动
> - `/烤森组卡 绿 mmj` 模拟绿mmj加成活动

## 养成

| 指令 | 功能 |
| :--- | :--- |
| `/每日挑战` `/pjsk challenge info` `/挑战信息` `/挑战详情` `/挑战进度` | 查询账号每日挑战的奖励获取进度。 |
| `/角色加成` `/pjsk power bonus info` `/加成信息` `/加成详情` `/加成进度` | 查询账号的各角色加成信息。 |
| `/区域道具` `/pjsk area item` `/area item` `/区域道具升级` | 查询对应区域道具升级所需要的素材 |
| `/羁绊` `/pjsk bonds` `/羁绊等级` `/角色羁绊` `/牵绊` | 查询账号的羁绊等级 |
| `/队长统计` `队长次数` `/领队统计` `/角色领队` `/pjsk leader count` | 查询队长次数 |

## MySekai相关查询

> **ℹ️ 提示**
> - Android 用户建议使用[Haruki工具箱-上传MySekai数据](https://haruki.seiunx.com/upload_mysekai) 的`继承码上传`
> - 台服/韩服 Android用户教程参考：[Haruki工具箱-HarukiProxy使用教程](/haruki-proxy/)
> - iOS / iPadOS 用户建议使用代理工具MitM模块更新，教程参考：[Haruki工具箱-iOS模块上传数据教程](/toolbox-tutorial/ios-module)

> **⚠️ 注意**
> - **所有 MySekai 指令需用户绑定 Haruki工具箱 账号**
> - **本功能不支持国服**

| 指令 | 功能 |
| :--- | :--- |
| `/msa` `/pjsk mysekai res` `/mysekai-resource` `/mysekai资源` `/烤森资源` | 查询烤森信息 （资源 天气 来访角色等） |
| `/msm` `/pjsk mysekai map` `/mysekai-map` `/mysekai地图` `/烤森地图` `/msmap` | 查询烤森地图 |
| `/msam` | 同时输出`msa`和`msm`对应的统计信息以及四张烤森地图 |
| `/烤森对话列表` `/mysekai-talk-list` `/mysekai对话列表` | 查询烤森角色对话列表 |
| `/烤森家具列表` `/mysekai-fixture-list` `/mysekai家具列表` | 查询账号已获得家具列表 |
| `/家具列表` `/pjsk mysekai furniture` `/pjsk mysekai fixture` `/msf` `/mysekai 家具` | 查询所有家具列表 |
| `/msg` `/pjsk mysekai gate` `/mysekai-door-upgrade` `/mysekai大门升级` `/烤森大门升级` `/msgate` | 查询烤森大门升级所需材料 |
| `/msr` `/pjsk mysekai musicrecord` `/mysekai-music-record` `/mysekai唱片` `/烤森唱片` `/mss` `/mssong` | 查询烤森音乐唱片收集 |
| `/msb` `/pjsk mysekai blueprint` `/mysekai blueprint` `/mysekai 蓝图` | 查询烤森蓝图列表 |
| `/msp` `/pjsk mysekai photo` `/pjsk mysekai picture` `/mysekai 照片` | 展示烤森内拍摄的照片 |

## 昵称设置

| 指令 | 功能 |
| :--- | :--- |
| `/歌曲别名` `/pjsk alias` `/music alias` `/查歌曲别名` | 查询曲目别名 |
| `/添加歌曲别名` `/music alias add` `/pjsk alias add` | 添加曲目别名至待审核列表 |
| `/删除歌曲别名` `/music alias del` `/pjsk alias del` | 管理员删除曲目别名 |
| `/角色别名` `/pjsk chara alias` `/chara alias` `/查角色别名` | 查询角色别名 |
| `/添加角色别名` `/pjsk chara alias add` `/chara alias add` | 添加角色别名至待审核列表 |
| `/删除角色别名` `/pjsk chara alias del` `/chara alias del` | 管理员删除角色别名 |
| `/待审核别名` `/别名待审核` `/歌曲别名待审核` `/角色别名待审核` | 管理员查询待审核列表 |
| `/同意别名` `/通过别名` | 管理员审核通过别名 |
| `/拒绝别名` | 管理员审核拒绝别名 |

> **ℹ️ 提示**
> 所有歌曲昵称设置，角色昵称设置的日志内容将会在实时日志页面按日公示。
> 若违反相关条例将会视情况采取删除对应昵称至禁止使用Bot不等的措施。
> 审核别名需要对应权限，为管理员人工进行，请不要添加网络垃圾增加大家的工作量。

## 杂项

| 指令 | 功能 |
| :--- | :--- |
| `/生日` `/pjsk chara birthday` `/角色生日` `/查生日` | 查询角色生日 |
| `/贴纸` `/查贴纸` `/pjsk贴纸` `/pjsk表情` `/pjsk stamp` `/stamp` | 查询贴纸 |
| `/pjsk live` `/虚拟live` `/pjsk vlive` `/vlive` | 查询虚拟 Live 信息 |
| `/逮捕` `/pjsk逮捕` `/pjsk arrest` | 查询指定账号乐曲clear/fc/ap进度，不指定uid会查询默认账号 |
| `/查卡池` `/pjsk gacha` `/卡池列表` `/卡池一览` `/卡池` | 查询卡池列表 |
| `/pjsktz` | 设置你所在的时区，HarukiBot NEO一切和时间有关的信息都会以你设置的时区渲染 |
""".strip(),
    extra=PluginExtraData(
        author="Team-Haruki",
        version="2.0.0",
        plugin_type=PluginType.NORMAL,
        menu_type="游戏相关",
        setting=PluginSetting(default_status=True),
        configs=REGISTER_CONFIGS,
        commands=[
            Command(command="pjsk宽松模式 <开启|关闭|状态> [群号]", description="群级开关 PJSK 免 / 快捷触发"),
            Command(command="pjsk快捷模式 / pjsk免前缀", description="同 pjsk宽松模式"),
            Command(command="sk帮助", description="查看 PJSK 本地帮助图片与网址"),
            Command(command="skhelp / pjskhelp", description="查看 PJSK 本地帮助图片与网址"),
            Command(command="/haruki_info", description="查看 HarukiBot NEO 状态"),
            Command(command="查卡 [ID]", description="查询 Project SEKAI 卡牌信息"),
            Command(command="/查卡 [ID]", description="查询 Project SEKAI 卡牌信息"),
        ],
        ignore_prompt=True,
        is_show=True,
    ).to_dict(),
)


external_onebot_gateway.register_app(
    ExternalOneBotAppSpec(
        name=APP_NAME,
        display_name="PJSK",
        plugin_module=APP_NAME,
        settings_factory=get_pjsk_app_settings,
    )
)

# 本地帮助需要先于黑箱转发命中，避免 help 文本被自动补斜杠后发给 Haruki。
loose_mode_matcher = on_message(
    priority=0,
    block=True,
    rule=Rule(_pjsk_loose_mode_rule),
)
help_matcher = on_message(priority=0, block=True, rule=Rule(_pjsk_help_rule))
matcher = on_message(priority=1, block=False, rule=Rule(_pjsk_rule))


@loose_mode_matcher.handle()
async def _handle_pjsk_loose_mode(
    bot: OneBotV11Bot,
    event: MessageEvent,
    state: T_State,
):
    parsed: LooseModeCommand = state["pjsk_loose_mode_command"]
    if parsed.error or not parsed.action:
        await MessageUtils.build_message(parsed.error or LOOSE_MODE_USAGE).finish(
            reply_to=True
        )

    target_group_id, error = _resolve_loose_mode_target_group(
        event,
        parsed,
        is_superuser=await SUPERUSER(bot, event),
    )
    if error or not target_group_id:
        await MessageUtils.build_message(error or LOOSE_MODE_USAGE).finish(
            reply_to=True
        )

    # 完成权限与目标群解析后，再修改配置并返回明确的操作结果。
    await MessageUtils.build_message(
        _set_loose_mode_status(parsed.action, target_group_id)
    ).finish(reply_to=True)


@help_matcher.handle()
async def _handle_pjsk_help():
    await MessageUtils.build_message(_build_help_message_items()).finish(
        reply_to=True
    )


@matcher.handle()
async def _handle_pjsk_gateway(
    bot: OneBotV11Bot,
    event: MessageEvent,
    matcher: Matcher,
):
    # 黑箱回复由 reply 归因后手动写统计；这个轻量入口本身不参与自动统计。
    matcher.state["_statistics_skip"] = True
    if not isinstance(bot, OneBotV11Bot):
        return
    external_onebot_gateway.submit_event(APP_NAME, bot, event)
