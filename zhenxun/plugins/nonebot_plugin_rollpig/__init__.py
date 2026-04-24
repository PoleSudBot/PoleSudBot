import json
import random
import datetime
import time
import asyncio
from functools import wraps
import httpx
from pathlib import Path
from typing import Optional

from nonebot import on_command, require, get_driver, get_bot
from nonebot.adapters.onebot.v11 import Event, MessageSegment, Message, GroupMessageEvent, Bot
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from zhenxun.services.avatar_service import avatar_service
from zhenxun.services.group_settings_service import group_settings_service
from zhenxun.utils.platform import PlatformUtils

# 确保依赖插件先被 NoneBot 注册（必须在本地模块 import 之前）
# data_manager.py 在模块加载时会调用 store.get_plugin_data_file()
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_localstore")

from nonebot_plugin_htmlrender import template_to_pic

# 本地模块（在 require() 之后 import）
from .ranking import (
    PigKingEntry,
    resolve_collection_reached_at,
    sort_pig_king_rankings,
)

from .config import Config, GroupSettings, MODULE_NAME, get_proxy, get_storage_backend
from .roast_manager import roast_manager
from .runtime import (
    is_daily_summary_push_enabled,
    is_group_rollpig_enabled,
    resolve_roast_cooldown_seconds,
)
from .store import store
from .store.cloud import CloudStoreError
from .store.models import RoastEvent
from .summary_service import build_daily_summary
from .texts import (
    TOMORROW_TEXTS,
    FOOD_PIG_IDS, HUMAN_PIG_ID,
    FORCE_ROAST_KEYWORDS, SUPER_FORCE_ROAST_KEYWORD,
    TODAY_ROAST_HUMAN_BLOCK_TEXTS, TODAY_ROAST_FOOD_BLOCK_TEXTS,
    TARGET_HUMAN_BLOCK_TEXTS, TARGET_FOOD_BLOCK_TEXTS,
    BACKFIRE_HUMAN_TEXTS, BACKFIRE_FOOD_TEXTS,
    BACKFIRE_NO_PIG_TEXTS, BACKFIRE_GENERIC_TEXTS,
    ESCAPE_TEXTS,
    SUPER_FORCE_ROAST_PREFIX_TEXTS, FORCE_ROAST_PREFIX_TEXTS,
    FORCE_ROAST_LIMIT_TEXTS,
    ROAST_BOT_TEXTS,
    DAILY_SUMMARY_EMPTY_TEXTS, DAILY_SUMMARY_HEADER, DAILY_SUMMARY_FOOTER,
    PROTECTION_BLOCK_TEXTS, PROTECTION_BREAK_TEXTS,
    RANDOM_ROAST_INTRO_TEXTS,
)

# --- 引入 PIL ---
try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ========================================================

__plugin_meta__ = PluginMetadata(
    name="今天是什么小猪",
    description="抽取属于自己的小猪",
    usage="""
    🐷 基础指令：
    今日小猪 / 今天是什么小猪 - 抽取今天的命运之猪
    随机小猪 - 随机看一张猪图
    找猪 -  从 PigHub 模糊搜索猪猪图
    
    🔮 趣味指令：
    明日小猪 - 预测明天的猪猪运势
    昨日小猪 - 查看昨天抽到了什么
    今日烤猪 - 把今天的猪做成美食（人类/熟食形态会拦截）
    烤群友 - 把群友做成烤猪（目标需已抽猪且非人类/熟食）
    烤群友 + 打点后厨/偷换烤架/贿赂主厨/加急生火(兼容加急生活) - 每日一次强制成功（目标仍需已抽猪且非人类/熟食）
    烤群友 + 强行点火 - superuser 专属，无限强制成功（目标仍需已抽猪且非人类/熟食）
    开启猪圈日报 / 关闭猪圈日报 - 开关当前群的猪圈日报推送
    
    📊 统计指令：
    我的猪圈 / 我的小猪 - 查看解锁进度
    猪王争霸榜 / 猪猪榜 / 猪猪排行 / 小猪榜 / 小猪排行 [数量] - 查看当前群图鉴排行
    猪猪总榜 / 猪猪总排行 [数量] - 查看全局图鉴排行
    本周小猪 - 生成本周猪猪总结长图
    """,
    type="application",
    homepage="https://github.com/Felis2026/nonebot-plugin-rollpig",
    supported_adapters={"~onebot.v11"},
    config=Config,
    extra={
        "author": "Felis2026",
        "version": "0.5.0",
        "configs": [
            {
                "module": MODULE_NAME,
                "key": "AI_ENABLED",
                "value": False,
                "default_value": False,
                "help": "是否启用 AI 烤猪文案生成",
                "type": bool,
            },
            {
                "module": MODULE_NAME,
                "key": "LLM_MODEL_NAME",
                "value": None,
                "default_value": None,
                "help": "烤猪文案使用的 LLM 模型名；留空时使用真寻全局默认模型",
                "type": str,
            },
            {
                "module": MODULE_NAME,
                "key": "ROAST_COOLDOWN_HOURS",
                "value": 8.0,
                "default_value": 8.0,
                "help": "普通烤群友冷却时间（小时）",
                "type": float,
            },
            {
                "module": MODULE_NAME,
                "key": "STORAGE_BACKEND",
                "value": "local",
                "default_value": "local",
                "help": "存储后端，可选 local / cloud",
                "type": str,
            },
            {
                "module": MODULE_NAME,
                "key": "CLOUD_API_URL",
                "value": None,
                "default_value": None,
                "help": "cloud 存储服务地址",
                "type": str,
            },
            {
                "module": MODULE_NAME,
                "key": "CLOUD_TOKEN",
                "value": None,
                "default_value": None,
                "help": "cloud 存储服务鉴权 token",
                "type": str,
            },
            {
                "module": MODULE_NAME,
                "key": "CLOUD_TIMEOUT",
                "value": 3.0,
                "default_value": 3.0,
                "help": "cloud 存储请求超时时间（秒）",
                "type": float,
            },
            {
                "module": MODULE_NAME,
                "key": "CLOUD_STRICT_MODE",
                "value": True,
                "default_value": True,
                "help": "cloud 存储是否启用严格模式",
                "type": bool,
            },
            {
                "module": MODULE_NAME,
                "key": "PROXY",
                "value": None,
                "default_value": None,
                "help": "rollpig 外部请求代理地址",
                "type": str,
            },
        ],
        "group_config_model": GroupSettings,
    },
)

# ================= 资源路径 =================

PLUGIN_DIR = Path(__file__).parent
PIGINFO_PATH = PLUGIN_DIR / "resource" / "pig.json"
IMAGE_DIR = PLUGIN_DIR / "resource" / "image"
RES_DIR = PLUGIN_DIR / "resource"
PIGHUB_IMAGE_BASE_URL = "https://pighub.top/data/"
PIGHUB_TTL_SECONDS = 6 * 3600  # PigHub 图库缓存有效期（6小时）

pighub_images: list = []
pighub_last_loaded: float = 0.0

# ================= 资源加载 =================

def load_resource_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception as e:
        logger.error(f"资源文件读取失败: {path} error={e}")
        return default


PIG_LIST = load_resource_json(PIGINFO_PATH, [])
RANKING_CONCURRENCY_LIMIT = 8
PANEL_AVATAR_CONCURRENCY_LIMIT = 8
DEFAULT_RANK_LIMIT = 5
MAX_RANK_LIMIT = 50
LOCAL_RANKING_UNSUPPORTED_TEXT = "当前模式暂不支持查看猪王争霸榜，请稍后再试。"

# ================= 工具函数 =================

def find_image_file(pig_id: str) -> Path | None:
    exts = ["png", "jpg", "jpeg", "webp", "gif"]
    for ext in exts:
        file = IMAGE_DIR / f"{pig_id}.{ext}"
        if file.exists():
            return file
    return None


def get_pig_by_id(pig_id: Optional[str]) -> Optional[dict]:
    if not pig_id:
        return None
    for p in PIG_LIST:
        if p["id"] == pig_id:
            return p
    return None


def is_food_pig(pig_data: Optional[dict]) -> bool:
    return bool(pig_data and pig_data.get("id") in FOOD_PIG_IDS)


def is_human_pig(pig_data: Optional[dict]) -> bool:
    return bool(pig_data and pig_data.get("id") == HUMAN_PIG_ID)


