from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.models.group_name_avatar_history import GroupNameAvatarHistory
from zhenxun.models.group_name_history import GroupNameHistory
from zhenxun.services.avatar_service import avatar_service
from zhenxun.services.log import logger

MODULE = "name_history"
AVATAR_SIZE = 128
AVATAR_HISTORY_LIMIT = 50
AVATAR_QUALITY = 82
AVATAR_HISTORY_ROOT = (DATA_PATH / "name_history" / "avatars").resolve()
IMPORT_CONCURRENCY = 2
IMPORT_SLEEP_SECONDS = 0.05
_persist_locks: dict[tuple[str, str], asyncio.Lock] = {}


@dataclass(slots=True)
class AvatarHistoryItem:
    avatar_hash: str
    avatar_uri: str


@dataclass(slots=True)
class AvatarPersistResult:
    avatar_hash: str
    avatar_uri: str
    created_history: bool


def build_avatar_path(platform: str, avatar_hash: str) -> Path:
    """根据hash生成两级目录路径，避免大量文件堆在单个目录。"""
    return (
        AVATAR_HISTORY_ROOT
        / platform
        / avatar_hash[:2]
        / avatar_hash[2:4]
        / f"{avatar_hash}.webp"
    )


def build_avatar_uri(platform: str, avatar_hash: str) -> str:
    """把历史头像hash转换为 htmlrender 可直接读取的 file URI。"""
    path = build_avatar_path(platform, avatar_hash)
    return path.as_uri() if path.exists() else ""


def _compress_avatar_file(path: Path) -> tuple[str, bytes]:
    """在同步线程里解码、裁剪、压缩头像并计算压缩后内容hash。"""
    with Image.open(path) as image:
        image = image.convert("RGBA")
        image.thumbnail((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (255, 255, 255, 0))
        left = (AVATAR_SIZE - image.width) // 2
        top = (AVATAR_SIZE - image.height) // 2
        canvas.alpha_composite(image, (left, top))
        output = BytesIO()
        canvas.save(output, format="WEBP", quality=AVATAR_QUALITY, method=6)
    data = output.getvalue()
    return sha256(data).hexdigest(), data


def _write_avatar_file(path: Path, image_bytes: bytes) -> None:
    """把压缩后的头像写入分片目录，避免在事件循环里做文件写入。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)


async def persist_avatar_snapshot(
    *,
    platform: str,
    user_id: str,
    avatar_path: Path,
    source: str,
) -> AvatarPersistResult | None:
    """压缩并持久化一个头像快照，连续相同头像只复用现有记录。"""
    if platform != "qq" or not str(user_id).isdigit() or not avatar_path.exists():
        return None

    try:
        avatar_hash, image_bytes = await asyncio.to_thread(
            _compress_avatar_file, avatar_path
        )
    except Exception as e:
        logger.warning("历史头像压缩失败", MODULE, target=user_id, e=e)
        return None

    target_path = build_avatar_path(platform, avatar_hash)
    if not target_path.exists():
        await asyncio.to_thread(_write_avatar_file, target_path, image_bytes)

    created_history = False
    lock = _persist_locks.setdefault((platform, user_id), asyncio.Lock())
    async with lock:
        latest = (
            await GroupNameAvatarHistory.filter(platform=platform, user_id=user_id)
            .order_by("-record_time", "-id")
            .first()
        )
        if not latest or latest.avatar_hash != avatar_hash:
            await GroupNameAvatarHistory.create(
                platform=platform,
                user_id=user_id,
                avatar_hash=avatar_hash,
                source=source,
            )
            created_history = True

    return AvatarPersistResult(
        avatar_hash=avatar_hash,
        avatar_uri=target_path.as_uri(),
        created_history=created_history,
    )


async def persist_refreshed_avatar(
    platform: str,
    user_id: str,
    avatar_path: Path,
) -> None:
    """跟随 zhenxun 头像缓存刷新，顺手写入历史头像持久层。"""
    await persist_avatar_snapshot(
        platform=platform,
        user_id=str(user_id),
        avatar_path=avatar_path,
        source="cache_refresh",
    )


async def force_refresh_and_bind_avatar(
    *,
    platform: str,
    user_id: str,
    record_ids: Iterable[int],
) -> None:
    """名称变化后强制刷新头像，并用主键ID精准回写新建的名称记录。"""
    try:
        ids = [record_id for record_id in record_ids if record_id]
        if not ids:
            return
        avatar_path = await avatar_service.get_avatar_path(
            platform, user_id, force_refresh=True
        )
        if not avatar_path:
            return

        result = await persist_avatar_snapshot(
            platform=platform,
            user_id=user_id,
            avatar_path=avatar_path,
            source="name_change",
        )
        if not result:
            return
        await GroupNameHistory.filter(id__in=ids).update(avatar_hash=result.avatar_hash)
    except Exception as e:
        logger.warning("历史昵称绑定头像失败", MODULE, target=user_id, e=e)


async def import_existing_avatar_cache() -> None:
    """首次启用时缓慢导入现有 zhenxun 头像缓存，不额外发起网络请求。"""
    cache_root = avatar_service.cache_path
    if not cache_root.exists():
        return
    try:
        avatar_files = [
            path
            for path in cache_root.glob("*/*")
            if path.is_file()
            and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
    except OSError as e:
        logger.warning("历史头像缓存目录扫描失败", MODULE, target=str(cache_root), e=e)
        return
    if not avatar_files:
        return

    imported = 0

    async def _import_one(path: Path) -> None:
        nonlocal imported
        try:
            platform = path.parent.name
            user_id = path.stem
            if await GroupNameAvatarHistory.filter(
                platform=platform,
                user_id=user_id,
            ).exists():
                return
            result = await persist_avatar_snapshot(
                platform=platform,
                user_id=user_id,
                avatar_path=path,
                source="startup_cache",
            )
            if result:
                imported += 1
        except Exception as e:
            logger.warning("历史头像缓存导入失败", MODULE, target=path.name, e=e)
        finally:
            # 启动期导入只做补偿采样，主动限速避免老缓存目录造成 CPU 峰值。
            await asyncio.sleep(IMPORT_SLEEP_SECONDS)

    queue = asyncio.Queue()
    for path in avatar_files:
        queue.put_nowait(path)

    async def _worker() -> None:
        while True:
            path = await queue.get()
            try:
                await _import_one(path)
            finally:
                queue.task_done()

    workers = [
        asyncio.create_task(_worker())
        for _ in range(min(IMPORT_CONCURRENCY, len(avatar_files)))
    ]
    await queue.join()
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    logger.info(f"历史头像缓存导入完成，共导入 {imported} 个用户", MODULE)


async def get_avatar_history_items(
    *,
    platform: str,
    user_id: str,
    limit: int = AVATAR_HISTORY_LIMIT,
) -> list[AvatarHistoryItem]:
    """读取最近一段历史头像，并转换成模板可用的 URI。"""
    records = (
        await GroupNameAvatarHistory.filter(platform=platform, user_id=user_id)
        .order_by("-record_time", "-id")
        .limit(limit)
    )
    items: list[AvatarHistoryItem] = []
    for record in records:
        avatar_uri = build_avatar_uri(platform, record.avatar_hash)
        if avatar_uri:
            items.append(
                AvatarHistoryItem(
                    avatar_hash=record.avatar_hash,
                    avatar_uri=avatar_uri,
                )
            )
    return items
