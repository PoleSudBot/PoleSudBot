import json
import asyncio
import datetime
import time
from pathlib import Path
from typing import List, Optional

from nonebot import get_plugin_config
from nonebot.log import logger
import nonebot_plugin_localstore as store

from .config import Config

# ================= 冷却时间解析 =================

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

# ================= 数据管理 =================

DATA_FILE = store.get_plugin_data_file("pig_data.json")


class PigDataManager:
    """
    负责插件所有持久化数据的读写。

    数据结构：
    - history    : {date: {user_id: pig_id}}  ← 新格式，仅存 pig_id（14天后自动清理）
                   旧版存完整 pig dict，_migrate() 会自动转换
    - collection : {user_id: [pig_id, ...]}   ← 永久保留，图鉴数据
    - usage      : {user_id: timestamp}        ← 烤群友普通模式 CD 时间戳
    - force_usage: {user_id: "YYYY-MM-DD"}    ← 后门口令每日计数

    写操作通过 asyncio.Lock 串行化，文件使用原子替换（.tmp → rename）防止 JSON 损坏。
    """

    def __init__(self):
        self.file = DATA_FILE
        self._lock = asyncio.Lock()
        self.data = self._load()

    # ---- 加载与迁移 ----

    def _load(self) -> dict:
        if not self.file.exists():
            default = {"history": {}, "collection": {}, "usage": {}, "force_usage": {}}
            self.file.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return default
        try:
            raw = json.loads(self.file.read_text("utf-8"))
            return self._migrate(raw)
        except Exception as e:
            logger.warning(f"pig_data.json 读取失败，已使用空数据兜底: {e}")
            return {"history": {}, "collection": {}, "usage": {}, "force_usage": {}}

    def _migrate(self, data: dict) -> dict:
        """将旧版 history（存完整 pig dict）迁移为新版（只存 pig_id 字符串）。
        迁移完成后立即同步落盘，防止进程在第一次写入前已退出导致磁盘仍为旧格式。
        """
        history = data.get("history", {})
        migrated = False
        for date_str, records in history.items():
            if not isinstance(records, dict):
                continue
            for uid, val in list(records.items()):
                if isinstance(val, dict) and "id" in val:
                    records[uid] = val["id"]
                    migrated = True
        if migrated:
            logger.info("pig_data.json 历史数据已自动迁移（完整 dict → pig_id 字符串），开始落盘...")
            self.data = data
            self._sync_save()  # 迁移后立即落盘，防止重启丢失
        return data

    # ---- 原子写 ----

    def _sync_save(self):
        """同步原子写（仅用于启动期迁移，运行期写操作应使用 _atomic_save）。"""
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    async def _atomic_save(self):
        """异步原子写：写入临时文件再原子替换，防止写入中途崩溃导致 JSON 损坏。"""
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)  # 同一文件系统上是原子操作（Windows/Linux 均支持）

    # ---- 今日/历史 抽猪记录 ----

    def get_today_pig(self, user_id: str) -> Optional[str]:
        """返回今日已抽的 pig_id，未抽返回 None。"""
        today = datetime.date.today().isoformat()
        return self.data["history"].get(today, {}).get(user_id)

    async def set_today_pig(self, user_id: str, pig_id: str):
        """记录今日抽到的 pig_id，并同步将其写入图鉴（永久保留）。"""
        async with self._lock:
            today = datetime.date.today().isoformat()
            if today not in self.data["history"]:
                self.data["history"][today] = {}
            self.data["history"][today][user_id] = pig_id

            # 图鉴：永久保留，不受 14 天历史清理影响
            col = self.data.setdefault("collection", {})
            user_col = col.setdefault(user_id, [])
            if pig_id not in user_col:
                user_col.append(pig_id)

            await self._atomic_save()

    def get_pig_by_date(self, user_id: str, date_str: str) -> Optional[str]:
        """返回指定日期的 pig_id，无记录返回 None。"""
        return self.data["history"].get(date_str, {}).get(user_id)

    def get_user_collection(self, user_id: str) -> List[str]:
        return self.data.get("collection", {}).get(user_id, [])

    async def clean_old_history(self, days_to_keep: int = 14):
        """清理超过 days_to_keep 天的历史记录（不影响图鉴数据）。"""
        async with self._lock:
            today = datetime.date.today()
            dates_to_del = [
                d for d in self.data["history"]
                if _is_valid_date(d)  # 必须先过滤非法日期键，再做计算（防止 ValueError）
                and (today - datetime.date.fromisoformat(d)).days > days_to_keep
            ]
            for d in dates_to_del:
                del self.data["history"][d]
            if dates_to_del:
                await self._atomic_save()

    # ---- 烤群友 普通模式 CD ----

    def check_roast_usage(self, user_id: str) -> tuple[bool, str]:
        """
        检查普通烤群友 CD 是否已过。
        返回: (是否可用, 若不可用时的提示信息)
        """
        # 兼容旧版数据结构
        if "usage" not in self.data or not isinstance(self.data["usage"], dict):
            self.data["usage"] = {}
        if self.data["usage"] and isinstance(list(self.data["usage"].values())[0], dict):
            self.data["usage"] = {}

        last_use = self.data["usage"].get(user_id, 0)
        now = time.time()
        cooldown = ROAST_COOLDOWN_SECONDS

        if now - last_use < cooldown:
            remaining = int(cooldown - (now - last_use))
            m, s = divmod(remaining, 60)
            h, m = divmod(m, 60)
            time_str = f"{h}小时{m}分" if h > 0 else f"{m}分{s}秒"
            return False, f"技能冷却中！还需要休息 {time_str} 才能再次烧烤。"

        return True, ""

    async def update_roast_usage(self, user_id: str):
        """记录本次使用时间戳。"""
        async with self._lock:
            usage = self.data.setdefault("usage", {})
            # 兼容旧版嵌套 dict 格式
            if usage and isinstance(list(usage.values())[0], dict):
                self.data["usage"] = {}
            self.data["usage"][user_id] = time.time()
            await self._atomic_save()

    # ---- 烤群友 后门口令 每日计数 ----

    def check_force_roast_usage(self, user_id: str) -> bool:
        """普通用户后门：每日仅 1 次，返回今日是否仍可用。"""
        today = datetime.date.today().isoformat()
        if "force_usage" not in self.data or not isinstance(self.data["force_usage"], dict):
            self.data["force_usage"] = {}
        return self.data["force_usage"].get(user_id) != today

    async def update_force_roast_usage(self, user_id: str):
        async with self._lock:
            today = datetime.date.today().isoformat()
            self.data.setdefault("force_usage", {})[user_id] = today
            await self._atomic_save()


def _is_valid_date(date_str: str) -> bool:
    try:
        datetime.date.fromisoformat(date_str)
        return True
    except ValueError:
        return False


# 全局单例，供各指令处理函数统一使用
data_manager = PigDataManager()