def is_superuser_user(user_id: str) -> bool:
    superusers = {str(x) for x in getattr(get_driver().config, "superusers", set())}
    if user_id in superusers:
        return True
    return any(s.endswith(f":{user_id}") for s in superusers)


def detect_force_roast_mode(raw_text: str, user_id: str) -> Optional[str]:
    normalized = raw_text.replace("/", "").replace(" ", "").replace("　", "")
    has_super_cmd = SUPER_FORCE_ROAST_KEYWORD in normalized
    has_force_cmd = any(k in normalized for k in FORCE_ROAST_KEYWORDS)

    if has_super_cmd:
        return "super" if is_superuser_user(user_id) else "super_denied"
    if has_force_cmd:
        return "normal"
    return None


def pick_backfire_text(attacker_name: str, target_name: str, attacker_pig: Optional[dict]) -> str:
    if not attacker_pig:
        pool = BACKFIRE_NO_PIG_TEXTS
    elif is_human_pig(attacker_pig):
        pool = BACKFIRE_HUMAN_TEXTS
    elif is_food_pig(attacker_pig):
        pool = BACKFIRE_FOOD_TEXTS
    else:
        pool = BACKFIRE_GENERIC_TEXTS

    return random.choice(pool).format(attacker=attacker_name, target=target_name)


def pick_escape_text(attacker_name: str, target_name: str, target_pig: Optional[dict]) -> str:
    return random.choice(ESCAPE_TEXTS).format(attacker=attacker_name, target=target_name)


def pick_force_prefix_text(target_name: str, is_super_mode: bool) -> str:
    pool = SUPER_FORCE_ROAST_PREFIX_TEXTS if is_super_mode else FORCE_ROAST_PREFIX_TEXTS
    return random.choice(pool).format(target=target_name)


def pick_force_limit_text(operator_name: str, target_name: str) -> str:
    return random.choice(FORCE_ROAST_LIMIT_TEXTS).format(operator=operator_name, target=target_name)


def get_event_group_id(event: Event) -> str:
    return str(event.group_id) if isinstance(event, GroupMessageEvent) else ""


def get_event_user_name(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender:
        return getattr(sender, "card", "") or getattr(sender, "nickname", "") or str(getattr(event, "user_id", ""))
    return str(getattr(event, "user_id", ""))


def sanitize_display_name(name: str, user_id: str) -> str:
    cleaned = (name or "").replace("\n", " ").strip()
    return cleaned or user_id


def parse_rank_limit(raw_text: str, default: int = DEFAULT_RANK_LIMIT) -> int | None:
    text = raw_text.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return None
    return max(1, min(value, MAX_RANK_LIMIT))


def get_rank_usage(command_name: str) -> str:
    return f"用法：{command_name} [数量]，数量需为 1-{MAX_RANK_LIMIT} 的整数。"


def get_shape_label(pig_data: Optional[dict]) -> str:
    if not pig_data:
        return "未抽形态"
    if is_human_pig(pig_data):
        return "人类"
    return pig_data.get("name", "未知形态")


def format_roast_actor_display(name: str, user_id: str = "") -> str:
    safe_user_id = user_id or "未知用户"
    display_name = sanitize_display_name(name, safe_user_id)
    return display_name


def format_roast_subject_display(
    name: str,
    pig_data: Optional[dict],
    user_id: str = "",
) -> str:
    safe_user_id = user_id or "未知用户"
    display_name = sanitize_display_name(name, safe_user_id)
    if not pig_data:
        return display_name
    return f"「{display_name}({get_shape_label(pig_data)})」"


async def get_avatar_uri(user_id: str) -> str:
    avatar_path = await avatar_service.get_avatar_path("qq", user_id)
    return avatar_path.as_uri() if avatar_path else ""


async def build_avatar_uri_map(user_ids: list[str]) -> dict[str, str]:
    unique_user_ids = list(dict.fromkeys(uid for uid in user_ids if uid))
    if not unique_user_ids:
        return {}

    semaphore = asyncio.Semaphore(
        min(PANEL_AVATAR_CONCURRENCY_LIMIT, len(unique_user_ids))
    )

    async def _fetch_avatar(user_id: str) -> tuple[str, str]:
        async with semaphore:
            return user_id, await get_avatar_uri(user_id)

    results = await asyncio.gather(
        *[_fetch_avatar(user_id) for user_id in unique_user_ids],
        return_exceptions=True,
    )

    avatar_map: dict[str, str] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"rollpig 头像获取失败: {result}")
            continue
        user_id, avatar_uri = result
        avatar_map[user_id] = avatar_uri
    return avatar_map


async def is_group_admin_or_superuser(bot: Bot, event: GroupMessageEvent) -> bool:
    if await SUPERUSER(bot, event):
        return True
    return getattr(event.sender, "role", "") in {"admin", "owner"}


async def build_group_pig_rankings(bot: Bot, group_id: str) -> list[PigKingEntry]:
    members = await PlatformUtils.get_group_member_list(bot, group_id)
    if not members:
        return []

    unique_members: dict[str, str] = {}
    for member in members:
        member_id = str(member.user_id or "").strip()
        if not member_id:
            continue
        unique_members[member_id] = sanitize_display_name(
            member.card or member.name or "",
            member_id,
        )

    if not unique_members:
        return []

    manager = None
    if get_storage_backend() == "local":
        from .data_manager import get_data_manager

        manager = get_data_manager()

    semaphore = asyncio.Semaphore(min(RANKING_CONCURRENCY_LIMIT, len(unique_members)))

    async def _fetch_collection(member_id: str, display_name: str) -> PigKingEntry | None:
        async with semaphore:
            collection = await store.get_user_collection(member_id)
        collection_count = len(collection)
        if collection_count <= 0:
            return None

        # 群榜只关心当前群成员，逐个读取本地进度即可，避免为一次群查询扫描全量账本。
        progress = manager.get_collection_progress(member_id) if manager else None
        return PigKingEntry(
            user_id=member_id,
            display_name=display_name,
            collection_count=collection_count,
            reached_at=resolve_collection_reached_at(
                progress,
                collection_count,
            ),
        )

    results = await asyncio.gather(
        *[
            _fetch_collection(member_id, display_name)
            for member_id, display_name in unique_members.items()
        ],
        return_exceptions=True,
    )

    rankings: list[PigKingEntry] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"rollpig 群排行统计失败: {result}")
            continue
        if result:
            rankings.append(result)

    return sort_pig_king_rankings(rankings)


async def build_global_pig_rankings(
    bot: Bot | None = None,
    context_group_id: str = "",
) -> list[PigKingEntry]:
    from .data_manager import get_data_manager

    manager = get_data_manager()
    recent_names = manager.get_recent_user_names()
    progress_map = manager.get_all_collection_progress()

    if bot and context_group_id:
        members = await PlatformUtils.get_group_member_list(bot, context_group_id)
        for member in members:
            member_id = str(member.user_id or "").strip()
            if not member_id:
                continue
            recent_names[member_id] = sanitize_display_name(
                member.card or member.name or "",
                member_id,
            )

    rankings = [
        PigKingEntry(
            user_id=user_id,
            display_name=sanitize_display_name(recent_names.get(user_id, ""), user_id),
            collection_count=len(collection),
            reached_at=resolve_collection_reached_at(
                progress_map.get(user_id),
                len(collection),
            ),
        )
        for user_id, collection in manager.get_all_collections().items()
        if len(collection) > 0
    ]
    return sort_pig_king_rankings(rankings)


def get_group_rank_position(rankings: list[PigKingEntry], user_id: str) -> int | None:
    for index, entry in enumerate(rankings, start=1):
        if entry.user_id == user_id:
            return index
    return None


def build_pig_king_board_text(
    rankings: list[PigKingEntry],
    trigger_user_id: str,
    *,
    title: str = "猪王争霸榜",
    limit: int = DEFAULT_RANK_LIMIT,
    empty_text: str = "本群还没人收集到猪图鉴",
) -> str:
    if not rankings:
        return f"【{title}】\n{empty_text}\n━━━━━━━━━━━━━━\n你的名次：未上榜"

    lines = [f"【{title}】"]
    for index, entry in enumerate(rankings[:limit], start=1):
        lines.append(f"{index}. {entry.display_name} - {entry.collection_count} 只")

    rank = get_group_rank_position(rankings, trigger_user_id)
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"你的名次：第 {rank} 位" if rank else "你的名次：未上榜")
    return "\n".join(lines)


