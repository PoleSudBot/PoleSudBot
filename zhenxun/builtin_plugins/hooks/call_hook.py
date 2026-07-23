import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from nonebot.adapters import Bot, Event, Message
from nonebot.matcher import Matcher, current_event
from nonebot_plugin_alconna.matcher import AlconnaMatcher

from zhenxun.configs.config import Config
from zhenxun.models.bot_message_store import BotMessageStore
from zhenxun.services.chat_history import create_outgoing_record
from zhenxun.services.log import logger
from zhenxun.utils.auto_withdraw import record_triggered_bot_message
from zhenxun.utils.enum import BotSentType
from zhenxun.utils.log_sanitizer import sanitize_for_logging
from zhenxun.utils.manager.message_manager import MessageManager
from zhenxun.utils.platform import PlatformUtils

LOG_COMMAND = "MessageHook"
SEND_MESSAGE_APIS = {
    "send_msg",
    "send_group_msg",
    "send_private_msg",
    "send_group_forward_msg",
    "send_private_forward_msg",
}


@dataclass(frozen=True)
class SendSourceContext:
    """保存一次发送可继承的最小消息来源上下文。"""

    task: asyncio.Task | None
    source_message_id: str
    source_user_id: str | None


_matcher_task_context: ContextVar[asyncio.Task | None] = ContextVar(
    "_zx_matcher_task_context",
    default=None,
)
_current_source_context: ContextVar[SendSourceContext | None] = ContextVar(
    "_zx_current_send_source_context",
    default=None,
)
_api_source_context: ContextVar[SendSourceContext | None] = ContextVar(
    "_zx_api_send_source_context",
    default=None,
)
_queued_source_context: ContextVar[SendSourceContext | None] = ContextVar(
    "_zx_queued_send_source_context",
    default=None,
)
_ORIGINAL_CALL_API = Bot.call_api
_ORIGINAL_ENSURE_CONTEXT = Matcher.ensure_context
_ORIGINAL_ALCONNA_ENSURE_CONTEXT = AlconnaMatcher.ensure_context


def _extract_result_message_id(result: Any) -> str | None:
    """从发送 API 返回值中提取可撤回的消息 ID。"""
    if isinstance(result, dict):
        message_id = result.get("message_id")
    else:
        message_id = getattr(result, "message_id", None)
    return str(message_id) if message_id is not None else None


def _get_running_task() -> asyncio.Task | None:
    """获取当前 asyncio 任务；没有运行循环时返回空。"""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _build_source_context(
    event: Event, task: asyncio.Task | None = None
) -> SendSourceContext | None:
    """获取消息事件的最小来源上下文，用于自动撤回和手动权限记录。"""
    if event.get_type() != "message":
        return None
    message_id = getattr(event, "message_id", None)
    if message_id is None:
        return None
    user_id = getattr(event, "user_id", None)
    return SendSourceContext(
        task=task or _get_running_task(),
        source_message_id=str(message_id),
        source_user_id=str(user_id) if user_id is not None else None,
    )


def get_current_send_source_context() -> SendSourceContext | None:
    """只允许当前处理任务导出来源上下文，避免后台任务误继承 matcher 事件。"""
    context = _current_source_context.get()
    if context and context.task is _get_running_task():
        return context
    matcher_task = _matcher_task_context.get()
    if matcher_task is None or matcher_task is not _get_running_task():
        return None
    try:
        event = current_event.get()
    except LookupError:
        return None
    return _build_source_context(event, matcher_task)


def _set_queued_source_context(context: SendSourceContext | None):
    """给发送队列 worker 临时注入入队时捕获的最小来源上下文。"""
    return _queued_source_context.set(context)


def _reset_queued_source_context(token) -> None:
    """恢复发送队列 worker 原有的来源上下文。"""
    _queued_source_context.reset(token)


def _resolve_source_context() -> SendSourceContext | None:
    """优先使用父协程注入的来源，否则读取兼容的队列来源。"""
    api_context = _api_source_context.get()
    if api_context is not None:
        return api_context
    queued_context = _queued_source_context.get()
    if queued_context is not None:
        return queued_context
    return get_current_send_source_context()


