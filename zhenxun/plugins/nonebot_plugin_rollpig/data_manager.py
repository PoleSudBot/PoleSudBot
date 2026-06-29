import json
import asyncio
import datetime
import time
import math
import shutil
from pathlib import Path
from typing import List, Optional

from nonebot.log import logger
import nonebot_plugin_localstore as store

from .runtime import rollpig_date_str, rollpig_today, resolve_roast_cooldown_seconds
from .store.models import CatalogSnapshot, DailyRollResult, DrawState, PigProgress

ROAST_COOLDOWN_SECONDS = resolve_roast_cooldown_seconds()
DATA_BACKUP_COUNT = 2

# ================= 数据管理 =================

DATA_FILE = store.get_plugin_data_file("pig_data.json")


class PigDataManager:
    """
    负责插件所有持久化数据的读写。

    数据结构：
    - history    : {date: {user_id: pig_id}}  ← 新格式，仅存 pig_id（14天后自动清理）
                   旧版存完整 pig dict，_migrate() 会自动转换
    - group_rolls: {date: {group_id: {user_id: pig_id}}} ← 群内“今日已抽/已显形”记录
    - collection : {user_id: [pig_id, ...]}   ← 永久保留，图鉴数据
    - collection_progress: {user_id: {"count": int, "reached_at": float | null}}
                         ← 记录用户达到当前图鉴数量的时间，用于排行榜并列时判先后
    - pig_progress: {user_id: {pig_id: {copies, first_obtained_at}}}
                         ← 记录每只猪累计抽到次数，用于 EX 等级
    - draw_state : {user_id: {"duplicate_streak": int}}
                         ← 记录连续重复次数，用于新猪候选权重
    - usage      : {user_id: timestamp}        ← 烤群友普通模式 CD 时间戳
    - force_usage: {user_id: "YYYY-MM-DD"}    ← 后门口令每日计数
    - daily_events: {date: [event, ...]}      ← 群内烧烤事件（用于日报）

    写操作通过 asyncio.Lock 串行化，文件使用原子替换（.tmp → rename）防止 JSON 损坏。
    """

    def __init__(self):
        self.file = DATA_FILE
        self._lock = asyncio.Lock()
        self._load_failed = False
        self._skip_backup_rotation_once = False
        self.data = self._load()

    # ---- 加载与迁移 ----

    def _default_data(self) -> dict:
        return {
            "history": {},
            "group_rolls": {},
            "collection": {},
            "collection_progress": {},
            "pig_progress": {},
            "draw_state": {},
            "usage": {},
            "force_usage": {},
            "daily_events": {},
            "protected": {},
        }

    def _load(self) -> dict:
        if not self.file.exists():
            default = self._default_data()
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return default
        try:
            raw = json.loads(self.file.read_text("utf-8"))
            return self._migrate(raw)
        except Exception as e:
            self._load_failed = True
            logger.error(f"pig_data.json 读取失败，进入写保护模式以避免覆盖旧数据: {e}")
            self._preserve_broken_file()

            recovered = self._load_backup()
            if recovered is not None:
                logger.warning("pig_data.json 已从备份恢复，并写回主文件。")
                self._load_failed = False
                self.data = recovered
                self._skip_backup_rotation_once = True
                self._sync_save()
                return recovered

            logger.error("pig_data.json 没有可用备份；本地存储写操作将被拒绝，请手动修复数据文件。")
            return self._default_data()

    def _migrate(self, data: dict, *, persist: bool = True) -> dict:
        """将旧版 history（存完整 pig dict）迁移为新版（只存 pig_id 字符串）。
        迁移完成后立即同步落盘，防止进程在第一次写入前已退出导致磁盘仍为旧格式。
        """
        if not isinstance(data, dict):
            data = {}

        migrated = False
        for key in (
            "history",
            "group_rolls",
            "collection",
            "collection_progress",
            "pig_progress",
            "draw_state",
            "usage",
            "force_usage",
            "daily_events",
        ):
            if not isinstance(data.get(key), dict):
                data[key] = {}
                migrated = True
        if not isinstance(data.get("protected"), dict):
            data["protected"] = {}
            migrated = True

        collection = data.get("collection", {})
        progress = data.get("collection_progress", {})
        normalized_progress: dict[str, dict[str, float | int | None]] = {}
        for user_id, pig_ids in collection.items():
            if not isinstance(pig_ids, list):
                continue
            expected_count = len(pig_ids)
            raw_progress = progress.get(str(user_id)) if isinstance(progress, dict) else None
            normalized_entry = self._normalize_collection_progress_entry(
                raw_progress,
                default_count=expected_count,
            )
            if raw_progress is None:
                # 旧账本没有“达到当前数”的时间，不能根据残缺历史反推出先后，只能补数量。
                normalized_entry["reached_at"] = None
                migrated = True
            elif normalized_entry != raw_progress:
                migrated = True
            normalized_progress[str(user_id)] = normalized_entry
        if normalized_progress != progress:
            data["collection_progress"] = normalized_progress
            migrated = True

        # 旧账本只有“是否拥有过”，无法还原真实重复次数；迁移时只保守补 copies=1。
        # 这样不会凭空制造 EX 等级，同时后续每日首次重复抽到才会继续累计。
        pig_progress = data.setdefault("pig_progress", {})
        draw_state = data.setdefault("draw_state", {})
        for user_id, pig_ids in collection.items():
            if not isinstance(pig_ids, list):
                continue
            normalized_user_id = str(user_id)
            user_progress = pig_progress.setdefault(normalized_user_id, {})
            if not isinstance(user_progress, dict):
                user_progress = {}
                pig_progress[normalized_user_id] = user_progress
                migrated = True
            for pig_id in pig_ids:
                normalized_pig_id = str(pig_id)
                item = user_progress.get(normalized_pig_id)
                if not isinstance(item, dict):
                    user_progress[normalized_pig_id] = {
                        "copies": 1,
                        "first_obtained_at": None,
                    }
                    migrated = True
                elif _safe_int(item.get("copies"), 0) <= 0:
                    item["copies"] = 1
                    migrated = True
            state = draw_state.get(normalized_user_id)
            if not isinstance(state, dict):
                draw_state[normalized_user_id] = {"duplicate_streak": 0}
                migrated = True
            elif _safe_int(state.get("duplicate_streak"), 0) < 0:
                state["duplicate_streak"] = 0
                migrated = True

        history = data.get("history", {})
        for date_str, records in history.items():
            if not isinstance(records, dict):
                continue
            for uid, val in list(records.items()):
                if isinstance(val, dict) and "id" in val:
                    records[uid] = val["id"]
                    migrated = True

        protected = data.get("protected", {})
        if "date" in protected and isinstance(protected.get("users"), list):
            protect_date = str(protected.get("date") or "")
            users = [str(user_id) for user_id in protected.get("users", []) if user_id]
            data["protected"] = {protect_date: {"__all__": users}} if protect_date else {}
            migrated = True
        else:
            normalized_protected: dict[str, dict[str, list[str]]] = {}
            for protect_date, group_map in protected.items():
                if not _is_valid_date(str(protect_date)):
                    continue
                if not isinstance(group_map, dict):
                    continue
                normalized_group_map: dict[str, list[str]] = {}
                for group_id, user_ids in group_map.items():
                    if not isinstance(user_ids, list):
                        continue
                    normalized_group_map[str(group_id)] = [str(user_id) for user_id in user_ids if user_id]
                normalized_protected[str(protect_date)] = normalized_group_map
            if normalized_protected != protected:
                data["protected"] = normalized_protected
                migrated = True
        if migrated:
            logger.info("pig_data.json 数据结构已自动迁移/补全，开始落盘...")
            if persist:
                self.data = data
                self._sync_save()  # 迁移后立即落盘，防止重启丢失
        return data

    # ---- 原子写 ----

    def _sync_save(self):
        """同步原子写（仅用于启动期迁移，运行期写操作应使用 _atomic_save）。"""
        self._ensure_writable()
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._rotate_backups()
        tmp.replace(self.file)

    async def _atomic_save(self):
        """异步原子写：把 JSON 序列化和磁盘 IO 放到线程，避免阻塞事件循环。"""
        # 调用方仍持有 self._lock；这里仅把同步 IO 挪到线程，保存顺序不变。
        await asyncio.to_thread(self._sync_save)

    def _ensure_writable(self):
        """账册读取失败后拒绝写入，避免空数据覆盖仍可人工修复的坏文件。"""
        if self._load_failed:
            raise RuntimeError("pig_data.json 读取失败，已拒绝写入以避免覆盖旧数据。请先修复数据文件或恢复备份。")

    def _backup_paths(self) -> list[Path]:
        return [
            self.file.with_name(f"{self.file.name}.bak{'' if index == 0 else f'.{index}'}")
            for index in range(DATA_BACKUP_COUNT + 1)
        ]

    def _rotate_backups(self) -> None:
        """每次成功写入前保留滚动备份；恢复场景跳过一次以保护已有好备份。"""
        if self._skip_backup_rotation_once:
            self._skip_backup_rotation_once = False
            return
        if not self.file.exists():
            return

        backup_paths = self._backup_paths()
        for index in range(len(backup_paths) - 1, -1, -1):
            source = self.file if index == 0 else backup_paths[index - 1]
            target = backup_paths[index]
            if not source.exists():
                continue
            if target.exists():
                target.unlink()
            shutil.copy2(source, target)

    def _preserve_broken_file(self) -> None:
        """把无法读取的主文件另存为 broken 备份，方便人工排查和恢复。"""
        if not self.file.exists():
            return
        broken_path = self.file.with_name(f"{self.file.name}.broken.{int(time.time())}.bak")
        try:
            shutil.copy2(self.file, broken_path)
            logger.error(f"pig_data.json 损坏文件已保留: {broken_path}")
        except Exception as error:
            logger.error(f"pig_data.json 损坏文件备份失败: {error}")

    def _load_backup(self) -> Optional[dict]:
        """按新到旧顺序尝试备份，成功后仍走迁移归一化但不立即写盘。"""
        for backup_path in self._backup_paths():
            if not backup_path.exists():
                continue
            try:
                raw = json.loads(backup_path.read_text("utf-8"))
                logger.warning(f"尝试从 pig_data.json 备份恢复: {backup_path}")
                return self._migrate(raw, persist=False)
            except Exception as error:
                logger.warning(f"pig_data.json 备份不可用: {backup_path}: {error}")
        return None

    def _normalize_collection_progress_entry(
        self,
        progress: object,
        *,
        default_count: int,
    ) -> dict[str, float | int | None]:
        normalized = {"count": max(0, int(default_count)), "reached_at": None}
        if not isinstance(progress, dict):
            return normalized

        try:
            normalized["count"] = max(0, int(progress.get("count", default_count)))
        except (TypeError, ValueError):
            normalized["count"] = max(0, int(default_count))

        raw_reached_at = progress.get("reached_at")
        if raw_reached_at is None:
            return normalized

        try:
            reached_at = float(raw_reached_at)
        except (TypeError, ValueError):
            return normalized

        if math.isfinite(reached_at):
            normalized["reached_at"] = reached_at
        return normalized

    def _update_collection_progress(
        self,
        user_id: str,
        user_collection: list[str],
        reached_at: Optional[float] = None,
    ) -> None:
        progress = self.data.setdefault("collection_progress", {})
        progress[user_id] = {
            "count": len(user_collection),
            "reached_at": float(reached_at if reached_at is not None else time.time()),
        }

    # ---- 抽猪成长状态 ----

    def get_draw_state(self, user_id: str) -> DrawState:
        """返回用户图鉴成长状态，旧 collection 数据会按 copies=1 兜底聚合。"""
        normalized_user_id = str(user_id)
        collection = self.data.setdefault("collection", {})
        raw_collection = collection.get(normalized_user_id, [])
        collection_ids = (
            [str(pig_id) for pig_id in raw_collection]
            if isinstance(raw_collection, list)
            else []
        )

        raw_progress = self.data.setdefault("pig_progress", {}).get(
            normalized_user_id,
            {},
        )
        progress: dict[str, PigProgress] = {}
        if isinstance(raw_progress, dict):
            for pig_id, item in raw_progress.items():
                if not isinstance(item, dict):
                    continue
                progress[str(pig_id)] = PigProgress(
                    copies=max(0, _safe_int(item.get("copies"), 0)),
                    first_obtained_at=item.get("first_obtained_at"),
                )

        for pig_id in collection_ids:
            progress.setdefault(pig_id, PigProgress(copies=1, first_obtained_at=None))

        raw_state = self.data.setdefault("draw_state", {}).get(normalized_user_id, {})
        duplicate_streak = (
            _safe_int(raw_state.get("duplicate_streak"), 0)
            if isinstance(raw_state, dict)
            else 0
        )
        return DrawState(
            pig_ids=sorted(progress),
            progress=dict(sorted(progress.items())),
            duplicate_streak=max(0, duplicate_streak),
        )

    def _apply_created_roll_progress_locked(
        self,
        user_id: str,
        pig_id: str,
    ) -> DailyRollResult:
        """在持有写锁时更新首次抽取产生的成长状态。"""
        collection = self.data.setdefault("collection", {})
        user_collection = collection.setdefault(user_id, [])
        if not isinstance(user_collection, list):
            user_collection = []
            collection[user_id] = user_collection

        pig_progress = self.data.setdefault("pig_progress", {})
        user_progress = pig_progress.setdefault(user_id, {})
        if not isinstance(user_progress, dict):
            user_progress = {}
            pig_progress[user_id] = user_progress

        draw_state = self.data.setdefault("draw_state", {})
        state = draw_state.setdefault(user_id, {"duplicate_streak": 0})
        if not isinstance(state, dict):
            state = {"duplicate_streak": 0}
            draw_state[user_id] = state

        previous_duplicate_streak = max(0, _safe_int(state.get("duplicate_streak"), 0))
        previous_item = user_progress.get(pig_id)
        has_progress = isinstance(previous_item, dict)
        already_collected = pig_id in user_collection
        previous_copies = (
            max(1, _safe_int(previous_item.get("copies"), 1))
            if has_progress
            else (1 if already_collected else 0)
        )
        is_new_pig = previous_copies <= 0 and not already_collected

        if pig_id not in user_collection:
            user_collection.append(pig_id)
            self._update_collection_progress(user_id, user_collection)

        # 重复猪只影响成长状态，不能刷新 collection_progress，否则会污染排行并列时间。
        if is_new_pig:
            copies = 1
            duplicate_streak = 0
            first_obtained_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        else:
            copies = previous_copies + 1
            duplicate_streak = previous_duplicate_streak + 1
            first_obtained_at = (
                previous_item.get("first_obtained_at")
                if has_progress and previous_item.get("first_obtained_at")
                else None
            )

        user_progress[pig_id] = {
            "copies": copies,
            "first_obtained_at": first_obtained_at,
        }
        state["duplicate_streak"] = duplicate_streak
        return DailyRollResult(
            pig_id=pig_id,
            created=True,
            is_new_pig=is_new_pig,
            previous_copies=previous_copies,
            copies=copies,
            previous_duplicate_streak=previous_duplicate_streak,
            duplicate_streak=duplicate_streak,
        )

    def _build_existing_roll_result_locked(
        self,
        user_id: str,
        pig_id: str,
    ) -> DailyRollResult:
        draw_state = self.get_draw_state(user_id)
        copies = draw_state.copies_of(pig_id)
        return DailyRollResult(
            pig_id=pig_id,
            created=False,
            previous_copies=copies,
            copies=copies,
            previous_duplicate_streak=draw_state.duplicate_streak,
            duplicate_streak=draw_state.duplicate_streak,
        )

    # ---- 今日/历史 抽猪记录 ----

    def get_today_pig(self, user_id: str, date_str: Optional[str] = None) -> Optional[str]:
        """返回今日已抽的 pig_id，未抽返回 None。"""
        target_date = date_str or rollpig_date_str()
        return self.data["history"].get(target_date, {}).get(user_id)

    def get_daily_rolls(self, date_str: Optional[str] = None) -> dict:
        target_date = date_str or rollpig_date_str()
        return dict(self.data.get("history", {}).get(target_date, {}))

    def _record_group_roll(self, date_str: str, group_id: str, user_id: str, pig_id: str):
        """在群维度登记今日已出现的猪形态，用于群内日报与随机烤群友。"""
        if not group_id:
            return
        group_rolls = self.data.setdefault("group_rolls", {})
        day_rolls = group_rolls.setdefault(date_str, {})
        group_roll_map = day_rolls.setdefault(group_id, {})
        group_roll_map[user_id] = pig_id

    async def set_today_pig(self, user_id: str, pig_id: str, group_id: str = ""):
        """记录今日抽到的 pig_id，并同步将其写入图鉴（永久保留）。"""
        async with self._lock:
            today = rollpig_date_str()
            if today not in self.data["history"]:
                self.data["history"][today] = {}
            previous_pig_id = self.data["history"][today].get(user_id)
            self.data["history"][today][user_id] = pig_id
            self._record_group_roll(today, group_id, user_id, pig_id)
            if previous_pig_id != pig_id:
                self._apply_created_roll_progress_locked(user_id, pig_id)

            await self._atomic_save()

    async def get_or_create_today_pig(
        self,
        user_id: str,
        proposed_pig_id: str,
        date_str: Optional[str] = None,
        group_id: str = "",
    ) -> DailyRollResult:
        target_date = date_str or rollpig_date_str()
        async with self._lock:
            history = self.data.setdefault("history", {})
            day_history = history.setdefault(target_date, {})
            existing_pig_id = day_history.get(user_id)
            dirty = False

            if existing_pig_id:
                if group_id:
                    before = (
                        self.data.get("group_rolls", {})
                        .get(target_date, {})
                        .get(group_id, {})
                        .get(user_id)
                    )
                    self._record_group_roll(target_date, group_id, user_id, existing_pig_id)
                    dirty = before != existing_pig_id
                if dirty:
                    await self._atomic_save()
                return self._build_existing_roll_result_locked(user_id, existing_pig_id)

            day_history[user_id] = proposed_pig_id
            self._record_group_roll(target_date, group_id, user_id, proposed_pig_id)
            result = self._apply_created_roll_progress_locked(user_id, proposed_pig_id)

            await self._atomic_save()
            return result

    async def mark_group_roll_seen(
        self,
        user_id: str,
        pig_id: str,
        group_id: str,
        date_str: Optional[str] = None,
    ):
        """将已有的今日形态登记到当前群，避免群内统计漏记。"""
        if not group_id:
            return
        async with self._lock:
            target_date = date_str or rollpig_date_str()
            self._record_group_roll(target_date, group_id, user_id, pig_id)
            await self._atomic_save()

    def get_pig_by_date(self, user_id: str, date_str: str) -> Optional[str]:
        """返回指定日期的 pig_id，无记录返回 None。"""
        return self.data["history"].get(date_str, {}).get(user_id)

    def get_user_collection(self, user_id: str) -> List[str]:
        return self.data.get("collection", {}).get(user_id, [])

    def get_all_collections(self) -> dict[str, List[str]]:
        collection = self.data.get("collection", {})
        if not isinstance(collection, dict):
            return {}
        return {
            str(user_id): list(pig_ids)
            for user_id, pig_ids in collection.items()
            if isinstance(pig_ids, list)
        }

    def get_collection_progress(self, user_id: str) -> dict[str, float | int | None] | None:
        collection = self.get_user_collection(user_id)
        progress = self.data.get("collection_progress", {})
        if not isinstance(progress, dict):
            return None
        user_progress = progress.get(user_id)
        if user_progress is None:
            return None
        return self._normalize_collection_progress_entry(
            user_progress,
            default_count=len(collection),
        )

    def get_all_collection_progress(self) -> dict[str, dict[str, float | int | None]]:
        collection = self.get_all_collections()
        progress = self.data.get("collection_progress", {})
        if not isinstance(progress, dict):
            progress = {}
        return {
            user_id: self._normalize_collection_progress_entry(
                progress.get(user_id),
                default_count=len(pig_ids),
            )
            for user_id, pig_ids in collection.items()
        }

    def get_recent_user_names(self) -> dict[str, str]:
        result: dict[str, str] = {}
        daily_events = self.data.get("daily_events", {})
        if not isinstance(daily_events, dict):
            return result

        for date_str in sorted(daily_events.keys(), reverse=True):
            events = daily_events.get(date_str)
            if not isinstance(events, list):
                continue
            for event in reversed(events):
                if not isinstance(event, dict):
                    continue
                attacker_id = str(event.get("attacker") or "").strip()
                attacker_name = str(event.get("attacker_name") or "").strip()
                target_id = str(event.get("target") or "").strip()
                target_name = str(event.get("target_name") or "").strip()
                if attacker_id and attacker_name and attacker_id not in result:
                    result[attacker_id] = attacker_name
                if target_id and target_name and target_id not in result:
                    result[target_id] = target_name
        return result

    def get_recent_rolls(self, user_id: str, days: int = 14) -> dict[str, str]:
        """读取最近 N 天抽猪记录；图鉴展示只读使用，不补写任何历史状态。"""
        today = rollpig_today()
        safe_days = max(1, min(60, int(days or 14)))
        start_date = today - datetime.timedelta(days=safe_days - 1)
        result: dict[str, str] = {}
        history = self.data.get("history", {})
        if not isinstance(history, dict):
            return result

        for date_str, rows in history.items():
            if not _is_valid_date(date_str) or not isinstance(rows, dict):
                continue
            date_obj = datetime.date.fromisoformat(date_str)
            if not (start_date <= date_obj <= today):
                continue
            pig_id = rows.get(str(user_id))
            if pig_id:
                result[date_str] = str(pig_id)
        return dict(sorted(result.items(), reverse=True))

    def count_success_roasted(self, user_id: str, days: int = 7) -> int:
        """统计近 N 天成功被烤次数；逃脱、反噬和自烤不计入图鉴状态。"""
        today = rollpig_today()
        safe_days = max(1, min(60, int(days or 7)))
        start_date = today - datetime.timedelta(days=safe_days - 1)
        events_by_date = self.data.get("daily_events", {})
        if not isinstance(events_by_date, dict):
            return 0

        total = 0
        for date_str, events in events_by_date.items():
            if not _is_valid_date(date_str) or not isinstance(events, list):
                continue
            date_obj = datetime.date.fromisoformat(date_str)
            if not (start_date <= date_obj <= today):
                continue
            total += sum(
                1
                for event in events
                if isinstance(event, dict)
                and event.get("type") == "success"
                and str(event.get("target") or "") == str(user_id)
            )
        return total

    def get_catalog_snapshot(self, user_id: str, days: int = 14) -> CatalogSnapshot:
        """聚合图片版图鉴只读快照，避免命令层多处重复扫描账册。"""
        return CatalogSnapshot(
            draw_state=self.get_draw_state(user_id),
            recent_rolls=self.get_recent_rolls(user_id, days=days),
            roasted_7d=self.count_success_roasted(user_id, days=7),
        )

    async def clean_old_history(self, days_to_keep: int = 14):
        """清理超过 days_to_keep 天的历史记录（不影响图鉴数据）。"""
        async with self._lock:
            today = rollpig_today()
            history_dates_to_del = [
                d for d in self.data["history"]
                if _is_valid_date(d)  # 必须先过滤非法日期键，再做计算（防止 ValueError）
                and (today - datetime.date.fromisoformat(d)).days > days_to_keep
            ]
            for d in history_dates_to_del:
                del self.data["history"][d]

            group_rolls = self.data.get("group_rolls", {})
            group_dates_to_del = [
                d for d in group_rolls
                if _is_valid_date(d)
                and (today - datetime.date.fromisoformat(d)).days > days_to_keep
            ]
            for d in group_dates_to_del:
                del group_rolls[d]

            if history_dates_to_del or group_dates_to_del:
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

    async def consume_roast_usage(
        self,
        user_id: str,
        now_ts: Optional[float] = None,
        cooldown_seconds: Optional[int] = None,
    ) -> tuple[bool, int]:
        now = float(now_ts or time.time())
        cooldown = cooldown_seconds or ROAST_COOLDOWN_SECONDS
        async with self._lock:
            usage = self.data.setdefault("usage", {})
            if usage and isinstance(list(usage.values())[0], dict):
                self.data["usage"] = {}
                usage = self.data["usage"]

            last_use = float(usage.get(user_id, 0) or 0)
            remaining = max(0, int(cooldown - (now - last_use)))
            if remaining > 0:
                return False, remaining

            usage[user_id] = now
            await self._atomic_save()
            return True, 0

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
        today = rollpig_date_str()
        if "force_usage" not in self.data or not isinstance(self.data["force_usage"], dict):
            self.data["force_usage"] = {}
        return self.data["force_usage"].get(user_id) != today

    async def consume_force_roast_usage(self, user_id: str, date_str: Optional[str] = None) -> bool:
        target_date = date_str or rollpig_date_str()
        async with self._lock:
            usage = self.data.setdefault("force_usage", {})
            if usage.get(user_id) == target_date:
                return False
            usage[user_id] = target_date
            await self._atomic_save()
            return True

    async def update_force_roast_usage(self, user_id: str):
        async with self._lock:
            today = rollpig_date_str()
            self.data.setdefault("force_usage", {})[user_id] = today
            await self._atomic_save()

    # ---- 烤群友事件记录（用于每日总结） ----

    async def log_roast_event(self, event_type: str, attacker_id: str, target_id: str,
                               attacker_name: str = "", target_name: str = "",
                               food: str = "", group_id: str = ""):
        """
        记录一次烤群友事件。
        event_type: "success" / "escape" / "backfire" / "bot_backfire" / "self_roast"
        """
        async with self._lock:
            today = rollpig_date_str()
            events = self.data.setdefault("daily_events", {})
            day_events = events.setdefault(today, [])
            day_events.append({
                "type": event_type,
                "attacker": attacker_id,
                "target": target_id,
                "attacker_name": attacker_name,
                "target_name": target_name,
                "food": food,
                "group_id": group_id,
            })
            await self._atomic_save()

    def get_daily_events(self, date_str: Optional[str] = None, group_id: Optional[str] = None) -> list:
        """获取指定日期（默认今天）的所有烤群友事件。"""
        if not date_str:
            date_str = rollpig_date_str()
        events = self.data.get("daily_events", {}).get(date_str, [])
        if not group_id:
            return events
        return [e for e in events if e.get("group_id") == group_id]

    def get_group_rolls(self, group_id: str, date_str: Optional[str] = None) -> dict:
        """获取指定群在某天登记过的今日形态。"""
        if not date_str:
            date_str = rollpig_date_str()
        return self.data.get("group_rolls", {}).get(date_str, {}).get(group_id, {})

    def get_active_group_ids(self, date_str: Optional[str] = None) -> set[str]:
        """获取指定日期内有抽猪或烧烤活动的群号集合。"""
        if not date_str:
            date_str = rollpig_date_str()

        event_groups = {
            str(e.get("group_id"))
            for e in self.get_daily_events(date_str)
            if e.get("group_id")
        }
        roll_groups = {
            str(group_id)
            for group_id in self.data.get("group_rolls", {}).get(date_str, {}).keys()
            if group_id
        }
        return event_groups | roll_groups

    def get_daily_summary(self, date_str: Optional[str] = None, group_id: Optional[str] = None) -> dict:
        """
        汇总指定日期的烤群友数据，返回:
        {
            "total": int,
            "most_roasted_id": str | None,      # 被烤最多的 UID
            "most_roasted_name": str,
            "most_roasted_count": int,
            "most_active_id": str | None,        # 烤人最多的 UID
            "most_active_name": str,
            "most_active_count": int,
            "escape_king_id": str | None,        # 逃脱最多的 UID
            "escape_king_name": str,
            "escape_king_count": int,
            "backfire_king_id": str | None,      # 反噬最多的 UID
            "backfire_king_name": str,
            "backfire_king_count": int,
        }
        """
        roll_stats = self._get_roll_stats(date_str, group_id=group_id)
        events = self.get_daily_events(date_str, group_id=group_id)
        if not events and roll_stats.get("roll_count", 0) == 0:
            return {"total": 0, **roll_stats}

        from collections import Counter
        roasted_counter: Counter = Counter()       # 被烤次数
        attacker_counter: Counter = Counter()      # 发起烤次数
        escape_counter: Counter = Counter()        # 逃脱次数
        backfire_counter: Counter = Counter()      # 反噬次数
        name_map: dict = {}

        for e in events:
            a_id = e.get("attacker", "")
            t_id = e.get("target", "")
            if e.get("attacker_name"):
                name_map[a_id] = e["attacker_name"]
            if e.get("target_name"):
                name_map[t_id] = e["target_name"]

            etype = e.get("type", "")
            if etype == "success":
                attacker_counter[a_id] += 1
                if a_id and t_id and a_id != t_id:
                    roasted_counter[t_id] += 1
            elif etype == "self_roast":
                attacker_counter[a_id] += 1
            elif etype == "escape":
                escape_counter[t_id] += 1
                attacker_counter[a_id] += 1
            elif etype in ("backfire", "bot_backfire"):
                backfire_counter[a_id] += 1
                attacker_counter[a_id] += 1

        def _top(counter: Counter):
            if not counter:
                return None, "", 0
            uid, count = counter.most_common(1)[0]
            return uid, name_map.get(uid, uid), count

        mr_id, mr_name, mr_count = _top(roasted_counter)
        ma_id, ma_name, ma_count = _top(attacker_counter)
        ek_id, ek_name, ek_count = _top(escape_counter)
        bk_id, bk_name, bk_count = _top(backfire_counter)

        return {
            "total": len(events),
            "most_roasted_id": mr_id, "most_roasted_name": mr_name, "most_roasted_count": mr_count,
            "most_active_id": ma_id, "most_active_name": ma_name, "most_active_count": ma_count,
            "escape_king_id": ek_id, "escape_king_name": ek_name, "escape_king_count": ek_count,
            "backfire_king_id": bk_id, "backfire_king_name": bk_name, "backfire_king_count": bk_count,
            **roll_stats,
        }

    def _get_roll_stats(self, date_str: Optional[str] = None, group_id: Optional[str] = None) -> dict:
        """从 history 中统计今日抽猪信息。"""
        from collections import Counter
        if not date_str:
            date_str = rollpig_date_str()
        if group_id:
            today_rolls = self.get_group_rolls(group_id, date_str)
        else:
            today_rolls = self.data.get("history", {}).get(date_str, {})
        if not today_rolls:
            return {"roll_count": 0}

        pig_counter: Counter = Counter(today_rolls.values())
        top_pig_id, top_pig_count = pig_counter.most_common(1)[0]

        # 统计人类形态的用户
        human_ids = [uid for uid, pid in today_rolls.items() if pid == "human"]

        return {
            "roll_count": len(today_rolls),
            "top_pig_id": top_pig_id,
            "top_pig_count": top_pig_count,
            "human_count": len(human_ids),
        }

    # ---- 被烤最多 → 次日保护 ----

    def is_protected(self, group_id: str, user_id: str, date_str: Optional[str] = None) -> bool:
        """检查用户在当前群今日是否受保护。"""
        target_date = date_str or rollpig_date_str()
        protected_map = self.data.get("protected", {}).get(target_date, {})
        if not isinstance(protected_map, dict):
            return False
        group_users = protected_map.get(group_id, [])
        legacy_users = protected_map.get("__all__", [])
        return user_id in group_users or user_id in legacy_users

    async def replace_group_protected_users(
        self,
        group_id: str,
        user_ids: list[str],
        protect_date: Optional[str] = None,
    ):
        """按群设置某日受保护的用户列表。"""
        target_date = protect_date or rollpig_date_str(1)
        async with self._lock:
            protected = self.data.setdefault("protected", {})
            day_map = protected.setdefault(target_date, {})
            day_map[group_id] = sorted({str(user_id) for user_id in user_ids if user_id})
            await self._atomic_save()

    async def set_protected_users(self, user_ids: list):
        """兼容旧接口：写入 legacy 全局保护名单。"""
        target_date = rollpig_date_str(1)
        async with self._lock:
            protected = self.data.setdefault("protected", {})
            day_map = protected.setdefault(target_date, {})
            day_map["__all__"] = sorted({str(user_id) for user_id in user_ids if user_id})
            await self._atomic_save()

    async def clean_old_events(self, days_to_keep: int = 7):
        """清理超过 days_to_keep 天的事件记录。"""
        async with self._lock:
            today = rollpig_today()
            events = self.data.get("daily_events", {})
            dates_to_del = [
                d for d in events
                if _is_valid_date(d)
                and (today - datetime.date.fromisoformat(d)).days > days_to_keep
            ]
            for d in dates_to_del:
                del events[d]

            protected = self.data.get("protected", {})
            protection_dates_to_del = [
                d for d in list(protected.keys())
                if _is_valid_date(d)
                and (today - datetime.date.fromisoformat(d)).days > 1
            ]
            for d in protection_dates_to_del:
                del protected[d]

            if dates_to_del or protection_dates_to_del:
                await self._atomic_save()


def _safe_int(value, default: int = 0) -> int:
    """将历史 JSON 中可能出现的字符串/空值转成整数，失败时使用默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_valid_date(date_str: str) -> bool:
    try:
        datetime.date.fromisoformat(date_str)
        return True
    except ValueError:
        return False


_data_manager: PigDataManager | None = None


def get_data_manager() -> PigDataManager:
    global _data_manager
    if _data_manager is None:
        _data_manager = PigDataManager()
    return _data_manager