def build_my_pigsty_text(
    owner_name: str,
    user_count: int,
    total_pigs: int,
    percent: int,
    ranking_note: str = "",
) -> str:
    lines = [
        "【我的猪圈】",
        f"猪圈主人：{owner_name}",
        f"已收集：{user_count} / {total_pigs} 只",
        f"收藏率：{percent}%",
    ]
    if ranking_note:
        lines.append(ranking_note)
    lines.append("继续加油，争取成为猪王！" if user_count > 0 else "今天先去抽一只小猪，猪圈就热闹起来了。")
    return "\n".join(lines)


def build_my_pigsty_ranking_note(
    *,
    group_rank: int | None = None,
    total_rank: int | None = None,
    include_group_rank: bool = False,
    include_total_rank: bool = False,
) -> str:
    parts: list[str] = []
    if include_group_rank:
        parts.append(
            f"当前群第 {group_rank} 位" if group_rank else "当前群未上榜"
        )
    if include_total_rank:
        parts.append(f"总第 {total_rank} 位" if total_rank else "总未上榜")
    return f"猪王争霸榜：{' / '.join(parts)}" if parts else ""


async def get_group_roll_candidates(bot: Bot, group_id: int, exclude_ids: set[str]) -> list[str]:
    """优先按当前群成员范围筛候选；接口异常时回退到群内已登记过的今日形态。"""
    today = datetime.date.today().isoformat()
    today_rolls = await store.get_daily_rolls(today)

    try:
        members = await bot.call_api("get_group_member_list", group_id=group_id)
        member_ids = {
            str(member.get("user_id"))
            for member in members
            if member.get("user_id") is not None
        }
        return [uid for uid in today_rolls if uid in member_ids and uid not in exclude_ids]
    except Exception as e:
        logger.debug(f"获取群成员列表失败: group={group_id} error={e}")
        group_rolls = await store.get_group_rolls(str(group_id), today)
        return [uid for uid in group_rolls if uid not in exclude_ids]


def format_cooldown_message(remaining_seconds: int) -> str:
    remaining = max(0, int(remaining_seconds))
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    time_str = f"{hours}小时{minutes}分" if hours > 0 else f"{minutes}分{seconds}秒"
    return f"技能冷却中！还需要休息 {time_str} 才能再次烧烤。"


# ================================ 群开关守卫 ================================ #
# 这里统一拦截群聊中的 rollpig 指令入口。
# 一旦宿主项目（如 nekobot_v2）给 runtime 挂上了外部群开关检查器，
# 未启用的群将直接静默跳过；没有接控制台时则默认放行。
def guard_group_enabled(matcher):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            event = kwargs.get("event")
            if event is None:
                for arg in args:
                    if isinstance(arg, Event):
                        event = arg
                        break

            group_id = get_event_group_id(event) if isinstance(event, Event) else ""
            if group_id and not is_group_rollpig_enabled(group_id):
                logger.debug(f"rollpig 群功能未启用，跳过处理: group={group_id}")
                await matcher.finish()

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def guard_store_errors(matcher, message: str = "猪圈云账本暂时离线，请稍后再试。"):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            event = kwargs.get("event")
            if event is None:
                for arg in args:
                    if isinstance(arg, Event):
                        event = arg
                        break

            try:
                return await func(*args, **kwargs)
            except CloudStoreError as error:
                logger.warning(f"rollpig cloud store unavailable: {error}")
                if event is not None:
                    await matcher.finish(MessageSegment.reply(event.message_id) + message)
                await matcher.finish(message)

        return wrapper

    return decorator


async def ensure_pighub_images_loaded() -> bool:
    """
    懒加载 PigHub 图库，带 TTL（默认6小时）自动刷新。
    刷新失败时回退旧缓存，避免因网络抖动打挂功能。
    """
    global pighub_images, pighub_last_loaded
    now = time.time()

    # 缓存有效：直接返回
    if pighub_images and (now - pighub_last_loaded) < PIGHUB_TTL_SECONDS:
        return True

    try:
        async with httpx.AsyncClient(timeout=10, proxy=get_proxy()) as client:
            resp = await client.get("https://pighub.top/api/all-images")
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, dict) or not isinstance(data.get("images"), list):
            raise ValueError("PigHub 返回结构异常，缺少 images 列表")

        valid = [item for item in data["images"] if isinstance(item, dict) and item.get("thumbnail")]
        if not valid:
            raise ValueError("PigHub 返回空图集")

        pighub_images = valid
        pighub_last_loaded = now
        return True

    except Exception as e:
        if pighub_images:
            # 刷新失败但有旧缓存：继续使用，不打挂功能
            logger.warning(f"PigHub 刷新失败，继续使用旧缓存（{len(pighub_images)} 张）: {e}")
            return True
        logger.warning(f"PigHub 连接失败: {e}")
        return False


def build_pighub_image_url(pig_item: dict) -> Optional[str]:
    thumbnail = pig_item.get("thumbnail")
    if not isinstance(thumbnail, str) or not thumbnail:
        return None
    return PIGHUB_IMAGE_BASE_URL + thumbnail.split("/")[-1]

# ================= 辅助渲染函数 =================
async def build_panel_picture(
    *,
    title: str,
    subtitle: str = "",
    hero_avatar: str = "",
    show_hero_avatar: bool = False,
    stats: Optional[list[dict[str, str]]] = None,
    notes: Optional[list[str]] = None,
    rankings: Optional[list[dict[str, str]]] = None,
    footer: str = "",
) -> bytes:
    return await template_to_pic(
        template_path=RES_DIR,
        template_name="panel.html",
        templates={
            "title": title,
            "subtitle": subtitle,
            "hero_avatar": hero_avatar,
            "show_hero_avatar": show_hero_avatar,
            "stats": stats or [],
            "notes": notes or [],
            "rankings": rankings or [],
            "footer": footer,
        },
        pages={"viewport": {"width": 980, "height": 10}},
        wait=80,
    )


async def send_rendered_panel(
    matcher,
    event: Event,
    *,
    fallback_text: str,
    title: str,
    subtitle: str = "",
    hero_avatar: str = "",
    show_hero_avatar: bool = False,
    stats: Optional[list[dict[str, str]]] = None,
    notes: Optional[list[str]] = None,
    rankings: Optional[list[dict[str, str]]] = None,
    footer: str = "",
):
    try:
        pic = await build_panel_picture(
            title=title,
            subtitle=subtitle,
            hero_avatar=hero_avatar,
            show_hero_avatar=show_hero_avatar,
            stats=stats,
            notes=notes,
            rankings=rankings,
            footer=footer,
        )
    except Exception as error:
        logger.warning(f"rollpig 面板渲染失败: title={title} error={error}")
        await matcher.finish(MessageSegment.reply(event.message_id) + fallback_text)
        return

    await matcher.finish(
        MessageSegment.reply(event.message_id) + MessageSegment.image(pic)
    )


async def build_ranking_panel_items(
    rankings: list[PigKingEntry],
    limit: int,
) -> list[dict[str, str]]:
    shown_rankings = rankings[:limit]
    avatar_map = await build_avatar_uri_map([entry.user_id for entry in shown_rankings])
    return [
        {
            "rank": str(index),
            "name": entry.display_name,
            "meta": "",
            "value": f"{entry.collection_count} 只",
            "avatar": avatar_map.get(entry.user_id, ""),
        }
        for index, entry in enumerate(shown_rankings, start=1)
    ]


