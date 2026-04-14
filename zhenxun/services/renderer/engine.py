import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
import contextlib
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import time
from typing import Any, ClassVar, cast

import nonebot_plugin_htmlrender.browser as htmlrender_browser
import psutil

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .types import BaseScreenshotEngine


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await cast(Awaitable[Any], value)
    return value


async def _get_browser_instance() -> Any:
    for attr_name in ("get_browser", "get_new_browser"):
        browser_getter = getattr(htmlrender_browser, attr_name, None)
        if callable(browser_getter):
            return await _await_if_needed(browser_getter())
    raise RuntimeError("nonebot_plugin_htmlrender.browser 未提供可用浏览器获取函数。")


async def get_managed_browser() -> Any:
    """返回经过真寻补丁管理的 htmlrender 浏览器实例。"""
    _patch_playwright_env_check_once()
    return await _get_browser_instance()


async def _shutdown_browser_instance() -> None:
    _clear_patched_browser_check_cache()
    for attr_name in (
        "shutdown_htmlrender",
        "shutdown_browser",
        "close_browser",
        "close_htmlrender",
    ):
        shutdown_func = getattr(htmlrender_browser, attr_name, None)
        if callable(shutdown_func):
            try:
                await _await_if_needed(shutdown_func())
            finally:
                with contextlib.suppress(Exception):
                    setattr(htmlrender_browser, "_browser", None)
                with contextlib.suppress(Exception):
                    setattr(htmlrender_browser, "_playwright", None)
                _clear_patched_browser_check_cache()
            return

    browser_obj = getattr(htmlrender_browser, "_browser", None)
    close_func = getattr(browser_obj, "close", None) if browser_obj else None
    if callable(close_func):
        with contextlib.suppress(Exception):
            await _await_if_needed(close_func())

    playwright_obj = getattr(htmlrender_browser, "_playwright", None)
    stop_func = getattr(playwright_obj, "stop", None) if playwright_obj else None
    if callable(stop_func):
        with contextlib.suppress(Exception):
            await _await_if_needed(stop_func())

    with contextlib.suppress(Exception):
        setattr(htmlrender_browser, "_browser", None)
    with contextlib.suppress(Exception):
        setattr(htmlrender_browser, "_playwright", None)
    _clear_patched_browser_check_cache()

    if callable(close_func) or callable(stop_func):
        return

    logger.debug(
        "未找到 htmlrender 浏览器关闭函数，跳过 shutdown。",
        "PlaywrightEngine",
    )


def _clear_patched_browser_check_cache() -> None:
    state = getattr(htmlrender_browser, "_zhenxun_check_once_state", None)
    if isinstance(state, dict):
        state["checked"] = False
        state["result"] = None


def _is_browser_closed_error(exc: Exception) -> bool:
    if exc.__class__.__name__ == "TargetClosedError":
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "target closed",
            "browser has been closed",
            "connection closed",
            "handler is closed",
            "writeunixtransport closed=true",
            "unable to perform operation on <writeunixtransport closed=true",
        )
    )


def _patch_playwright_env_check_once() -> None:
    if getattr(htmlrender_browser, "_zhenxun_check_once_patched", False):
        return

    original_check: Callable[..., Awaitable[Any]] | None = None
    check_attr_name = ""
    for attr_name in ("check_playwright_env", "check_browser_env"):
        candidate = getattr(htmlrender_browser, attr_name, None)
        if callable(candidate):
            original_check = cast(Callable[..., Awaitable[Any]], candidate)
            check_attr_name = attr_name
            break

    if original_check is None:
        logger.debug(
            "未找到 htmlrender 环境检查函数，跳过 check_once 补丁。",
            "PlaywrightEngine",
        )
        setattr(htmlrender_browser, "_zhenxun_check_once_patched", True)
        return

    check_func = original_check
    state: dict[str, Any] = {"checked": False, "result": None}
    check_lock: asyncio.Lock | None = None

    def _is_browser_usable(browser_obj: Any) -> bool:
        if browser_obj is None:
            return False
        is_connected = getattr(browser_obj, "is_connected", None)
        if callable(is_connected):
            with contextlib.suppress(Exception):
                return bool(is_connected())
        # 无法判断连接状态时，保守认为可用
        return True

    def _get_current_browser_candidate() -> Any:
        fallback = getattr(htmlrender_browser, "_browser", None)
        if _is_browser_usable(fallback):
            state["result"] = fallback
            return fallback
        return None

    async def _check_once(**kwargs: Any) -> Any:
        nonlocal check_lock
        if state["checked"]:
            cached_browser = _get_current_browser_candidate()
            if cached_browser is not None:
                return cached_browser
            state["checked"] = False
            state["result"] = None

        if check_lock is None:
            check_lock = asyncio.Lock()
        async with check_lock:
            if state["checked"]:
                cached_browser = _get_current_browser_candidate()
                if cached_browser is not None:
                    return cached_browser
                state["checked"] = False
                state["result"] = None

            result = await check_func(**kwargs)
            state["checked"] = True
            state["result"] = result

            browser = _get_current_browser_candidate()
            if browser is not None:
                return browser
            return result

    setattr(htmlrender_browser, check_attr_name, _check_once)
    setattr(htmlrender_browser, "_zhenxun_check_once_state", state)
    setattr(htmlrender_browser, "_zhenxun_check_once_patched", True)


