from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time
from typing import Any

from nonebot.log import logger
from nonebot_plugin_htmlrender import template_to_pic
import nonebot_plugin_localstore as localstore
from PIL import Image

from .config import (
    get_catalog_cache_seconds,
    get_catalog_render_timeout,
    get_growth_max_expert_level,
)
from .render_budget import html_render_budget
from .resource_manager import pig_resource_manager
from .runtime import ROLLPIG_TIMEZONE, rollpig_today
from .store.models import CatalogSnapshot, DrawState, PigProgress

RES_DIR = Path(__file__).parent / "resource"
CATALOG_TEMPLATE = "catalog.html"
THUMB_CACHE_DIR = localstore.get_plugin_cache_dir() / "catalog_thumbs"
CATALOG_PAGE_SIZE = 30
CATALOG_CACHE_MAX_ENTRIES = 64
CATALOG_CACHE_MAX_BYTES = 64 * 1024 * 1024
NEW_BADGE_DAYS = 7


@dataclass
class _CachedCatalogImage:
    created_at: float
    payload: bytes


_catalog_cache: dict[str, _CachedCatalogImage] = {}
_catalog_cache_lock = asyncio.Lock()
_catalog_render_tasks: dict[str, asyncio.Task[bytes]] = {}


def clear_catalog_runtime_cache() -> None:
    """清空进程内图鉴缓存，供测试或资源热更新后的保守刷新使用。"""
    _catalog_cache.clear()
    for task in _catalog_render_tasks.values():
        if not task.done():
            task.cancel()
    _catalog_render_tasks.clear()


def get_expert_level(copies: int) -> int:
    """图鉴渲染等级必须与抽猪成长文案保持同一套 copies -> EX Lv. 规则。"""
    return min(max(int(copies or 0) - 1, 0), get_growth_max_expert_level())


def _catalog_cache_bytes() -> int:
    return sum(len(cached.payload) for cached in _catalog_cache.values())


def _prune_catalog_cache(*, ttl: int, now: float) -> None:
    """按 TTL、条数和总字节三层上限清理图鉴缓存，防止大图长期占内存。"""
    if not _catalog_cache:
        return
    if ttl <= 0:
        _catalog_cache.clear()
        return

    expired_keys = [
        key for key, cached in _catalog_cache.items() if now - cached.created_at > ttl
    ]
    for key in expired_keys:
        _catalog_cache.pop(key, None)

    overflow = len(_catalog_cache) - CATALOG_CACHE_MAX_ENTRIES
    if overflow > 0:
        oldest_keys = sorted(
            _catalog_cache,
            key=lambda key: _catalog_cache[key].created_at,
        )[:overflow]
        for key in oldest_keys:
            _catalog_cache.pop(key, None)

    for key in sorted(_catalog_cache, key=lambda key: _catalog_cache[key].created_at):
        if _catalog_cache_bytes() <= CATALOG_CACHE_MAX_BYTES:
            break
        _catalog_cache.pop(key, None)


def _get_catalog_cache_locked(cache_key: str, *, ttl: int, now: float) -> bytes | None:
    """读取缓存时顺手执行淘汰；调用方需持有 `_catalog_cache_lock`。"""
    _prune_catalog_cache(ttl=ttl, now=now)
    cached = _catalog_cache.get(cache_key)
    if cached and ttl > 0 and now - cached.created_at <= ttl:
        return cached.payload
    return None


def _store_catalog_cache_locked(
    cache_key: str,
    payload: bytes,
    *,
    ttl: int,
    now: float,
) -> None:
    """写缓存后立即按上限淘汰，避免并发请求短时堆积大量 PNG。"""
    if ttl <= 0:
        return
    _catalog_cache[cache_key] = _CachedCatalogImage(created_at=now, payload=payload)
    _prune_catalog_cache(ttl=ttl, now=now)


def _parse_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    normalized = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ROLLPIG_TIMEZONE).replace(tzinfo=None)
    return parsed


def _is_recent_new(first_obtained_at: str | None, *, today: datetime.date) -> bool:
    obtained_at = _parse_datetime(first_obtained_at)
    if obtained_at is None:
        return False
    return 0 <= (today - obtained_at.date()).days < NEW_BADGE_DAYS


