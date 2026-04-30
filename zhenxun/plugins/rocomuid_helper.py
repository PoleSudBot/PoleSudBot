from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any, TypeVar

SENSITIVE_FEATURE_DISCLAIMER = (
    "该功能依赖 RocomUID 开发者提供的外部服务，"
    "并非本地部署或本仓库维护。若涉及 QQ 扫码登录、账号授权或凭证提交，"
    "请自行评估风险后再使用。"
)

QQ_LOGIN_CASE_HINT = "请使用 `rcQQ登录`（QQ 大写）。"

_DISCLAIMER_COMMANDS = {
    "rc绑定uid",
    "rcqq登录",
}

_CANONICAL_QQ_LOGIN_COMMAND = "rcQQ登录"
_ContinueResult = TypeVar("_ContinueResult")


@dataclass(frozen=True)
class RocomUIDLocalAction:
    send_disclaimer: bool = False
    finish_message: str | None = None
    statistics_skip: bool = False


def extract_rocomuid_command_head(plain_text: str) -> str:
    # 这里只看命令头，让“命令 + 参数”类写法共用同一条本地策略，
    # 避免把参数差异带进免责声明和纠错分支判断。
    parts = (plain_text or "").strip().split(maxsplit=1)
    return parts[0] if parts else ""


def normalize_rocomuid_command_head(command_head: str) -> str:
    return (command_head or "").strip().lower()


def should_pre_send_sensitive_disclaimer(command_head: str) -> bool:
    return normalize_rocomuid_command_head(command_head) in _DISCLAIMER_COMMANDS


def should_finish_with_login_case_hint(command_head: str) -> bool:
    normalized_head = normalize_rocomuid_command_head(command_head)
    return (
        normalized_head == "rcqq登录"
        and (command_head or "").strip() != _CANONICAL_QQ_LOGIN_COMMAND
    )


def get_rocomuid_local_action(plain_text: str) -> RocomUIDLocalAction:
    command_head = extract_rocomuid_command_head(plain_text)
    if should_finish_with_login_case_hint(command_head):
        # 大小写写错时本地直接纠正，比把请求继续发给 sidecar 更稳定，
        # 也能确保免责声明和正确命令提示一次性给全。
        return RocomUIDLocalAction(
            finish_message=(
                f"{SENSITIVE_FEATURE_DISCLAIMER}\n\n{QQ_LOGIN_CASE_HINT}"
            ),
            statistics_skip=True,
        )
    if should_pre_send_sensitive_disclaimer(command_head):
        return RocomUIDLocalAction(send_disclaimer=True)
    return RocomUIDLocalAction()


async def run_rocomuid_local_action(
    plain_text: str,
    matcher_state: MutableMapping[str, Any],
    *,
    send_message: Callable[[str], Awaitable[None]],
    finish_message: Callable[[str], Awaitable[None]],
    continue_handler: Callable[[], Awaitable[_ContinueResult]],
) -> _ContinueResult | None:
    action = get_rocomuid_local_action(plain_text)
    if action.send_disclaimer:
        await send_message(SENSITIVE_FEATURE_DISCLAIMER)
    if action.finish_message:
        if action.statistics_skip:
            matcher_state["_statistics_skip"] = True
        await finish_message(action.finish_message)
        return None
    return await continue_handler()