class PlaywrightEngine(BaseScreenshotEngine):
    """使用 nonebot-plugin-htmlrender 实现的截图引擎。"""

    _BROWSER_PROCESS_MARKERS: ClassVar[tuple[str, ...]] = (
        "chrome",
        "chromium",
        "msedge",
        "playwright",
        "headless_shell",
    )
    _MAX_CONCURRENT_RENDER = 2
    _CONTEXT_POOL_SIZE = 2
    _PREWARM_CONTEXT_COUNT = 1
    _SET_CONTENT_WAIT_UNTIL = "domcontentloaded"
    _READY_STATE_TIMEOUT_MS = 2_000
    _IMAGE_READY_TIMEOUT_MS = 1_800
    _FONT_READY_TIMEOUT_MS = 1_200
    _FULL_PAGE_VIEWPORT_MAX_HEIGHT = 4_096
    _FULL_PAGE_VIEWPORT_MAX_WIDTH = 4_096
    _CLIP_PADDING_DEFAULT = 0
    _DISABLE_ANIMATIONS_STYLE = """
        *, *::before, *::after {
            animation: none !important;
            transition: none !important;
            caret-color: transparent !important;
            scroll-behavior: auto !important;
        }
    """
    _RECENT_RESULT_TTL_SECONDS = 1.5
    _RECENT_RESULT_MAX_ITEMS = 64
    _RSS_RECYCLE_MIN_THRESHOLD_BYTES = 700 * 1024 * 1024
    _RSS_RECYCLE_MAX_THRESHOLD_BYTES = 1200 * 1024 * 1024
    _RSS_RECYCLE_HEADROOM_BYTES = 224 * 1024 * 1024
    _RECYCLE_COOLDOWN_SECONDS = 300
    _RECYCLE_CHECK_EVERY = 8
    _IDLE_CHECK_INTERVAL_SECONDS = 15
    _IDLE_RECYCLE_SECONDS = 180
    _POOL_UNSAFE_OPTION_KEYS: ClassVar[set[str]] = {
        "device_scale_factor",
        "color_scheme",
        "extra_http_headers",
        "forced_colors",
        "geolocation",
        "has_touch",
        "http_credentials",
        "ignore_https_errors",
        "is_mobile",
        "java_script_enabled",
        "locale",
        "permissions",
        "proxy",
        "record_har_content",
        "record_har_mode",
        "record_har_omit_content",
        "record_har_path",
        "record_video_dir",
        "record_video_size",
        "reduced_motion",
        "screen",
        "service_workers",
        "storage_state",
        "timezone_id",
        "user_agent",
    }

    def __init__(self):
        _patch_playwright_env_check_once()
        self._render_semaphore = asyncio.Semaphore(self._MAX_CONCURRENT_RENDER)
        self._debug_console_log = bool(Config.get_config("UI", "DEBUG_MODE", False))
        self._memory_diag_enabled = bool(
            Config.get_config("UI", "MEMORY_DIAG_ENABLED", False)
        )
        self._memory_diag_interval_seconds = max(
            10,
            self._coerce_non_negative_int(
                Config.get_config("UI", "MEMORY_DIAG_INTERVAL_SECONDS", 600),
                600,
            ),
        )
        self._idle_recycle_enabled = bool(
            Config.get_config("UI", "UI_RENDERER_IDLE_RECYCLE_ENABLED", False)
        )
        self._prewarm_enabled = bool(
            Config.get_config("UI", "UI_RENDERER_PREWARM_ENABLED", True)
        )
        self._state_lock = asyncio.Lock()
        self._recycle_lock = asyncio.Lock()
        self._active_renders = 0
        self._render_count = 0
        self._recycle_pending = False
        self._last_recycle_at = 0.0
        self._last_render_finished_at = time.monotonic()
        self._rss_baseline_bytes: int | None = None
        self._recent_results: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._inflight_tasks: dict[str, asyncio.Task[bytes]] = {}
        self._context_pool: asyncio.LifoQueue[Any] = asyncio.LifoQueue()
        self._all_contexts: set[Any] = set()
        self._idle_recycle_task: asyncio.Task[None] | None = None
        self._memory_diag_task: asyncio.Task[None] | None = None
        self._initialized = False
        self._closing = False
        self._process = psutil.Process()

    @staticmethod
    def _normalize_base_url(path: Path) -> str:
        base_url = path.absolute().as_uri()
        if not base_url.endswith("/"):
            base_url += "/"
        return base_url

    @staticmethod
    def _build_render_key(
        html: str, template_path: str, render_options: dict[str, Any]
    ) -> str:
        options_json = json.dumps(render_options, sort_keys=True, default=str)
        hasher = hashlib.sha256()
        hasher.update(template_path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(options_json.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(html.encode("utf-8", errors="ignore"))
        return hasher.hexdigest()

    def _cleanup_recent_results_nolock(self, now: float) -> None:
        while self._recent_results:
            expire_at, _ = next(iter(self._recent_results.values()))
            if expire_at > now:
                break
            self._recent_results.popitem(last=False)
        while len(self._recent_results) > self._RECENT_RESULT_MAX_ITEMS:
            self._recent_results.popitem(last=False)

    def _get_recent_result_nolock(self, key: str, now: float) -> bytes | None:
        entry = self._recent_results.get(key)
        if not entry:
            return None
        expire_at, result = entry
        if expire_at <= now:
            self._recent_results.pop(key, None)
            return None
        self._recent_results.move_to_end(key)
        return result

    def _get_total_rss(self) -> int | None:
        try:
            total_rss = self._process.memory_info().rss
            for child in self._process.children(recursive=True):
                with contextlib.suppress(Exception):
                    total_rss += child.memory_info().rss
            return total_rss
        except Exception:
            return None

    @classmethod
    def _is_browser_process(cls, process: psutil.Process) -> bool:
        parts: list[str] = []
        with contextlib.suppress(Exception):
            parts.append(process.name().lower())
        with contextlib.suppress(Exception):
            parts.extend(arg.lower() for arg in process.cmdline())
        return any(
            marker in part
            for part in parts
            for marker in cls._BROWSER_PROCESS_MARKERS
        )

    def _get_process_tree_memory_metrics(self) -> tuple[int | None, int, int, int]:
        try:
            main_rss = self._process.memory_info().rss
        except Exception:
            return None, 0, 0, 0

        child_rss_total = 0
        browser_rss_total = 0
        browser_process_count = 0
        with contextlib.suppress(Exception):
            for child in self._process.children(recursive=True):
                try:
                    child_rss = child.memory_info().rss
                except Exception:
                    continue
                child_rss_total += child_rss
                if self._is_browser_process(child):
                    browser_process_count += 1
                    browser_rss_total += child_rss

        return main_rss, child_rss_total, browser_rss_total, browser_process_count

    @staticmethod
    def _get_dev_shm_usage() -> tuple[int, int] | None:
        shm_path = Path("/dev/shm")
        if not shm_path.exists():
            return None
        with contextlib.suppress(OSError):
            usage = shutil.disk_usage(shm_path)
            return usage.used, usage.total
        return None

    @staticmethod
    def _get_system_shmem_bytes() -> int | None:
        meminfo_path = Path("/proc/meminfo")
        if not meminfo_path.exists():
            return None
        try:
            with meminfo_path.open(encoding="utf-8") as meminfo_file:
                for line in meminfo_file:
                    if not line.startswith("Shmem:"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
                    return None
        except OSError:
            return None
        return None

    @staticmethod
    def _format_bytes(value: int | None) -> str:
        if value is None:
            return "n/a"

        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                if unit == "B":
                    return f"{int(size)}{unit}"
                return f"{size:.1f}{unit}"
            size /= 1024
        return "n/a"

    async def _log_memory_diagnostics(self, source: str) -> None:
        async with self._state_lock:
            if self._closing:
                return
            context_pool_size = self._context_pool.qsize()
            all_contexts = len(self._all_contexts)
            inflight_tasks = len(self._inflight_tasks)
            active_renders = self._active_renders
            render_count = self._render_count

        main_rss, child_rss, browser_rss, browser_process_count = (
            self._get_process_tree_memory_metrics()
        )
        dev_shm_usage = self._get_dev_shm_usage()
        system_shmem = self._get_system_shmem_bytes()

        dev_shm_text = "n/a"
        if dev_shm_usage is not None:
            dev_shm_used, dev_shm_total = dev_shm_usage
            dev_shm_text = (
                f"{self._format_bytes(dev_shm_used)}/"
                f"{self._format_bytes(dev_shm_total)}"
            )

        logger.info(
            "内存诊断({source}): rss(main={main} child={child} "
            "browser={browser} browser_proc={browser_proc}) "
            "shm(/dev/shm={dev_shm} shmem={system_shmem}) "
            "renderer(pool={pool} contexts={contexts} inflight={inflight} "
            "active={active} renders={renders})".format(
                source=source,
                main=self._format_bytes(main_rss),
                child=self._format_bytes(child_rss),
                browser=self._format_bytes(browser_rss),
                browser_proc=browser_process_count,
                dev_shm=dev_shm_text,
                system_shmem=self._format_bytes(system_shmem),
                pool=context_pool_size,
                contexts=all_contexts,
                inflight=inflight_tasks,
                active=active_renders,
                renders=render_count,
            ),
            "PlaywrightEngine",
        )

    async def _memory_diag_loop(self) -> None:
        while True:
            await asyncio.sleep(self._memory_diag_interval_seconds)
            await self._log_memory_diagnostics("periodic")

    def _update_rss_baseline_nolock(self, current_rss: int) -> None:
        if self._rss_baseline_bytes is None or current_rss < self._rss_baseline_bytes:
            self._rss_baseline_bytes = current_rss
            return

        threshold = self._rss_baseline_bytes + self._RSS_RECYCLE_HEADROOM_BYTES * 2
        if current_rss >= threshold:
            self._rss_baseline_bytes = int(
                self._rss_baseline_bytes * 0.9 + current_rss * 0.1
            )

    def _get_dynamic_threshold_nolock(self, current_rss: int) -> int:
        self._update_rss_baseline_nolock(current_rss)
        baseline = self._rss_baseline_bytes or current_rss
        dynamic = baseline + self._RSS_RECYCLE_HEADROOM_BYTES
        dynamic = max(dynamic, self._RSS_RECYCLE_MIN_THRESHOLD_BYTES)
        dynamic = min(dynamic, self._RSS_RECYCLE_MAX_THRESHOLD_BYTES)
        return dynamic

    def _mark_recycle_if_needed_nolock(self, now: float) -> None:
        if self._render_count % self._RECYCLE_CHECK_EVERY != 0:
            return
        if now - self._last_recycle_at < self._RECYCLE_COOLDOWN_SECONDS:
            return
        current_rss = self._get_total_rss()
        if current_rss is None:
            return
        threshold = self._get_dynamic_threshold_nolock(current_rss)
        if current_rss >= threshold:
            self._recycle_pending = True

    async def initialize(self) -> None:
        should_prewarm = False
        idle_recycle_enabled = False
        async with self._state_lock:
            if self._initialized:
                return
            self._initialized = True
            self._closing = False
            self._last_render_finished_at = time.monotonic()
            if current_rss := self._get_total_rss():
                self._rss_baseline_bytes = current_rss
            idle_recycle_enabled = self._idle_recycle_enabled
            if idle_recycle_enabled:
                self._idle_recycle_task = asyncio.create_task(self._idle_recycle_loop())
            if self._memory_diag_enabled:
                self._memory_diag_task = asyncio.create_task(self._memory_diag_loop())
            should_prewarm = self._prewarm_enabled

        if self._memory_diag_enabled:
            logger.info(
                f"截图引擎内存诊断已开启，输出间隔 {self._memory_diag_interval_seconds} 秒。",
                "PlaywrightEngine",
            )

        if idle_recycle_enabled:
            logger.info("截图引擎空闲回收已启用。", "PlaywrightEngine")
        else:
            logger.info(
                "截图引擎空闲回收已禁用，仅保留渲染中回收与异常恢复回收。",
                "PlaywrightEngine",
            )

        if should_prewarm:
            await self._prewarm_browser_and_pool()
        else:
            logger.info(
                "截图引擎启动预热已禁用，将在首次渲染时按需拉起浏览器。",
                "PlaywrightEngine",
            )

        if self._memory_diag_enabled:
            await self._log_memory_diagnostics("startup")

    async def close(self) -> None:
        idle_task: asyncio.Task[None] | None = None
        memory_diag_task: asyncio.Task[None] | None = None
        async with self._state_lock:
            self._closing = True
            self._initialized = False
            idle_task = self._idle_recycle_task
            self._idle_recycle_task = None
            memory_diag_task = self._memory_diag_task
            self._memory_diag_task = None
            for task in self._inflight_tasks.values():
                task.cancel()
            self._inflight_tasks.clear()
            self._recent_results.clear()
            self._recycle_pending = False

        if idle_task:
            idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await idle_task
        if memory_diag_task:
            memory_diag_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await memory_diag_task

        await self._dispose_context_pool()
        await _shutdown_browser_instance()

    async def _on_render_begin(self) -> None:
        async with self._state_lock:
            self._active_renders += 1

    async def _on_render_end(self) -> None:
        should_recycle = False
        async with self._state_lock:
            self._active_renders = max(0, self._active_renders - 1)
            self._render_count += 1
            now = time.monotonic()
            self._last_render_finished_at = now
            self._mark_recycle_if_needed_nolock(now)
            if self._recycle_pending and self._active_renders == 0:
                self._recycle_pending = False
                self._last_recycle_at = now
                should_recycle = True
        if should_recycle:
            await self._recycle_browser("active")

    @staticmethod
    def _build_page_options(
        render_options: dict[str, Any], *, pooled: bool
    ) -> dict[str, Any]:
        options = render_options.copy()
        options.pop("wait", None)
        options.pop("type", None)
        options.pop("quality", None)
        options.pop("scale", None)
        options.pop("screenshot_scale", None)
        options.pop("screenshot_timeout", None)
        options.pop("full_page", None)
        options.pop("clip_selector", None)
        options.pop("clip_padding", None)
        options.pop("disable_animations", None)
        if pooled:
            options.pop("base_url", None)
        return options

    @staticmethod
    def _build_screenshot_options(render_options: dict[str, Any]) -> dict[str, Any]:
        scale = render_options.get("screenshot_scale", render_options.get("scale"))
        if scale not in ("css", "device"):
            scale = None
        return {
            "full_page": bool(render_options.get("full_page", True)),
            "type": render_options.get("type", "png"),
            "quality": render_options.get("quality"),
            "scale": scale,
            "timeout": render_options.get("screenshot_timeout", 30_000),
        }

    @staticmethod
    def _get_wait_timeout(render_options: dict[str, Any]) -> int:
        wait = render_options.get("wait", 0)
        if isinstance(wait, int):
            return max(wait, 0)
        return 0

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int = 0) -> int:
        try:
            value_int = int(value)
        except (TypeError, ValueError):
            return default
        return value_int if value_int >= 0 else default

    @classmethod
    def _should_use_context_pool(cls, render_options: dict[str, Any]) -> bool:
        for key in cls._POOL_UNSAFE_OPTION_KEYS:
            if key in render_options:
                return False
        return True

    async def _render_with_page(
        self,
        page: Any,
        html: str,
        template_path: str,
        render_options: dict[str, Any],
    ) -> bytes:
        if self._debug_console_log:
            page.on("console", lambda msg: logger.debug(f"浏览器控制台: {msg.text}"))
        await page.goto(template_path, wait_until="domcontentloaded")
        await page.set_content(html, wait_until=self._SET_CONTENT_WAIT_UNTIL)
        if bool(render_options.get("disable_animations", False)):
            await self._disable_page_animations(page)
        await self._wait_for_visual_stability(page)
        if wait_ms := self._get_wait_timeout(render_options):
            await page.wait_for_timeout(wait_ms)
        screenshot_options = self._build_screenshot_options(render_options)
        clip_selector = render_options.get("clip_selector")
        if isinstance(clip_selector, str) and clip_selector.strip():
            if image_bytes := await self._capture_by_selector(
                page,
                selector=clip_selector.strip(),
                screenshot_options=screenshot_options,
                clip_padding=self._coerce_non_negative_int(
                    render_options.get("clip_padding"),
                    self._CLIP_PADDING_DEFAULT,
                ),
            ):
                return image_bytes
        await self._optimize_full_page_capture(page, screenshot_options)
        return await page.screenshot(**screenshot_options)

    async def _disable_page_animations(self, page: Any) -> None:
        with contextlib.suppress(Exception):
            await page.add_style_tag(content=self._DISABLE_ANIMATIONS_STYLE)

    async def _capture_by_selector(
        self,
        page: Any,
        selector: str,
        screenshot_options: dict[str, Any],
        clip_padding: int,
    ) -> bytes | None:
        element = await page.query_selector(selector)
        if element is None:
            return None

        element_screenshot_options = {
            "type": screenshot_options.get("type", "png"),
            "quality": screenshot_options.get("quality"),
            "timeout": screenshot_options.get("timeout", 30_000),
        }
        with contextlib.suppress(Exception):
            box = await element.bounding_box()
            if box and clip_padding > 0:
                viewport = page.viewport_size or {}
                width = int(viewport.get("width") or 0)
                if width > 0:
                    target_height = int(box["y"] + box["height"] + clip_padding)
                    current_height = int(viewport.get("height") or 0)
                    if target_height > current_height:
                        await page.set_viewport_size(
                            {
                                "width": width,
                                "height": min(
                                    target_height,
                                    self._FULL_PAGE_VIEWPORT_MAX_HEIGHT,
                                ),
                            }
                        )

        if clip_padding <= 0:
            return await element.screenshot(**element_screenshot_options)

        with contextlib.suppress(Exception):
            clip_box = await element.bounding_box()
            if clip_box is None:
                return await element.screenshot(**element_screenshot_options)
            clip = {
                "x": max(clip_box["x"] - clip_padding, 0),
                "y": max(clip_box["y"] - clip_padding, 0),
                "width": clip_box["width"] + clip_padding * 2,
                "height": clip_box["height"] + clip_padding * 2,
            }
            page_options = {
                "type": screenshot_options.get("type", "png"),
                "quality": screenshot_options.get("quality"),
                "timeout": screenshot_options.get("timeout", 30_000),
                "clip": clip,
            }
            return await page.screenshot(**page_options)

        return await element.screenshot(**element_screenshot_options)

    async def _wait_for_visual_stability(self, page: Any) -> None:
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "() => document.readyState === 'complete'",
                timeout=self._READY_STATE_TIMEOUT_MS,
            )

        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "() => Array.from(document.images || []).every(img => img.complete)",
                timeout=self._IMAGE_READY_TIMEOUT_MS,
            )

        with contextlib.suppress(Exception):
            await page.evaluate(
                """
                async (timeoutMs) => {
                    if (!document.fonts || !document.fonts.ready) return;
                    await Promise.race([
                        document.fonts.ready,
                        new Promise(resolve => setTimeout(resolve, timeoutMs)),
                    ]);
                }
                """,
                self._FONT_READY_TIMEOUT_MS,
            )

    async def _optimize_full_page_capture(
        self, page: Any, screenshot_options: dict[str, Any]
    ) -> None:
        if not bool(screenshot_options.get("full_page")):
            return

        viewport = page.viewport_size or {}
        width = viewport.get("width")
        height = viewport.get("height")
        if (
            not isinstance(width, int)
            or width <= 0
            or not isinstance(height, int)
            or height <= 0
        ):
            return

        with contextlib.suppress(Exception):
            content_size = await page.evaluate(
                """
                () => {
                    const body = document.body;
                    const doc = document.documentElement;
                    const bodyWidth = body ? Math.max(
                        body.scrollWidth,
                        body.offsetWidth,
                        body.clientWidth
                    ) : 0;
                    const bodyHeight = body ? Math.max(
                        body.scrollHeight,
                        body.offsetHeight,
                        body.clientHeight
                    ) : 0;
                    const docWidth = doc ? Math.max(
                        doc.scrollWidth,
                        doc.offsetWidth,
                        doc.clientWidth
                    ) : 0;
                    const docHeight = doc ? Math.max(
                        doc.scrollHeight,
                        doc.offsetHeight,
                        doc.clientHeight
                    ) : 0;
                    return {
                        width: Math.ceil(Math.max(bodyWidth, docWidth, 10)),
                        height: Math.ceil(Math.max(bodyHeight, docHeight, 10)),
                    };
                }
                """
            )
            if not isinstance(content_size, dict):
                return

            content_width = content_size.get("width")
            content_height = content_size.get("height")
            if not isinstance(content_width, int) or not isinstance(
                content_height, int
            ):
                return
            if (
                content_width < 10
                or content_height < 10
                or content_width > self._FULL_PAGE_VIEWPORT_MAX_WIDTH
                or content_height > self._FULL_PAGE_VIEWPORT_MAX_HEIGHT
            ):
                return

            target_width = max(width, content_width)
            target_height = max(height, content_height)
            await page.set_viewport_size(
                {"width": target_width, "height": target_height}
            )
            screenshot_options["full_page"] = False

    async def _render_with_oneoff_page(
        self,
        html: str,
        template_path: str,
        render_options: dict[str, Any],
    ) -> bytes:
        browser = await _get_browser_instance()
        page_options = self._build_page_options(render_options, pooled=False)
        page = await browser.new_page(**page_options)
        try:
            return await self._render_with_page(
                page, html, template_path, render_options
            )
        finally:
            with contextlib.suppress(Exception):
                await page.close()

    async def _acquire_context(self) -> Any:
        try:
            return self._context_pool.get_nowait()
        except asyncio.QueueEmpty:
            pass

        async with self._state_lock:
            if len(self._all_contexts) < self._CONTEXT_POOL_SIZE:
                create_new = True
            else:
                create_new = False

        if create_new:
            browser = await _get_browser_instance()
            context = await browser.new_context(
                viewport={"width": 800, "height": 10},
                device_scale_factor=2,
            )
            async with self._state_lock:
                self._all_contexts.add(context)
            return context
        return await self._context_pool.get()

    async def _release_context(self, context: Any, broken: bool = False) -> None:
        if broken:
            await self._discard_context(context)
            return

        async with self._state_lock:
            if self._closing:
                broken = True
            elif context not in self._all_contexts:
                broken = True
            else:
                self._context_pool.put_nowait(context)
                return

        if broken:
            await self._discard_context(context)

    async def _discard_context(self, context: Any) -> None:
        async with self._state_lock:
            existed = context in self._all_contexts
            if existed:
                self._all_contexts.remove(context)
        if existed:
            with contextlib.suppress(Exception):
                await context.close()

    async def _dispose_context_pool(self) -> None:
        async with self._state_lock:
            contexts = list(self._all_contexts)
            self._all_contexts.clear()
            while True:
                try:
                    self._context_pool.get_nowait()
                except asyncio.QueueEmpty:
                    break

        for context in contexts:
            with contextlib.suppress(Exception):
                await context.close()

    async def _render_with_context_pool(
        self,
        html: str,
        template_path: str,
        render_options: dict[str, Any],
    ) -> bytes:
        context = await self._acquire_context()
        page = None
        broken = False
        try:
            page = await context.new_page()
            page_options = self._build_page_options(render_options, pooled=True)
            viewport = page_options.get("viewport")
            if isinstance(viewport, dict):
                width = viewport.get("width")
                height = viewport.get("height")
                if isinstance(width, int) and isinstance(height, int):
                    await page.set_viewport_size({"width": width, "height": height})
            return await self._render_with_page(
                page, html, template_path, render_options
            )
        except Exception:
            broken = True
            raise
        finally:
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.close()
            await self._release_context(context, broken=broken)

    async def _render_html(
        self,
        html: str,
        template_path: str,
        render_options: dict[str, Any],
        retry_on_browser_reset: bool = True,
    ) -> bytes:
        try:
            if self._should_use_context_pool(render_options):
                return await self._render_with_context_pool(
                    html, template_path, render_options
                )
            return await self._render_with_oneoff_page(
                html, template_path, render_options
            )
        except Exception as e:
            if not retry_on_browser_reset or not _is_browser_closed_error(e):
                raise

            logger.warning(
                "截图引擎检测到浏览器实例已关闭，正在重建后重试一次。",
                "PlaywrightEngine",
                e=e,
            )
            await self._recycle_browser("recover", prewarm_retry=False)
            return await self._render_html(
                html,
                template_path,
                render_options,
                retry_on_browser_reset=False,
            )

    async def _recycle_browser(self, reason: str, *, prewarm_retry: bool = True) -> None:
        async with self._recycle_lock:
            try:
                await self._dispose_context_pool()
                await _shutdown_browser_instance()
                current_rss = self._get_total_rss()
                if current_rss is not None:
                    self._update_rss_baseline_nolock(current_rss)
                await self._prewarm_browser_and_pool(
                    retry_on_browser_reset=prewarm_retry
                )
                logger.debug(
                    f"截图引擎触发回收({reason})，已重建浏览器实例。",
                    "PlaywrightEngine",
                )
                if self._memory_diag_enabled:
                    await self._log_memory_diagnostics(f"recycle:{reason}")
            except Exception as e:
                logger.warning("浏览器实例重建失败。", "PlaywrightEngine", e=e)

    async def _prewarm_browser_and_pool(
        self, *, retry_on_browser_reset: bool = True
    ) -> None:
        if self._closing:
            return
        try:
            browser = await _get_browser_instance()
        except Exception as e:
            logger.warning("截图引擎浏览器预热失败。", "PlaywrightEngine", e=e)
            return

        for _ in range(self._PREWARM_CONTEXT_COUNT):
            async with self._state_lock:
                if self._closing:
                    return
                if len(self._all_contexts) >= self._CONTEXT_POOL_SIZE:
                    return
                if self._context_pool.qsize() >= self._PREWARM_CONTEXT_COUNT:
                    return

            context = None
            try:
                context = await browser.new_context(
                    viewport={"width": 800, "height": 10},
                    device_scale_factor=2,
                )
                page = await context.new_page()
                await page.goto("about:blank", wait_until="domcontentloaded")
                await page.set_content(
                    "<html><body></body></html>",
                    wait_until="domcontentloaded",
                )
                await page.close()
            except Exception as e:
                if context is not None:
                    with contextlib.suppress(Exception):
                        await context.close()
                if retry_on_browser_reset and _is_browser_closed_error(e):
                    logger.warning(
                        "截图引擎上下文预热命中已关闭浏览器，正在重建后重试一次。",
                        "PlaywrightEngine",
                        e=e,
                    )
                    await self._recycle_browser("prewarm", prewarm_retry=False)
                    return
                logger.warning("截图引擎上下文预热失败。", "PlaywrightEngine", e=e)
                return

            async with self._state_lock:
                if self._closing:
                    with contextlib.suppress(Exception):
                        await context.close()
                    return
                if context in self._all_contexts:
                    continue
                self._all_contexts.add(context)
                self._context_pool.put_nowait(context)

    async def _idle_recycle_loop(self) -> None:
        while True:
            await asyncio.sleep(self._IDLE_CHECK_INTERVAL_SECONDS)
            should_recycle = False
            async with self._state_lock:
                if self._closing:
                    return
                now = time.monotonic()
                if self._active_renders > 0:
                    continue
                if now - self._last_recycle_at < self._RECYCLE_COOLDOWN_SECONDS:
                    continue
                idle_for = now - self._last_render_finished_at
                if idle_for < self._IDLE_RECYCLE_SECONDS:
                    continue
                current_rss = self._get_total_rss()
                if current_rss is None:
                    continue
                threshold = self._get_dynamic_threshold_nolock(current_rss)
                if current_rss >= threshold:
                    self._last_recycle_at = now
                    should_recycle = True
            if should_recycle:
                await self._recycle_browser("idle")

    async def _render_and_store_result(
        self,
        key: str,
        html: str,
        base_url_for_browser: str,
        render_options: dict[str, Any],
    ) -> bytes:
        async with self._render_semaphore:
            await self._on_render_begin()
            try:
                result = await self._render_html(
                    html,
                    base_url_for_browser,
                    render_options,
                )
            finally:
                await self._on_render_end()

        async with self._state_lock:
            now = time.monotonic()
            self._recent_results[key] = (
                now + self._RECENT_RESULT_TTL_SECONDS,
                result,
            )
            self._recent_results.move_to_end(key)
            self._cleanup_recent_results_nolock(now)
        return result

    async def render(self, html: str, base_url_path: Path, **render_options) -> bytes:
        base_url_for_browser = self._normalize_base_url(base_url_path)

        final_render_options = {
            "viewport": {"width": 800, "height": 10},
            **render_options,
            "base_url": base_url_for_browser,
        }

        dedupe_key = self._build_render_key(
            html,
            base_url_for_browser,
            final_render_options,
        )

        owner = False
        async with self._state_lock:
            now = time.monotonic()
            self._cleanup_recent_results_nolock(now)
            if cached_result := self._get_recent_result_nolock(dedupe_key, now):
                return cached_result

            task = self._inflight_tasks.get(dedupe_key)
            if task is None:
                task = asyncio.create_task(
                    self._render_and_store_result(
                        dedupe_key,
                        html,
                        base_url_for_browser,
                        final_render_options,
                    )
                )
                self._inflight_tasks[dedupe_key] = task
                owner = True

        try:
            return await task
        finally:
            if owner:
                async with self._state_lock:
                    if self._inflight_tasks.get(dedupe_key) is task:
                        self._inflight_tasks.pop(dedupe_key, None)


class EngineManager:
    """
    引擎管理器，负责加载和提供具体的截图引擎实例。
    未来可在此处根据 Config 读取不同的驱动配置。
    """

    def __init__(self):
        self._engine_class: type[BaseScreenshotEngine] = PlaywrightEngine
        self._instance: BaseScreenshotEngine | None = None

    async def get_engine(self) -> BaseScreenshotEngine:
        if not self._instance:
            self._instance = self._engine_class()
            await self._instance.initialize()
        return self._instance

    async def close(self):
        if self._instance:
            await self._instance.close()
            self._instance = None


engine_manager = EngineManager()