def _thumbnail_uri(pig_id: str, image_file: Path) -> str:
    """生成小图缓存；源文件状态进入指纹，资源包更新后旧缩略图自然失效。"""
    try:
        stat = image_file.stat()
    except OSError:
        return image_file.as_uri()

    cache_key = hashlib.sha256(
        "|".join(
            [
                pig_resource_manager.resource_version,
                pig_id,
                str(image_file.resolve()),
                str(stat.st_size),
                str(stat.st_mtime_ns),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    target = THUMB_CACHE_DIR / f"{pig_id}_{cache_key}.png"
    if target.exists():
        return target.as_uri()

    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_file) as image:
            image = image.convert("RGBA")
            image.thumbnail((120, 120), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
            # 缩略图只是性能缓存，写入失败时继续使用原图，不能阻断图鉴生成。
            target.write_bytes(output.getvalue())
    except Exception as error:
        logger.warning(f"rollpig 图鉴缩略图生成失败: pig_id={pig_id} error={error}")
        return image_file.as_uri()
    return target.as_uri()


def _image_uri(pig_id: str) -> str:
    image_file = pig_resource_manager.find_image_file(pig_id)
    return _thumbnail_uri(pig_id, image_file) if image_file else ""


def _calculate_checkin_streak(
    recent_rolls: dict[str, str],
    *,
    today: datetime.date,
) -> int:
    """从今天往前数连续抽猪天数；只看已有历史，不补写漏掉的日期。"""
    streak = 0
    for offset in range(60):
        date_str = (today - datetime.timedelta(days=offset)).isoformat()
        if date_str not in recent_rolls:
            break
        streak += 1
    return streak


def _next_milestone(unlocked: int, total: int) -> int:
    if total <= 0:
        return 0
    if unlocked >= total:
        return total
    return min(total, max(10, ((unlocked // 10) + 1) * 10))


def _sort_progress_items(draw_state: DrawState) -> list[tuple[str, PigProgress]]:
    """按成长强度排序图鉴卡；资源顺序只作为稳定兜底，不覆盖用户成长优先级。"""
    resource_order = {
        str(pig.get("id")): index
        for index, pig in enumerate(pig_resource_manager.pig_list)
    }
    return sorted(
        draw_state.progress.items(),
        key=lambda item: (
            -get_expert_level(item[1].copies),
            -int(item[1].copies or 0),
            item[1].first_obtained_at or "",
            resource_order.get(item[0], 10**9),
            item[0],
        ),
    )


def _build_template_payload(
    *,
    user_name: str,
    snapshot: CatalogSnapshot,
    page: int,
) -> dict[str, Any]:
    today = rollpig_today()
    progress_items = _sort_progress_items(snapshot.draw_state)
    total_pigs = len(pig_resource_manager.pig_list)
    unlocked = len(snapshot.draw_state.pig_ids)
    pages = max(1, math.ceil(max(1, unlocked) / CATALOG_PAGE_SIZE))
    safe_page = max(1, min(int(page or 1), pages))
    page_items = progress_items[
        (safe_page - 1) * CATALOG_PAGE_SIZE : safe_page * CATALOG_PAGE_SIZE
    ]
    max_level = get_growth_max_expert_level()

    cards: list[dict[str, Any]] = []
    for pig_id, progress in page_items:
        pig = pig_resource_manager.pig_map.get(pig_id, {})
        level = get_expert_level(progress.copies)
        is_max = level >= max_level
        is_new = (not is_max) and _is_recent_new(
            progress.first_obtained_at,
            today=today,
        )
        badge = "MAX" if is_max else ("NEW" if is_new else "")
        cards.append(
            {
                "id": pig_id,
                "name": str(pig.get("name") or pig_id),
                "image": _image_uri(pig_id),
                "level": level,
                "copies": int(progress.copies or 0),
                "badge": badge,
                "badge_class": badge.lower(),
            }
        )

    if progress_items:
        favorite_id, favorite_progress = progress_items[0]
        favorite_pig = pig_resource_manager.pig_map.get(favorite_id, {})
        favorite = {
            "name": str(favorite_pig.get("name") or favorite_id),
            "image": _image_uri(favorite_id),
            "level": get_expert_level(favorite_progress.copies),
            "copies": int(favorite_progress.copies or 0),
        }
    else:
        favorite = {"name": "暂无", "image": "", "level": 0, "copies": 0}

    levels = [get_expert_level(progress.copies) for _, progress in progress_items]
    progress_percent = (
        round((unlocked / total_pigs) * 100, 1) if total_pigs > 0 else 0.0
    )
    stats = {
        "unlocked": unlocked,
        "total": total_pigs,
        "progress_percent": progress_percent,
        "max_level": max(levels, default=0),
        "maxed_count": sum(1 for level in levels if level >= max_level),
        "recent_new_count": sum(
            1
            for _, progress in progress_items
            if _is_recent_new(progress.first_obtained_at, today=today)
        ),
        "checkin_streak": _calculate_checkin_streak(
            snapshot.recent_rolls,
            today=today,
        ),
        "roasted_7d": int(snapshot.roasted_7d or 0),
        "next_milestone": _next_milestone(unlocked, total_pigs),
        "page": safe_page,
        "pages": pages,
    }
    return {
        "user_name": user_name,
        "stats": stats,
        "favorite": favorite,
        "cards": cards,
    }


def _build_cache_key(payload: dict[str, Any], snapshot: CatalogSnapshot) -> str:
    """缓存指纹只放稳定摘要，避免把 HTML 或图片二进制塞进 key。"""
    key_payload = {
        "resource_version": pig_resource_manager.resource_version,
        "user_name": payload["user_name"],
        "stats": payload["stats"],
        "favorite": payload["favorite"],
        "cards": [
            (card["id"], card["level"], card["copies"], card["badge"])
            for card in payload["cards"]
        ],
        "recent_rolls": snapshot.recent_rolls,
        "roasted_7d": snapshot.roasted_7d,
    }
    raw = json.dumps(
        key_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _render_catalog_image_uncached(
    *,
    payload: dict[str, Any],
    cache_key: str,
    ttl: int,
    started_at: float,
) -> bytes:
    """执行真实 HTML 截图；外层负责缓存命中和同 key 合流。"""
    render_started_at = time.perf_counter()
    timeout = get_catalog_render_timeout()
    async with html_render_budget("catalog"):
        result = await asyncio.wait_for(
            template_to_pic(
                template_path=RES_DIR,
                template_name=CATALOG_TEMPLATE,
                templates=payload,
                pages={"viewport": {"width": 980, "height": 10}},
                wait=100,
            ),
            timeout=timeout,
        )
    finished_at = time.perf_counter()
    logger.info(
        "rollpig catalog rendered: "
        f"user={payload['user_name']} page={payload['stats']['page']} "
        f"render={finished_at - render_started_at:.2f}s "
        f"total={finished_at - started_at:.2f}s bytes={len(result)}"
    )
    async with _catalog_cache_lock:
        _store_catalog_cache_locked(cache_key, result, ttl=ttl, now=time.time())
    return result


async def render_catalog_image(
    *,
    user_name: str,
    snapshot: CatalogSnapshot,
    page: int = 1,
) -> bytes:
    """渲染图片版小猪图鉴；只消费快照，不修改抽猪状态或 copies。"""
    started_at = time.perf_counter()
    payload = _build_template_payload(
        user_name=user_name,
        snapshot=snapshot,
        page=page,
    )
    cache_key = _build_cache_key(payload, snapshot)
    ttl = get_catalog_cache_seconds()

    render_owner = False
    async with _catalog_cache_lock:
        cached_payload = _get_catalog_cache_locked(cache_key, ttl=ttl, now=time.time())
        if cached_payload is not None:
            logger.debug(
                "rollpig catalog cache hit: "
                f"user={user_name} page={payload['stats']['page']} "
                f"bytes={len(cached_payload)}"
            )
            return cached_payload

        render_task = _catalog_render_tasks.get(cache_key)
        if render_task is None or render_task.done():
            render_task = asyncio.create_task(
                _render_catalog_image_uncached(
                    payload=payload,
                    cache_key=cache_key,
                    ttl=ttl,
                    started_at=started_at,
                )
            )
            _catalog_render_tasks[cache_key] = render_task
            render_owner = True

    if not render_owner:
        logger.debug(
            "rollpig catalog render coalesced: "
            f"user={user_name} page={payload['stats']['page']}"
        )

    try:
        return await render_task
    finally:
        if render_owner:
            async with _catalog_cache_lock:
                if _catalog_render_tasks.get(cache_key) is render_task:
                    _catalog_render_tasks.pop(cache_key, None)
