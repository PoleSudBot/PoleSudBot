from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TypeAlias

from nonebot_plugin_alconna import At, Image, Reference, Text, UniMessage
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from .runtime import MessageUtils

_FONT_PATH = Path("resources/font/msyh.ttf")


MoeSendable: TypeAlias = str | bytes | UniMessage | list[str | bytes | UniMessage]


@dataclass
class MoeForwardMessage:
    nodes: list[UniMessage]


@dataclass
class MoeImageTextMessage:
    image_bytes: bytes
    text: str | None = None


def to_unimessage(message: str | bytes | UniMessage) -> UniMessage:
    if isinstance(message, UniMessage):
        return message
    return MessageUtils.build_message(message)


def build_forward_message(
    nodes: list[str | bytes | UniMessage],
    *,
    sender_id: str = "10000",
    sender_name: str = "南极萝卜",
) -> MoeForwardMessage:
    unimessages = [to_unimessage(node) for node in nodes]
    reference = MessageUtils.alc_forward_msg(unimessages, sender_id, sender_name)
    if isinstance(reference, UniMessage):
        return MoeForwardMessage([reference])
    return MoeForwardMessage([UniMessage(Reference(nodes=[]))])


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if _FONT_PATH.exists():
        return ImageFont.truetype(str(_FONT_PATH), size=size)
    return ImageFont.load_default()


def build_card_image(
    *,
    title: str,
    lines: list[str],
    banner: bytes | None = None,
    width: int = 920,
    background: tuple[int, int, int] = (248, 250, 252),
) -> bytes:
    title_font = _load_font(36)
    text_font = _load_font(24)
    small_font = _load_font(20)

    text_padding = 28
    line_gap = 16
    banner_image: PILImage.Image | None = None
    banner_height = 0
    if banner:
        banner_image = PILImage.open(BytesIO(banner)).convert("RGB")
        ratio = width / banner_image.width
        banner_height = max(200, int(banner_image.height * ratio))
        banner_image = banner_image.resize((width, banner_height))

    canvas = PILImage.new(
        "RGB",
        (width, 600),
        color=background,
    )
    draw = ImageDraw.Draw(canvas)

    current_y = 0
    if banner_image:
        canvas.paste(banner_image, (0, 0))
        current_y += banner_height
    else:
        current_y += 32

    draw.rounded_rectangle(
        (18, current_y + 12, width - 18, 580),
        radius=26,
        fill=(255, 255, 255),
        outline=(228, 232, 240),
        width=2,
    )
    current_y += 36
    draw.text((text_padding + 18, current_y), title, fill=(22, 28, 36), font=title_font)
    title_box = draw.textbbox((text_padding + 18, current_y), title, font=title_font)
    current_y = title_box[3] + 22
    for index, line in enumerate(lines):
        font = small_font if len(line) > 32 else text_font
        draw.text((text_padding + 18, current_y), line, fill=(55, 65, 81), font=font)
        box = draw.textbbox((text_padding + 18, current_y), line, font=font)
        current_y = box[3] + line_gap
        if index > 16:
            break

    height = max(current_y + 28, banner_height + 80 if banner_image else current_y + 20)
    canvas = canvas.crop((0, 0, width, min(height, canvas.height)))
    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def build_at_message(user_ids: list[str], text: str) -> UniMessage:
    message = UniMessage()
    for user_id in user_ids:
        message += UniMessage([At(flag="user", target=user_id)])
    if user_ids:
        message += UniMessage([Text("\n")])
    message += UniMessage([Text(text)])
    return message


def build_image_message(image_bytes: bytes, text: str | None = None) -> UniMessage:
    segments = [Image(raw=image_bytes)]
    if text:
        segments.append(Text(f"\n{text}"))
    return UniMessage(segments)


def build_native_image_text_message(
    image_bytes: bytes,
    text: str | None = None,
) -> MoeImageTextMessage:
    return MoeImageTextMessage(image_bytes=image_bytes, text=text)
