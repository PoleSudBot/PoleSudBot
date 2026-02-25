import json
import random
import datetime
import asyncio
import httpx
from pathlib import Path
from typing import List, Dict, Optional

from nonebot import on_command, require, get_bot, get_driver, get_plugin_config
from nonebot.adapters.onebot.v11 import Event, MessageSegment, Message, GroupMessageEvent, Bot
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from .config import Config  
from .roast_manager import roast_manager 

# 确保依赖插件先被 NoneBot 注册
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_localstore")

from nonebot_plugin_htmlrender import template_to_pic
import nonebot_plugin_localstore as store

# --- 引入 PIL ---
try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ================= 配置与文案区域 =================

TOMORROW_TEXTS = [
    "天机不可泄露，但我闻到了一股红烧味...",
    "明日运势：大概率是一只特立独行的猪。",
    "不要问明天，明天你会变瘦（确信）。",
    "水晶球显示，明天你的猪槽里会有好吃的。",
    "明天的猪猪正在赶来的路上，据说是坐火箭来的。",
    "睡觉吧，梦里什么猪都有。",
    "根据星象，明天你可能会进化成更高级的形态。",
    "塔罗牌告诉我，明天的小猪身上有金色的光芒✨",
    "我夜观天象，明日宜养猪，忌烤猪。",
    "根据猪猪星历，明天是‘吃饱不愁’的黄道吉日。",
    "水晶猪猪球显示：明日财运与饲料量成正比。",
    "温馨提示：明天小猪的体重取决于你今天的投喂。",
    "占卜结果显示：明天的你，猪格魅力+100%✨",
    "猪神悄悄告诉我：明天有人要‘猪’力全开哦。",
    "明日预告：你将解锁‘人间小猪’限定皮肤🐷",
    "根据猪猪星象，明天是你的‘变身日’，敬请期待。",
    "温馨提示：明天的你，请离厨房远一点🔪",
    "小道消息：明天你的床会格外有‘猪’引力。",
    "据说明天的你，看到饲料会莫名兴奋...",
    "预警：明天你可能会对泥坑产生特殊兴趣🕳️",
    "剧透一下：明天你的‘哼哼’声会异常悦耳。",
    "放心，明天的你绝对不会变成小猪佩奇...大概吧。",
    "我保证，明天的你肯定不会胖成球——才怪！",
    "据可靠情报，明天你绝不会想赖床...真的吗？",
    "明天的你：我肯定能管住嘴！🐷：不，你不能。",
    "预言：明天你会说自己‘猪’事顺利（物理）。",
    "明天的你，连自己都会觉得‘猪’么可爱。",
    "偷偷告诉你：明天你的鼻子会有点痒...",
    "剧透禁止！但我可以提示：明天记得带纸巾🧻",
    "明天的惊喜正在加载中...当前进度：99%（猪化）",
    "准备好迎接‘全新’的自己了吗？🐷",
    "明天的你，会重新思考‘猪生’的意义。",
    "猪生三问：我是谁？我在哪？明天我是什么猪？",
    "你将亲身体验：什么叫‘笨猪先飞’🐷✈️",
    "明天过后，你会深刻理解‘猪队友’这个词。",
    "预告：明天的你将获得‘猪’一样的睡眠质量。",
    "想提前知道？先学三声猪叫来听听🐷🐷🐷",
    "明天的你已经在我手里了，拿零食来换！",
    "如果现在告诉我你最喜欢的饲料，我就...还是不说。",
    "给你三个提示：哼哼、咕噜、呼...猜到了吗？",
    "明天的你正在发送好友申请，是否通过？✅"
]

FOOD_PIG_IDS = ["roasted-pig", "bacon", "mc_porkchop", "pork-skewer"]
HUMAN_PIG_ID = "human"
FORCE_ROAST_KEYWORDS = {"打点后厨", "偷换烤架", "贿赂主厨", "加急生火", "加急生活"}
SUPER_FORCE_ROAST_KEYWORD = "强行点火"

# 烧烤拦截/失败文案池（尽量保持项目原有的玩梗风格）
TODAY_ROAST_HUMAN_BLOCK_TEXTS = [
    "你今天抽到的是【人类】。烤架识别到高等智慧生物，已自动断电。",
    "【人类】禁止入炉。今日烤猪驳回，请把火力留给真正的小猪。",
]