def build_daily_summary_panel(summary: dict) -> dict[str, object]:
    roll_count = summary.get("roll_count", 0)
    roast_total = summary.get("total", 0)
    stats = [
        {"label": "今日抽猪", "value": f"{roll_count} 人"},
        {"label": "烧烤事件", "value": f"{roast_total} 场"},
    ]

    notes: list[str] = []
    if roll_count == 0 and roast_total == 0:
        notes.append(random.choice(DAILY_SUMMARY_EMPTY_TEXTS))
    else:
        top_pig_id = summary.get("top_pig_id")
        if top_pig_id:
            pig_data = get_pig_by_id(top_pig_id)
            pig_name = pig_data["name"] if pig_data else top_pig_id
            notes.append(f"最热门形态：{pig_name}（共 {summary.get('top_pig_count', 0)} 人抽到）")

        human_count = summary.get("human_count", 0)
        if human_count > 0:
            stats.append({"label": "人类形态", "value": f"{human_count} 人"})

        if roast_total > 0:
            if summary.get("most_active_id"):
                notes.append(
                    f"烧烤狂人：{summary['most_active_name']}（发起 {summary['most_active_count']} 次）"
                )
            if summary.get("most_roasted_id"):
                notes.append(
                    f"最惨食材：{summary['most_roasted_name']}（被烤 {summary['most_roasted_count']} 次）"
                )
            if summary.get("escape_king_id") and summary.get("escape_king_count", 0) > 0:
                notes.append(
                    f"逃脱大师：{summary['escape_king_name']}（成功逃脱 {summary['escape_king_count']} 次）"
                )
            if summary.get("backfire_king_id") and summary.get("backfire_king_count", 0) > 0:
                notes.append(
                    f"反噬之王：{summary['backfire_king_name']}（自爆 {summary['backfire_king_count']} 次）"
                )
            if summary.get("most_roasted_id") and summary.get("most_roasted_count", 0) >= 2:
                notes.append(
                    f"{summary['most_roasted_name']} 明天将获得猪圈保护协议，免受一切烧烤。"
                )
        else:
            notes.append("今天无人烧烤，猪们度过了平静的一天。")

    return {
        "title": "今日猪圈日报",
        "subtitle": datetime.date.today().isoformat(),
        "stats": stats,
        "notes": notes,
        "footer": "明天继续，猪圈永不打烊。",
    }


async def send_rendered_pig(
    matcher, event, pig_data: dict, extra_text: str = "", is_new: bool = False
):
    pig_id = pig_data.get("id", "")
    avatar_file = find_image_file(pig_id)
    avatar_uri = avatar_file.as_uri() if avatar_file else ""
    name = pig_data.get("name", "未知小猪")
    desc = pig_data.get("description", "")
    analysis = pig_data.get("analysis", "你今天是只神秘小猪。")
    new_icon_file = IMAGE_DIR / "new.png"
    new_icon_uri = new_icon_file.as_uri() if new_icon_file.exists() else ""

    pic = None
    try:
        pic = await template_to_pic(
            template_path=RES_DIR,
            template_name="template.html",
            templates={
                "avatar": avatar_uri,
                "name": name,
                "desc": desc,
                "analysis": analysis,
                "is_new": is_new,
                "new_icon_uri": new_icon_uri,
            },
        )
    except Exception as e:
        logger.error(f"图片渲染失败: pig_id={pig_id}, error={e}")
        await matcher.finish("图片生成失败。")
        return

    msg = MessageSegment.reply(event.message_id)
    if extra_text:
        msg += extra_text + "\n"
    msg += MessageSegment.image(pic)
    await matcher.finish(msg)

# ================= 指令处理区域 =================

# 1. 今日小猪
cmd_today = on_command("今天是什么小猪", aliases={"今日小猪"}, block=True)

@cmd_today.handle()
@guard_group_enabled(cmd_today)
@guard_store_errors(cmd_today)
async def _(event: Event):
    user_id = str(event.user_id)
    group_id = get_event_group_id(event)
    pig_id = await store.get_daily_roll(user_id)
    pig = get_pig_by_id(pig_id)
    is_new = False

    if not pig:
        if not PIG_LIST:
            await cmd_today.finish("猪圈塌房了（数据缺失）")
            return
        proposed_pig = random.choice(PIG_LIST)
        unlocked_before = set(await store.get_user_collection(user_id))
        resolved_pig_id, created = await store.get_or_create_daily_roll(
            user_id,
            proposed_pig["id"],
            group_id=group_id,
        )
        is_new = created and resolved_pig_id not in unlocked_before
        pig = get_pig_by_id(resolved_pig_id) or proposed_pig
    elif group_id:
        await store.mark_group_roll_seen(user_id, pig["id"], group_id)

    await send_rendered_pig(cmd_today, event, pig, is_new=is_new)


# 2. 随机小猪
cmd_roll = on_command("随机小猪", block=True)

@cmd_roll.handle()
@guard_group_enabled(cmd_roll)
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    if not await ensure_pighub_images_loaded():
        await cmd_roll.finish("连不上 PigHub...")
        return

    text = args.extract_plain_text().strip()
    try:
        count = int(text) if text else 1
    except ValueError:
        count = 1
    count = max(1, min(count, 10))

    pig = random.choice(pighub_images)
    image_url = build_pighub_image_url(pig)
    if not image_url:
        await cmd_roll.finish("PigHub 返回了异常图片数据，请稍后再试。")
        return

    if count == 1:
        await cmd_roll.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image_url))
        return

    # 私聊不支持合并转发，降级为单张
    if not isinstance(event, GroupMessageEvent):
        await cmd_roll.finish(
            MessageSegment.reply(event.message_id)
            + "私聊暂不支持多张连发，先给你一张：\n"
            + MessageSegment.image(image_url)
        )
        return

    # 多图去重：用 sample 避免重复（若图库数量不足则取全部）
    pool_size = min(count, len(pighub_images))
    selected = random.sample(pighub_images, pool_size)

    messages = []
    for pig in selected:
        url = build_pighub_image_url(pig)
        if not url:
            continue
        messages.append({
            "type": "node",
            "data": {
                "name": "随机小猪Bot",
                "uin": event.self_id,
                "content": Message(pig.get("title", "随机小猪")) + MessageSegment.image(url),
            },
        })

    if not messages:
        await cmd_roll.finish("PigHub 图片数据异常，请稍后再试。")
        return

    await bot.send_group_forward_msg(group_id=event.group_id, messages=messages)


# 2.5 找猪
cmd_find = on_command("找猪", aliases={"搜猪"}, block=True)

@cmd_find.handle()
@guard_group_enabled(cmd_find)
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    if not await ensure_pighub_images_loaded():
        await cmd_find.finish("连不上 PigHub，请稍后再试。")
        return

    keyword = args.extract_plain_text().strip()
    if not keyword:
        await cmd_find.finish("请加上关键词，如：/找猪 玩偶")
        return

    found_pigs = [pig for pig in pighub_images if keyword.lower() in pig.get("title", "").lower()]
    if not found_pigs:
        await cmd_find.finish(f"没找到叫「{keyword}」的猪。")
        return

    if isinstance(event, GroupMessageEvent):
        messages = []
        count = min(len(found_pigs), 10)
        for i in range(count):
            pig = found_pigs[i]
            image_url = build_pighub_image_url(pig)
            if not image_url:
                continue
            messages.append({
                "type": "node",
                "data": {
                    "name": "搜猪小助手",
                    "uin": event.self_id,
                    "content": Message(pig.get("title", "未命名小猪")) + MessageSegment.image(image_url),
                },
            })
        if not messages:
            await cmd_find.finish("搜索结果数据异常，请稍后再试。")
            return
        await bot.send_group_forward_msg(group_id=event.group_id, messages=messages)
        return

    # 私聊降级：展示首条匹配
    pig = found_pigs[0]
    image_url = build_pighub_image_url(pig)
    if not image_url:
        await cmd_find.finish("搜索结果数据异常，请稍后再试。")
        return
    msg = Message(pig.get("title", "未命名小猪"))
    msg += MessageSegment.image(image_url)
    if len(found_pigs) > 1:
        msg += Message(f"\n共找到 {len(found_pigs)} 张，私聊仅展示第 1 张。")
    await cmd_find.finish(MessageSegment.reply(event.message_id) + msg)


# 3. 明日小猪
cmd_tmr = on_command("明日小猪", block=True)

@cmd_tmr.handle()
@guard_group_enabled(cmd_tmr)
async def _(event: Event):
    await cmd_tmr.finish(MessageSegment.reply(event.message_id) + random.choice(TOMORROW_TEXTS))


# 4. 昨日小猪
cmd_yest = on_command("昨日小猪", block=True)

@cmd_yest.handle()
@guard_group_enabled(cmd_yest)
@guard_store_errors(cmd_yest)
async def _(event: Event):
    user_id = str(event.user_id)
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    pig = get_pig_by_id(await store.get_pig_by_date(user_id, yesterday))

    if not pig:
        await cmd_yest.finish(MessageSegment.reply(event.message_id) + "你昨天没抽猪。")
    msg = f"你昨天是一只【{pig['name']}】！"
    await send_rendered_pig(cmd_yest, event, pig, extra_text=msg)


