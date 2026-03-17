import asyncio
from dataclasses import dataclass
import datetime
from pathlib import Path
import random
import traceback

from bilibili_api import Credential as BilibiliCredential
from bilibili_api import live as bilibili_live_module
from bilibili_api import user as bilibili_user_module
from nonebot_plugin_htmlrender.browser import get_browser

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except Exception:  # pragma: no cover - 运行环境缺少 playwright 时仅作兜底
    PlaywrightTimeoutError = TimeoutError

try:
    from playwright_stealth import Stealth
except ImportError:  # pragma: no cover - 依赖为可选
    Stealth = None

from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .config import (
    AVATAR_CACHE_DIR,
    BANGUMI_COVER_CACHE_DIR,
    base_config,
    get_credential,
)

BORDER_PATH = IMAGE_PATH / "border"
BORDER_PATH.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://api.bilibili.com"
DYNAMIC_SCREENSHOT_URL = "https://www.bilibili.com/opus/{dynamic_id}"
DYNAMIC_SCREENSHOT_SELECTORS = (
    "#app > div.opus-detail > div.bili-opus-view",
    ".opus-detail .bili-opus-view",
    ".bili-opus-view",
    ".opus-modules",
    ".card",
)
DYNAMIC_SCREENSHOT_RISK_SELECTORS = (
    "#risk-captcha-app",
    ".geetest_panel",
    ".geetest_panel_box",
)
DYNAMIC_SCREENSHOT_LOGIN_SELECTORS = (
    ".bili-mini-login-container",
    ".login-panel",
    ".login-scan-box",
    ".unlogin-popover",
)
DYNAMIC_SCREENSHOT_RISK_KEYWORDS = (
    "验证码",
    "安全验证",
    "完成验证",
    "异常访问",
    "访问受限",
)
DYNAMIC_SCREENSHOT_LOGIN_KEYWORDS = (
    "请先登录",
    "登录后",
    "扫码登录",
)
DYNAMIC_SCREENSHOT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_dynamic_screenshot_semaphore: asyncio.Semaphore | None = None
_dynamic_screenshot_semaphore_size = 0
_dynamic_screenshot_stealth_logged: bool | None = None
_dynamic_screenshot_non_retryable_failures = {"404"}


@dataclass(slots=True)
class DynamicScreenshotResult:
    image: bytes | None
    failure_type: str | None = None
    failure_detail: str | None = None
    attempt_count: int = 0
    page_url: str | None = None
    page_title: str | None = None


def _get_dynamic_screenshot_config_int(
    key: str, default: int, minimum: int = 0
) -> int:
    value = base_config.get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            f"B站动态截图配置无效: {key}={value!r}, 将使用默认值 {default}"
        )
        return default
    return max(minimum, parsed)


def _get_dynamic_screenshot_semaphore() -> asyncio.Semaphore:
    global _dynamic_screenshot_semaphore, _dynamic_screenshot_semaphore_size

    concurrency = _get_dynamic_screenshot_config_int(
        "DYNAMIC_SCREENSHOT_CONCURRENCY", 1, minimum=1
    )
    if (
        _dynamic_screenshot_semaphore is None
        or _dynamic_screenshot_semaphore_size != concurrency
    ):
        _dynamic_screenshot_semaphore = asyncio.Semaphore(concurrency)
        _dynamic_screenshot_semaphore_size = concurrency
        logger.debug(f"B站动态截图并发限制已更新: {concurrency}")
    return _dynamic_screenshot_semaphore


def _build_bilibili_cookies() -> list[dict[str, str]]:
    credential = get_credential()
    if not credential:
        return []

    try:
        cookies = credential.get_cookies()
    except Exception as e:
        logger.warning(f"获取 B站截图 cookies 失败: {e}")
        return []

    return [
        {
            "domain": ".bilibili.com",
            "name": name,
            "path": "/",
            "value": value,
        }
        for name, value in cookies.items()
        if value
    ]