TODAY_ROAST_FOOD_BLOCK_TEXTS = [
    "你今天已经是【{shape}】了，再烤就只剩锅巴和报警器。",
    "【{shape}】都端上桌了你还想加热？后厨表示拒绝返工。",
]

TARGET_HUMAN_BLOCK_TEXTS = [
    "【{target}】今天是【人类】形态，烤架拒绝处理活体高智商单位。",
    "想烤【{target}】？系统判定目标为【人类】，本次烧烤被城管叫停。",
]

TARGET_FOOD_BLOCK_TEXTS = [
    "【{target}】今天是【{shape}】，已经熟了，别鞭尸了。",
    "【{target}】当前形态【{shape}】已出锅，再烤就成炭了。",
]

BACKFIRE_HUMAN_TEXTS = [
    "【{attacker}】反手一通操作，火没点着，先把自己的刘海燎没了。{target} 在旁边看乐子。",
    "【{attacker}】身为【人类】强行上灶，结果油烟倒灌，场面比烧烤还呛。",
]

BACKFIRE_FOOD_TEXTS = [
    "【{attacker}】本来就是【{shape}】，还想掌勺？一个转身把自己又翻面了。",
    "【{attacker}】顶着【{shape}】去烤人，最后成功把自己烤到外焦里焦。",
]

BACKFIRE_NO_PIG_TEXTS = [
    "【{attacker}】今天连猪都没抽就敢开火，锅都没热就先社死了。",
    "【{attacker}】空手点火失败，火星子没见到，尴尬值先拉满。",
]

BACKFIRE_GENERIC_TEXTS = [
    "【{attacker}】玩火自焚！不仅没烤到【{target}】，还把自己的眉毛烧没了。",
    "【{attacker}】一叉子扎空，脚底一滑栽进火坑，现场香味却来自自己。",
]

ESCAPE_TEXTS = [
    "【{attacker}】刚举起烤叉，【{target}】（{shape}）一个滑铲钻进人群，瞬间消失。",
    "【{attacker}】火都点好了，结果【{target}】（{shape}）反手就是蛇皮走位，烤架当场空军。",
    "【{target}】（{shape}）闻到孜然味拔腿就跑，留下【{attacker}】在风中举叉沉默。",
    "【{attacker}】瞄准半天正要下手，【{target}】（{shape}）一个假动作直接逃出生天。",
    "【{target}】（{shape}）临场开了疾跑，【{attacker}】追了三条街只捡回一撮胡椒粉。",
    "【{attacker}】端着烤盘冲锋，结果【{target}】（{shape}）踩着锅沿完成极限撤离。",
    "火候拉满，气氛到位，偏偏【{target}】（{shape}）先一步跑路，气得【{attacker}】把炭都捏碎了。",
    "【{attacker}】本想来场现场烧烤秀，没想到主角【{target}】（{shape}）直接退场。",
]

SUPER_FORCE_ROAST_PREFIX_TEXTS = [
    "老板好！主厨一看是您来了，吓得赶紧一脚踹翻了【{target}】的旧烤架，换上了全新的无冷却核动力烤炉！",
    "最高权限已介入。后厨为您强行拉闸断电，【{target}】的烧烤计时器被你一把捏碎了。",
    "你一言不发地走进后厨，所有帮厨立刻起立敬礼。你指了指【{target}】的炉子，火瞬间就重燃了。",
    "城管？在这里你就是城管！【{target}】的烤架已强行预热完毕，请老板随时下嘴！",
]

FORCE_ROAST_PREFIX_TEXTS = [
    "你趁着夜色往主厨的围裙里塞了一把碎银子。主厨心领神会，偷偷往【{target}】的炉子里泼了盆冷水。（今日打点次数已用尽）",
    "你在黑市花高价买了一块千年寒冰，成功给【{target}】的烤架物理降温。准备好再次开火了吗？（黑市通行证今日已作废）",
    "你和负责烧烤的赛博义体大爷对上了暗号，大爷悄悄拨回了【{target}】的倒计时器。（今日后厨暗门已对你关闭）",
    "你牺牲了一包辣条，成功贿赂了看守烤架的保安。【{target}】的开火权已重置！（今天别再来了，容易被抓）",
]

