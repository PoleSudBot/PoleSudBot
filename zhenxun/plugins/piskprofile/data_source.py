from zhenxun.services.log import logger
from zhenxun.utils.browser import AsyncPlaywright

MODULE_NAME = "piskprofile"

# iPhone 6/7/8 尺寸的移动端 viewport
_MOBILE_VIEWPORT = {"width": 414, "height": 896}
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Mobile/15E148 Safari/604.1"
)


async def take_pjsk_screenshot(
    server: str, pjsk_id: str, token: str = ""
) -> bytes | str:
    """使用内置 Playwright 截取 PJSK 个人档案页面

    参数:
        server: 区服 (jp / cn / tw)
        pjsk_id: 游戏内ID
        token: 鉴权 Token

    返回:
        bytes: 成功时返回图片二进制数据 (JPEG)
        str: 失败时返回错误提示文字
    """
    profile_url = f"https://sekaiprofile.exmeaning.com/profile/{server}/{pjsk_id}"
    if token:
        profile_url += f"?token={token}"

    try:
        async with AsyncPlaywright.new_page(
            viewport=_MOBILE_VIEWPORT,
            user_agent=_MOBILE_USER_AGENT,
            device_scale_factor=2,
        ) as page:
            await page.goto(
                profile_url,
                timeout=30000,
                wait_until="domcontentloaded",
            )
            # 等待 SPA 渲染完成：页面会依次给 body 添加
            # page-fully-loaded → animation-finished 类名
            await page.wait_for_selector(
                "body.animation-finished",
                timeout=25000,
            )

            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=85,
                full_page=True,
            )

        logger.info(
            f"PJSK截图成功: server={server}, id={pjsk_id}, "
            f"size={len(screenshot_bytes)} bytes",
            MODULE_NAME,
        )
        return screenshot_bytes

    except Exception as e:
        error_name = type(e).__name__
        logger.error(f"PJSK截图失败 ({error_name}): {e}", MODULE_NAME)
        if "timeout" in str(e).lower() or "Timeout" in error_name:
            return "世界正在维护或网络超时，请稍后再试"
        return "截图失败，请稍后再试"
