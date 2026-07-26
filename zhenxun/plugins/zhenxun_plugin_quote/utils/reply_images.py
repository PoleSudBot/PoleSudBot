import json
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot_plugin_alconna.uniseg import Image, UniMessage

from .exceptions import ImageProcessError


def iter_raw_segments(message: Any) -> list[Any]:
    if not message or isinstance(message, str):
        return []
    if isinstance(message, dict):
        return [message]
    try:
        return list(message)
    except TypeError:
        return []


def segment_type(segment: Any) -> str:
    if isinstance(segment, dict):
        return str(segment.get("type") or "")
    return str(getattr(segment, "type", "") or "")


def segment_data(segment: Any) -> dict[str, Any]:
    if isinstance(segment, dict):
        data = segment.get("data", {})
    else:
        data = getattr(segment, "data", {})
    return dict(data) if isinstance(data, dict) else {}


def to_v11_message(message: Any) -> Message:
    if isinstance(message, Message):
        return message

    segments = []
    for segment in iter_raw_segments(message):
        if isinstance(segment, MessageSegment):
            segments.append(segment)
        elif isinstance(segment, dict) and segment.get("type"):
            segments.append(
                MessageSegment(str(segment["type"]), dict(segment.get("data") or {}))
            )
    return Message(segments)


async def extract_direct_images(bot: Bot, message: Any) -> list[Image]:
    v11_message = to_v11_message(message)
    if not v11_message:
        return []
    uni_message = UniMessage.of(v11_message, bot=bot)
    return [segment for segment in uni_message if isinstance(segment, Image)]


def forward_nodes_from_response(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return []
    for key in ("messages", "message"):
        if isinstance(response.get(key), list):
            return response[key]
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("messages", "message"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def inline_forward_nodes(data: dict[str, Any]) -> list[Any]:
    nodes = data.get("nodes") or data.get("content")
    if isinstance(nodes, list):
        return nodes
    if isinstance(nodes, str):
        try:
            parsed = json.loads(nodes)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def json_forward_id(data: dict[str, Any]) -> str | None:
    raw_payload = data.get("data")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw_payload, dict):
        payload = raw_payload
    else:
        return None

    if not isinstance(payload, dict) or not (
        payload.get("app") == "com.tencent.multimsg"
        or payload.get("view") == "Forward"
    ):
        return None

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    detail = meta.get("detail")
    if not isinstance(detail, dict) or not (forward_id := detail.get("resid")):
        return None
    return str(forward_id)


def forward_node_message(node: Any) -> Any:
    if not isinstance(node, dict):
        return None
    payload = node.get("data") if isinstance(node.get("data"), dict) else node
    return payload.get("message") or payload.get("content")


async def extract_forward_images(
    bot: Bot,
    message: Any,
    direct_image_extractor=extract_direct_images,
) -> tuple[list[Image], bool]:
    for segment in iter_raw_segments(message):
        current_segment_type = segment_type(segment)
        data = segment_data(segment)
        nodes: list[Any] = []
        forward_id: Any = None

        if current_segment_type == "forward":
            nodes = inline_forward_nodes(data)
            forward_id = data.get("id") or data.get("resid")
            if not nodes and not forward_id:
                return [], True
        elif current_segment_type == "json":
            forward_id = json_forward_id(data)
            if not forward_id:
                continue
        else:
            continue

        if not nodes:
            try:
                response = await bot.call_api("get_forward_msg", id=str(forward_id))
            except Exception as e:
                raise ImageProcessError(f"获取合并转发内容失败: {e}") from e
            nodes = forward_nodes_from_response(response)

        images: list[Image] = []
        for node in nodes:
            node_message = forward_node_message(node)
            if node_message:
                images.extend(await direct_image_extractor(bot, node_message))
        return images, True
    return [], False


async def extract_images_from_source(
    bot: Bot, message: Any
) -> tuple[list[Image], bool]:
    direct_images = await extract_direct_images(bot, message)
    forward_images, has_forward = await extract_forward_images(bot, message)
    return direct_images + forward_images, has_forward