def _resolve_call_api_source_context(api: str) -> SendSourceContext | None:
    """在 call_api 父协程中解析来源，再传给 called_api 子任务。"""
    if api not in SEND_MESSAGE_APIS:
        return None
    queued_context = _queued_source_context.get()
    if queued_context is not None:
        return queued_context
    return get_current_send_source_context()


def _resolve_manual_withdraw_user_id(
    api: str, data: dict[str, Any], source_context: SendSourceContext | None
) -> str | None:
    """根据发送 API 解析可手动撤回该消息的用户 ID。"""
    user_id = data.get("user_id")
    if api == "send_private_msg":
        return str(user_id) if user_id is not None else None
    if user_id is not None:
        return str(user_id)
    if api == "send_group_msg" or (
        api == "send_msg" and data.get("message_type") == "group"
    ):
        return source_context.source_user_id if source_context else None
    return None


def _normalize_bot_message_record(
    api: str, data: dict[str, Any], source_context: SendSourceContext | None
) -> tuple[str | None, str | None, str, Message | str] | None:
    """把普通发送 API 归一成消息账本所需字段，forward API 本轮不入库。"""
    message: Message | str = data.get("message", "")
    if api == "send_group_msg":
        group_id = data.get("group_id")
        return (
            source_context.source_user_id if source_context else None,
            str(group_id) if group_id is not None else None,
            "group",
            message,
        )
    if api == "send_private_msg":
        user_id = data.get("user_id")
        return (
            str(user_id) if user_id is not None else None,
            None,
            "private",
            message,
        )
    if api == "send_msg":
        user_id = data.get("user_id")
        group_id = data.get("group_id")
        message_type = str(data.get("message_type") or "")
        if message_type == "group" and user_id is None and source_context:
            user_id = source_context.source_user_id
        return (
            str(user_id) if user_id is not None else None,
            str(group_id) if group_id is not None else None,
            message_type,
            message,
        )
    return None


def _bind_send_source_context(event: Event):
    """测试辅助：在当前任务中绑定发送来源。"""
    _current_source_context.set(_build_source_context(event))


def _clear_send_source_context():
    """清理当前任务的发送来源。"""
    _current_source_context.set(None)


def _build_ensure_context_with_task(original_ensure_context):
    """基于原始 ensure_context 构造带任务标记的上下文包装。"""

    @contextmanager
    def _ensure_context_with_task(self: Matcher, bot: Bot, event: Event):
        """标记真正运行 matcher handler 的任务，避免后台 task 继承消息来源。"""
        token = _matcher_task_context.set(_get_running_task())
        try:
            with original_ensure_context(self, bot, event):
                yield
        finally:
            _matcher_task_context.reset(token)

    return _ensure_context_with_task


_ensure_context_with_task = _build_ensure_context_with_task(_ORIGINAL_ENSURE_CONTEXT)
_alconna_ensure_context_with_task = _build_ensure_context_with_task(
    _ORIGINAL_ALCONNA_ENSURE_CONTEXT
)


async def _call_api_with_source_context(self: Bot, api: str, **data: Any) -> Any:
    """在父协程注入来源上下文，让 called_api 的 anyio 子任务可见。"""
    source_context = _resolve_call_api_source_context(api)
    token = _api_source_context.set(source_context)
    try:
        return await _ORIGINAL_CALL_API(self, api, **data)
    finally:
        _api_source_context.reset(token)


def _patch_runtime_context() -> None:
    """注册运行期上下文补丁，保持发送钩子能跨框架子任务读取来源。"""
    if not getattr(Matcher.ensure_context, "__zx_withdraw_context_patch__", False):
        setattr(
            _ensure_context_with_task,
            "__zx_withdraw_context_patch__",
            True,
        )
        Matcher.ensure_context = _ensure_context_with_task
    # AlconnaMatcher 覆写了基类 ensure_context，必须单独 patch 才能捕获 alc 命令来源。
    if not getattr(
        AlconnaMatcher.ensure_context, "__zx_withdraw_context_patch__", False
    ):
        setattr(
            _alconna_ensure_context_with_task,
            "__zx_withdraw_context_patch__",
            True,
        )
        AlconnaMatcher.ensure_context = _alconna_ensure_context_with_task
    if not getattr(Bot.call_api, "__zx_withdraw_source_patch__", False):
        setattr(
            _call_api_with_source_context,
            "__zx_withdraw_source_patch__",
            True,
        )
        Bot.call_api = _call_api_with_source_context


