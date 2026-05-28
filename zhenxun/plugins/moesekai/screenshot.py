from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from io import BytesIO
from math import ceil, floor
import time
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image

from .adapters.runtime import logger
from .config import get_settings
from .constants import MODULE_NAME

_DEFAULT_VIEWPORT_HEIGHT = 932
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Mobile/15E148 Safari/604.1"
)

WaitCallback = Callable[[Any, int, dict[str, Any] | None], Awaitable[None]]


def _build_viewport(width: int) -> dict[str, int]:
    return {"width": width, "height": _DEFAULT_VIEWPORT_HEIGHT}


def _redact_url_secrets(url: str) -> str:
    split = urlsplit(url)
    query = []
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        if key == "token" and value:
            query.append((key, "***"))
        else:
            query.append((key, value))
    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(query),
            split.fragment,
        )
    )


class ScreenshotError(RuntimeError):
    def __init__(self, kind: str, errors: list[str]):
        self.kind = kind
        self.errors = errors
        super().__init__(errors[-1] if errors else f"{kind} 截图失败")

    def to_user_message(self) -> str:
        return f"{self.kind}截图失败，请稍后重试"


@dataclass
class ScreenshotJob:
    kind: str
    urls: list[str]
    viewport: dict[str, int]
    device_scale_factor: float
    full_page: bool = True
    capture_mode: Literal["full_page", "element", "content_union"] = "full_page"
    required_body_classes: list[str] = field(default_factory=list)
    target_selectors: list[str] = field(default_factory=list)
    user_agent: str | None = None
    wait_until: Literal["domcontentloaded", "load", "networkidle"] = "networkidle"
    wait_selector: str | None = None
    wait_function: str | None = None
    wait_callback: WaitCallback | None = None
    prepare_script: str | None = None
    before_capture_script: str | None = None
    scroll_if_function: str | None = None
    scroll_through_page: bool = False
    top_crop_css_pixels: int = 0
    stability_wait_ms: int = 0
    extra_wait_seconds: float = 1.5
    timeout_seconds: int = 45
    log_timing: bool = False