async def _apply_dynamic_screenshot_stealth(context) -> None:
    global _dynamic_screenshot_stealth_logged

    if Stealth is None:
        if _dynamic_screenshot_stealth_logged is None:
            logger.debug("B站动态截图未启用 playwright-stealth: 依赖不可用")
            _dynamic_screenshot_stealth_logged = False
        return

    try:
        await Stealth().apply_stealth_async(context)
        if _dynamic_screenshot_stealth_logged is not True:
            logger.info("B站动态截图已启用 playwright-stealth")
            _dynamic_screenshot_stealth_logged = True
    except Exception as e:
        if _dynamic_screenshot_stealth_logged is None:
            logger.warning(
                f"B站动态截图启用 playwright-stealth 失败，将继续普通模式: {e}"
            )
        _dynamic_screenshot_stealth_logged = False


async def _page_contains_selector(page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            if await page.query_selector(selector):
                return True
        except Exception:
            continue
    return False


async def _get_page_text_excerpt(page) -> str:
    try:
        content = await page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 1200) : ''"
        )
    except Exception:
        return ""
    return content if isinstance(content, str) else ""


async def _classify_dynamic_page(page) -> tuple[str | None, str | None, str, str]:
    page_url = page.url

    try:
        page_title = await page.title()
    except Exception:
        page_title = ""

    text_excerpt = await _get_page_text_excerpt(page)

    if (
        page_url.rstrip("/") == "https://www.bilibili.com/404"
        or page_url.endswith("/404")
        or "页面走丢" in page_title
        or page_title.strip() == "404"
    ):
        return "404", "动态页返回 404", page_title, page_url

    if await _page_contains_selector(page, DYNAMIC_SCREENSHOT_RISK_SELECTORS) or any(
        keyword in page_title or keyword in text_excerpt
        for keyword in DYNAMIC_SCREENSHOT_RISK_KEYWORDS
    ):
        return "风控页", "检测到验证码或安全验证页面", page_title, page_url

    if (
        "passport.bilibili.com" in page_url
        or await _page_contains_selector(page, DYNAMIC_SCREENSHOT_LOGIN_SELECTORS)
        or any(
            keyword in page_title or keyword in text_excerpt
            for keyword in DYNAMIC_SCREENSHOT_LOGIN_KEYWORDS
        )
    ):
        return "未知页面结构", "疑似登录页或未登录中间页", page_title, page_url

    return None, None, page_title, page_url


async def _cleanup_dynamic_page(page) -> None:
    try:
        await page.evaluate(
            """
            () => {
                const selectors = [
                    "#biliMainHeader",
                    ".bili-header",
                    ".bili-header__bar",
                    ".fixed-header",
                    ".bili-mini-login-container",
                    ".login-panel",
                    ".unlogin-popover"
                ];
                selectors.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => {
                        if (el) {
                            el.style.display = "none";
                        }
                    });
                });
            }
            """
        )
    except Exception as e:
        logger.debug(f"动态截图页面清理失败，将继续原页面截图: {e}")


