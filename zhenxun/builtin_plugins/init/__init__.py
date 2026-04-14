from pathlib import Path

import nonebot
from nonebot.adapters import Bot
from nonebot.exception import NetworkError

from zhenxun.models.group_console import GroupConsole
from zhenxun.services import onebot_transport
from zhenxun.services.cache import CacheException
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

nonebot.load_plugins(str(Path(__file__).parent.resolve()))

try:
    from .__init_cache import register_cache_types
except CacheException as e:
    raise SystemError(f"ERROR：{e}")

driver = nonebot.get_driver()


@PriorityLifecycle.on_startup(priority=5)
async def _register_cache_types():
    register_cache_types()
    logger.info("缓存类型注册完成")


@driver.on_bot_connect
async def _sync_group_auth_on_bot_connect(bot: Bot):
    """将bot已存在的群组添加群认证

    参数:
        bot: Bot
    """
    if PlatformUtils.get_platform(bot) != "qq":
        return
    # OneBot V11 在连接钩子触发时可能还没把 WS 放进 adapter.connections。
    # 这里先短暂等待控制面就绪，避免连接刚建立就退化成错误的 HTTP fallback。
    if not await onebot_transport.wait_until_ready(bot, timeout=2.0, interval=0.1):
        onebot_transport.note_unavailable(
            bot,
            "跳过连接期群认证同步",
            "连接已建立，但控制面尚未就绪。",
        )
        return
    logger.debug(f"更新Bot: {bot.self_id} 的群认证...")
    try:
        group_list, _ = await PlatformUtils.get_group_list(bot)
    except NetworkError as exc:
        # 这里只吞掉连接期可预期的控制面异常；其他异常继续抛出，
        # 避免把真实逻辑错误误判成断连抖动。
        logger.error(
            f"更新Bot: {bot.self_id} 的群认证失败",
            "初始化",
            session=str(bot.self_id),
            e=exc,
        )
        return
    db_group_list = await GroupConsole.all().values_list("group_id", flat=True)
    create_list = []
    update_id = []
    for group in group_list:
        if group.group_id not in db_group_list:
            group.group_flag = 1
            create_list.append(group)
        else:
            update_id.append(group.group_id)
    if create_list:
        await GroupConsole.bulk_create(create_list, 10)
    else:
        await GroupConsole.filter(group_id__in=update_id).update(group_flag=1)
    logger.debug(
        f"更新Bot: {bot.self_id} 的群认证完成，共创建 {len(create_list)} 条数据，"
        f"共修改 {len(update_id)} 条数据..."
    )
