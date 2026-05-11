from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field

from zhenxun.services.log import logger
from zhenxun.utils.exception import AllURIsFailedError
from zhenxun.utils.http_utils import AsyncHttpx

from .clients import (
    AnimeTraceClient,
    SauceNAOClient,
    SearchClientError,
    SerpApiGoogleLensClient,
    TraceMoeClient,
    is_supplemental_source_url,
)
from .config import PicSearchSettings, load_settings
from .models import (
    ImageInput,
    SauceNAOResult,
    SearchPresentation,
    SearchResultItem,
    SearchSection,
)


@dataclass(slots=True)
class PicSearchClients:
    """搜图工作流依赖的外部客户端集合，便于测试替换。"""

    saucenao: SauceNAOClient = field(default_factory=SauceNAOClient)
    google_lens: SerpApiGoogleLensClient = field(
        default_factory=SerpApiGoogleLensClient
    )
    tracemoe: TraceMoeClient = field(default_factory=TraceMoeClient)
    animetrace: AnimeTraceClient = field(default_factory=AnimeTraceClient)


def validate_image_size(image: ImageInput, settings: PicSearchSettings) -> None:
    """检查用户图片大小是否超过插件配置限制。"""

    max_bytes = max(settings.max_image_size_mb, 1) * 1024 * 1024
    if len(image.content) > max_bytes:
        raise ValueError(f"图片大小超过 {settings.max_image_size_mb}MB，请压缩后再试。")


async def _thumbnail_to_data_uri(url: str) -> str:
    """下载缩略图并转换成浏览器渲染稳定的 data URI。"""

    if not url:
        return ""
    if url.startswith("data:image/"):
        return url
    try:
        content = await AsyncHttpx.get_content(url, timeout=15)
    except AllURIsFailedError as e:
        logger.debug(f"缩略图下载失败：{url} {e}", "搜图")
        return ""
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


async def _prepare_thumbnails(
    items: list[SearchResultItem],
    settings: PicSearchSettings,
) -> None:
    """按 NSFW 和全局开关策略准备可展示缩略图。"""

    async def worker(item: SearchResultItem) -> None:
        # hidden 表示上游明确标记或插件策略判定有风险；普通的“按配置隐藏预览”
        # 不走 hidden，避免在结果页误显示“媒体已隐藏”。
        if settings.hide_thumbnail:
            item.metadata.setdefault("预览", "已按配置隐藏")
            item.thumbnail_visible = False
            item.thumbnail_data_uri = ""
            return
        if item.hidden or not item.thumbnail_visible:
            item.thumbnail_visible = False
            item.thumbnail_data_uri = ""
            return
        item.thumbnail_data_uri = await _thumbnail_to_data_uri(item.thumbnail_url)
        item.thumbnail_visible = bool(item.thumbnail_data_uri)

    await asyncio.gather(*(worker(item) for item in items))


def _append_section_links(
    lines: list[str],
    section: SearchSection,
    start_index: int,
) -> int:
    """把区块里的 URL 展开成最终链接清单。"""

    index = start_index
    for item in section.items:
        preview = item.metadata.get("预览视频")
        item_links = item.links or ([item.url] if item.url else [])
        if not item_links and not preview:
            continue

        item.display_index = index
        detail_lines = [f"[{index}] {section.title} - {item.title}"]
        # SauceNAO 常同时返回原始来源和 booru 镜像；把更可能是原始发布页的链接
        # 放在上方，镜像/原图直链空一行后展示，降低用户点错链接的概率。
        if section.title == "SauceNAO":
            primary_links = [
                link for link in item_links if not is_supplemental_source_url(link)
            ]
            supplemental_links = [
                link for link in item_links if is_supplemental_source_url(link)
            ]
            detail_lines.extend(primary_links or supplemental_links)
            if primary_links and supplemental_links:
                detail_lines.append("")
                detail_lines.extend(supplemental_links)
        else:
            detail_lines.extend(item_links)
        if preview:
            detail_lines.append(f"预览视频：{preview}")
        lines.append("\n".join(detail_lines))
        index += 1
    return index


def _append_character_results(lines: list[str], section: SearchSection) -> None:
    """把识角色候选整理成可复制文本，而不是伪造网页链接。"""

    result_lines = []
    for item_index, item in enumerate(section.items, start=1):
        candidates = item.candidates or []
        if not candidates:
            result_lines.append(f"[{item_index}] {item.title}")
            continue
        result_lines.append(f"[{item_index}] {item.title}")
        for candidate_index, candidate in enumerate(candidates, start=1):
            suffix = f" / {candidate.subtitle}" if candidate.subtitle else ""
            result_lines.append(f"{candidate_index}. {candidate.title}{suffix}")
    if result_lines:
        lines.append("识角色实际结果\n" + "\n".join(result_lines))


def _build_saucenao_section(result: SauceNAOResult) -> SearchSection:
    """构造 SauceNAO 结果区块。"""

    notices = []
    if result.quota.long_remaining is not None and result.quota.long_remaining < 10:
        notices.append(f"SauceNAO 24h 剩余额度：{result.quota.long_remaining}")
    if result.quota.short_remaining is not None and result.quota.short_remaining < 1:
        notices.append("SauceNAO 短时间额度已耗尽。")
    section = SearchSection(
        title="SauceNAO",
        subtitle="来源相似度搜索",
        items=result.items,
        notices=notices,
    )
    if result.search_url:
        section.link_lines.append(f"SauceNAO 搜索页面\n{result.search_url}")
    return section


