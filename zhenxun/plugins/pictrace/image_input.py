from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import UniMessage, UniMsg
from nonebot_plugin_alconna.uniseg import Image as UniImage
from nonebot_plugin_alconna.uniseg import image_fetch
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_waiter import waiter

from zhenxun.utils.http_utils import AsyncHttpx

from .models import ImageInput


def _max_image_bytes(max_image_size_mb: int) -> int:
    """把 MB 配置转换成字节上限，并避免配置小于 1 时变成无效限制。"""

    return max(max_image_size_mb, 1) * 1024 * 1024


def _raise_image_too_large(max_image_size_mb: int) -> None:
    """抛出统一的图片过大提示。"""

    raise ValueError(f"图片大小超过 {max_image_size_mb}MB，请压缩后再试。")


def _ensure_content_size(content: bytes, max_image_size_mb: int) -> None:
    """检查已取得的图片 bytes 是否超过配置限制。"""

    if len(content) > _max_image_bytes(max_image_size_mb):
        _raise_image_too_large(max_image_size_mb)


def _read_limited_file(path: Path, max_image_size_mb: int) -> bytes:
    """读取本地图片前先检查文件大小，避免把超大文件一次性读入内存。"""

    if path.stat().st_size > _max_image_bytes(max_image_size_mb):
        _raise_image_too_large(max_image_size_mb)
    return path.read_bytes()


async def _download_limited_image(url: str, max_image_size_mb: int) -> bytes:
    """按大小上限下载远端图片，避免 URL 图片在检查前耗尽内存或带宽。"""

    max_bytes = _max_image_bytes(max_image_size_mb)
    chunks: list[bytes] = []
    total = 0
    async with AsyncHttpx.temporary_client(follow_redirects=True, timeout=30) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = 0
                if declared_length > max_bytes:
                    _raise_image_too_large(max_image_size_mb)
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    _raise_image_too_large(max_image_size_mb)
                chunks.append(chunk)
    return b"".join(chunks)


def _get_images(message: UniMessage) -> list[UniImage]:
    """从 UniMessage 中提取所有图片段。"""

    return [segment for segment in message if isinstance(segment, UniImage)]


def _extract_onebot_image_info(resp: Any) -> tuple[str | None, str | None]:
    """兼容不同 OneBot 实现的 get_image 返回结构。"""

    data = resp.get("data", resp) if isinstance(resp, dict) else {}
    if not isinstance(data, dict):
        return None, None
    return data.get("url") or resp.get("url"), data.get("file") or resp.get("file")


async def _load_image_bytes(
    bot: Bot,
    event: Event,
    state: dict,
    image: UniImage,
    max_image_size_mb: int,
) -> ImageInput:
    """把适配器图片段解析为 bytes，同时尽量保留腾讯图片 URL。"""

    image_url = image.url
    if image.raw:
        _ensure_content_size(image.raw_bytes, max_image_size_mb)
        return ImageInput(
            content=image.raw_bytes,
            url=image_url,
            filename=image.name or "image.jpg",
            mimetype=image.mimetype or "image/jpeg",
        )

    if image.path:
        path = Path(image.path)
        content = _read_limited_file(path, max_image_size_mb)
        return ImageInput(
            content=content,
            url=image_url,
            filename=path.name,
            mimetype=image.mimetype or "image/jpeg",
        )

    if image.url:
        content = await _download_limited_image(image.url, max_image_size_mb)
        return ImageInput(
            content=content,
            url=image.url,
            filename=image.name or "image.jpg",
            mimetype=image.mimetype or "image/jpeg",
        )

    # OneBot V11 的图片 id 可换取腾讯 URL；这里优先保留 URL 给 SerpAPI Lens 使用。
    if (
        image.id
        and bot.adapter.get_name() == "OneBot V11"
        and hasattr(bot, "get_image")
    ):
        resp = await bot.get_image(file=image.id)
        image_url, file_path = _extract_onebot_image_info(resp)
        if file_path and Path(file_path).exists():
            path = Path(file_path)
            content = _read_limited_file(path, max_image_size_mb)
            return ImageInput(
                content=content,
                url=image_url,
                filename=path.name,
                mimetype=image.mimetype or "image/jpeg",
            )
        if image_url:
            content = await _download_limited_image(image_url, max_image_size_mb)
            return ImageInput(
                content=content,
                url=image_url,
                filename=image.name or "image.jpg",
                mimetype=image.mimetype or "image/jpeg",
            )

    # 其他适配器交给 alconna 的通用获取逻辑处理，失败时由上层给出用户提示。
    if content := await image_fetch(event, bot, state, image, timeout=30):
        _ensure_content_size(content, max_image_size_mb)
        return ImageInput(
            content=content,
            url=image_url,
            filename=image.name or "image.jpg",
            mimetype=image.mimetype or "image/jpeg",
        )

    raise ValueError("未能获取图片数据，请检查图片是否有效。")


async def _extract_reply_images(bot: Bot, event: Event) -> tuple[Event, list[UniImage]]:
    """从引用消息里提取图片。"""

    reply = await reply_fetch(event, bot)
    if not reply or not reply.msg:
        return event, []
    reply_message = await UniMessage.generate(message=reply.msg, bot=bot)
    return event, _get_images(reply_message)


async def _wait_for_image(timeout: int) -> tuple[Event, UniMessage] | None:
    """等待用户补发一张图片。"""

    @waiter(waits=["message"], keep_session=True)
    async def wait_msg(event: Event, msg: UniMsg) -> tuple[Event, UniMessage]:
        return event, msg

    return await wait_msg.wait(
        f"请在 {timeout} 秒内发送要搜索的图片，发送其他内容取消搜索。",
        timeout=timeout,
    )


async def resolve_image_input(
    bot: Bot,
    event: Event,
    message: UniMsg,
    state: dict,
    timeout: int,
    max_image_size_mb: int,
) -> ImageInput:
    """按当前消息、引用消息、等待补图的顺序获取搜索图片。"""

    current_images = _get_images(message)
    if current_images:
        return await _load_image_bytes(
            bot, event, state, current_images[0], max_image_size_mb
        )

    reply_event, reply_images = await _extract_reply_images(bot, event)
    if reply_images:
        return await _load_image_bytes(
            bot, reply_event, state, reply_images[0], max_image_size_mb
        )

    waited = await _wait_for_image(timeout)
    if not waited:
        raise ValueError("操作超时，已退出搜图。")
    waited_event, waited_message = waited
    waited_images = _get_images(waited_message)
    if not waited_images:
        raise ValueError("没有收到图片，已退出搜图。")
    return await _load_image_bytes(
        bot, waited_event, state, waited_images[0], max_image_size_mb
    )
