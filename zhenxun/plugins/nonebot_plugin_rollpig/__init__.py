import json
import random
import datetime
import time
import httpx
from pathlib import Path
from typing import Optional

from nonebot import on_command, require, get_driver
from nonebot.adapters.onebot.v11 import Event, MessageSegment, Message, GroupMessageEvent, Bot
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

# 确保依赖插件先被 NoneBot 注册（必须在本地模块 import 之前）
# data_manager.py 在模块加载时会调用 store.get_plugin_data_file()
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_localstore")

from nonebot_plugin_htmlrender import template_to_pic

# 本地模块（在 require() 之后 import）
from .config import Config
from .roast_manager import roast_manager
from .data_manager import data_manager
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
    
    📊 统计指令：
    我的猪圈 - 查看解锁进度
    本周小猪 - 生成本周猪猪总结长图
    """,
    type="application",
    homepage="https://github.com/Felis2026/nonebot-plugin-rollpig",
    supported_adapters={"~onebot.v11"},
    config=Config,
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
        shape = "未抽形态"
    elif is_human_pig(attacker_pig):
        pool = BACKFIRE_HUMAN_TEXTS
        shape = "人类"
    elif is_food_pig(attacker_pig):
        pool = BACKFIRE_FOOD_TEXTS
        shape = attacker_pig.get("name", "熟食")
    else:
        pool = BACKFIRE_GENERIC_TEXTS
        shape = attacker_pig.get("name", "未知形态")

    return random.choice(pool).format(attacker=attacker_name, target=target_name, shape=shape)


def pick_escape_text(attacker_name: str, target_name: str, target_pig: Optional[dict]) -> str:
    shape = target_pig.get("name", "未知形态") if target_pig else "未知形态"
    return random.choice(ESCAPE_TEXTS).format(attacker=attacker_name, target=target_name, shape=shape)


def pick_force_prefix_text(target_name: str, is_super_mode: bool) -> str:
    pool = SUPER_FORCE_ROAST_PREFIX_TEXTS if is_super_mode else FORCE_ROAST_PREFIX_TEXTS
    return random.choice(pool).format(target=target_name)


def pick_force_limit_text(operator_name: str, target_name: str) -> str:
    return random.choice(FORCE_ROAST_LIMIT_TEXTS).format(operator=operator_name, target=target_name)


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
        async with httpx.AsyncClient(timeout=10) as client:
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

async def send_rendered_pig(matcher, event, pig_data: dict, extra_text: str = ""):
    pig_id = pig_data.get("id", "")
    avatar_file = find_image_file(pig_id)
    avatar_uri = avatar_file.as_uri() if avatar_file else ""
    name = pig_data.get("name", "未知小猪")
    desc = pig_data.get("description", "")
    analysis = pig_data.get("analysis", "你今天是只神秘小猪。")

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
async def _(event: Event):
    user_id = str(event.user_id)
    pig_id = data_manager.get_today_pig(user_id)
    pig = get_pig_by_id(pig_id)

    if not pig:
        if not PIG_LIST:
            await cmd_today.finish("猪圈塌房了（数据缺失）")
            return
        pig = random.choice(PIG_LIST)
        await data_manager.set_today_pig(user_id, pig["id"])
        if random.randint(1, 20) == 1:
            await data_manager.clean_old_history()

    await send_rendered_pig(cmd_today, event, pig)


# 2. 随机小猪
cmd_roll = on_command("随机小猪", block=True)

@cmd_roll.handle()
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
async def _(event: Event):
    await cmd_tmr.finish(MessageSegment.reply(event.message_id) + random.choice(TOMORROW_TEXTS))


# 4. 昨日小猪
cmd_yest = on_command("昨日小猪", block=True)

@cmd_yest.handle()
async def _(event: Event):
    user_id = str(event.user_id)
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    pig = get_pig_by_id(data_manager.get_pig_by_date(user_id, yesterday))

    if not pig:
        await cmd_yest.finish(MessageSegment.reply(event.message_id) + "你昨天没抽猪。")
    msg = f"你昨天是一只【{pig['name']}】！"
    await send_rendered_pig(cmd_yest, event, pig, extra_text=msg)


# 5. 今日烤猪
cmd_roast = on_command("今日烤猪", block=True)

@cmd_roast.handle()
async def _(event: Event):
    user_id = str(event.user_id)
    original_pig = get_pig_by_id(data_manager.get_today_pig(user_id))

    if not original_pig:
        await cmd_roast.finish(MessageSegment.reply(event.message_id) + "你连猪都不是，怎么烤？")
        return

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

    await send_rendered_pig(cmd_roast, event, roasted_pig_data)


# 5.5 烤群友
cmd_roast_member = on_command("烤群友", block=True)

@cmd_roast_member.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    attacker_id = str(event.user_id)
    attacker_name = event.sender.card or event.sender.nickname
    force_mode = detect_force_roast_mode(event.get_plaintext(), attacker_id)

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
        target_name = event.reply.sender.card or event.reply.sender.nickname
    else:
        for seg in event.message:
            if seg.type == "at":
                target_id = str(seg.data["qq"])
                target_name = "对方"
                break

    # 尝试获取更准确的 target_name
    if target_id:
        try:
            member_info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(target_id))
            target_name = member_info.get("card") or member_info.get("nickname")
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
        bot_text = random.choice(ROAST_BOT_TEXTS).format(attacker=attacker_name, food=food_name)
        logger.info(f"[烤群友→Bot] 特殊反噬 | 凶手={attacker_name}({attacker_id}) 变成={food_name}")
        await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + bot_text)
        return

    # 读取目标形态（后门模式也不绕过此检查）
    target_pig = get_pig_by_id(data_manager.get_today_pig(target_id))
    if not target_pig:
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id) + f"【{target_name}】今天还没抽猪，没法下嘴！"
        )
        return

    if is_human_pig(target_pig):
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TARGET_HUMAN_BLOCK_TEXTS).format(target=target_name)
        )
        return

    if is_food_pig(target_pig):
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TARGET_FOOD_BLOCK_TEXTS).format(
                target=target_name, shape=target_pig.get("name", "熟食")
            )
        )
        return

    # 模式化限制/计数
    if force_mode == "normal":
        if not data_manager.check_force_roast_usage(attacker_id):
            reject_text = pick_force_limit_text(attacker_name, target_name)
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + reject_text)
            return
        await data_manager.update_force_roast_usage(attacker_id)
    elif force_mode is None:
        is_available, tip_msg = data_manager.check_roast_usage(attacker_id)
        if not is_available:
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + tip_msg)
            return
        await data_manager.update_roast_usage(attacker_id)
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
            operator_name=attacker_name, target_name=target_name,
        )
        prefix_text = pick_force_prefix_text(target_name, is_super_mode=(force_mode == "super"))

        logger.info(
            f"[烤群友] 后门成功 | 凶手={attacker_name}({attacker_id}) "
            f"目标={target_name}({target_id}) 模式={force_mode} 结果={food_pig_template['name']}"
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
            operator_name=attacker_name, target_name=target_name,
        )
        logger.info(
            f"[烤群友] 成功 | 凶手={attacker_name}({attacker_id}) "
            f"目标={target_name}({target_id}) 结果={food_pig_template['name']}"
        )
        roasted_data = food_pig_template.copy()
        roasted_data["analysis"] = text
        await send_rendered_pig(cmd_roast_member, event, roasted_data)

    # === 逃脱 (30%) ===
    elif roll <= 90:
        escape_text = pick_escape_text(attacker_name, target_name, target_pig)
        logger.info(
            f"[烤群友] 逃脱 | 凶手={attacker_name}({attacker_id}) 目标={target_name}({target_id})"
        )
        await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + escape_text)

    # === 反噬 (10%) ===
    else:
        attacker_pig = get_pig_by_id(data_manager.get_today_pig(attacker_id))

        if attacker_pig and (not is_food_pig(attacker_pig)) and (not is_human_pig(attacker_pig)):
            food_id = random.choice(FOOD_PIG_IDS)
            food_pig_template = get_pig_by_id(food_id)
            if not food_pig_template:
                await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
                return

            text = await roast_manager.get_roast_text(attacker_pig, food_pig_template)
            fail_text = f"偷鸡不成蚀把米！【{attacker_name}】抓猪失败，反倒把自己摔进了火坑！\n\n" + text

            logger.info(
                f"[烤群友] 反噬 | 凶手={attacker_name}({attacker_id}) "
                f"目标={target_name}({target_id}) 凶手变成={food_pig_template['name']}"
            )
            roasted_data = food_pig_template.copy()
            roasted_data["analysis"] = fail_text
            await send_rendered_pig(cmd_roast_member, event, roasted_data)
        else:
            fail_text = pick_backfire_text(attacker_name, target_name, attacker_pig)
            logger.info(
                f"[烤群友] 反噬(文字) | 凶手={attacker_name}({attacker_id}) "
                f"目标={target_name}({target_id})"
            )
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + fail_text)


# 6. 我的猪圈
cmd_sty = on_command("我的猪圈", aliases={"我的小猪"}, block=True)

@cmd_sty.handle()
async def _(event: Event):
    user_id = str(event.user_id)
    collection = data_manager.get_user_collection(user_id)
    total_pigs = len(PIG_LIST)
    user_count = len(collection)

    if total_pigs <= 0:
        await cmd_sty.finish(MessageSegment.reply(event.message_id) + "猪图鉴为空，请先检查资源文件。")
        return

    if user_count == 0:
        await cmd_sty.finish(MessageSegment.reply(event.message_id) + "你的猪圈空空如也！")
        return

    percent = int((user_count / total_pigs) * 100)
    msg = (
        f"【我的猪圈统计】\n"
        f"👑 猪圈主人：{event.sender.card or event.sender.nickname}\n"
        f"📦 已收集：{user_count} / {total_pigs} 只\n"
        f"📈 收藏率：{percent}%\n"
        f"━━━━━━━━━━━━━━\n"
        f"继续加油，争取成为猪王！"
    )
    await cmd_sty.finish(MessageSegment.reply(event.message_id) + msg)


# 7. 本周小猪
cmd_week = on_command("本周小猪", block=True)

@cmd_week.handle()
async def _(event: Event):
    if not HAS_PIL:
        await cmd_week.finish("Bot 未安装 PIL 库。")

    user_id = str(event.user_id)
    today = datetime.date.today()

    images_to_merge = []
    for i in range(7):
        d = today - datetime.timedelta(days=(6 - i))
        pig = get_pig_by_id(data_manager.get_pig_by_date(user_id, d.isoformat()))
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