async def _search_google_lens_section(
    image: ImageInput,
    settings: PicSearchSettings,
    client: SerpApiGoogleLensClient,
) -> SearchSection:
    """查询 SerpAPI Google Lens 并包装成结果区块。"""

    try:
        items = await client.search(image.url, settings)
        await _prepare_thumbnails(items, settings)
        return SearchSection(
            title="Google Lens",
            subtitle="视觉相似网页结果",
            items=items,
            error="" if items else "Google Lens 未找到结果。",
        )
    except SearchClientError as e:
        return SearchSection(
            title="Google Lens", subtitle="视觉相似网页结果", error=e.user_message
        )


async def search_picture(
    image: ImageInput,
    *,
    force_lens: bool = False,
    settings: PicSearchSettings | None = None,
    clients: PicSearchClients | None = None,
) -> SearchPresentation:
    """执行搜图主流程。"""

    settings = settings or load_settings()
    clients = clients or PicSearchClients()
    validate_image_size(image, settings)

    presentation = SearchPresentation(title="搜图结果")

    if force_lens:
        lens_section = await _search_google_lens_section(
            image, settings, clients.google_lens
        )
        presentation.sections.append(lens_section)
    else:
        saucenao_result: SauceNAOResult | None = None
        saucenao_error = ""
        try:
            saucenao_result = await clients.saucenao.search(image, settings)
        except SearchClientError as e:
            saucenao_error = e.user_message

        best = (
            saucenao_result.items[0]
            if saucenao_result and saucenao_result.items
            else None
        )
        best_similarity = best.similarity if best else None

        # 这里按可信区间分流：低可信 SauceNAO 结果不展示，避免给用户制造错误来源暗示。
        if (
            best_similarity is not None
            and best_similarity >= settings.saucenao_confident_threshold
        ):
            assert saucenao_result is not None
            await _prepare_thumbnails(saucenao_result.items, settings)
            presentation.sections.append(_build_saucenao_section(saucenao_result))
        elif (
            best_similarity is not None
            and best_similarity >= settings.saucenao_low_threshold
        ):
            assert saucenao_result is not None
            await _prepare_thumbnails(saucenao_result.items, settings)
            presentation.sections.append(_build_saucenao_section(saucenao_result))
            presentation.sections.append(
                await _search_google_lens_section(image, settings, clients.google_lens)
            )
        else:
            if saucenao_error:
                presentation.notices.append(saucenao_error)
            elif best_similarity is not None:
                presentation.notices.append(
                    f"SauceNAO 最高相似度 {best_similarity:.2f}% 低于 "
                    f"{settings.saucenao_low_threshold:.0f}%，已切换 Google Lens。"
                )
            else:
                presentation.notices.append(
                    "SauceNAO 未找到可用结果，已切换 Google Lens。"
                )
            presentation.sections.append(
                await _search_google_lens_section(image, settings, clients.google_lens)
            )

    index = 1
    deferred_section_links: list[str] = []
    for section in presentation.sections:
        index = _append_section_links(presentation.link_lines, section, index)
        deferred_section_links.extend(section.link_lines)
    presentation.link_lines.extend(deferred_section_links)
    return presentation


async def search_anime(
    image: ImageInput,
    *,
    settings: PicSearchSettings | None = None,
    clients: PicSearchClients | None = None,
) -> SearchPresentation:
    """执行搜番流程。"""

    settings = settings or load_settings()
    clients = clients or PicSearchClients()
    validate_image_size(image, settings)

    try:
        items = await clients.tracemoe.search(image, settings)
        await _prepare_thumbnails(items, settings)
        section = SearchSection(
            title="TraceMoe",
            subtitle="动画场景搜索",
            items=items,
            error="" if items else "TraceMoe 未找到结果。",
        )
    except SearchClientError as e:
        section = SearchSection(
            title="TraceMoe", subtitle="动画场景搜索", error=e.user_message
        )

    presentation = SearchPresentation(title="搜番结果", sections=[section])
    _append_section_links(presentation.link_lines, section, 1)
    return presentation


async def search_character(
    image: ImageInput,
    *,
    settings: PicSearchSettings | None = None,
    clients: PicSearchClients | None = None,
) -> SearchPresentation:
    """执行识角色流程。"""

    settings = settings or load_settings()
    clients = clients or PicSearchClients()
    validate_image_size(image, settings)

    try:
        items = await clients.animetrace.search(image, settings)
        await _prepare_thumbnails(items, settings)
        section = SearchSection(
            title="AnimeTrace",
            subtitle="动画角色识别",
            items=items,
            error="" if items else "AnimeTrace 未识别到角色。",
        )
    except SearchClientError as e:
        section = SearchSection(
            title="AnimeTrace", subtitle="动画角色识别", error=e.user_message
        )

    presentation = SearchPresentation(title="识角色结果", sections=[section])
    _append_character_results(presentation.link_lines, section)
    return presentation
