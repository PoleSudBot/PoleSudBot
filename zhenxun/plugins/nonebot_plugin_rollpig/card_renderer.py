from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageSequence

from zhenxun.utils._build_image import BuildImage

CANVAS_SIZE = (800, 800)
CONTENT_WIDTH = 720
AVATAR_SIZE = 240
NAME_FONT_SIZE = 48
DESC_FONT_SIZE = 30
ANALYSIS_FONT_SIZE = 28
ANALYSIS_MIN_FONT_SIZE = 20
GIF_MAX_FRAMES = 80
GIF_MIN_FRAME_DURATION_MS = 20
GIF_MAX_FRAME_DURATION_MS = 2000
GIF_FALLBACK_FRAME_DURATION_MS = 100
GIF_PALETTE_SAMPLE_FRAMES = 16
GIF_PALETTE_SAMPLE_SIZE = 200
PLUGIN_DIR = Path(__file__).parent
NEW_ICON_FILE = PLUGIN_DIR / "resource" / "assets" / "new.png"
FONT_DIR = (
    Path(__file__).resolve().parents[3]
    / "resources"
    / "font"
    / "HarmonyOS_Sans_SC"
)
REGULAR_FONT = FONT_DIR / "HarmonyOS_SansSC_Regular.ttf"
BOLD_FONT = FONT_DIR / "HarmonyOS_SansSC_Bold.ttf"


@dataclass(frozen=True)
class PigCardRenderResult:
    data: bytes
    image_format: Literal["png", "gif"]
    renderer: Literal["pillow", "pillow-gif"]


@dataclass(frozen=True)
class _PreparedCard:
    canvas: Image.Image
    avatar_y: int


def _font(path: Path, size: int):
    return BuildImage.load_font(path, size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text or " ", font=font)
    return box[2] - box[0]


