from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import re
import time
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError

from zhenxun.services.log import logger

from .constants import MODULE_NAME

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_FALLBACK_HEAD_PATH = _ASSETS_DIR / "face.png"
_UUID_PATTERN = re.compile(r"[^0-9a-fA-F]")


@dataclass(frozen=True)
class PlayerHeadRequest:
    name: str
    uuid: str = ""


@dataclass
class _HeadCacheEntry:
    uri: str
    expire_at: float


_HEAD_CACHE: dict[str, _HeadCacheEntry] = {}
_HEAD_INFLIGHT: dict[str, asyncio.Task[str]] = {}
_HEAD_LOCK = asyncio.Lock()


def default_player_head_uri() -> str:
    return _FALLBACK_HEAD_PATH.as_uri() if _FALLBACK_HEAD_PATH.exists() else ""


async def resolve_player_head_uris(
    requests: Iterable[PlayerHeadRequest],
) -> dict[str, str]:
    """按玩家名批量解析头像，供图片卡片直接注入模板。"""
    settings = _get_settings()
    unique: dict[str, PlayerHeadRequest] = {}
    for request in requests:
        name = _normalize_name(request.name)
        if name:
            unique.setdefault(name.lower(), PlayerHeadRequest(name, request.uuid))
    if not unique or not getattr(settings, "player_heads_enabled", True):
        return {}
    pairs = await asyncio.gather(
        *[_resolve_named_head(key, request) for key, request in unique.items()]
    )
    return dict(pairs)


async def _resolve_named_head(key: str, request: PlayerHeadRequest) -> tuple[str, str]:
    return key, await get_player_head_uri(request.name, request.uuid)


async def get_player_head_uri(player_name: str, uuid: str = "") -> str:
    """解析单个玩家头像，任何失败都回退到本地占位头像。"""
    name = _normalize_name(player_name)
    if not name:
        return default_player_head_uri()
    cache_key = _cache_key(name, uuid)
    cached = await _get_cached(cache_key)
    if cached is not None:
        return cached
    async with _HEAD_LOCK:
        task = _HEAD_INFLIGHT.get(cache_key)
        if task is None:
            task = asyncio.create_task(_resolve_and_cache_head(name, uuid, cache_key))
            _HEAD_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        async with _HEAD_LOCK:
            _HEAD_INFLIGHT.pop(cache_key, None)


async def _resolve_and_cache_head(name: str, uuid: str, cache_key: str) -> str:
    settings = _get_settings()
    fallback = default_player_head_uri()
    try:
        uri = await _fetch_player_head_uri(name, uuid, settings)
        if uri:
            await _set_cached(cache_key, uri, settings.player_head_cache_seconds)
            await _set_cached(
                f"name:{name.lower()}",
                uri,
                settings.player_head_cache_seconds,
            )
            return uri
    except Exception as exc:
        # 头像是展示增强，失败时只记录上下文，不能影响状态卡主体渲染。
        logger.warning(
            "MC玩家头像解析失败，使用本地占位头像回退",
            MODULE_NAME,
            player=name,
            e=exc,
        )
    await _set_cached(cache_key, fallback, settings.player_head_failure_cache_seconds)
    return fallback


async def _fetch_player_head_uri(name: str, uuid: str, settings) -> str:
    """先试状态来源UUID，再按玩家名查正版UUID以兼容离线服。"""
    normalized_uuid = _normalize_uuid(uuid)
    if normalized_uuid:
        if skin_url := await _skin_url_from_uuid(normalized_uuid, settings):
            return _png_data_uri(await _build_head_png_from_url(skin_url, settings))
    if profile_uuid := await _uuid_from_name(name, settings):
        if skin_url := await _skin_url_from_uuid(profile_uuid, settings):
            uri = _png_data_uri(await _build_head_png_from_url(skin_url, settings))
            await _set_cached(
                f"uuid:{profile_uuid}",
                uri,
                settings.player_head_cache_seconds,
            )
            return uri
    return ""