async def _find_dynamic_screenshot_target(page, timeout_ms: int):
    for selector in DYNAMIC_SCREENSHOT_SELECTORS:
        try:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return element, selector
        except Exception:
            continue

    wait_timeout = max(1500, timeout_ms // max(len(DYNAMIC_SCREENSHOT_SELECTORS), 1))
    for selector in DYNAMIC_SCREENSHOT_SELECTORS:
        try:
            element = await page.wait_for_selector(
                selector, timeout=wait_timeout, state="visible"
            )
            if element:
                return element, selector
        except PlaywrightTimeoutError:
            continue
    return None, None


def _calc_dynamic_retry_delay(base_delay: int, attempt_index: int) -> float:
    if attempt_index <= 1:
        delay = base_delay
    else:
        delay = base_delay + 3 * (attempt_index - 1)
    return delay + random.uniform(0, 0.6)


def _build_dynamic_failure_result(
    attempt_count: int,
    failure_type: str,
    failure_detail: str | None,
    page_title: str | None,
    page_url: str | None,
) -> DynamicScreenshotResult:
    return DynamicScreenshotResult(
        image=None,
        failure_type=failure_type,
        failure_detail=failure_detail,
        attempt_count=attempt_count,
        page_title=page_title,
        page_url=page_url,
    )


async def _capture_dynamic_screenshot_once(
    dynamic_id: int, timeout_ms: int
) -> DynamicScreenshotResult:
    request_url = DYNAMIC_SCREENSHOT_URL.format(dynamic_id=dynamic_id)
    page_url = request_url
    page_title = ""
    browser = await get_browser()
    if not browser:
        return _build_dynamic_failure_result(
            0,
            "未知页面结构",
            "浏览器实例不可用",
            page_title,
            page_url,
        )
    context = None
    page = None

    try:
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1400},
            user_agent=DYNAMIC_SCREENSHOT_USER_AGENT,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            device_scale_factor=2,
        )
        await _apply_dynamic_screenshot_stealth(context)

        cookies = _build_bilibili_cookies()
        if cookies:
            await context.add_cookies(cookies)  # type: ignore[arg-type]

        page = await context.new_page()
        await page.goto(request_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page_url = page.url
        try:
            await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
        except PlaywrightTimeoutError:
            logger.debug(
                f"动态页面等待 networkidle 超时，继续尝试定位主体: ID={dynamic_id}"
            )

        failure_type, failure_detail, page_title, page_url = (
            await _classify_dynamic_page(page)
        )
        if failure_type:
            return _build_dynamic_failure_result(
                0, failure_type, failure_detail, page_title, page_url
            )

        await _cleanup_dynamic_page(page)
        target, selector = await _find_dynamic_screenshot_target(page, timeout_ms)
        if not target:
            failure_type, failure_detail, page_title, page_url = (
                await _classify_dynamic_page(page)
            )
            if failure_type:
                return _build_dynamic_failure_result(
                    0, failure_type, failure_detail, page_title, page_url
                )
            return _build_dynamic_failure_result(
                0,
                "选择器超时",
                "未找到动态主体选择器",
                page_title,
                page_url,
            )

        try:
            await target.scroll_into_view_if_needed()
        except Exception:
            pass

        await asyncio.sleep(0.3)
        image = await target.screenshot(
            type="png",
            timeout=timeout_ms,
            animations="disabled",
        )
        logger.debug(f"动态截图命中选择器: ID={dynamic_id}, selector={selector}")
        return DynamicScreenshotResult(
            image=image,
            attempt_count=0,
            page_title=page_title,
            page_url=page_url,
        )
    except PlaywrightTimeoutError as e:
        return _build_dynamic_failure_result(
            0,
            "选择器超时",
            str(e),
            page_title,
            page_url,
        )
    except Exception as e:
        logger.debug(
            f"动态截图发生未预期异常: ID={dynamic_id}, 错误={type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return _build_dynamic_failure_result(
            0,
            "未知页面结构",
            f"{type(e).__name__}: {e}",
            page_title,
            page_url,
        )
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def get_pic(url: str) -> bytes:
    """获取图像"""
    return (await AsyncHttpx.get(url, timeout=10)).content


async def get_cached_avatar(uid: int, avatar_url: str) -> Path | None:
    """获取缓存的用户头像路径，如果不存在则下载"""
    if not avatar_url or not uid:
        return None
    cached_path = AVATAR_CACHE_DIR / f"{uid}.png"
    if cached_path.exists():
        logger.debug(f"头像缓存命中: UID {uid}")
        return cached_path

    logger.debug(f"头像缓存未命中，正在下载: UID {uid}")
    try:
        if await AsyncHttpx.download_file(avatar_url, cached_path):
            return cached_path
    except Exception as e:
        logger.error(f"下载头像失败 UID: {uid}, URL: {avatar_url}", e=e)
    return None


async def get_cached_bangumi_cover(season_or_ep_id: int, cover_url: str) -> Path | None:
    """获取缓存的番剧或剧集封面路径，如果不存在则下载"""
    if not cover_url or not season_or_ep_id:
        return None
    cached_path = BANGUMI_COVER_CACHE_DIR / f"{season_or_ep_id}.png"
    if cached_path.exists():
        logger.debug(f"番剧封面缓存命中: ID {season_or_ep_id}")
        return cached_path

    logger.debug(f"番剧封面缓存未命中，正在下载: ID {season_or_ep_id}")
    try:
        if await AsyncHttpx.download_file(cover_url, cached_path):
            return cached_path
    except Exception as e:
        logger.error(f"下载番剧封面失败 ID: {season_or_ep_id}, URL: {cover_url}", e=e)
    return None


async def get_videos(uid: int, auth: BilibiliCredential | None = None, **kwargs):
    """获取用户投搞视频信息"""
    credential = auth or get_credential()
    user_instance = bilibili_user_module.User(uid=uid, credential=credential)
    return await user_instance.get_videos(**kwargs)


async def get_user_card(
    mid: int, photo: bool = False, auth: BilibiliCredential | None = None, **kwargs
):
    """获取用户卡片信息"""
    credential = auth or get_credential()
    user_instance = bilibili_user_module.User(uid=mid, credential=credential)
    user_info = await user_instance.get_user_info()
    return user_info


async def get_user_dynamics(
    uid: int,
    offset: str = "",
    auth: BilibiliCredential | None = None,
    **kwargs,
):
    """获取指定用户历史动态（使用新版API）"""
    credential = auth or get_credential()
    user_instance = bilibili_user_module.User(uid=uid, credential=credential)
    return await user_instance.get_dynamics_new(offset=offset, **kwargs)


async def get_room_info_by_id(
    live_id: int, auth: BilibiliCredential | None = None, **kwargs
):
    """根据房间号获取指定直播间信息"""
    credential = auth or get_credential()
    liveroom_instance = bilibili_live_module.LiveRoom(
        room_display_id=live_id, credential=credential
    )
    return await liveroom_instance.get_room_info()


async def get_dynamic_screenshot(dynamic_id: int) -> DynamicScreenshotResult:
    retries = _get_dynamic_screenshot_config_int(
        "DYNAMIC_SCREENSHOT_RETRIES", 3, minimum=1
    )
    base_delay = _get_dynamic_screenshot_config_int(
        "DYNAMIC_SCREENSHOT_RETRY_DELAY_SECONDS", 2, minimum=0
    )
    timeout_seconds = _get_dynamic_screenshot_config_int(
        "DYNAMIC_SCREENSHOT_TIMEOUT_SECONDS", 20, minimum=5
    )
    timeout_ms = timeout_seconds * 1000

    async with _get_dynamic_screenshot_semaphore():
        result = DynamicScreenshotResult(image=None, attempt_count=0)
        for attempt in range(1, retries + 1):
            result = await _capture_dynamic_screenshot_once(dynamic_id, timeout_ms)
            result.attempt_count = attempt

            if result.image:
                return result

            detail = f", 详情={result.failure_detail}" if result.failure_detail else ""
            logger.warning(
                f"动态截图获取失败: ID={dynamic_id}, "
                f"原因={result.failure_type or '未知页面结构'}, "
                f"尝试={attempt}/{retries}, "
                f"页面标题={result.page_title or 'N/A'}, "
                f"页面地址={result.page_url or 'N/A'}"
                f"{detail}"
            )

            if (
                attempt >= retries
                or result.failure_type in _dynamic_screenshot_non_retryable_failures
            ):
                break

            delay = _calc_dynamic_retry_delay(base_delay, attempt)
            await asyncio.sleep(delay)

        return result


def calc_time_total(t: float):
    """计算人类可读格式的总时间"""
    if not isinstance(t, int | float):
        try:
            t = float(t)
        except (ValueError, TypeError):
            return "时间格式错误"

    t_int = int(t * 1000)
    if t_int < 5000:
        return f"{t_int} 毫秒"
    timedelta_obj = datetime.timedelta(seconds=t_int // 1000)
    day = timedelta_obj.days
    hour, mint, sec = tuple(
        int(n) for n in str(timedelta_obj).split(",")[-1].split(":")
    )
    total = ""
    if day:
        total += f"{day} 天 "
    if hour:
        total += f"{hour} 小时 "
    if mint:
        total += f"{mint} 分钟 "
    if sec and not day and not hour:
        total += f"{sec} 秒 "
    return total.strip()
