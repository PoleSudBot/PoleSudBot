from __future__ import annotations

from datetime import datetime
from pathlib import Path

import aiofiles
from nonebot.adapters.onebot.v11 import Bot
from nonebot_plugin_alconna.uniseg import MsgTarget, UniMessage
from pydantic import BaseModel, Field

from zhenxun.services.llm import LLMException
from zhenxun.services.log import logger

from . import base_config
from .utils.core import SummaryException
from .utils.message_processing import MessageFetchResult, get_group_messages
from .utils.message_selector import MessageSelector
from .utils.summary_generation import messages_summary, send_summary


class SummaryParameters(BaseModel):
    """封装单次总结任务所需的所有参数"""

    bot: Bot
    target_group_id: int
    selector: MessageSelector
    style: str | None = None
    content_filter: str | None = None
    target_user_ids: set[str] = Field(default_factory=set)
    response_target: MsgTarget

    class Config:
        arbitrary_types_allowed = True


class ExportParameters(BaseModel):
    """封装单次导出任务所需的所有参数"""

    bot: Bot
    target_group_id: int
    selector: MessageSelector
    response_target: MsgTarget

    class Config:
        arbitrary_types_allowed = True


def _build_no_message_text(
    group_id: int,
    selector: MessageSelector,
    target_user_ids: set[str] | None = None,
) -> str:
    if target_user_ids:
        return f"在群聊 {group_id} 中未能获取到指定用户的有效聊天记录。"
    if selector.mode == "today":
        return f"在群聊 {group_id} 中未能获取到今日的聊天记录。"
    if selector.mode == "yesterday":
        return f"在群聊 {group_id} 中未能获取到昨日的聊天记录。"
    if selector.is_time_based:
        return f"在群聊 {group_id} 中未能获取到时间范围内的聊天记录。"
    return f"未能获取到群聊 {group_id} 的聊天记录。"


class SummaryService:
    """封装群聊总结的核心业务逻辑"""

    def __init__(self, params: SummaryParameters):
        self.params = params
        self.logger = logger
        self.fetch_result = MessageFetchResult(processed_messages=[], user_info_cache={})
        self.model_name: str | None = None

    async def _fetch_and_process_messages(self):
        self.logger.debug(
            f"Service: 开始获取群 {self.params.target_group_id} 的原始消息: "
            f"selector={self.params.selector.mode}",
            command="总结服务",
        )
        use_db = bool(base_config.get("USE_DB_HISTORY", False))
        self.fetch_result = await get_group_messages(
            self.params.bot,
            self.params.target_group_id,
            self.params.selector,
            use_db=use_db,
            target_user_ids=self.params.target_user_ids,
            enforce_summary_limit=True,
        )

        if not self.fetch_result.processed_messages:
            raise SummaryException(
                _build_no_message_text(
                    self.params.target_group_id,
                    self.params.selector,
                    self.params.target_user_ids,
                )
            )

        self.logger.debug(
            f"Service: 成功获取并处理消息，得到 {len(self.fetch_result.processed_messages)} 条记录",
            command="总结服务",
        )

    async def _generate_summary(self) -> str:
        target_user_names = []
        if self.params.target_user_ids:
            target_user_names = [
                self.fetch_result.user_info_cache.get(uid, f"用户{uid[-4:]}")
                for uid in self.params.target_user_ids
            ]

        summary_content_target = MsgTarget(str(self.params.target_group_id))
        summary, model_name = await messages_summary(
            target=summary_content_target,
            messages=self.fetch_result.processed_messages,
            content=self.params.content_filter,
            target_user_names=target_user_names or None,
            style=self.params.style,
        )
        self.model_name = model_name
        self.logger.debug(
            f"Service: 群 {self.params.target_group_id} 总结生成成功，长度: {len(summary)} 字符",
            command="总结服务",
        )
        return summary

    async def _send_summary(self, summary_text: str) -> bool:
        if self.fetch_result.warning_msg:
            await UniMessage.text(self.fetch_result.warning_msg).send(
                self.params.response_target
            )

        return await send_summary(
            self.params.bot,
            self.params.response_target,
            summary_text,
            self.fetch_result.user_info_cache,
            group_id=self.params.target_group_id,
            model_name=self.model_name,
        )

    async def execute(self) -> bool:
        try:
            await self._fetch_and_process_messages()
            summary_text = await self._generate_summary()
            return await self._send_summary(summary_text)
        except SummaryException as e:
            self.logger.warning(
                f"总结服务执行失败 (业务异常): {e}",
                command="总结服务",
                e=e,
            )
            await UniMessage.text(e.user_friendly_message).send(
                self.params.response_target
            )
            return False
        except LLMException as e:
            self.logger.error(
                f"总结服务执行失败 (LLM异常): {e}",
                command="总结服务",
                e=e,
            )
            await UniMessage.text(e.user_friendly_message).send(
                self.params.response_target
            )
            return False
        except Exception as e:
            self.logger.error(
                f"总结服务执行时发生未知错误: {e}",
                command="总结服务",
                e=e,
            )
            await UniMessage.text(
                "处理总结时发生了一个未知的内部错误，请联系管理员。"
            ).send(self.params.response_target)
            return False