async def _uuid_from_name(name: str, settings) -> str:
    url = settings.mojang_profile_url_template.format(name=quote(name, safe=""))
    payload = await _get_json(url, settings)
    if not isinstance(payload, dict):
        return ""
    return _normalize_uuid(str(payload.get("id") or ""))


async def _skin_url_from_uuid(uuid: str, settings) -> str:
    url = settings.mojang_session_url_template.format(uuid=uuid)
    payload = await _get_json(url, settings)
    if not isinstance(payload, dict):
        return ""
    properties = payload.get("properties")
    if not isinstance(properties, list):
        return ""
    for prop in properties:
        if not isinstance(prop, dict) or prop.get("name") != "textures":
            continue
        return _skin_url_from_texture_value(str(prop.get("value") or ""))
    return ""


def _skin_url_from_texture_value(value: str) -> str:
    if not value:
        return ""
    try:
        decoded = base64.b64decode(value)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    textures = payload.get("textures")
    if not isinstance(textures, dict):
        return ""
    skin = textures.get("SKIN")
    if not isinstance(skin, dict):
        return ""
    return str(skin.get("url") or "")


async def _build_head_png_from_url(url: str, settings) -> bytes:
    if not url:
        raise ValueError("skin url is empty")
    from zhenxun.utils.http_utils import AsyncHttpx

    content = await AsyncHttpx.get_content(
        url,
        timeout=settings.request_timeout_seconds,
    )
    return build_head_png(content)


def build_head_png(skin_bytes: bytes, *, size: int = 32) -> bytes:
    """裁剪Minecraft皮肤头部与帽子层，生成像素风小头像PNG。"""
    try:
        with Image.open(BytesIO(skin_bytes)) as raw:
            skin = raw.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("invalid skin image") from exc
    scale = max(skin.width // 64, 1)
    if skin.width < 64 * scale or skin.height < 16 * scale:
        raise ValueError("skin image is too small")
    base = skin.crop(_scaled_box((8, 8, 16, 16), scale))
    hat = skin.crop(_scaled_box((40, 8, 48, 16), scale))
    # 先合成帽子层再放大，避免半透明像素在缩放后产生模糊边。
    base.alpha_composite(hat)
    head = base.resize((size, size), Image.Resampling.NEAREST)
    output = BytesIO()
    head.save(output, format="PNG")
    return output.getvalue()


async def _get_json(url: str, settings) -> object:
    from zhenxun.utils.http_utils import AsyncHttpx

    return await AsyncHttpx.get_json(
        url,
        default={},
        timeout=settings.request_timeout_seconds,
    )


async def _get_cached(key: str) -> str | None:
    now = time.monotonic()
    async with _HEAD_LOCK:
        entry = _HEAD_CACHE.get(key)
        if not entry:
            return None
        if entry.expire_at <= now:
            _HEAD_CACHE.pop(key, None)
            return None
        return entry.uri


async def _set_cached(key: str, uri: str, ttl: int) -> None:
    async with _HEAD_LOCK:
        _HEAD_CACHE[key] = _HeadCacheEntry(uri=uri, expire_at=time.monotonic() + ttl)


def _cache_key(name: str, uuid: str) -> str:
    normalized_uuid = _normalize_uuid(uuid)
    return f"uuid:{normalized_uuid}" if normalized_uuid else f"name:{name.lower()}"


def _normalize_name(value: str) -> str:
    return str(value or "").strip()


def _normalize_uuid(value: str) -> str:
    normalized = _UUID_PATTERN.sub("", str(value or "")).lower()
    return normalized if len(normalized) == 32 else ""


def _scaled_box(
    box: tuple[int, int, int, int],
    scale: int,
) -> tuple[int, int, int, int]:
    return tuple(item * scale for item in box)


def _png_data_uri(payload: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(payload).decode('ascii')}"


def _get_settings():
    from .config import get_settings

    return get_settings()
