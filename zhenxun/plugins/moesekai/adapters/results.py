from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TypeAlias

from nonebot_plugin_alconna import At, Image, Reference, Text, UniMessage
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from .runtime import MessageUtils

# 原先使用 resources/font/msyh.ttf。
_FONT_PATH = Path("resources/font/HarmonyOS_Sans_SC/HarmonyOS_SansSC_Regular.ttf")


MoeSendable: TypeAlias = str | bytes | UniMessage | list[str | bytes | UniMessage]


@dataclass
class MoeForwardMessage:
    nodes: list[UniMessage]


@dataclass
class MoeImageTextMessage:
    image_bytes: bytes
    text: str | None = None


@dataclass
class _AliasItemBlock:
    lines: list[str]
    line_height: int
    box_height: int
    column_span: int


@dataclass
class _AliasItemPlacement:
    block: _AliasItemBlock
    column_start: int
    box_width: int


@dataclass
class _AliasRowLayout:
    items: list[_AliasItemPlacement]
    row_height: int


@dataclass
class _AliasSectionLayout:
    base_column_count: int
    base_column_width: int
    rows: list[_AliasRowLayout]


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


def build_text_block_image(
    text: str,
    *,
    width: int = 920,
    padding: int = 28,
    title_background: tuple[int, int, int] = (248, 250, 252),
    card_background: tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    lines = [line.rstrip() for line in text.splitlines()] or [text]
    title_font = _load_font(34)
    text_font = _load_font(24)
    line_spacing = 14
    card_left = 18
    card_top = 18

    canvas = PILImage.new("RGB", (width, 1600), color=title_background)
    draw = ImageDraw.Draw(canvas)

    current_y = card_top
    draw.rounded_rectangle(
        (card_left, card_top, width - card_left, canvas.height - card_top),
        radius=26,
        fill=card_background,
        outline=(228, 232, 240),
        width=2,
    )
    for index, line in enumerate(lines):
        if not line:
            current_y += text_font.size + 8
            continue
        font = title_font if index == 0 else text_font
        draw.text(
            (card_left + padding, current_y),
            line,
            fill=(22, 28, 36),
            font=font,
        )
        box = draw.textbbox((card_left + padding, current_y), line, font=font)
        current_y = box[3] + line_spacing

    height = min(max(current_y + padding, 120), canvas.height - card_top)
    canvas = canvas.crop((0, 0, width, height))
    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _measure_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _wrap_text_by_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = f"{current}{char}"
        width, _ = _measure_text(draw, candidate, font)
        if current and width > max_width:
            lines.append(current)
            current = char
            continue
        current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _measure_alias_box_width(
    *,
    base_column_width: int,
    column_gap: int,
    column_span: int,
) -> int:
    return base_column_width * column_span + column_gap * max(0, column_span - 1)


def _build_alias_item_block(
    draw: ImageDraw.ImageDraw,
    item: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    box_width: int,
    item_padding_x: int,
    item_padding_y: int,
    bullet_size: int,
    bullet_gap: int,
    column_span: int,
    multiline: bool,
) -> _AliasItemBlock:
    text_width = max(
        1,
        box_width - item_padding_x * 2 - bullet_size - bullet_gap,
    )
    lines = (
        _wrap_text_by_width(
            draw,
            item,
            font=font,
            max_width=text_width,
        )
        if multiline
        else [item]
    )
    line_sizes = [_measure_text(draw, line, font) for line in lines]
    line_height = max(
        (height for _, height in line_sizes),
        default=font.size,
    )
    box_height = (
        item_padding_y * 2
        + line_height * len(lines)
        + max(0, len(lines) - 1) * 8
    )
    return _AliasItemBlock(
        lines=lines,
        line_height=line_height,
        box_height=box_height,
        column_span=column_span,
    )


def _build_alias_section_layout(
    draw: ImageDraw.ImageDraw,
    items: list[str],
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    content_width: int,
    column_gap: int,
    item_padding_x: int,
    item_padding_y: int,
    bullet_size: int,
    bullet_gap: int,
) -> _AliasSectionLayout:
    if not items:
        return _AliasSectionLayout(
            base_column_count=1,
            base_column_width=content_width,
            rows=[],
        )

    base_column_count = max(1, min(4, len(items)))
    base_column_width = (
        content_width - column_gap * max(0, base_column_count - 1)
    ) // base_column_count

    blocks: list[_AliasItemBlock] = []
    for item in items:
        item_width, _ = _measure_text(draw, item, font)
        block: _AliasItemBlock | None = None
        for column_span in range(1, base_column_count + 1):
            box_width = _measure_alias_box_width(
                base_column_width=base_column_width,
                column_gap=column_gap,
                column_span=column_span,
            )
            text_width = box_width - item_padding_x * 2 - bullet_size - bullet_gap
            if text_width > 0 and item_width <= text_width:
                block = _build_alias_item_block(
                    draw,
                    item,
                    font=font,
                    box_width=box_width,
                    item_padding_x=item_padding_x,
                    item_padding_y=item_padding_y,
                    bullet_size=bullet_size,
                    bullet_gap=bullet_gap,
                    column_span=column_span,
                    multiline=False,
                )
                break
        if block is None:
            box_width = _measure_alias_box_width(
                base_column_width=base_column_width,
                column_gap=column_gap,
                column_span=base_column_count,
            )
            block = _build_alias_item_block(
                draw,
                item,
                font=font,
                box_width=box_width,
                item_padding_x=item_padding_x,
                item_padding_y=item_padding_y,
                bullet_size=bullet_size,
                bullet_gap=bullet_gap,
                column_span=base_column_count,
                multiline=True,
            )
        blocks.append(block)

    rows: list[_AliasRowLayout] = []
    current_items: list[_AliasItemPlacement] = []
    current_span = 0
    current_row_height = 0

    for block in blocks:
        if current_items and current_span + block.column_span > base_column_count:
            rows.append(
                _AliasRowLayout(
                    items=current_items,
                    row_height=current_row_height,
                )
            )
            current_items = []
            current_span = 0
            current_row_height = 0

        box_width = _measure_alias_box_width(
            base_column_width=base_column_width,
            column_gap=column_gap,
            column_span=block.column_span,
        )
        current_items.append(
            _AliasItemPlacement(
                block=block,
                column_start=current_span,
                box_width=box_width,
            )
        )
        current_span += block.column_span
        current_row_height = max(current_row_height, block.box_height)

        if current_span == base_column_count:
            rows.append(
                _AliasRowLayout(
                    items=current_items,
                    row_height=current_row_height,
                )
            )
            current_items = []
            current_span = 0
            current_row_height = 0

    if current_items:
        rows.append(
            _AliasRowLayout(
                items=current_items,
                row_height=current_row_height,
            )
        )

    return _AliasSectionLayout(
        base_column_count=base_column_count,
        base_column_width=base_column_width,
        rows=rows,
    )


def _render_alias_section(
    draw: ImageDraw.ImageDraw,
    *,
    section_title: str,
    items: list[str],
    top: int,
    content_left: int,
    content_width: int,
    section_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    item_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    item_gap: int,
    column_gap: int,
    item_padding_x: int,
    item_padding_y: int,
    bullet_size: int,
    bullet_gap: int,
    paint: bool,
) -> int:
    if not items:
        return top

    layout = _build_alias_section_layout(
        draw,
        items,
        font=item_font,
        content_width=content_width,
        column_gap=column_gap,
        item_padding_x=item_padding_x,
        item_padding_y=item_padding_y,
        bullet_size=bullet_size,
        bullet_gap=bullet_gap,
    )
    if paint:
        accent_top = top + 6
        draw.rounded_rectangle(
            (content_left, accent_top, content_left + 6, accent_top + 26),
            radius=3,
            fill=(78, 113, 214),
        )
        draw.text(
            (content_left + 18, top),
            section_title,
            fill=(31, 41, 55),
            font=section_font,
        )

    _, section_height = _measure_text(draw, section_title, section_font)
    row_top = top + section_height + 18
    for row in layout.rows:
        row_height = row.row_height
        if paint:
            for placement in row.items:
                block = placement.block
                left = content_left + placement.column_start * (
                    layout.base_column_width + column_gap
                )
                top_y = row_top
                right = left + placement.box_width
                bottom = top_y + row_height
                draw.rounded_rectangle(
                    (left, top_y, right, bottom),
                    radius=18,
                    fill=(248, 250, 252),
                    outline=(231, 235, 241),
                    width=2,
                )
                text_x = left + item_padding_x + bullet_size + bullet_gap
                line_y = top_y + item_padding_y
                bullet_center_y = line_y + block.line_height // 2
                draw.ellipse(
                    (
                        left + item_padding_x,
                        bullet_center_y - bullet_size // 2,
                        left + item_padding_x + bullet_size,
                        bullet_center_y + bullet_size // 2,
                    ),
                    fill=(78, 113, 214),
                )
                for line in block.lines:
                    draw.text(
                        (text_x, line_y),
                        line,
                        fill=(55, 65, 81),
                        font=item_font,
                    )
                    _, line_height = _measure_text(draw, line, item_font)
                    line_y += line_height + 8
        row_top += row_height + item_gap
    return row_top - item_gap


def build_alias_profile_image(
    *,
    title: str,
    aliases: list[str],
    group_aliases: list[str] | None = None,
    width: int = 1120,
    background: tuple[int, int, int] = (245, 247, 250),
) -> bytes:
    title_font = _load_font(36)
    section_font = _load_font(28)
    item_font = _load_font(23)
    card_left = 18
    card_top = 18
    card_padding = 30
    section_gap = 28
    item_gap = 14
    column_gap = 18
    item_padding_x = 16
    item_padding_y = 12
    bullet_size = 8
    bullet_gap = 12

    content_left = card_left + card_padding
    content_right = width - card_left - card_padding
    content_width = content_right - content_left

    measure_canvas = PILImage.new("RGB", (width, 64), color=background)
    measure_draw = ImageDraw.Draw(measure_canvas)
    current_y = card_top + card_padding
    _, title_height = _measure_text(measure_draw, title, title_font)
    current_y += title_height + 26

    current_y = _render_alias_section(
        measure_draw,
        section_title="别名",
        items=aliases,
        top=current_y,
        content_left=content_left,
        content_width=content_width,
        section_font=section_font,
        item_font=item_font,
        item_gap=item_gap,
        column_gap=column_gap,
        item_padding_x=item_padding_x,
        item_padding_y=item_padding_y,
        bullet_size=bullet_size,
        bullet_gap=bullet_gap,
        paint=False,
    )
    group_items = list(group_aliases or [])
    if group_items:
        current_y += section_gap
        current_y = _render_alias_section(
            measure_draw,
            section_title="本群别名",
            items=group_items,
            top=current_y,
            content_left=content_left,
            content_width=content_width,
            section_font=section_font,
            item_font=item_font,
            item_gap=item_gap,
            column_gap=column_gap,
            item_padding_x=item_padding_x,
            item_padding_y=item_padding_y,
            bullet_size=bullet_size,
            bullet_gap=bullet_gap,
            paint=False,
        )

    height = max(current_y + card_padding, 140)
    canvas = PILImage.new("RGB", (width, height), color=background)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (card_left, card_top, width - card_left, height - card_top),
        radius=26,
        fill=(255, 255, 255),
        outline=(228, 232, 240),
        width=2,
    )
    current_y = card_top + card_padding
    draw.text(
        (content_left, current_y),
        title,
        fill=(22, 28, 36),
        font=title_font,
    )
    current_y += title_height + 26
    current_y = _render_alias_section(
        draw,
        section_title="别名",
        items=aliases,
        top=current_y,
        content_left=content_left,
        content_width=content_width,
        section_font=section_font,
        item_font=item_font,
        item_gap=item_gap,
        column_gap=column_gap,
        item_padding_x=item_padding_x,
        item_padding_y=item_padding_y,
        bullet_size=bullet_size,
        bullet_gap=bullet_gap,
        paint=True,
    )
    if group_items:
        current_y += section_gap
        current_y = _render_alias_section(
            draw,
            section_title="本群别名",
            items=group_items,
            top=current_y,
            content_left=content_left,
            content_width=content_width,
            section_font=section_font,
            item_font=item_font,
            item_gap=item_gap,
            column_gap=column_gap,
            item_padding_x=item_padding_x,
            item_padding_y=item_padding_y,
            bullet_size=bullet_size,
            bullet_gap=bullet_gap,
            paint=True,
        )

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
