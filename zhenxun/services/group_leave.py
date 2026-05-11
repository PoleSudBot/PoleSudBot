from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import secrets
import time
from typing import Literal

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services.log import logger
from zhenxun.utils.exception import NotFindSuperuser
from zhenxun.utils.platform import PlatformUtils

LeaveConfirmStatus = Literal["ok", "missing", "mismatch"]

_CONFIRM_CODE_MIN = 1000
_CONFIRM_CODE_RANGE = 9000
_confirm_states: dict[str, "LeaveConfirmation"] = {}


@dataclass(slots=True)
class LeaveConfirmation:
    code: str
    expires_at: float


@dataclass(slots=True)
class GroupLeaveNoticeContext:
    operator_id: str
    operator_name: str
    group_id: str
    group_name: str


def build_leave_confirm_key(
    bot_id: str, group_id: int | str, operator_id: int | str
) -> str:
    """构造确认状态键，限定确认码只能被同一 Bot、同一群、同一操作者使用。"""
    return f"{bot_id}:{group_id}:{operator_id}"


def _now(now: float | None = None) -> float:
    """获取当前时间，测试可传入固定时间避免依赖真实时钟。"""
    return time.time() if now is None else now


def cleanup_expired_leave_confirmations(now: float | None = None) -> None:
    """清理过期确认状态，避免长期运行时内存中残留旧验证码。"""
    current_time = _now(now)
    expired_keys = [
        key for key, state in _confirm_states.items() if state.expires_at < current_time
    ]
    for key in expired_keys:
        _confirm_states.pop(key, None)


def clear_leave_confirmations() -> None:
    """清空所有确认状态，主要用于测试隔离。"""
    _confirm_states.clear()


def generate_leave_confirm_code() -> str:
    """生成 4 位确认码，避免退群这类高风险操作被随手确认。"""
    return str(secrets.randbelow(_CONFIRM_CODE_RANGE) + _CONFIRM_CODE_MIN)


def create_leave_confirmation(
    bot_id: str,
    group_id: int | str,
    operator_id: int | str,
    timeout_seconds: int,
    *,
    now: float | None = None,
) -> LeaveConfirmation:
    """创建或刷新退群确认状态。"""
    current_time = _now(now)
    cleanup_expired_leave_confirmations(current_time)
    confirmation = LeaveConfirmation(
        code=generate_leave_confirm_code(),
        expires_at=current_time + max(timeout_seconds, 0),
    )
    _confirm_states[build_leave_confirm_key(bot_id, group_id, operator_id)] = (
        confirmation
    )
    return confirmation


def consume_leave_confirmation(
    bot_id: str,
    group_id: int | str,
    operator_id: int | str,
    input_code: str,
    *,
    now: float | None = None,
) -> LeaveConfirmStatus:
    """校验并消费确认码，错误码不会清除状态以便用户重试。"""
    key = build_leave_confirm_key(bot_id, group_id, operator_id)
    confirmation = _confirm_states.get(key)
    current_time = _now(now)

    if confirmation is None:
        return "missing"
    if confirmation.expires_at < current_time:
        _confirm_states.pop(key, None)
        return "missing"
    if confirmation.code != input_code:
        return "mismatch"

    _confirm_states.pop(key, None)
    return "ok"


async def is_bot_joined_group(bot, group_id: int | str) -> bool:
    """检查 Bot 是否仍在目标群内，避免为不可执行的退群请求生成确认码。"""
    target_group_id = str(group_id)
    group_list = await bot.get_group_list()
    for group in group_list:
        current_group_id = (
            group.get("group_id") if isinstance(group, dict) else None
        ) or getattr(group, "group_id", None)
        if str(current_group_id) == target_group_id:
            return True
    return False


def _format_name_with_id(
    name: str,
    identity: int | str,
    fallback_name: str = "",
) -> str:
    """格式化报告中的名称与 ID，名称缺失时避免生成重复的 123(123)。"""
    normalized_name = name.strip()
    normalized_id = str(identity)
    if not normalized_name:
        return f"{fallback_name}({normalized_id})" if fallback_name else normalized_id
    return f"{normalized_name}({normalized_id})"


def build_group_leave_report(
    context: GroupLeaveNoticeContext,
    *,
    now: datetime | None = None,
) -> str:
    """构造退群报告文本，和被踢出群报告保持相近的信息密度。"""
    leave_time = now or datetime.now()
    operator_text = _format_name_with_id(
        context.operator_name,
        context.operator_id,
    )
    group_text = _format_name_with_id(
        context.group_name,
        context.group_id,
        fallback_name="群聊",
    )
    return (
        "****呜..一份退群报告****\n"
        f"我收到 {operator_text}\n"
        "发来的退群确认啦\n"
        f"已离开 {group_text}\n"
        f"日期：{leave_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def build_group_leave_notice_context(
    group_id: int | str,
    operator_id: int | str,
) -> GroupLeaveNoticeContext:
    """读取退群报告所需上下文，必须在删除群记录前完成。"""
    group_id_text = str(group_id)
    operator_id_text = str(operator_id)
    operator_name = ""

    if user := await GroupInfoUser.get_or_none(
        user_id=operator_id_text,
        group_id=group_id_text,
    ):
        operator_name = user.nickname or user.user_name or ""

    group = await GroupConsole.get_group_db(group_id_text)
    return GroupLeaveNoticeContext(
        operator_id=operator_id_text,
        operator_name=operator_name,
        group_id=group_id_text,
        group_name=group.group_name if group else "",
    )


async def send_group_leave_superuser_notice(
    bot,
    context: GroupLeaveNoticeContext,
) -> None:
    """退群成功后向超级用户发送报告，通知失败不反向影响退群结果。"""
    try:
        await PlatformUtils.send_superuser(bot, build_group_leave_report(context))
    except NotFindSuperuser:
        logger.warning(
            "未找到超级用户，跳过退群报告",
            "退群",
            target=context.group_id,
        )


async def delete_group_console_record(group_id: int | str) -> None:
    """删除本地群记录，走模型实例 delete() 以同步清理运行时缓存。"""
    group = await GroupConsole.get_group_db(str(group_id))
    if group:
        await group.delete()


async def execute_group_leave(
    bot,
    group_id: int | str,
    *,
    delay_seconds: int,
    log_command: str,
    operator_id: int | str,
    log_session=None,
) -> None:
    """发送退群请求，并在成功后清理本地群记录。"""
    target_group_id = int(group_id)
    notice_context = await build_group_leave_notice_context(
        target_group_id,
        operator_id,
    )
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    await bot.set_group_leave(group_id=target_group_id)
    await delete_group_console_record(target_group_id)
    await send_group_leave_superuser_notice(bot, notice_context)
    logger.info(
        "退出群组成功",
        log_command,
        session=log_session,
        target=target_group_id,
    )
