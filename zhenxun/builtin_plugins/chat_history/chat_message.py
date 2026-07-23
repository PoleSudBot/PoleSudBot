import asyncio
from dataclasses import dataclass
from datetime import datetime
import time

from nonebot import get_driver, on, on_message
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_uninfo import Uninfo
from tortoise import timezone

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.models.chat_history import ChatHistory
from zhenxun.services.chat_history import (
    build_incoming_record,
    create_outgoing_record,
    enrich_forward_segments,
    register_pending_history_flusher,
)
from zhenxun.services.log import logger
from zhenxun.services.message_load import is_overloaded, should_pause_tasks
from zhenxun.utils.enum import PluginType

__plugin_meta__ = PluginMetadata(
    name="消息存储",
    description="消息存储，被动存储群消息",
    usage="",
    extra=PluginExtraData(
        author="HibiKier",
        version="0.2",
        plugin_type=PluginType.HIDDEN,
        configs=[
            RegisterConfig(
                module="chat_history",
                key="FLAG",
                value=True,
                help="是否开启消息自从存储",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="chat_history",
                key="QUEUE_MAX_SIZE",
                value=5000,
                help="消息记录队列最大长度",
                default_value=5000,
                type=int,
            ),
            RegisterConfig(
                module="chat_history",
                key="FLUSH_INTERVAL_SECONDS",
                value=5,
                help="消息记录批量写入间隔，单位秒",
                default_value=5,
                type=int,
            ),
            RegisterConfig(
                module="chat_history",
                key="DROP_LOG_INTERVAL_SECONDS",
                value=10,
                help="消息记录队列丢弃日志限流间隔，单位秒",
                default_value=10,
                type=int,
            )
        ],
    ).to_dict(),
)