def _wrap_text(text: str, draw: ImageDraw.ImageDraw, font) -> list[str]:
    """按实际像素宽度换行，避免中文长文案溢出卡片。"""

    lines: list[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and _text_width(draw, candidate, font) > CONTENT_WIDTH:
                lines.append(current)
                current = character
            else:
                current = candidate
        lines.append(current)
    return lines or [""]


def _layout_text(pig_data: Mapping[str, Any]) -> tuple[list[str], list[str], int]:
    probe = Image.new("RGB", (1, 1), "white")
    draw = ImageDraw.Draw(probe)
    desc_lines = _wrap_text(
        str(pig_data.get("description") or ""),
        draw,
        _font(REGULAR_FONT, DESC_FONT_SIZE),
    )
    analysis_text = str(pig_data.get("analysis") or "你今天是只神秘小猪。")
    analysis_size = ANALYSIS_FONT_SIZE
    while analysis_size > ANALYSIS_MIN_FONT_SIZE:
        analysis_lines = _wrap_text(
            analysis_text,
            draw,
            _font(REGULAR_FONT, analysis_size),
        )
        total_height = (
            AVATAR_SIZE
            + 20
            + 58
            + (20 + len(desc_lines) * 40 if any(desc_lines) else 0)
            + 30
            + len(analysis_lines) * int(analysis_size * 1.5)
        )
        if total_height <= CONTENT_WIDTH:
            return desc_lines, analysis_lines, analysis_size
        analysis_size -= 2

    analysis_lines = _wrap_text(
        analysis_text,
        draw,
        _font(REGULAR_FONT, ANALYSIS_MIN_FONT_SIZE),
    )
    max_lines = 8
    if len(analysis_lines) > max_lines:
        analysis_lines = analysis_lines[:max_lines]
        analysis_lines[-1] = analysis_lines[-1].rstrip("。…") + "…"
    return desc_lines, analysis_lines, ANALYSIS_MIN_FONT_SIZE


def _draw_centered_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font,
    fill: tuple[int, int, int, int],
) -> None:
    box = draw.textbbox((0, 0), text or " ", font=font)
    width = box[2] - box[0]
    draw.text(((CANVAS_SIZE[0] - width) // 2, y), text, font=font, fill=fill)


def _prepare_card(pig_data: Mapping[str, Any]) -> _PreparedCard:
    """生成不含头像的静态卡片层，供 PNG 和每一帧 GIF 复用。"""

    desc_lines, analysis_lines, analysis_size = _layout_text(pig_data)
    desc_height = len(desc_lines) * 40 if any(desc_lines) else 0
    analysis_line_height = int(analysis_size * 1.5)
    total_height = (
        AVATAR_SIZE
        + 20
        + 58
        + (20 + desc_height if desc_height else 0)
        + 30
        + len(analysis_lines) * analysis_line_height
    )
    avatar_y = max(20, (CANVAS_SIZE[1] - total_height) // 2)
    build_image = BuildImage(
        *CANVAS_SIZE,
        color=(255, 255, 255, 255),
        font=_font(REGULAR_FONT, ANALYSIS_FONT_SIZE),
    )
    canvas = build_image.markImg
    draw = ImageDraw.Draw(canvas)
    y = avatar_y + AVATAR_SIZE + 20
    _draw_centered_line(
        draw,
        str(pig_data.get("name") or "未知小猪"),
        y,
        _font(BOLD_FONT, NAME_FONT_SIZE),
        (0, 0, 0, 255),
    )
    y += 58
    if desc_height:
        y += 20
        desc_font = _font(REGULAR_FONT, DESC_FONT_SIZE)
        for line in desc_lines:
            _draw_centered_line(draw, line, y, desc_font, (85, 85, 85, 255))
            y += 40
    y += 30
    analysis_font = _font(REGULAR_FONT, analysis_size)
    for line in analysis_lines:
        _draw_centered_line(draw, line, y, analysis_font, (51, 51, 51, 255))
        y += analysis_line_height

    return _PreparedCard(canvas=canvas, avatar_y=avatar_y)


def _paste_new_icon(canvas: Image.Image, avatar_y: int) -> None:
    if NEW_ICON_FILE.exists():
        with Image.open(NEW_ICON_FILE) as opened:
            new_icon = opened.convert("RGBA")
            new_icon.thumbnail((180, 180), Image.Resampling.LANCZOS)
            canvas.alpha_composite(new_icon, (390, max(0, avatar_y - 50)))


def _paste_avatar(canvas: Image.Image, avatar: Image.Image, avatar_y: int) -> None:
    """按资源原始画布居中合成头像，不额外裁切、缩放或补留白。"""

    source = avatar.convert("RGBA")
    canvas.alpha_composite(source, ((CANVAS_SIZE[0] - source.width) // 2, avatar_y))


def _normalize_duration(value: object) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError):
        duration = GIF_FALLBACK_FRAME_DURATION_MS
    if duration <= 0:
        duration = GIF_FALLBACK_FRAME_DURATION_MS
    return min(max(duration, GIF_MIN_FRAME_DURATION_MS), GIF_MAX_FRAME_DURATION_MS)


def _load_gif_frames(image_file: Path | None) -> list[tuple[Image.Image, int]]:
    if image_file is None or image_file.suffix.lower() != ".gif":
        return []
    with Image.open(image_file) as opened:
        if int(getattr(opened, "n_frames", 1)) <= 1:
            return []
        frames: list[tuple[Image.Image, int]] = []
        for index, frame in enumerate(ImageSequence.Iterator(opened)):
            if index >= GIF_MAX_FRAMES:
                break
            frames.append(
                (frame.convert("RGBA"), _normalize_duration(frame.info.get("duration")))
            )
        return frames


def _encode_png(
    prepared: _PreparedCard,
    image_file: Path | None,
    *,
    is_new: bool,
) -> PigCardRenderResult:
    canvas = prepared.canvas.copy()
    if image_file is not None:
        with Image.open(image_file) as opened:
            _paste_avatar(canvas, opened, prepared.avatar_y)
    if is_new:
        _paste_new_icon(canvas, prepared.avatar_y)
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return PigCardRenderResult(output.getvalue(), "png", "pillow")


def _encode_gif(
    prepared: _PreparedCard,
    avatar_frames: list[tuple[Image.Image, int]],
    *,
    is_new: bool,
) -> PigCardRenderResult:
    rgb_frames: list[Image.Image] = []
    durations: list[int] = []
    for avatar, duration in avatar_frames:
        frame = prepared.canvas.copy()
        _paste_avatar(frame, avatar, prepared.avatar_y)
        if is_new:
            _paste_new_icon(frame, prepared.avatar_y)
        rgb_frames.append(frame.convert("RGB"))
        durations.append(duration)

    # 抽样缩略帧足以覆盖卡片颜色，并避免 80 帧时为调色板额外分配数百 MB。
    sample_count = min(len(rgb_frames), GIF_PALETTE_SAMPLE_FRAMES)
    sample_indices = {
        round(index * (len(rgb_frames) - 1) / max(sample_count - 1, 1))
        for index in range(sample_count)
    }
    palette_source = Image.new(
        "RGB",
        (GIF_PALETTE_SAMPLE_SIZE, GIF_PALETTE_SAMPLE_SIZE * len(sample_indices)),
    )
    for output_index, frame_index in enumerate(sorted(sample_indices)):
        sample = rgb_frames[frame_index].resize(
            (GIF_PALETTE_SAMPLE_SIZE, GIF_PALETTE_SAMPLE_SIZE),
            Image.Resampling.LANCZOS,
        )
        palette_source.paste(sample, (0, output_index * GIF_PALETTE_SAMPLE_SIZE))
    palette = palette_source.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    output_frames = [
        frame.quantize(palette=palette, dither=Image.Dither.NONE)
        for frame in rgb_frames
    ]
    output = BytesIO()
    output_frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=output_frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=False,
    )
    return PigCardRenderResult(output.getvalue(), "gif", "pillow-gif")


def _render_sync(
    pig_data: Mapping[str, Any],
    image_file: Path | None,
    *,
    is_new: bool,
) -> PigCardRenderResult:
    prepared = _prepare_card(pig_data)
    gif_frames = _load_gif_frames(image_file)
    if gif_frames:
        return _encode_gif(prepared, gif_frames, is_new=is_new)
    return _encode_png(prepared, image_file, is_new=is_new)


async def render_pig_card_image(
    pig_data: Mapping[str, Any],
    image_file: Path | None,
    *,
    is_new: bool = False,
) -> PigCardRenderResult:
    """在线程中生成普通小猪卡片，动态头像输出 GIF。"""

    return await asyncio.to_thread(_render_sync, pig_data, image_file, is_new=is_new)
