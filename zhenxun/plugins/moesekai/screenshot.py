from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from math import ceil, floor
from typing import Any, Literal
from urllib.parse import urlsplit

from nonebot_plugin_htmlrender.browser import get_browser
from PIL import Image

from zhenxun.services.log import logger

from .config import get_settings
from .constants import MODULE_NAME, server_path_prefix

_DEFAULT_VIEWPORT_HEIGHT = 932
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Mobile/15E148 Safari/604.1"
)


def _build_viewport(width: int) -> dict[str, int]:
    return {"width": width, "height": _DEFAULT_VIEWPORT_HEIGHT}


class ScreenshotError(RuntimeError):
    def __init__(self, kind: str, errors: list[str]):
        self.kind = kind
        self.errors = errors
        super().__init__(errors[-1] if errors else f"{kind} 截图失败")

    def to_user_message(self) -> str:
        detail = self.errors[-1] if self.errors else "未知错误"
        return f"{self.kind}截图失败：{detail}"


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
    prepare_script: str | None = None
    before_capture_script: str | None = None
    scroll_if_function: str | None = None
    scroll_through_page: bool = False
    top_crop_css_pixels: int = 0
    stability_wait_ms: int = 0
    extra_wait_seconds: float = 1.5
    timeout_seconds: int = 45