# 5. 今日烤猪
cmd_roast = on_command("今日烤猪", block=True)

@cmd_roast.handle()
@guard_group_enabled(cmd_roast)
@guard_store_errors(cmd_roast)
async def _(event: Event):
    user_id = str(event.user_id)
    group_id = get_event_group_id(event)
    attacker_name = get_event_user_name(event)
    original_pig = get_pig_by_id(await store.get_daily_roll(user_id))

    if not original_pig:
        await cmd_roast.finish(
            MessageSegment.reply(event.message_id) + "你今天还没抽猪，先抽猪再来烤吧。"
        )
        return

    if group_id:
        await store.mark_group_roll_seen(user_id, original_pig["id"], group_id)

    if is_human_pig(original_pig):
        await cmd_roast.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TODAY_ROAST_HUMAN_BLOCK_TEXTS)
        )
        return

    if is_food_pig(original_pig):
        await cmd_roast.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TODAY_ROAST_FOOD_BLOCK_TEXTS).format(shape=original_pig.get("name", "熟食"))
        )
        return

    food_id = random.choice(FOOD_PIG_IDS)
    food_pig_template = get_pig_by_id(food_id)
    if not food_pig_template:
        await cmd_roast.finish("食材配置缺失，请检查 pig.json。")
        return

    roast_text = await roast_manager.get_roast_text(original_pig, food_pig_template)
    roasted_pig_data = food_pig_template.copy()
    roasted_pig_data["analysis"] = roast_text

    if group_id:
        await store.append_roast_event(
            RoastEvent(
                event_type="self_roast",
                attacker_id=user_id,
                target_id=user_id,
                attacker_name=attacker_name,
                target_name=attacker_name,
                food=food_pig_template["name"],
                group_id=group_id,
            )
        )
    await send_rendered_pig(cmd_roast, event, roasted_pig_data)


# 5.5 烤群友
cmd_roast_member = on_command("烤群友", block=True)

@cmd_roast_member.handle()
@guard_group_enabled(cmd_roast_member)
@guard_store_errors(cmd_roast_member)
async def _(bot: Bot, event: GroupMessageEvent):
    attacker_id = str(event.user_id)
    attacker_name = sanitize_display_name(
        event.sender.card or event.sender.nickname or "",
        attacker_id,
    )
    group_id = str(event.group_id)
    force_mode = detect_force_roast_mode(event.get_plaintext(), attacker_id)
    attacker_pig = get_pig_by_id(await store.get_daily_roll(attacker_id))
    attacker_actor_display = format_roast_actor_display(attacker_name, attacker_id)
    attacker_full_display = format_roast_subject_display(
        attacker_name,
        attacker_pig,
        attacker_id,
    )

    if attacker_pig:
        await store.mark_group_roll_seen(attacker_id, attacker_pig["id"], group_id)

    if force_mode == "super_denied":
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id) + "口令【强行点火】仅 superuser 可用。"
        )
        return

    # 提取目标 ID 和名字
    target_id = None
    target_name = "群友"

    if event.reply:
        target_id = str(event.reply.sender.user_id)
        target_name = sanitize_display_name(
            event.reply.sender.card or event.reply.sender.nickname or "",
            target_id,
        )
    else:
        for seg in event.message:
            if seg.type == "at":
                target_id = str(seg.data["qq"])
                target_name = "对方"
                break

    # @Bot 时框架会把 at 消费掉，补充判断
    if not target_id and event.to_me:
        target_id = str(event.self_id)

    # 尝试获取更准确的 target_name
    if target_id:
        try:
            member_info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(target_id))
            target_name = sanitize_display_name(
                member_info.get("card") or member_info.get("nickname") or "",
                target_id,
            )
        except Exception as e:
            logger.debug(f"获取群成员信息失败: group={event.group_id} user={target_id} error={e}")

    if not target_id:
        await cmd_roast_member.finish("请 At 或回复你要烤的群友！")
        return

    if target_id == attacker_id:
        await cmd_roast_member.finish("对自己好一点，别自焚。请发送「今日烤猪」。")
        return

    # 检测目标是否是 Bot 自身 → 特殊反噬，不消耗 CD，纯文本回复
    if target_id == str(event.self_id):
        food_id = random.choice(FOOD_PIG_IDS)
        food_pig = get_pig_by_id(food_id)
        food_name = food_pig["name"] if food_pig else "美食"
        bot_text = random.choice(ROAST_BOT_TEXTS).format(
            attacker=attacker_name,
            food=food_name,
        )
        logger.info(f"[烤群友→Bot] 特殊反噬 | 凶手={attacker_name}({attacker_id}) 变成={food_name}")
        await store.append_roast_event(
            RoastEvent(
                event_type="bot_backfire",
                attacker_id=attacker_id,
                target_id=target_id,
                attacker_name=attacker_name,
                target_name=target_name,
                food=food_name,
                group_id=group_id,
            )
        )
        await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + bot_text)
        return
    # 读取目标形态（后门模式也不绕过此检查）
    target_pig = get_pig_by_id(await store.get_daily_roll(target_id))
    target_full_display = format_roast_subject_display(
        target_name,
        target_pig,
        target_id,
    )
    if not target_pig:
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id)
            + f"{target_full_display}今天还没抽猪，没法下嘴！"
        )
        return
    await store.mark_group_roll_seen(target_id, target_pig["id"], group_id)
    target_full_display = format_roast_subject_display(
        target_name,
        target_pig,
        target_id,
    )

    # 保护检查：被烤最多的用户次日受保护（后门可突破）
    if await store.is_protected(group_id, target_id):
        if force_mode in {"normal", "super"}:
            break_text = random.choice(PROTECTION_BREAK_TEXTS).format(
                target=target_full_display
            )
            logger.info(f"[烤群友] 保护被突破 | 凶手={attacker_name}({attacker_id}) 目标={target_name}({target_id})")
            await cmd_roast_member.send(MessageSegment.reply(event.message_id) + break_text)
        else:
            prot_text = random.choice(PROTECTION_BLOCK_TEXTS).format(
                target=target_full_display
            )
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + prot_text)
            return

    if is_human_pig(target_pig):
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TARGET_HUMAN_BLOCK_TEXTS).format(target=target_full_display)
        )
        return

    if is_food_pig(target_pig):
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TARGET_FOOD_BLOCK_TEXTS).format(target=target_full_display)
        )
        return

    # 模式化限制/计数
    if force_mode == "normal":
        if not await store.consume_force_usage(attacker_id):
            reject_text = pick_force_limit_text(
                attacker_actor_display,
                target_full_display,
            )
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + reject_text)
            return
    elif force_mode is None:
        cooldown_result = await store.consume_roast_cooldown(
            attacker_id,
            cooldown_seconds=resolve_roast_cooldown_seconds(),
        )
        if not cooldown_result.allowed:
            await cmd_roast_member.finish(
                MessageSegment.reply(event.message_id) + format_cooldown_message(cooldown_result.remaining_seconds)
            )
            return
    # super 模式：无限制，不消耗后门次数，不走 CD

    # --- 后门模式：必定成功 ---
    if force_mode in {"normal", "super"}:
        food_id = random.choice(FOOD_PIG_IDS)
        food_pig_template = get_pig_by_id(food_id)
        if not food_pig_template:
            await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
            return

        text = await roast_manager.get_roast_text(
            target_pig, food_pig_template,
            operator_name=attacker_actor_display,
            target_name=target_full_display,
        )
        prefix_text = pick_force_prefix_text(
            target_full_display,
            is_super_mode=(force_mode == "super"),
        )

        logger.info(
            f"[烤群友] 后门成功 | 凶手={attacker_name}({attacker_id}) "
            f"目标={target_name}({target_id}) 模式={force_mode} 结果={food_pig_template['name']}"
        )
        await store.append_roast_event(
            RoastEvent(
                event_type="success",
                attacker_id=attacker_id,
                target_id=target_id,
                attacker_name=attacker_name,
                target_name=target_name,
                food=food_pig_template["name"],
                group_id=str(event.group_id),
            )
        )
        roasted_data = food_pig_template.copy()
        roasted_data["analysis"] = text
        await send_rendered_pig(cmd_roast_member, event, roasted_data, extra_text=prefix_text)
        return

    # --- 普通模式概率判定 ---
    roll = random.randint(1, 100)

    # === 成功 (60%) ===
    if roll <= 60:
        food_id = random.choice(FOOD_PIG_IDS)
        food_pig_template = get_pig_by_id(food_id)
        if not food_pig_template:
            await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
            return

        text = await roast_manager.get_roast_text(
            target_pig, food_pig_template,
            operator_name=attacker_actor_display,
            target_name=target_full_display,
        )
        logger.info(
            f"[烤群友] 成功 | 凶手={attacker_name}({attacker_id}) "
            f"目标={target_name}({target_id}) 结果={food_pig_template['name']}"
        )
        await store.append_roast_event(
            RoastEvent(
                event_type="success",
                attacker_id=attacker_id,
                target_id=target_id,
                attacker_name=attacker_name,
                target_name=target_name,
                food=food_pig_template["name"],
                group_id=str(event.group_id),
            )
        )
        roasted_data = food_pig_template.copy()
        roasted_data["analysis"] = text
        await send_rendered_pig(cmd_roast_member, event, roasted_data)

    # === 逃脱 (30%) ===
    elif roll <= 90:
        escape_text = pick_escape_text(
            attacker_actor_display,
            target_full_display,
            target_pig,
        )
        logger.info(
            f"[烤群友] 逃脱 | 凶手={attacker_name}({attacker_id}) 目标={target_name}({target_id})"
        )
        await store.append_roast_event(
            RoastEvent(
                event_type="escape",
                attacker_id=attacker_id,
                target_id=target_id,
                attacker_name=attacker_name,
                target_name=target_name,
                group_id=str(event.group_id),
            )
        )
        await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + escape_text)

    # === 反噬 (10%) ===
    else:
        if attacker_pig and (not is_food_pig(attacker_pig)) and (not is_human_pig(attacker_pig)):
            food_id = random.choice(FOOD_PIG_IDS)
            food_pig_template = get_pig_by_id(food_id)
            if not food_pig_template:
                await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
                return

            text = await roast_manager.get_roast_text(attacker_pig, food_pig_template)
            fail_intro = pick_backfire_text(
                attacker_full_display,
                target_full_display,
                attacker_pig,
            )
            fail_text = fail_intro + "\n\n" + text

            logger.info(
                f"[烤群友] 反噬 | 凶手={attacker_name}({attacker_id}) "
                f"目标={target_name}({target_id}) 凶手变成={food_pig_template['name']}"
            )
            await store.append_roast_event(
                RoastEvent(
                    event_type="backfire",
                    attacker_id=attacker_id,
                    target_id=target_id,
                    attacker_name=attacker_name,
                    target_name=target_name,
                    food=food_pig_template["name"],
                    group_id=group_id,
                )
            )
            roasted_data = food_pig_template.copy()
            roasted_data["analysis"] = fail_text
            await send_rendered_pig(cmd_roast_member, event, roasted_data)
        else:
            fail_text = pick_backfire_text(
                attacker_full_display,
                target_full_display,
                attacker_pig,
            )
            logger.info(
                f"[烤群友] 反噬(文字) | 凶手={attacker_name}({attacker_id}) "
                f"目标={target_name}({target_id})"
            )
            await store.append_roast_event(
                RoastEvent(
                    event_type="backfire",
                    attacker_id=attacker_id,
                    target_id=target_id,
                    attacker_name=attacker_name,
                    target_name=target_name,
                    group_id=group_id,
                )
            )
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + fail_text)