def _build_export_header(
    group_id: int,
    selector: MessageSelector,
    messages: list[dict[str, object]],
) -> str:
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    range_text = selector.label
    if selector.start_time and selector.end_time:
        range_text = (
            f"{selector.start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ "
            f"{selector.end_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    return "\n".join(
        [
            "# 聊天记录导出",
            "",
            f"- 群号: {group_id}",
            f"- 导出模式: {selector.mode}",
            f"- 范围: {range_text}",
            f"- 导出时间: {exported_at}",
            f"- 总条数: {len(messages)}",
            "",
            "## 聊天记录",
            "",
        ]
    )


def _format_export_message(message: dict[str, object]) -> str:
    formatted_time = str(message.get("formatted_time") or "未知时间")
    user_name = str(message.get("name") or "未知用户")
    user_id = str(message.get("user_id") or "unknown")
    content = str(message.get("content") or "")
    return f"[{formatted_time}] {user_name}({user_id}): {content}"


async def _write_export_file(
    group_id: int,
    selector: MessageSelector,
    messages: list[dict[str, object]],
) -> Path:
    from zhenxun.configs.path_config import DATA_PATH

    save_dir = DATA_PATH / "summary_group"
    save_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"chat_export_{group_id}_{selector.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    file_path = save_dir / filename
    content = _build_export_header(group_id, selector, messages) + "\n".join(
        _format_export_message(message) for message in messages
    )

    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(content)

    return file_path


class ExportService:
    """导出聊天记录到本地 Markdown 文件"""

    def __init__(self, params: ExportParameters):
        self.params = params
        self.logger = logger

    async def execute(self) -> bool:
        try:
            result = await get_group_messages(
                self.params.bot,
                self.params.target_group_id,
                self.params.selector,
                use_db=bool(base_config.get("USE_DB_HISTORY", False)),
                enforce_summary_limit=False,
            )
            if not result.processed_messages:
                raise SummaryException(
                    _build_no_message_text(
                        self.params.target_group_id,
                        self.params.selector,
                    )
                )

            file_path = await _write_export_file(
                self.params.target_group_id,
                self.params.selector,
                result.processed_messages,
            )
            await UniMessage.text(
                f"聊天记录已导出到: {file_path}\n共导出 {len(result.processed_messages)} 条记录。"
            ).send(self.params.response_target)
            return True
        except SummaryException as e:
            self.logger.warning(
                f"导出聊天记录失败 (业务异常): {e}",
                command="聊天记录导出",
                e=e,
            )
            await UniMessage.text(e.user_friendly_message).send(
                self.params.response_target
            )
            return False
        except Exception as e:
            self.logger.error(
                f"导出聊天记录时发生未知错误: {e}",
                command="聊天记录导出",
                e=e,
            )
            await UniMessage.text(
                "导出聊天记录时发生了一个未知的内部错误，请联系管理员。"
            ).send(self.params.response_target)
            return False