FORCE_ROAST_LIMIT_TEXTS = [
    "你今天已经在后厨晃悠过一次了！主厨举着带血的菜刀把你赶了出去：‘再搞事连你一起烤！’",
    "黑市大门紧闭，门上贴着条子：【{operator}】今日交易额度已超标，已被列入后厨失信名单。",
    "你刚掏出钱，就被暗中埋伏的群管当场按倒！偷换烤架失败，快跑吧！",
    "你还来？【{target}】的烤架都快被你盘包浆了，回去等明天的黑市开门吧！",
]

plugin_config = get_plugin_config(Config)


def resolve_roast_cooldown_seconds() -> int:
    """解析普通烤群友 CD（秒），支持通过配置覆盖。"""
    raw_hours = getattr(plugin_config, "rollpig_roast_cooldown_hours", 8.0)
    try:
        hours = float(raw_hours)
    except (TypeError, ValueError):
        logger.warning(f"rollpig_roast_cooldown_hours 配置非法: {raw_hours}，已回退到 8 小时")
        hours = 8.0

    if hours <= 0:
        logger.warning(f"rollpig_roast_cooldown_hours 必须 > 0，当前值: {hours}，已回退到 8 小时")
        hours = 8.0

    return max(1, int(hours * 3600))


ROAST_COOLDOWN_SECONDS = resolve_roast_cooldown_seconds()

# ========================================================

__plugin_meta__ = PluginMetadata(
    name="今天是什么小猪",
    description="抽取属于自己的小猪",
    usage="""
## 🐷 基础指令

- **今日小猪** / **今天是什么小猪** - 抽取今天的命运之猪
- **随机小猪** - 随机看一张猪图
- **找猪** - 从 PigHub 模糊搜索猪猪图
    
## 🔮 趣味指令

- **明日小猪** - 预测明天的猪猪运势
- **昨日小猪** - 查看昨天抽到了什么
- **今日烤猪** - 把今天的猪做成美食（人类/熟食形态会拦截）
- **烤群友** - 把群友做成烤猪（目标需已抽猪且非人类/熟食）
- **烤群友 [追加文本]** - 追加「打点后厨/偷换烤架/贿赂主厨/加急生火/加急生活」可每日一次强制成功
- **烤群友 强行点火** - superuser专属，无限强制成功
    
## 📊 统计指令

- **我的猪圈** - 查看解锁进度
- **本周小猪** - 生成本周猪猪总结长图
""".strip(),
    type="application",
    homepage="https://github.com/Felis2026/nonebot-plugin-rollpig",
    supported_adapters={"~onebot.v11"},
    config=Config,
    extra={
        "author": "Felis2026",
        "version": "unknown",
        "menu_type": "娱乐功能",
    },
)

PLUGIN_DIR = Path(__file__).parent
PIGINFO_PATH = PLUGIN_DIR / "resource" / "pig.json"
IMAGE_DIR = PLUGIN_DIR / "resource" / "image"
RES_DIR = PLUGIN_DIR / "resource"
DATA_FILE = store.get_plugin_data_file("pig_data.json")
PIGHUB_IMAGE_BASE_URL = "https://pighub.top/data/"

pighub_images = []