# 5.6 随机烤群友
cmd_random_roast = on_command("随机烤群友", aliases={"随机烤猪", "抽个群友烤了"}, block=True)

@cmd_random_roast.handle()
@guard_group_enabled(cmd_random_roast)
@guard_store_errors(cmd_random_roast)
async def _(bot: Bot, event: GroupMessageEvent):
    attacker_id = str(event.user_id)
    attacker_name = sanitize_display_name(
        event.sender.card or event.sender.nickname or "",
        attacker_id,
    )
    group_id = str(event.group_id)
    attacker_pig = get_pig_by_id(await store.get_daily_roll(attacker_id))
    attacker_actor_display = format_roast_actor_display(attacker_name, attacker_id)
    attacker_full_display = format_roast_subject_display(
        attacker_name,
        attacker_pig,
        attacker_id,
    )

    if attacker_pig:
        await store.mark_group_roll_seen(attacker_id, attacker_pig["id"], group_id)

    bot_id = str(event.self_id)
    candidates = await get_group_roll_candidates(bot, event.group_id, {attacker_id, bot_id})

    if not candidates:
        await cmd_random_roast.finish(
            MessageSegment.reply(event.message_id) + "今天还没有别人抽猪，没有可以烤的目标！"
        )
        return

    target_id = random.choice(candidates)

    # 获取目标昵称
    target_name = "群友"
    try:
        member_info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(target_id))
        target_name = sanitize_display_name(
            member_info.get("card") or member_info.get("nickname") or "",
            target_id,
        )
    except Exception:
        pass

    # 检查攻击者 CD
    cooldown_result = await store.consume_roast_cooldown(
        attacker_id,
        cooldown_seconds=resolve_roast_cooldown_seconds(),
    )
    if not cooldown_result.allowed:
        await cmd_random_roast.finish(
            MessageSegment.reply(event.message_id) + format_cooldown_message(cooldown_result.remaining_seconds)
        )
        return

    # 读取目标形态
    target_pig = get_pig_by_id(await store.get_daily_roll(target_id))
    target_full_display = format_roast_subject_display(
        target_name,
        target_pig,
        target_id,
    )
    if not target_pig:
        await cmd_random_roast.finish(
            MessageSegment.reply(event.message_id)
            + f"系统随机选中了{target_full_display}，但对方的猪数据异常。"
        )
        return
    await store.mark_group_roll_seen(target_id, target_pig["id"], group_id)
    target_full_display = format_roast_subject_display(
        target_name,
        target_pig,
        target_id,
    )

    # 保护检查
    if await store.is_protected(group_id, target_id):
        prot_text = random.choice(PROTECTION_BLOCK_TEXTS).format(
            target=target_full_display
        )
        await cmd_random_roast.finish(
            MessageSegment.reply(event.message_id)
            + f"系统随机选中了{target_full_display}。\n{prot_text}"
        )
        return

    # 目标是人类/熟食形态 → 拦截
    if is_human_pig(target_pig):
        await cmd_random_roast.finish(
            MessageSegment.reply(event.message_id)
            + f"系统随机选中了{target_full_display}，但烤架拒绝处理活体高智商单位。换一次试试？"
        )
        return

    if is_food_pig(target_pig):
        await cmd_random_roast.finish(
            MessageSegment.reply(event.message_id)
            + f"系统随机选中了{target_full_display}，但对方已经是熟食了，别鞭尸了。"
        )
        return

    # 正常概率判定
    intro = (
        random.choice(RANDOM_ROAST_INTRO_TEXTS).format(target=target_full_display)
        + "\n\n"
    )
    roll = random.randint(1, 100)

    # 成功 (60%)
    if roll <= 60:
        food_id = random.choice(FOOD_PIG_IDS)
        food_pig_template = get_pig_by_id(food_id)
        if not food_pig_template:
            await cmd_random_roast.finish("食材配置缺失，请联系管理员修复 pig.json。")
            return

        text = await roast_manager.get_roast_text(
            target_pig, food_pig_template,
            operator_name=attacker_actor_display,
            target_name=target_full_display,
        )
        logger.info(
            f"[随机烤群友] 成功 | 凶手={attacker_name}({attacker_id}) "
            f"目标={target_name}({target_id}) 结果={food_pig_template['name']}"
        )
        await store.append_roast_event(
            RoastEvent(
                event_type="success",
                attacker_id=attacker_id,
                target_id=target_id,
                attacker_name=attacker_name,
                target_name=target_name,
                food=food_pig_template["name"],
                group_id=str(event.group_id),
            )
        )
        roasted_data = food_pig_template.copy()
        roasted_data["analysis"] = text
        await send_rendered_pig(cmd_random_roast, event, roasted_data, extra_text=intro)

    # 逃脱 (30%)
    elif roll <= 90:
        escape_text = pick_escape_text(
            attacker_actor_display,
            target_full_display,
            target_pig,
        )
        logger.info(
            f"[随机烤群友] 逃脱 | 凶手={attacker_name}({attacker_id}) 目标={target_name}({target_id})"
        )
        await store.append_roast_event(
            RoastEvent(
                event_type="escape",
                attacker_id=attacker_id,
                target_id=target_id,
                attacker_name=attacker_name,
                target_name=target_name,
                group_id=str(event.group_id),
            )
        )
        await cmd_random_roast.finish(MessageSegment.reply(event.message_id) + intro + escape_text)

    # 反噬 (10%)
    else:
        if attacker_pig and (not is_food_pig(attacker_pig)) and (not is_human_pig(attacker_pig)):
            food_id = random.choice(FOOD_PIG_IDS)
            food_pig_template = get_pig_by_id(food_id)
            if not food_pig_template:
                await cmd_random_roast.finish("食材配置缺失。")
                return
            text = await roast_manager.get_roast_text(attacker_pig, food_pig_template)
            fail_intro = pick_backfire_text(
                attacker_full_display,
                target_full_display,
                attacker_pig,
            )
            fail_text = fail_intro + "\n\n" + text
            logger.info(
                f"[随机烤群友] 反噬 | 凶手={attacker_name}({attacker_id}) "
                f"目标={target_name}({target_id}) 凶手变成={food_pig_template['name']}"
            )
            await store.append_roast_event(
                RoastEvent(
                    event_type="backfire",
                    attacker_id=attacker_id,
                    target_id=target_id,
                    attacker_name=attacker_name,
                    target_name=target_name,
                    food=food_pig_template["name"],
                    group_id=group_id,
                )
            )
            roasted_data = food_pig_template.copy()
            roasted_data["analysis"] = fail_text
            await send_rendered_pig(cmd_random_roast, event, roasted_data, extra_text=intro)
        else:
            fail_text = pick_backfire_text(
                attacker_full_display,
                target_full_display,
                attacker_pig,
            )
            logger.info(
                f"[随机烤群友] 反噬(文字) | 凶手={attacker_name}({attacker_id}) "
                f"目标={target_name}({target_id})"
            )
            await store.append_roast_event(
                RoastEvent(
                    event_type="backfire",
                    attacker_id=attacker_id,
                    target_id=target_id,
                    attacker_name=attacker_name,
                    target_name=target_name,
                    group_id=group_id,
                )
            )
            await cmd_random_roast.finish(MessageSegment.reply(event.message_id) + intro + fail_text)