def _positive_int_config(key: str, default: int) -> int:
    """读取正整数配置，异常值回退默认值，避免队列初始化被错误配置打断。"""
    value = Config.get_config("chat_history", key, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class ChatHistoryQueueState:
    """暴露消息记录队列的最小运行状态，供诊断和后续WebUI复用。"""

    queue_size: int
    queue_max_size: int
    enqueue_dropped: int
    overload_dropped: int
    flush_failures: int
    requeue_dropped: int


def rule(message: UniMsg) -> bool:
    return bool(Config.get_config("chat_history", "FLAG") and message)


chat_history = on_message(rule=rule, priority=1, block=False)
message_sent = on("message_sent", priority=1, block=False)

_QUEUE_MAX_SIZE = _positive_int_config("QUEUE_MAX_SIZE", 5000)
_FLUSH_INTERVAL_SECONDS = _positive_int_config("FLUSH_INTERVAL_SECONDS", 5)
_DROP_LOG_INTERVAL = float(_positive_int_config("DROP_LOG_INTERVAL_SECONDS", 10))
_HISTORY_QUEUE: asyncio.Queue[ChatHistory] = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
_FLUSH_LOCK = asyncio.Lock()
_DROP_COUNT = 0
_OVERLOAD_DROP_COUNT = 0
_FLUSH_FAILURE_COUNT = 0
_REQUEUE_DROP_COUNT = 0
_LAST_DROP_LOG = 0.0


def get_history_queue_state() -> ChatHistoryQueueState:
    """读取队列状态快照，不暴露可变队列对象。"""
    return ChatHistoryQueueState(
        queue_size=_HISTORY_QUEUE.qsize(),
        queue_max_size=_HISTORY_QUEUE.maxsize,
        enqueue_dropped=_DROP_COUNT,
        overload_dropped=_OVERLOAD_DROP_COUNT,
        flush_failures=_FLUSH_FAILURE_COUNT,
        requeue_dropped=_REQUEUE_DROP_COUNT,
    )


def _drain_history_queue() -> list[ChatHistory]:
    """一次性取出当前待写批次，避免持有队列期间阻塞新消息入队。"""
    message_list: list[ChatHistory] = []
    while True:
        try:
            message_list.append(_HISTORY_QUEUE.get_nowait())
        except asyncio.QueueEmpty:
            break
    return message_list


def _requeue_history_batch(message_list: list[ChatHistory]) -> int:
    """写库失败后尽量把批次放回队列，队列已满时只丢无法回填的尾部。"""
    requeued = 0
    for message in message_list:
        try:
            _HISTORY_QUEUE.put_nowait(message)
            requeued += 1
        except asyncio.QueueFull:
            break
    return requeued


async def _flush_history_queue() -> int:
    """批量写入聊天记录；失败时回填队列，避免短暂数据库故障造成整批丢失。"""
    global _FLUSH_FAILURE_COUNT, _REQUEUE_DROP_COUNT
    async with _FLUSH_LOCK:
        message_list = _drain_history_queue()
        if not message_list:
            return 0
        try:
            await ChatHistory.bulk_create(message_list)
        except Exception as e:
            _FLUSH_FAILURE_COUNT += 1
            requeued = _requeue_history_batch(message_list)
            dropped = len(message_list) - requeued
            _REQUEUE_DROP_COUNT += dropped
            logger.warning(
                (
                    "存储聊天记录失败，已尝试回填队列 "
                    f"batch={len(message_list)} requeued={requeued} "
                    f"dropped={dropped}"
                ),
                "chat_history",
                e=e,
            )
            for _ in message_list:
                _HISTORY_QUEUE.task_done()
            return 0
        for _ in message_list:
            _HISTORY_QUEUE.task_done()
        logger.debug(f"批量添加聊天记录 {len(message_list)} 条", "定时任务")
        return len(message_list)


register_pending_history_flusher(_flush_history_queue)


@chat_history.handle()
async def handle_chat_history(
    bot: Bot,
    message: UniMsg,
    session: Uninfo,
    event: Event,
) -> None:
    global _DROP_COUNT, _LAST_DROP_LOG, _OVERLOAD_DROP_COUNT
    if is_overloaded():
        _OVERLOAD_DROP_COUNT += 1
        return
    now = time.time()
    try:
        raw_message = await enrich_forward_segments(
            bot,
            getattr(event, "original_message", None) or message,
        )
        # 构造轻量结构化历史，具体写库仍由队列批量完成。
        _HISTORY_QUEUE.put_nowait(
            build_incoming_record(
                message,
                session,
                event,
                raw_message=raw_message,
            )
        )
    except asyncio.QueueFull:
        _DROP_COUNT += 1
        if now - _LAST_DROP_LOG > _DROP_LOG_INTERVAL:
            _LAST_DROP_LOG = now
            logger.warning(
                f"chat_history queue full, dropped {_DROP_COUNT} items",
                "chat_history",
            )


@message_sent.handle()
async def handle_message_sent(bot: Bot, event: Event) -> None:
    """使用NapCat message_sent补全Bot出站消息的真实平台时间。"""
    if not Config.get_config("chat_history", "FLAG"):
        return
    message_id = getattr(event, "message_id", None)
    message = getattr(event, "message", None)
    if message_id is None or message is None:
        return
    try:
        create_time = datetime.fromtimestamp(
            int(getattr(event, "time")),
            timezone.get_default_timezone(),
        )
    except (TypeError, ValueError, OSError):
        logger.warning("message_sent缺少有效平台时间，保留API hook回退", "chat_history")
        return

    message_type = str(getattr(event, "message_type", "") or "") or None
    group_id = getattr(event, "group_id", None)
    user_id = (
        getattr(event, "target_id", None) or getattr(event, "user_id", None)
        if message_type == "private"
        else bot.self_id
    )
    raw_message = getattr(event, "original_message", None) or message
    try:
        # 事件与API hook共享幂等入口，事件先后顺序不会制造重复记录。
        await create_outgoing_record(
            bot,
            user_id=str(user_id) if user_id is not None else None,
            group_id=str(group_id) if group_id is not None else None,
            message_type=message_type,
            message=message,
            result={"message_id": message_id},
            create_time=create_time,
            platform_event=True,
            raw_message=raw_message,
        )
    except Exception as e:
        logger.warning("记录message_sent平台事件失败", "chat_history", e=e)


@scheduler.scheduled_job(
    "interval",
    seconds=_FLUSH_INTERVAL_SECONDS,
)
async def _():
    if should_pause_tasks():
        return
    await _flush_history_queue()


driver = get_driver()


@driver.on_shutdown
async def _flush_chat_history_on_shutdown() -> None:
    await _flush_history_queue()