# --- 核心数据管理类 ---
class PigDataManager:
    def __init__(self):
        self.file = DATA_FILE
        # 数据结构:
        # {
        #   "history": {"YYYY-MM-DD": {"user_id": pig_obj}},
        #   "collection": {"user_id": [pig_id, ...]},
        #   "usage": {"user_id": last_roast_timestamp},
        #   "force_usage": {"user_id": "YYYY-MM-DD"}
        # }
        self.data = self._load()

    def _load(self):
        if not self.file.exists():
            default_data = {"history": {}, "collection": {}, "usage": {}, "force_usage": {}}
            self.file.write_text(json.dumps(default_data, ensure_ascii=False, indent=2), encoding="utf-8")
            return default_data
        try:
            return json.loads(self.file.read_text("utf-8"))
        except Exception as e:
            logger.warning(f"pig_data.json 读取失败，已使用空数据兜底: {e}")
            return {"history": {}, "collection": {}, "usage": {}, "force_usage": {}}

    def save(self):
        self.file.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_today_pig(self, user_id: str) -> Optional[dict]:
        today = datetime.date.today().isoformat()
        return self.data["history"].get(today, {}).get(user_id)

    def set_today_pig(self, user_id: str, pig_data: dict):
        today = datetime.date.today().isoformat()
        if today not in self.data["history"]:
            self.data["history"][today] = {}
        self.data["history"][today][user_id] = pig_data
        
        if "collection" not in self.data: self.data["collection"] = {}
        if user_id not in self.data["collection"]: self.data["collection"][user_id] = []
        if pig_data["id"] not in self.data["collection"][user_id]:
            self.data["collection"][user_id].append(pig_data["id"])
        self.save()

    def get_pig_by_date(self, user_id: str, date_str: str) -> Optional[dict]:
        return self.data["history"].get(date_str, {}).get(user_id)
    
    def get_user_collection(self, user_id: str) -> List[str]:
        return self.data.get("collection", {}).get(user_id, [])

    def check_roast_usage(self, user_id: str) -> tuple[bool, str]:
        """
        检查是否可用（普通模式 CD）
        返回: (是否可用, 提示信息)
        """
        import time
        
        # 兼容性处理：如果 usage 还是旧格式(字典)，先重置为空字典
        if "usage" not in self.data or not isinstance(self.data["usage"], dict):
             self.data["usage"] = {}

        # 再次检查：如果是旧版的日期嵌套结构 {"2023-xx":...}，也清理掉
        # 简单判断：如果 value 是 dict，说明是旧数据
        if self.data["usage"] and isinstance(list(self.data["usage"].values())[0], dict):
             self.data["usage"] = {}

        last_use = self.data["usage"].get(user_id, 0)
        now = time.time()
        cooldown = ROAST_COOLDOWN_SECONDS
        
        if now - last_use < cooldown:
            # 计算剩余时间
            remaining = int(cooldown - (now - last_use))
            m, s = divmod(remaining, 60)
            h, m = divmod(m, 60)
            
            # 构造提示语
            if h > 0: time_str = f"{h}小时{m}分"
            else: time_str = f"{m}分{s}秒"
            
            return False, f"技能冷却中！还需要休息 {time_str} 才能再次烧烤。"
            
        return True, ""

    def update_roast_usage(self, user_id: str):
        """更新使用时间到现在"""
        import time
        # 确保存储结构是平铺的 {user_id: timestamp}
        if "usage" not in self.data or isinstance(list(self.data.get("usage", {}).values())[0] if self.data.get("usage") else 0, dict):
            self.data["usage"] = {}
            
        self.data["usage"][user_id] = time.time()
        self.save()

    def check_force_roast_usage(self, user_id: str) -> bool:
        """普通用户后门：每日仅 1 次"""
        today = datetime.date.today().isoformat()
        if "force_usage" not in self.data or not isinstance(self.data["force_usage"], dict):
            self.data["force_usage"] = {}
        return self.data["force_usage"].get(user_id) != today

    def update_force_roast_usage(self, user_id: str):
        today = datetime.date.today().isoformat()
        if "force_usage" not in self.data or not isinstance(self.data["force_usage"], dict):
            self.data["force_usage"] = {}
        self.data["force_usage"][user_id] = today
        self.save()

    def clean_old_history(self, days_to_keep=14):
        today = datetime.date.today()
        dates_to_del = []
        for date_str in self.data["history"].keys():
            try:
                d = datetime.date.fromisoformat(date_str)
                if (today - d).days > days_to_keep:
                    dates_to_del.append(date_str)
            except ValueError:
                continue
        for d in dates_to_del: del self.data["history"][d]
        self.save()

data_manager = PigDataManager()

# --- 载入资源 ---
def load_resource_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception as e:
        logger.error(f"资源文件读取失败: {path} error={e}")
        return default

PIG_LIST = load_resource_json(PIGINFO_PATH, [])

def find_image_file(pig_id: str) -> Path | None:
    exts = ["png", "jpg", "jpeg", "webp", "gif"]
    for ext in exts:
        file = IMAGE_DIR / f"{pig_id}.{ext}"
        if file.exists(): return file
    return None