# 5.7 猪圈日报开关
cmd_summary_on = on_command("开启猪圈日报", block=True)
cmd_summary_off = on_command("关闭猪圈日报", block=True)


@cmd_summary_on.handle()
@guard_group_enabled(cmd_summary_on)
async def _(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        await cmd_summary_on.finish("请在群聊中使用这个指令。")
        return
    if not await is_group_admin_or_superuser(bot, event):
        await cmd_summary_on.finish(
            MessageSegment.reply(event.message_id) + "只有群管理员或超级用户才能修改猪圈日报开关。"
        )
        return

    await group_settings_service.set_key_value(
        str(event.group_id),
        MODULE_NAME,
        "daily_summary_enabled",
        True,
    )
    await cmd_summary_on.finish(MessageSegment.reply(event.message_id) + "已开启本群猪圈日报。")


@cmd_summary_off.handle()
@guard_group_enabled(cmd_summary_off)
async def _(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        await cmd_summary_off.finish("请在群聊中使用这个指令。")
        return
    if not await is_group_admin_or_superuser(bot, event):
        await cmd_summary_off.finish(
            MessageSegment.reply(event.message_id) + "只有群管理员或超级用户才能修改猪圈日报开关。"
        )
        return

    await group_settings_service.set_key_value(
        str(event.group_id),
        MODULE_NAME,
        "daily_summary_enabled",
        False,
    )
    await cmd_summary_off.finish(MessageSegment.reply(event.message_id) + "已关闭本群猪圈日报。")


# 6. 我的猪圈
cmd_sty = on_command("我的猪圈", aliases={"我的小猪"}, block=True)

@cmd_sty.handle()
@guard_group_enabled(cmd_sty)
@guard_store_errors(cmd_sty)
async def _(bot: Bot, event: Event):
    user_id = str(event.user_id)
    collection = await store.get_user_collection(user_id)
    total_pigs = len(PIG_LIST)
    user_count = len(collection)

    if total_pigs <= 0:
        await cmd_sty.finish(MessageSegment.reply(event.message_id) + "猪图鉴为空，请先检查资源文件。")
        return

    percent = int((user_count / total_pigs) * 100)
    group_rank: int | None = None
    total_rank: int | None = None
    if isinstance(event, GroupMessageEvent):
        rankings = await build_group_pig_rankings(bot, str(event.group_id))
        group_rank = get_group_rank_position(rankings, user_id)
    if get_storage_backend() == "local":
        global_rankings = await build_global_pig_rankings(
            bot,
            context_group_id=str(event.group_id) if isinstance(event, GroupMessageEvent) else "",
        )
        total_rank = get_group_rank_position(global_rankings, user_id)
    ranking_note = build_my_pigsty_ranking_note(
        group_rank=group_rank,
        total_rank=total_rank,
        include_group_rank=isinstance(event, GroupMessageEvent),
        include_total_rank=get_storage_backend() == "local",
    )

    owner_name = sanitize_display_name(get_event_user_name(event), user_id)
    avatar_uri = await get_avatar_uri(user_id)
    fallback_text = build_my_pigsty_text(
        owner_name=owner_name,
        user_count=user_count,
        total_pigs=total_pigs,
        percent=percent,
        ranking_note=ranking_note,
    )
    stats = [
        {"label": "已收集", "value": f"{user_count} / {total_pigs}"},
        {"label": "收藏率", "value": f"{percent}%"},
    ]
    notes = [ranking_note] if ranking_note else []
    footer = "继续加油，争取成为猪王！" if user_count > 0 else "今天先去抽一只小猪，猪圈就热闹起来了。"
    await send_rendered_panel(
        cmd_sty,
        event,
        fallback_text=fallback_text,
        title="我的猪圈",
        subtitle=owner_name,
        hero_avatar=avatar_uri,
        show_hero_avatar=True,
        stats=stats,
        notes=notes,
        footer=footer,
    )


# 6.5 猪王争霸榜
cmd_pig_king = on_command(
    "猪王争霸榜",
    aliases={"猪猪榜", "猪猪排行", "小猪榜", "小猪排行"},
    block=True,
)
cmd_pig_global = on_command("猪猪总榜", aliases={"猪猪总排行"}, block=True)


@cmd_pig_king.handle()
@guard_group_enabled(cmd_pig_king)
@guard_store_errors(cmd_pig_king)
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await cmd_pig_king.finish("请在群聊中查看猪王争霸榜。")
        return
    if get_storage_backend() != "local":
        await cmd_pig_king.finish(MessageSegment.reply(event.message_id) + LOCAL_RANKING_UNSUPPORTED_TEXT)
        return

    limit = parse_rank_limit(args.extract_plain_text())
    if limit is None:
        await cmd_pig_king.finish(
            MessageSegment.reply(event.message_id) + get_rank_usage("猪猪榜")
        )
        return

    rankings = await build_group_pig_rankings(bot, str(event.group_id))
    fallback_text = build_pig_king_board_text(
        rankings,
        str(event.user_id),
        limit=limit,
    )
    notes = [] if rankings else ["本群还没人收集到猪图鉴"]
    footer_rank = get_group_rank_position(rankings, str(event.user_id))
    footer = f"你的名次：第 {footer_rank} 位" if footer_rank else "你的名次：未上榜"
    ranking_items = await build_ranking_panel_items(rankings, limit) if rankings else []
    await send_rendered_panel(
        cmd_pig_king,
        event,
        fallback_text=fallback_text,
        title="猪王争霸榜",
        subtitle="当前群实时图鉴排行",
        notes=notes,
        rankings=ranking_items,
        footer=footer,
    )


@cmd_pig_global.handle()
@guard_group_enabled(cmd_pig_global)
@guard_store_errors(cmd_pig_global)
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    if get_storage_backend() != "local":
        await cmd_pig_global.finish(
            MessageSegment.reply(event.message_id) + LOCAL_RANKING_UNSUPPORTED_TEXT
        )
        return

    limit = parse_rank_limit(args.extract_plain_text())
    if limit is None:
        await cmd_pig_global.finish(
            MessageSegment.reply(event.message_id) + get_rank_usage("猪猪总榜")
        )
        return

    context_group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    rankings = await build_global_pig_rankings(bot, context_group_id=context_group_id)
    fallback_text = build_pig_king_board_text(
        rankings,
        str(event.user_id),
        title="猪王争霸榜",
        limit=limit,
        empty_text="全局还没人收集到猪图鉴",
    )
    notes = [] if rankings else ["全局还没人收集到猪图鉴"]
    footer_rank = get_group_rank_position(rankings, str(event.user_id))
    footer = f"你的名次：第 {footer_rank} 位" if footer_rank else "你的名次：未上榜"
    ranking_items = await build_ranking_panel_items(rankings, limit) if rankings else []
    await send_rendered_panel(
        cmd_pig_global,
        event,
        fallback_text=fallback_text,
        title="猪王争霸榜",
        subtitle="当前全局实时图鉴排行",
        notes=notes,
        rankings=ranking_items,
        footer=footer,
    )


# 7. 本周小猪
cmd_week = on_command("本周小猪", block=True)

@cmd_week.handle()
@guard_group_enabled(cmd_week)
@guard_store_errors(cmd_week)
async def _(event: Event):
    if not HAS_PIL:
        await cmd_week.finish("Bot 未安装 PIL 库。")

    user_id = str(event.user_id)
    today = datetime.date.today()

    images_to_merge = []
    for i in range(7):
        d = today - datetime.timedelta(days=(6 - i))
        pig = get_pig_by_id(await store.get_pig_by_date(user_id, d.isoformat()))
        if pig:
            img_file = find_image_file(pig["id"])
            if img_file:
                images_to_merge.append(img_file)

    if not images_to_merge:
        await cmd_week.finish(MessageSegment.reply(event.message_id) + "你这周还没抽过猪呢！")
        return

    try:
        item_w, item_h = 150, 150
        padding = 20
        total_w = (item_w + padding) * len(images_to_merge) + padding
        total_h = item_h + 80

        canvas = PILImage.new("RGB", (total_w, total_h), (255, 255, 255))
        for idx, img_path in enumerate(images_to_merge):
            with PILImage.open(img_path) as opened:
                img = opened.convert("RGBA").resize((item_w, item_h))
                x = padding + idx * (item_w + padding)
                y = padding
                canvas.paste(img, (x, y), img)

        from io import BytesIO
        output = BytesIO()
        canvas.save(output, format="PNG")

        msg = (
            MessageSegment.reply(event.message_id)
            + f"你这周变了 {len(images_to_merge)} 次猪！"
            + MessageSegment.image(output.getvalue())
        )
    except Exception as e:
        logger.error(f"本周小猪长图生成失败: user={user_id}, error={e}")
        await cmd_week.finish("生成图片失败。")
        return

    await cmd_week.finish(msg)


# ================= 定时任务：每日总结 =================

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


def build_daily_summary_text(summary: dict) -> str:
    """将按群聚合后的日报结果拼成文案。"""
    roll_count = summary.get("roll_count", 0)
    roast_total = summary.get("total", 0)

    # 完全无活动
    if roll_count == 0 and roast_total == 0:
        return random.choice(DAILY_SUMMARY_EMPTY_TEXTS)

    lines = [DAILY_SUMMARY_HEADER]

    # 抽猪统计
    if roll_count > 0:
        top_pig_id = summary.get("top_pig_id")
        if top_pig_id:
            pig_data = get_pig_by_id(top_pig_id)
            pig_name = pig_data["name"] if pig_data else top_pig_id
            lines.append(f"\U0001f451 最热门形态：【{pig_name}】（共 {summary.get('top_pig_count', 0)} 人抽到）")
        human_count = summary.get("human_count", 0)
        if human_count > 0:
            lines.append(f"\U0001f9cd 今日人类：{human_count} 位幸运儿逃过了猪化")
        lines.append("")

    # 烧烤统计
    if roast_total > 0:
        lines.append(f"\U0001f525 今日共发生 {roast_total} 场烧烤事件")

        if summary.get("most_active_id"):
            lines.append(f"\U0001f3c6 烧烤狂人：【{summary['most_active_name']}】（发起 {summary['most_active_count']} 次）")

        if summary.get("most_roasted_id"):
            lines.append(f"\U0001f356 最惨食材：【{summary['most_roasted_name']}】（被烤 {summary['most_roasted_count']} 次）")

        if summary.get("escape_king_id") and summary["escape_king_count"] > 0:
            lines.append(f"\U0001f3c3 逃脱大师：【{summary['escape_king_name']}】（成功逃脱 {summary['escape_king_count']} 次）")

        if summary.get("backfire_king_id") and summary["backfire_king_count"] > 0:
            lines.append(f"\U0001f4a5 反噬之王：【{summary['backfire_king_name']}】（自爆 {summary['backfire_king_count']} 次）")

        # 保护提示
        if summary.get("most_roasted_id") and summary["most_roasted_count"] >= 2:
            lines.append(f"\n\U0001f6e1\ufe0f 【{summary['most_roasted_name']}】明天将获得猪圈保护协议，免受一切烧烤！")
    else:
        lines.append("\U0001f54a 今天无人烧烤，猪们度过了平静的一天。")

    lines.append("\n" + DAILY_SUMMARY_FOOTER)
    return "\n".join(lines)


@scheduler.scheduled_job("cron", hour=23, minute=45, id="rollpig_daily_summary")
async def daily_summary_job():
    """每晚 23:45~23:55 推送当日猪圈日报（随机延迟 0~10 分钟防风控）。"""
    delay = random.randint(0, 600)  # 0~10 分钟随机延迟
    logger.info(f"[每日总结] 定时触发，随机延迟 {delay} 秒后推送")
    await asyncio.sleep(delay)
    try:
        active_groups = await store.get_active_group_ids()
        if not active_groups:
            logger.info("[每日总结] 今日无活跃群，跳过推送")
            return

        # ================================ 控制台开关过滤 ================================ #
        # 如果宿主项目接入了 admin_console 群开关，这里必须在定时任务层同步收口：
        # 未启用的群既不推日报，也不写次日保护名单，保证“关闭就是彻底关闭”。
        enabled_active_groups = [
            group_id for group_id in sorted(active_groups)
            if is_group_rollpig_enabled(group_id)
        ]
        if not enabled_active_groups:
            logger.info("[每日总结] 今日没有启用 rollpig 的活跃群，跳过推送")
            return

        group_summaries = {}
        protect_date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        for group_id in enabled_active_groups:
            summary = await build_daily_summary(store, group_id=group_id)
            group_summaries[group_id] = summary
            if summary.get("most_roasted_id") and summary.get("most_roasted_count", 0) >= 2:
                await store.replace_group_protections(group_id, [summary["most_roasted_id"]], protect_date)
            else:
                await store.replace_group_protections(group_id, [], protect_date)

        # 清理旧事件
        await store.prune_events(days_to_keep=7)
        await store.prune_history(days_to_keep=14)

        try:
            bot = get_bot()
        except ValueError:
            logger.warning("[每日总结] 无可用 Bot，跳过推送")
            return

        # ================================ 日报推送开关过滤 ================================ #
        # “日报推送”是独立于 rollpig 主功能的第二层开关：
        # 群内玩法可以开启，但日报消息可以单独关闭。
        summary_push_groups = [
            group_id
            for group_id in enabled_active_groups
            if await is_daily_summary_push_enabled(group_id)
        ]
        if not summary_push_groups:
            logger.info("[每日总结] 已完成保护名单刷新，但没有群开启日报推送")
            return

        for group_id in summary_push_groups:
            try:
                panel_kwargs = build_daily_summary_panel(group_summaries[group_id])
                pic = await build_panel_picture(**panel_kwargs)
                message = MessageSegment.image(pic)
            except Exception as render_error:
                logger.warning(f"[每日总结] 图片渲染失败，回退文本: group={group_id} error={render_error}")
                message = build_daily_summary_text(group_summaries[group_id])
            try:
                await bot.send_group_msg(group_id=int(group_id), message=message)
            except Exception as send_error:
                logger.warning(f"[每日总结] 推送失败: group={group_id} error={send_error}")

        logger.info(f"[每日总结] 推送完成, 共 {len(summary_push_groups)} 个群")
    except CloudStoreError as e:
        logger.warning(f"[每日总结] 云端账本暂时不可用，跳过本轮推送: {e}")
    except Exception as e:
        logger.error(f"[每日总结] 任务异常: {e}")