class ScreenshotService:
    def __init__(self) -> None:
        self._site_failures: dict[str, float] = {}
        self._last_success_site: dict[str, str] = {}

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

    @asynccontextmanager
    async def _new_page(
        self,
        *,
        viewport: dict[str, int],
        user_agent: str | None,
        device_scale_factor: float,
    ):
        browser = await get_browser()
        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            device_scale_factor=device_scale_factor,
        )
        page = await context.new_page()
        try:
            yield page
        finally:
            await page.close()
            await context.close()

    async def _capture_once(self, job: ScreenshotJob, url: str) -> bytes:
        timeout_ms = job.timeout_seconds * 1000
        async with self._new_page(
            viewport=job.viewport,
            user_agent=job.user_agent,
            device_scale_factor=job.device_scale_factor,
        ) as page:
            await page.goto(url, wait_until=job.wait_until, timeout=timeout_ms)
            if job.prepare_script:
                await page.evaluate(job.prepare_script)
            if job.required_body_classes:
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
            if job.wait_selector:
                await page.wait_for_selector(job.wait_selector, timeout=timeout_ms)
            should_scroll = job.scroll_through_page
            if job.scroll_if_function:
                should_scroll = should_scroll or bool(
                    await page.evaluate(job.scroll_if_function)
                )
            if should_scroll:
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
            if job.wait_function:
                await page.wait_for_function(job.wait_function, timeout=timeout_ms)
            if job.before_capture_script:
                await page.evaluate(job.before_capture_script)
            await self._wait_for_stability(page, job.stability_wait_ms)
            if job.extra_wait_seconds > 0:
                await asyncio.sleep(job.extra_wait_seconds)
            image = await self._capture_image(page, job)
            if job.top_crop_css_pixels > 0:
                image = self._crop_image_top(
                    image,
                    top_css_pixels=job.top_crop_css_pixels,
                    device_scale_factor=job.device_scale_factor,
                )
            return image

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
        candidates = self._filter_urls(job.kind, job.urls)
        for attempt in range(1, settings.screenshot_retry_times + 1):
            for url in candidates:
                site_key = self._site_key(url)
                try:
                    image = await self._capture_once(job, url)
                    self._last_success_site[job.kind] = site_key
                    return image
                except Exception as exc:
                    detail = (
                        f"第{attempt}次 {url} "
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
                candidates = self._filter_urls(job.kind, job.urls)
        raise ScreenshotError(job.kind, errors)

    async def capture_profile(self, server: str, game_id: str) -> bytes:
        settings = get_settings()
        urls = [
            template.format(
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

    async def capture_ranking(
        self,
        server: str,
        event_id: int | None = None,
    ) -> bytes:
        settings = get_settings()
        if event_id is not None:
            urls = [
                template.format(
                    server=server,
                    server_path=server_path_prefix(server),
                    event_id=event_id,
                )
                for template in settings.ranking_history_screenshot_templates
            ]
            wait_selector = ".header-wrapper"
            wait_function = f"""
() => {{
  const loading = document.querySelector('.loading-overlay');
  const loadingHidden = !loading || loading.classList.contains('hidden');
  const header = document.querySelector('.header-wrapper');
  const tags = Array.from(document.querySelectorAll('.meta-tags .tag'));
  const cards = document.querySelectorAll('.kline-card, .card, .mini-card');
  const hasEventTag = tags.some((tag) => (tag.textContent || '').includes('Event {event_id}'));
  return loadingHidden && !!header && hasEventTag && cards.length > 0;
}}
            """.strip()
            job = ScreenshotJob(
                kind="榜线",
                urls=urls,
                viewport=_build_viewport(settings.ranking_viewport_width),
                device_scale_factor=self._quality_scale_factor(settings.screenshot_quality),
                full_page=True,
                capture_mode="full_page",
                user_agent=_MOBILE_USER_AGENT,
                wait_until="domcontentloaded",
                wait_selector=wait_selector,
                wait_function=wait_function,
                stability_wait_ms=24,
                extra_wait_seconds=0,
                timeout_seconds=settings.screenshot_timeout_seconds,
            )
            return await self.capture(job)

        urls = [
            template.format(server=server, server_path=server_path_prefix(server))
            for template in settings.ranking_screenshot_templates
        ]
        wait_function = """
() => {
  const headerImages = Array.from(document.querySelectorAll('.header-simple img'));
  const rankCards = Array.from(document.querySelectorAll('.rank-card'));
  if (!rankCards.length) {
    return false;
  }
  if (!headerImages.every((img) => img.complete && img.naturalWidth > 0)) {
    return false;
  }
  const chartContainers = rankCards
    .slice(0, 3)
    .map((card) => card.querySelector('.chart-container'))
    .filter(Boolean);
  return chartContainers.every((container) => {
    const canvas = container.querySelector('canvas');
    if (!canvas) {
      return false;
    }
    const containerRect = container.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    return (
      containerRect.width > 0 &&
      containerRect.height > 0 &&
      canvasRect.width >= containerRect.width * 0.85 &&
      canvasRect.height >= containerRect.height * 0.85 &&
      Math.abs(canvasRect.left - containerRect.left) < 24 &&
      Math.abs(canvasRect.top - containerRect.top) < 24
    );
  });
}
        """.strip()
        before_capture_script = """
() => {
  const hiddenLink = document.querySelector('a[aria-hidden="true"][rel*="nofollow"]');
  if (hiddenLink instanceof HTMLElement) {
    hiddenLink.style.display = 'none';
  }
  const echartsGlobal = window.echarts;
  if (!echartsGlobal || typeof echartsGlobal.getInstanceByDom !== 'function') {
    return;
  }
  const targets = Array.from(document.querySelectorAll('#kline-mini, .chart-container'))
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width < 24 || rect.height < 24) {
        return false;
      }
      if (rect.bottom < 0 || rect.top > window.innerHeight + 120) {
        return false;
      }
      const style = window.getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden';
    });
  targets.forEach((element) => {
    const instance = echartsGlobal.getInstanceByDom(element);
    if (instance && typeof instance.resize === 'function') {
      instance.resize();
    }
  });
}
        """.strip()
        job = ScreenshotJob(
            kind="榜线",
            urls=urls,
            viewport=_build_viewport(settings.ranking_viewport_width),
            device_scale_factor=self._quality_scale_factor(settings.screenshot_quality),
            full_page=True,
            capture_mode="full_page",
            user_agent=_MOBILE_USER_AGENT,
            wait_until="domcontentloaded",
            wait_selector=".rank-list",
            wait_function=wait_function,
            before_capture_script=before_capture_script,
            stability_wait_ms=36,
            extra_wait_seconds=0,
            timeout_seconds=settings.screenshot_timeout_seconds,
        )
        return await self.capture(job)

    async def capture_deck(
        self,
        *,
        server: str,
        game_id: str,
        event_id: int,
        music_id: int,
        difficulty: str,
        live_type: str,
    ) -> bytes:
        settings = get_settings()
        urls = [
            (
                f"{base.rstrip('/')}/deck-recommend/?mode=screenshot"
                f"&userId={game_id}&server={server}&deckMode=event"
                f"&eventId={event_id}&musicId={music_id}"
                f"&difficulty={difficulty}&liveType={live_type}"
            )
            for base in settings.site_bases
        ]
        wait_function = """
() => {
  const text = document.body?.innerText || '';
  const hasResult = text.includes('推荐卡组 Top');
  const hasKnownError =
    text.includes('未找到可推荐的卡组') ||
    text.includes('用户数据未找到') ||
    text.includes('公开API未开启') ||
    text.includes('未找到可推荐');
  const calculating = text.includes('计算中...');
  if (calculating) {
    return false;
  }
  if (hasKnownError) {
    return true;
  }
  if (!hasResult) {
    return false;
  }
  const card = document.querySelector(
    '.deck-card, .result-card, [class*="deck-card"], [class*="result-card"]'
  );
  if (!(card instanceof HTMLElement)) {
    return true;
  }
  const rect = card.getBoundingClientRect();
  if (rect.width < 24 || rect.height < 24) {
    return false;
  }
  const images = Array.from(card.querySelectorAll('img')).slice(0, 8);
  return images.every((img) => img.complete && img.naturalWidth > 0);
}
""".strip()
        prepare_script = """
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
    }
  `;
  document.head.appendChild(style);
}
        """.strip()
        job = ScreenshotJob(
            kind="活动组卡",
            urls=urls,
            viewport=_build_viewport(settings.deck_viewport_width),
            device_scale_factor=self._quality_scale_factor(settings.screenshot_quality),
            full_page=True,
            capture_mode="full_page",
            user_agent=_MOBILE_USER_AGENT,
            wait_until="domcontentloaded",
            wait_selector="main",
            wait_function=wait_function,
            prepare_script=prepare_script,
            top_crop_css_pixels=settings.deck_top_crop,
            stability_wait_ms=42,
            extra_wait_seconds=0,
            timeout_seconds=settings.deck_wait_timeout_seconds,
        )
        return await self.capture(job)


screenshot_service = ScreenshotService()