def get_pig_by_id(pig_id: str) -> Optional[dict]:
    for p in PIG_LIST:
        if p["id"] == pig_id: return p
    return None


def is_food_pig(pig_data: Optional[dict]) -> bool:
    return bool(pig_data and pig_data.get("id") in FOOD_PIG_IDS)


def is_human_pig(pig_data: Optional[dict]) -> bool:
    return bool(pig_data and pig_data.get("id") == HUMAN_PIG_ID)


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


def is_superuser_user(user_id: str) -> bool:
    superusers = {str(x) for x in getattr(get_driver().config, "superusers", set())}
    if user_id in superusers:
        return True
    # 兼容 "adapter:user_id" 形式
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


def pick_force_prefix_text(target_name: str, is_super_mode: bool) -> str:
    pool = SUPER_FORCE_ROAST_PREFIX_TEXTS if is_super_mode else FORCE_ROAST_PREFIX_TEXTS
    return random.choice(pool).format(target=target_name)


def pick_force_limit_text(operator_name: str, target_name: str) -> str:
    return random.choice(FORCE_ROAST_LIMIT_TEXTS).format(operator=operator_name, target=target_name)


async def ensure_pighub_images_loaded() -> bool:
    """懒加载 PigHub 图库，避免每次命令都请求远端。"""
    global pighub_images
    if pighub_images:
        return True

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # 保持与历史版本一致的请求目标，便于你对照维护。
            resp = await client.get("https://pighub.top/api/all-images")
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"PigHub 连接失败: {e}")
        return False

    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        logger.warning("PigHub 返回结构异常，缺少 images 列表")
        return False

    # 过滤掉缺少 thumbnail 的脏数据，避免后续拼接图片 URL 时崩溃。
    valid = [item for item in data["images"] if isinstance(item, dict) and item.get("thumbnail")]
    if not valid:
        logger.warning("PigHub 返回空图集")
        return False

    pighub_images = valid
    return True


def build_pighub_image_url(pig_item: dict) -> Optional[str]:
    thumbnail = pig_item.get("thumbnail")
    if not isinstance(thumbnail, str) or not thumbnail:
        return None
    return PIGHUB_IMAGE_BASE_URL + thumbnail.split("/")[-1]

# ================= 指令处理区域 =================

# 1. 今日小猪
cmd_today = on_command("今天是什么小猪", aliases={"今日小猪"}, block=True)

@cmd_today.handle()
async def _(event: Event):
    user_id = str(event.user_id)
    pig = data_manager.get_today_pig(user_id)
    
    if not pig:
        if not PIG_LIST:
            await cmd_today.finish("猪圈塌房了（数据缺失）")
            return
        pig = random.choice(PIG_LIST)
        data_manager.set_today_pig(user_id, pig)
        if random.randint(1, 20) == 1: data_manager.clean_old_history()

    await send_rendered_pig(cmd_today, event, pig)


# 2. 随机小猪
cmd_roll = on_command("随机小猪", block=True)

@cmd_roll.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()): 
    if not await ensure_pighub_images_loaded():
        await cmd_roll.finish("连不上 PigHub...")
        return
    
    text = args.extract_plain_text().strip()
    try: count = int(text) if text else 1
    except ValueError: count = 1
    count = max(1, min(count, 10)) 

    pig = random.choice(pighub_images)
    image_url = build_pighub_image_url(pig)
    if not image_url:
        await cmd_roll.finish("PigHub 返回了异常图片数据，请稍后再试。")
        return

    if count == 1:
        await cmd_roll.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image_url))
        return

    # OneBot 私聊不支持合并转发时，降级回单图而不是无响应。
    if not isinstance(event, GroupMessageEvent):
        await cmd_roll.finish(
            MessageSegment.reply(event.message_id)
            + "私聊暂不支持多张连发，先给你一张：\n"
            + MessageSegment.image(image_url)
        )
        return

    messages = []
    for _ in range(count):
        pig = random.choice(pighub_images)
        image_url = build_pighub_image_url(pig)
        if not image_url:
            continue
        messages.append({
            "type": "node",
            "data": {
                "name": "随机小猪Bot", 
                "uin": event.self_id, 
                "content": Message(pig.get("title", "随机小猪")) + MessageSegment.image(image_url)
            }
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
                    "name": "搜猪小助手", "uin": event.self_id,
                    "content": Message(pig.get("title", "未命名小猪")) + MessageSegment.image(image_url)
                }
            })
        if not messages:
            await cmd_find.finish("搜索结果数据异常，请稍后再试。")
            return
        await bot.send_group_forward_msg(group_id=event.group_id, messages=messages)
        return

    # 私聊降级：展示首条匹配，避免“无响应”。
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
    pig = data_manager.get_pig_by_date(user_id, yesterday)
    
    if not pig: await cmd_yest.finish(MessageSegment.reply(event.message_id) + "你昨天没抽猪。")
    msg = f"你昨天是一只【{pig['name']}】！"
    await send_rendered_pig(cmd_yest, event, pig, extra_text=msg)