class ScreenshotService:
    def __init__(self) -> None:
        self._site_failures: dict[str, float] = {}
        self._last_success_site: dict[str, str] = {}
        self._capture_semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _quality_scale_factor(quality: int) -> float:
        if quality >= 90:
            return 3.0
        if quality >= 75:
            return 2.5
        if quality >= 55:
            return 2.0
        if quality >= 35:
            return 1.5
        return 1.0

    @staticmethod
    def _site_key(url: str) -> str:
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _append_stage_timing(
        stage_timings: list[tuple[str, float]] | None,
        stage_name: str,
        started_at: float,
    ) -> None:
        if stage_timings is None:
            return
        stage_timings.append((stage_name, (time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _format_stage_timings(stage_timings: list[tuple[str, float]]) -> str:
        if not stage_timings:
            return "无阶段耗时"
        return " | ".join(f"{name}={elapsed:.1f}ms" for name, elapsed in stage_timings)

    @staticmethod
    def _format_attempt_meta(attempt_meta: dict[str, Any]) -> str:
        parts: list[str] = []
        page_state = attempt_meta.get("page_state")
        if page_state:
            parts.append(f"状态={page_state}")
        page_state_detail = attempt_meta.get("page_state_detail")
        if page_state_detail:
            parts.append(f"详情={page_state_detail}")
        if attempt_meta.get("fallback_clicked") == "1":
            parts.append("兜底点击=1")
        return " | ".join(parts)

    def _filter_urls(self, kind: str, urls: list[str]) -> list[str]:
        now = time.time()
        preferred_site = self._last_success_site.get(kind)
        preferred_urls: list[str] = []
        normal_urls: list[str] = []
        fallback_urls: list[str] = []
        for url in urls:
            site_key = self._site_key(url)
            if self._site_failures.get(site_key, 0) > now:
                fallback_urls.append(url)
                continue
            if preferred_site and site_key == preferred_site:
                preferred_urls.append(url)
            else:
                normal_urls.append(url)
        filtered = preferred_urls + normal_urls
        if filtered:
            return filtered
        if preferred_site:
            prioritized = [
                url for url in fallback_urls if self._site_key(url) == preferred_site
            ]
            prioritized.extend(
                url for url in fallback_urls if self._site_key(url) != preferred_site
            )
            if prioritized:
                return prioritized
        return fallback_urls or urls

    @staticmethod
    async def _wait_for_stability(page: Any, milliseconds: int = 0) -> None:
        await page.evaluate(
            """
async (ms) => {
  await new Promise((resolve) =>
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        if (ms > 0) {
          setTimeout(resolve, ms);
          return;
        }
        resolve();
      })
    )
  );
}
            """.strip(),
            milliseconds,
        )

    @staticmethod
    async def _get_browser() -> Any:
        from zhenxun.services.renderer.engine import get_managed_browser

        return await get_managed_browser()

    async def _capture_once(
        self,
        job: ScreenshotJob,
        url: str,
        *,
        stage_timings: list[tuple[str, float]] | None = None,
        attempt_meta: dict[str, Any] | None = None,
    ) -> bytes:
        timeout_ms = job.timeout_seconds * 1000
        context = None
        page = None
        stage_started = time.perf_counter()
        browser = await self._get_browser()
        self._append_stage_timing(stage_timings, "browser/get", stage_started)
        stage_started = time.perf_counter()
        context = await browser.new_context(
            viewport=job.viewport,
            user_agent=job.user_agent,
            device_scale_factor=job.device_scale_factor,
        )
        self._append_stage_timing(stage_timings, "context/new", stage_started)
        stage_started = time.perf_counter()
        page = await context.new_page()
        self._append_stage_timing(stage_timings, "page/new", stage_started)
        try:
            stage_started = time.perf_counter()
            await page.goto(url, wait_until=job.wait_until, timeout=timeout_ms)
            self._append_stage_timing(stage_timings, "goto", stage_started)
            if job.prepare_script:
                stage_started = time.perf_counter()
                await page.evaluate(job.prepare_script)
                self._append_stage_timing(
                    stage_timings, "prepare_script", stage_started
                )
            if job.required_body_classes:
                stage_started = time.perf_counter()
                await page.wait_for_function(
                    """
(classes) => {
  const body = document.body;
  return !!body && classes.every((cls) => body.classList.contains(cls));
}
                    """.strip(),
                    arg=job.required_body_classes,
                    timeout=timeout_ms,
                )
                self._append_stage_timing(
                    stage_timings,
                    "required_body_classes",
                    stage_started,
                )
            if job.wait_selector:
                stage_started = time.perf_counter()
                await page.wait_for_selector(job.wait_selector, timeout=timeout_ms)
                self._append_stage_timing(stage_timings, "wait_selector", stage_started)
            if job.wait_callback:
                stage_started = time.perf_counter()
                await job.wait_callback(page, timeout_ms, attempt_meta)
                self._append_stage_timing(stage_timings, "wait_callback", stage_started)
            should_scroll = job.scroll_through_page
            if job.scroll_if_function:
                stage_started = time.perf_counter()
                should_scroll = should_scroll or bool(
                    await page.evaluate(job.scroll_if_function)
                )
                self._append_stage_timing(stage_timings, "scroll/check", stage_started)
            if should_scroll:
                stage_started = time.perf_counter()
                await page.evaluate(
                    """
async () => {
  const maxHeight = Math.max(
    document.body?.scrollHeight || 0,
    document.documentElement?.scrollHeight || 0
  );
  const step = Math.max(Math.floor(window.innerHeight * 0.85), 320);
  for (let offset = 0; offset < maxHeight; offset += step) {
    window.scrollTo(0, offset);
    await new Promise((resolve) => setTimeout(resolve, 70));
  }
  window.scrollTo(0, maxHeight);
  await new Promise((resolve) => setTimeout(resolve, 120));
  window.scrollTo(0, 0);
}
                    """.strip()
                )
                await asyncio.sleep(0.05)
                self._append_stage_timing(stage_timings, "scroll", stage_started)
            if job.wait_function:
                stage_started = time.perf_counter()
                await page.wait_for_function(job.wait_function, timeout=timeout_ms)
                self._append_stage_timing(stage_timings, "wait_function", stage_started)
            if job.before_capture_script:
                stage_started = time.perf_counter()
                await page.evaluate(job.before_capture_script)
                self._append_stage_timing(
                    stage_timings,
                    "before_capture_script",
                    stage_started,
                )
            stage_started = time.perf_counter()
            await self._wait_for_stability(page, job.stability_wait_ms)
            self._append_stage_timing(stage_timings, "stability_wait", stage_started)
            if job.extra_wait_seconds > 0:
                stage_started = time.perf_counter()
                await asyncio.sleep(job.extra_wait_seconds)
                self._append_stage_timing(stage_timings, "extra_wait", stage_started)
            stage_started = time.perf_counter()
            image = await self._capture_image(page, job)
            self._append_stage_timing(stage_timings, "capture_image", stage_started)
            if job.top_crop_css_pixels > 0:
                stage_started = time.perf_counter()
                image = self._crop_image_top(
                    image,
                    top_css_pixels=job.top_crop_css_pixels,
                    device_scale_factor=job.device_scale_factor,
                )
                self._append_stage_timing(stage_timings, "crop_image", stage_started)
            return image
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    @staticmethod
    async def _page_dimensions(page: Any) -> dict[str, int]:
        return await page.evaluate(
            """
() => ({
  width: Math.max(
    document.body?.scrollWidth || 0,
    document.documentElement?.scrollWidth || 0
  ),
  height: Math.max(
    document.body?.scrollHeight || 0,
    document.documentElement?.scrollHeight || 0
  ),
})
            """.strip()
        )

    async def _collect_target_boxes(
        self,
        page: Any,
        selectors: list[str],
        *,
        max_targets: int = 24,
    ) -> list[dict[str, float]]:
        boxes: list[dict[str, float]] = []
        remaining = max_targets
        for selector in selectors:
            if remaining <= 0:
                break
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(min(count, remaining)):
                box = await locator.nth(index).bounding_box()
                if box and box["width"] > 8 and box["height"] > 8:
                    boxes.append(box)
                    remaining -= 1
                    if remaining <= 0:
                        break
        return boxes

    @staticmethod
    def _clip_from_boxes(
        boxes: list[dict[str, float]],
        page_size: dict[str, int],
        *,
        padding: int = 16,
    ) -> dict[str, float] | None:
        if not boxes:
            return None
        left = max(0, floor(min(box["x"] for box in boxes)) - padding)
        top = max(0, floor(min(box["y"] for box in boxes)) - padding)
        right = min(
            page_size["width"],
            ceil(max(box["x"] + box["width"] for box in boxes)) + padding,
        )
        bottom = min(
            page_size["height"],
            ceil(max(box["y"] + box["height"] for box in boxes)) + padding,
        )
        width = max(1, right - left)
        height = max(1, bottom - top)
        return {
            "x": float(left),
            "y": float(top),
            "width": float(width),
            "height": float(height),
        }

    async def _capture_image(self, page: Any, job: ScreenshotJob) -> bytes:
        if job.capture_mode == "element":
            for selector in job.target_selectors:
                locator = page.locator(selector)
                if await locator.count():
                    image = await locator.first.screenshot(type="png")
                    return bytes(image)

        if job.capture_mode == "content_union":
            page_size = await self._page_dimensions(page)
            boxes = await self._collect_target_boxes(page, job.target_selectors)
            clip = self._clip_from_boxes(boxes, page_size)
            if clip:
                image = await page.screenshot(type="png", clip=clip)
                return bytes(image)

        image = await page.screenshot(type="png", full_page=job.full_page)
        return bytes(image)

    @staticmethod
    def _crop_image_top(
        image_bytes: bytes,
        *,
        top_css_pixels: int,
        device_scale_factor: float,
    ) -> bytes:
        crop_pixels = max(1, round(top_css_pixels * device_scale_factor))
        with Image.open(BytesIO(image_bytes)) as image:
            if crop_pixels >= image.height:
                return image_bytes
            cropped = image.crop((0, crop_pixels, image.width, image.height))
            buffer = BytesIO()
            cropped.save(buffer, format="PNG")
            return buffer.getvalue()

    async def capture(self, job: ScreenshotJob) -> bytes:
        settings = get_settings()
        errors: list[str] = []
        selection_started = time.perf_counter()
        candidates = self._filter_urls(job.kind, job.urls)
        selection_elapsed = (time.perf_counter() - selection_started) * 1000
        if job.log_timing:
            logger.debug(
                "MoeSekai 截图阶段 "
                f"[{job.kind}] 候选站点选择 {selection_elapsed:.1f}ms -> "
                f"{', '.join(_redact_url_secrets(url) for url in candidates)}",
                MODULE_NAME,
            )
        async with self._capture_semaphore:
            for attempt in range(1, settings.screenshot_retry_times + 1):
                for url in candidates:
                    site_key = self._site_key(url)
                    attempt_started = time.perf_counter()
                    stage_timings: list[tuple[str, float]] = []
                    attempt_meta: dict[str, Any] = {}
                    try:
                        image = await self._capture_once(
                            job,
                            url,
                            stage_timings=stage_timings if job.log_timing else None,
                            attempt_meta=attempt_meta,
                        )
                        attempt_elapsed = (time.perf_counter() - attempt_started) * 1000
                        if job.log_timing:
                            meta_text = self._format_attempt_meta(attempt_meta)
                            logger.debug(
                                "MoeSekai 截图阶段 "
                                f"[{job.kind}] 第{attempt}次命中站点 "
                                f"{_redact_url_secrets(url)} 总耗时 {attempt_elapsed:.1f}ms"
                                f"{f' | {meta_text}' if meta_text else ''} | "
                                f"{self._format_stage_timings(stage_timings)}",
                                MODULE_NAME,
                            )
                        self._last_success_site[job.kind] = site_key
                        return image
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        attempt_elapsed = (time.perf_counter() - attempt_started) * 1000
                        safe_url = _redact_url_secrets(url)
                        if job.log_timing:
                            meta_text = self._format_attempt_meta(attempt_meta)
                            logger.debug(
                                "MoeSekai 截图阶段 "
                                f"[{job.kind}] 第{attempt}次失败站点 "
                                f"{safe_url} 总耗时 {attempt_elapsed:.1f}ms"
                                f"{f' | {meta_text}' if meta_text else ''} | "
                                f"{self._format_stage_timings(stage_timings)}",
                                MODULE_NAME,
                            )
                        detail = (
                            f"第{attempt}次 {safe_url} "
                            f"{type(exc).__name__}: {exc}"
                        )
                        logger.warning(
                            f"MoeSekai 截图失败 [{job.kind}] {detail}",
                            MODULE_NAME,
                        )
                        errors.append(detail)
                        self._site_failures[site_key] = (
                            time.time() + settings.screenshot_retry_delay_seconds * 30
                        )
                if attempt < settings.screenshot_retry_times:
                    await asyncio.sleep(settings.screenshot_retry_delay_seconds)
                    selection_started = time.perf_counter()
                    candidates = self._filter_urls(job.kind, job.urls)
                    selection_elapsed = (time.perf_counter() - selection_started) * 1000
                    if job.log_timing:
                        logger.debug(
                            "MoeSekai 截图阶段 "
                            f"[{job.kind}] 重选候选站点 {selection_elapsed:.1f}ms -> "
                            f"{', '.join(_redact_url_secrets(url) for url in candidates)}",
                            MODULE_NAME,
                        )
        raise ScreenshotError(job.kind, errors)

    async def capture_profile(self, server: str, game_id: str) -> bytes:
        settings = get_settings()
        urls = [
            self._build_profile_url(
                template,
                server=server,
                game_id=game_id,
                token=settings.profile_token,
            )
            for template in settings.profile_url_templates
        ]
        job = ScreenshotJob(
            kind="个人档案",
            urls=urls,
            viewport=_build_viewport(settings.profile_viewport_width),
            device_scale_factor=self._quality_scale_factor(settings.screenshot_quality),
            user_agent=_MOBILE_USER_AGENT,
            full_page=True,
            required_body_classes=["page-fully-loaded", "animation-finished"],
            capture_mode="full_page",
            wait_until="domcontentloaded",
            wait_selector="#app .pjsk-container",
            wait_function="""
() => {
  const container = document.querySelector('#app .pjsk-container');
  if (!container) {
    return false;
  }
  const leader = document.querySelector(
    '#app > div > div.pjsk-container > div.section-card > div.deck-grid > div.deck-card.is-leader'
  );
  const cards = Array.from(
    document.querySelectorAll('div.deck-grid .deck-card')
  );
  const images = Array.from(container.querySelectorAll('img'));
  if (cards.length && !leader) {
    return false;
  }
  if (!images.length) {
    return true;
  }
  return images.every((img) => img.complete && img.naturalWidth > 0);
}
            """.strip(),
            before_capture_script="""
() => {
  if (!document.head || document.getElementById('__moesekai_capture_style__')) {
    return;
  }
  const style = document.createElement('style');
  style.id = '__moesekai_capture_style__';
  style.textContent = `
    *, *::before, *::after {
      animation-duration: 0s !important;
      animation-delay: 0s !important;
      transition-duration: 0s !important;
      transition-delay: 0s !important;
      scroll-behavior: auto !important;
    }
  `;
  document.head.appendChild(style);
}
            """.strip(),
            scroll_if_function="""
() => {
  const container = document.querySelector('#app .pjsk-container');
  if (!container) {
    return false;
  }
  const images = Array.from(container.querySelectorAll('img'));
  return images.some((img) => !img.complete || img.naturalWidth <= 0);
}
            """.strip(),
            stability_wait_ms=40,
            extra_wait_seconds=0,
            timeout_seconds=settings.screenshot_timeout_seconds,
        )
        return await self.capture(job)

    @staticmethod
    def _build_profile_url(
        template: str,
        *,
        server: str,
        game_id: str,
        token: str,
    ) -> str:
        formatted = template.format(server=server, game_id=game_id, token=token)
        split = urlsplit(formatted)
        query = []
        has_token = False
        for key, value in parse_qsl(split.query, keep_blank_values=True):
            if key == "token":
                has_token = True
                if value == "":
                    continue
            query.append((key, value))
        if token and not has_token:
            query.append(("token", token))
        return urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                urlencode(query),
                split.fragment,
            )
        )

    async def capture_story(self, event_id: int) -> bytes:
        settings = get_settings()
        urls = [
            f"{base.rstrip('/')}/story/event/{event_id}/?mode=screenshot"
            for base in settings.site_bases
        ]
        job = ScreenshotJob(
            kind="活动剧情",
            urls=urls,
            viewport=_build_viewport(settings.character_viewport_width),
            device_scale_factor=self._quality_scale_factor(settings.screenshot_quality),
            full_page=True,
            capture_mode="full_page",
            user_agent=_MOBILE_USER_AGENT,
            wait_until="domcontentloaded",
            wait_selector="main",
            prepare_script="""
() => {
  const nav = document.querySelector('body > main > nav');
  if (nav instanceof HTMLElement) {
    nav.style.display = 'none';
  }
  if (!document.head || document.getElementById('__moesekai_capture_style__')) {
    return;
  }
  const style = document.createElement('style');
  style.id = '__moesekai_capture_style__';
  style.textContent = `
    *, *::before, *::after {
      animation-duration: 0s !important;
      animation-delay: 0s !important;
      transition-duration: 0s !important;
      transition-delay: 0s !important;
      scroll-behavior: auto !important;
    }
  `;
  document.head.appendChild(style);
}
            """.strip(),
            wait_function="""
() => {
  const bodyText = document.body?.innerText || '';
  if (bodyText.includes('正在加载') || bodyText.includes('加载中')) {
    return false;
  }
  const root = document.querySelector('main > div:nth-of-type(2)');
  if (!(root instanceof HTMLElement)) {
    return false;
  }
  const content = Array.from(root.children).find((element) => element.tagName !== 'ASIDE');
  if (!(content instanceof HTMLElement)) {
    return false;
  }
  if (content.getBoundingClientRect().height < 480) {
    return false;
  }
  const headings = Array.from(content.querySelectorAll('h1, h2, h3'))
    .map((element) => (element.textContent || '').trim());
  if (!headings.some((text) => text.includes('活动概要'))) {
    return false;
  }
  if (!headings.some((text) => text.includes('章节列表'))) {
    return false;
  }
  const episodeCards = Array.from(content.querySelectorAll('a[href]')).filter((element) => {
    const href = element.getAttribute('href') || '';
    return /\\/story\\/event\\/\\d+\\/\\d+\\/?$/.test(href);
  });
  if (!episodeCards.length) {
    return false;
  }
  const images = Array.from(content.querySelectorAll('img'));
  return images.every((img) => img.complete && img.naturalWidth > 0);
}
            """.strip(),
            scroll_if_function="""
() => {
  const root = document.querySelector('main > div:nth-of-type(2)');
  if (!(root instanceof HTMLElement)) {
    return false;
  }
  const content = Array.from(root.children).find((element) => element.tagName !== 'ASIDE');
  if (!(content instanceof HTMLElement)) {
    return false;
  }
  return Array.from(content.querySelectorAll('img')).some(
    (img) => !img.complete || img.naturalWidth <= 0
  );
}
            """.strip(),
            top_crop_css_pixels=settings.story_top_crop,
            stability_wait_ms=30,
            extra_wait_seconds=0,
            timeout_seconds=settings.screenshot_timeout_seconds,
        )
        return await self.capture(job)

    async def capture_character(self, character_id: int) -> bytes:
        settings = get_settings()
        urls = [
            f"{base.rstrip('/')}/character/{character_id}/?mode=screenshot"
            for base in settings.site_bases
        ]
        job = ScreenshotJob(
            kind="查角色",
            urls=urls,
            viewport=_build_viewport(settings.character_viewport_width),
            device_scale_factor=self._quality_scale_factor(settings.screenshot_quality),
            full_page=True,
            capture_mode="full_page",
            user_agent=_MOBILE_USER_AGENT,
            wait_until="domcontentloaded",
            wait_selector="main",
            prepare_script="""
() => {
  const nav = document.querySelector('body > main > nav');
  if (nav instanceof HTMLElement) {
    nav.style.display = 'none';
  }
  if (!document.head || document.getElementById('__moesekai_capture_style__')) {
    return;
  }
  const style = document.createElement('style');
  style.id = '__moesekai_capture_style__';
  style.textContent = `
    *, *::before, *::after {
      animation-duration: 0s !important;
      animation-delay: 0s !important;
      transition-duration: 0s !important;
      transition-delay: 0s !important;
      scroll-behavior: auto !important;
    }
  `;
  document.head.appendChild(style);
}
            """.strip(),
            wait_function="""
() => {
  const bodyText = document.body?.innerText || '';
  if (bodyText.includes('正在加载角色信息')) {
    return false;
  }
  const root = document.querySelector('main > div:nth-of-type(2)');
  if (!(root instanceof HTMLElement)) {
    return false;
  }
  const content = Array.from(root.children).find((element) => element.tagName !== 'ASIDE');
  if (!(content instanceof HTMLElement)) {
    return false;
  }
  if (content.getBoundingClientRect().height < 320) {
    return false;
  }
  const headings = Array.from(content.querySelectorAll('h1, h2, h3'))
    .map((element) => (element.textContent || '').trim());
  if (!headings.some((text) => text.includes('基本信息'))) {
    return false;
  }
  if (!headings.some((text) => text.includes('个人档案'))) {
    return false;
  }
  if (!headings.some((text) => text.includes('相关卡牌'))) {
    return false;
  }
  const heroImage = Array.from(content.querySelectorAll('img')).find((img) =>
    (img.getAttribute('alt') || '').includes('Character Trim')
  );
  if (!heroImage || !heroImage.complete || heroImage.naturalWidth <= 0) {
    return false;
  }
  const cardLinks = Array.from(content.querySelectorAll('a[href]')).filter((element) => {
    const href = element.getAttribute('href') || '';
    return /\\/cards\\/\\d+\\/?$/.test(href);
  });
  if (!cardLinks.length) {
    return false;
  }
  return cardLinks.some((element) => {
    const rect = element.getBoundingClientRect();
    return rect.width >= 24 && rect.height >= 24;
  });
}
            """.strip(),
            scroll_if_function="""
() => {
  const root = document.querySelector('main > div:nth-of-type(2)');
  if (!(root instanceof HTMLElement)) {
    return false;
  }
  const content = Array.from(root.children).find((element) => element.tagName !== 'ASIDE');
  if (!(content instanceof HTMLElement)) {
    return false;
  }
  const heroImage = Array.from(content.querySelectorAll('img')).find((img) =>
    (img.getAttribute('alt') || '').includes('Character Trim')
  );
  if (!heroImage || !heroImage.complete || heroImage.naturalWidth <= 0) {
    return true;
  }
  const cardLinks = Array.from(content.querySelectorAll('a[href]')).filter((element) => {
    const href = element.getAttribute('href') || '';
    return /\\/cards\\/\\d+\\/?$/.test(href);
  });
  if (!cardLinks.length) {
    return false;
  }
  const hasVisibleCard = cardLinks.some((element) => {
    const rect = element.getBoundingClientRect();
    return rect.width >= 24 && rect.height >= 24;
  });
  if (!hasVisibleCard) {
    return true;
  }
  return Array.from(content.querySelectorAll('a[href*="/cards/"] svg image')).some((img) => {
    const href =
      img.getAttribute('href') ||
      img.getAttributeNS('http://www.w3.org/1999/xlink', 'href') ||
      '';
    return !href;
  });
}
            """.strip(),
            before_capture_script="""
async () => {
  const cardImageUrls = Array.from(
    document.querySelectorAll('a[href*="/cards/"] svg image')
  )
    .map((image) =>
      image.getAttribute('href') ||
      image.getAttributeNS('http://www.w3.org/1999/xlink', 'href') ||
      ''
    )
    .filter((url) => !!url);
  const uniqueUrls = Array.from(new Set(cardImageUrls));
  await Promise.allSettled(
    uniqueUrls.map(
      (url) =>
        new Promise((resolve) => {
          const preloader = new Image();
          let settled = false;
          const finish = () => {
            if (settled) {
              return;
            }
            settled = true;
            resolve(null);
          };
          const timer = window.setTimeout(() => {
            finish();
          }, 1800);
          preloader.onload = () => {
            window.clearTimeout(timer);
            finish();
          };
          preloader.onerror = () => {
            window.clearTimeout(timer);
            finish();
          };
          preloader.src = url;
          if (preloader.complete) {
            window.clearTimeout(timer);
            finish();
          }
        })
    )
  );
}
            """.strip(),
            top_crop_css_pixels=settings.character_top_crop,
            stability_wait_ms=45,
            extra_wait_seconds=0,
            timeout_seconds=settings.screenshot_timeout_seconds,
        )
        return await self.capture(job)


screenshot_service = ScreenshotService()