async def _record_triggered_message(
    bot: Bot, result: Any, source_context: SendSourceContext | None
) -> str | None:
    """记录当前消息触发出的机器人回复，源消息已撤回时立即清理。"""
    message_id = _extract_result_message_id(result)
    if not message_id or not source_context:
        return message_id
    source_message_id = source_context.source_message_id
    if await record_triggered_bot_message(bot, source_message_id, message_id):
        logger.debug(
            (
                "收集自动撤回关联，"
                f"source_msg_id: {source_message_id}, bot_msg_id: {message_id}"
            ),
            LOG_COMMAND,
        )
    return message_id


def replace_message(message: Message) -> str:
    """将消息中的at、image、record、face替换为字符串

    参数:
        message: Message

    返回:
        str: 文本消息
    """
    result = ""
    for msg in message:
        if isinstance(msg, str):
            result += msg
        elif msg.type == "at":
            result += f"@{msg.data['qq']}"
        elif msg.type == "image":
            result += "[image]"
        elif msg.type == "record":
            result += "[record]"
        elif msg.type == "face":
            result += f"[face:{msg.data['id']}]"
        elif msg.type == "reply":
            result += ""
        else:
            result += str(msg)
    return result


async def _record_outgoing_chat_history(
    bot: Bot,
    *,
    user_id: str | None,
    group_id: str | None,
    message_type: str,
    message: Message | str,
    result: Any,
) -> None:
    """独立记录结构化历史，任何失败都不反向影响消息发送或旧审计。"""
    if not Config.get_config("chat_history", "FLAG"):
        return
    try:
        await create_outgoing_record(
            bot,
            user_id=user_id,
            group_id=group_id,
            message_type=message_type,
            message=message,
            result=result,
        )
    except Exception as e:
        logger.warning("记录ChatHistory出站消息失败", "chat_history", e=e)


@Bot.on_called_api
async def handle_api_result(
    bot: Bot, exception: Exception | None, api: str, data: dict[str, Any], result: Any
):
    if exception or api not in SEND_MESSAGE_APIS:
        return
    source_context = _resolve_source_context()
    message_id = await _record_triggered_message(bot, result, source_context)
    user_id = _resolve_manual_withdraw_user_id(api, data, source_context)
    try:
        if user_id and message_id:
            MessageManager.add(str(user_id), str(message_id))
            logger.debug(
                f"收集消息id，user_id: {user_id}, msg_id: {message_id}", LOG_COMMAND
            )
    except Exception as e:
        logger.warning(
            f"收集消息id发生错误...data: {data}, result: {result}", LOG_COMMAND, e=e
        )
    record = _normalize_bot_message_record(api, data, source_context)
    if record is None:
        return
    store_user_id, store_group_id, message_type, message = record
    if Config.get_config("hook", "RECORD_BOT_SENT_MESSAGES"):
        try:
            await BotMessageStore.create(
                bot_id=bot.self_id,
                user_id=store_user_id,
                group_id=store_group_id,
                sent_type=BotSentType.GROUP
                if message_type == "group"
                else BotSentType.PRIVATE,
                text=replace_message(message),
                plain_text=message.extract_plain_text()
                if isinstance(message, Message)
                else replace_message(message),
                platform=PlatformUtils.get_platform(bot),
            )
            sanitized_message = sanitize_for_logging(
                message,
                context="nonebot_message",
            )
            logger.debug(f"消息发送记录，message: {sanitized_message}")
        except Exception as e:
            logger.warning(
                f"消息发送记录发生错误...data: {data}, result: {result}",
                LOG_COMMAND,
                e=e,
            )
    # 两个账本顺序执行但故障隔离，旧审计失败仍会尝试写入ChatHistory。
    await _record_outgoing_chat_history(
        bot,
        user_id=store_user_id,
        group_id=store_group_id,
        message_type=message_type,
        message=message,
        result=result,
    )


_patch_runtime_context()