# 5. 今日烤猪
cmd_roast = on_command("今日烤猪", block=True)

@cmd_roast.handle()
async def _(event: Event):
    user_id = str(event.user_id)
    original_pig = data_manager.get_today_pig(user_id)
    
    if not original_pig:
        await cmd_roast.finish(MessageSegment.reply(event.message_id) + "你连猪都不是，怎么烤？")
        return
    
    # 人类形态不允许进入烧烤流程。
    if is_human_pig(original_pig):
        await cmd_roast.finish(
            MessageSegment.reply(event.message_id)
            + random.choice(TODAY_ROAST_HUMAN_BLOCK_TEXTS)
        )
        return

    # 已经是熟食形态时直接拦截，避免继续“二次烧烤”造成语义混乱。
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
async def _(bot: Bot, event: GroupMessageEvent): # 确保用 GroupMessageEvent 方便获取群名片
    attacker_id = str(event.user_id)
    attacker_name = event.sender.card or event.sender.nickname
    force_mode = detect_force_roast_mode(event.get_plaintext(), attacker_id)  # None / normal / super / super_denied

    if force_mode == "super_denied":
        await cmd_roast_member.finish(
            MessageSegment.reply(event.message_id) + "口令【强行点火】仅 superuser 可用。"
        )
        return

    # 2. 提取目标 ID 和 名字
    target_id = None
    target_name = "群友" # 默认值

    # 优先检查回复
    if event.reply:
        target_id = str(event.reply.sender.user_id)
        target_name = event.reply.sender.card or event.reply.sender.nickname
    else:
        # 检查 At
        for seg in event.message:
            if seg.type == "at":
                target_id = str(seg.data["qq"])
                # 需要调 API 获取被艾特人的昵称，为了不阻塞，这里先暂时不获取，或者依赖 segment 里的显示
                # 简单处理：如果能从群成员列表拿最好，拿不到就用"对方"
                # 这里为了代码简洁，暂不额外调用 get_group_member_info，除非你想更完美
                target_name = "对方" 
                break
    
    # 尝试获取更准确的 target_name (如果 API 允许)
    if target_id:
        try:
            member_info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(target_id))
            target_name = member_info.get("card") or member_info.get("nickname")
        except Exception as e:
            # 不阻断主流程，仅记录以便排查权限/接口异常。
            logger.debug(f"获取群成员信息失败: group={event.group_id} user={target_id} error={e}")

    if not target_id:
        await cmd_roast_member.finish("请 At 或回复你要烤的群友！")
        return
    
    if target_id == attacker_id:
        await cmd_roast_member.finish("对自己好一点，别自焚。请发送「今日烤猪」。")
        return

    # 3. 读取目标形态：后门模式也不绕过“未抽猪/人类/熟食”限制
    target_pig = data_manager.get_today_pig(target_id)
    if not target_pig:
        await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + f"【{target_name}】今天还没抽猪，没法下嘴！")
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

    # 4. 模式化限制/计数
    if force_mode == "normal":
        if not data_manager.check_force_roast_usage(attacker_id):
            reject_text = pick_force_limit_text(attacker_name, target_name)
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + reject_text)
            return
        data_manager.update_force_roast_usage(attacker_id)
    elif force_mode is None:
        is_available, tip_msg = data_manager.check_roast_usage(attacker_id)
        if not is_available:
            await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + tip_msg)
            return
        data_manager.update_roast_usage(attacker_id)
    # super 模式无限制，不消耗普通后门计数，也不走 8h CD

    # 5. 后门模式：必定成功
    if force_mode in {"normal", "super"}:
        food_id = random.choice(FOOD_PIG_IDS)
        food_pig_template = get_pig_by_id(food_id)
        if not food_pig_template:
            await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
            return

        text = await roast_manager.get_roast_text(
            target_pig,
            food_pig_template,
            operator_name=attacker_name,
            target_name=target_name
        )
        prefix_text = pick_force_prefix_text(target_name, is_super_mode=(force_mode == "super"))

        roasted_data = food_pig_template.copy()
        roasted_data["analysis"] = text
        await send_rendered_pig(cmd_roast_member, event, roasted_data, extra_text=prefix_text)
        return

    # 6. 普通模式概率判定
    roll = random.randint(1, 100)

    # === 场景 A: 成功 (60%) ===
    if roll <= 60:
        food_id = random.choice(FOOD_PIG_IDS)
        food_pig_template = get_pig_by_id(food_id)
        if not food_pig_template:
            await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
            return
        
        # 核心修改：传入 target_name，并由 Manager 处理占位符
        text = await roast_manager.get_roast_text(
            target_pig, 
            food_pig_template, 
            operator_name=attacker_name,
            target_name=target_name  # 👈 传入受害者名字
        )
        
        # 渲染图片
        roasted_data = food_pig_template.copy()
        roasted_data["analysis"] = text
        await send_rendered_pig(cmd_roast_member, event, roasted_data)
        
    # === 场景 B: 逃脱 (30%) ===
    elif roll <= 90:
        escape_text = pick_escape_text(attacker_name, target_name, target_pig)
        await cmd_roast_member.finish(MessageSegment.reply(event.message_id) + escape_text)
        
    # === 场景 C: 反噬 (10%) ===
    else:
        # 读取凶手当天形态，用于决定反噬走向
        attacker_pig = data_manager.get_today_pig(attacker_id)
        
        # 凶手为可烤形态时，会被反噬成食材并渲染图片
        if attacker_pig and (not is_food_pig(attacker_pig)) and (not is_human_pig(attacker_pig)):
            food_id = random.choice(FOOD_PIG_IDS)
            food_pig_template = get_pig_by_id(food_id)
            if not food_pig_template:
                await cmd_roast_member.finish("食材配置缺失，请联系管理员修复 pig.json。")
                return
            
            # 复用普通烤猪文案逻辑，拼接反噬前缀后返回
            text = await roast_manager.get_roast_text(attacker_pig, food_pig_template)
            fail_text = f"偷鸡不成蚀把米！【{attacker_name}】抓猪失败，反倒把自己摔进了火坑！\n\n" + text
            
            roasted_data = food_pig_template.copy()
            roasted_data["analysis"] = fail_text
            await send_rendered_pig(cmd_roast_member, event, roasted_data)
            
        # 如果凶手是人类/熟食/未抽猪 -> 走通用失败文案
        else:
            fail_text = pick_backfire_text(attacker_name, target_name, attacker_pig)
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
    if not HAS_PIL: await cmd_week.finish("Bot 未安装 PIL 库。")

    user_id = str(event.user_id)
    today = datetime.date.today()
    
    images_to_merge = []
    
    for i in range(7):
        d = today - datetime.timedelta(days=(6-i))
        d_str = d.isoformat()
        pig = data_manager.get_pig_by_date(user_id, d_str)
        if pig:
            img_file = find_image_file(pig["id"])
            if img_file:
                images_to_merge.append(img_file)
        
    if not images_to_merge:
        await cmd_week.finish(MessageSegment.reply(event.message_id) + "你这周还没抽过猪呢！")
        return

    msg = None
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
        
        msg = MessageSegment.reply(event.message_id) + \
              f"你这周变了 {len(images_to_merge)} 次猪！" + \
              MessageSegment.image(output.getvalue())
    except Exception as e:
        logger.error(f"本周小猪长图生成失败: user={user_id}, error={e}")
        await cmd_week.finish("生成图片失败。")
        return

    await cmd_week.finish(msg)   


# --- 辅助渲染函数 ---
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
