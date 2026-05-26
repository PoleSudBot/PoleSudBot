from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from nonebot.adapters import Bot
import ujson as json

from zhenxun.configs.path_config import DATA_PATH

if TYPE_CHECKING:
    from nonebot.adapters import Event
    from nonebot_plugin_alconna import UniMessage, UniMsg

try:
    from enum import StrEnum
except ImportError:
    from strenum import StrEnum

BASE_PATH = DATA_PATH / "auto_greeting"
BASE_PATH.mkdir(parents=True, exist_ok=True)


def _get_logger():
    """延迟获取项目 logger，避免模块导入时触发 NoneBot 插件初始化。"""
    from zhenxun.services.log import logger

    return logger


async def _download_image(url: str, path: Path) -> bool:
    """延迟加载下载工具，避免模块导入时触发配置层初始化。"""
    from zhenxun.utils.http_utils import AsyncHttpx

    return await AsyncHttpx.download_file(url, path)


async def _send_message(
    bot: Bot, user_id: str | None, group_id: str | None, message: UniMessage
) -> object:
    """延迟加载平台发送工具，保持模型导入阶段轻量。"""
    from zhenxun.utils.platform import PlatformUtils

    return await PlatformUtils.send_message(bot, user_id, group_id, message)


def _load_message(data: list[dict]) -> UniMessage:
    """按需加载 UniMessage，避免导入管理器时提前加载 alconna 插件。"""
    from nonebot_plugin_alconna import UniMessage

    return UniMessage().load(data)


async def _fetch_reply(event: Event, bot: Bot):
    """按需加载引用消息工具，避免模块导入时触发 alconna 插件初始化。"""
    from nonebot_plugin_alconna.uniseg.tools import reply_fetch

    return await reply_fetch(event, bot)


def _build_reply_message(raw_message, bot: Bot) -> UniMessage:
    """将适配器原始消息体转换为可持久化的 UniMessage。"""
    from nonebot_plugin_alconna import UniMessage

    if isinstance(raw_message, str):
        return UniMessage(raw_message)
    if isinstance(raw_message, UniMessage):
        return raw_message
    return UniMessage.of(raw_message, bot=bot)


def _is_blank_text(data: dict) -> bool:
    """判断序列化后的文本段是否为空白内容。"""
    return data.get("type") == "text" and not str(data.get("text", "")).strip()


class GreetingScene(StrEnum):
    """区分自动消息发送场景，避免好友私聊和群内介绍共用一份文案。"""

    FRIEND = "friend"
    GROUP = "group"


class AutoGreetingManager:
    """自动欢迎消息管理器"""

    @classmethod
    def _message_file(cls, scene: GreetingScene) -> Path:
        """获取指定场景的消息配置文件。"""
        return BASE_PATH / f"{scene.value}.json"

    @classmethod
    def _image_dir(cls, scene: GreetingScene) -> Path:
        """获取指定场景的图片持久化目录。"""
        return BASE_PATH / scene.value

    @classmethod
    def _load_data(cls, scene: GreetingScene) -> dict | None:
        """读取自动消息配置。"""
        file = cls._message_file(scene)
        if not file.exists():
            return None
        with file.open(encoding="utf8") as f:
            return json.load(f)

    @classmethod
    def get_message(cls, scene: GreetingScene) -> UniMessage | None:
        """获取指定场景的自动消息。"""
        data = cls._load_data(scene)
        if not data or not data.get("message"):
            return None
        return _load_message(data["message"])

    @classmethod
    async def get_reply_message(cls, bot: Bot, event: Event) -> UniMessage | None:
        """从当前事件引用的消息中提取可保存的自动消息内容。"""
        reply = await _fetch_reply(event, bot)
        raw_message = getattr(reply, "msg", None) if reply else None
        if not raw_message:
            return None
        try:
            # 引用消息来自适配器原始消息体，统一转为 UniMessage 后复用同一保存流程。
            return _build_reply_message(raw_message, bot)
        except Exception as e:
            _get_logger().warning("解析引用消息失败", "自动介绍", e=e)
            return None

    @classmethod
    async def save_message(cls, scene: GreetingScene, message: UniMsg) -> UniMessage:
        """保存指定场景的自动消息。"""
        image_dir = cls._image_dir(scene)
        image_dir.mkdir(parents=True, exist_ok=True)
        data = []
        for msg in message.dump(True):
            # 引用段只用于定位原消息，不属于之后自动发送的欢迎内容。
            if msg["type"] == "reply" or _is_blank_text(msg):
                continue
            if msg["type"] == "image":
                image_file = image_dir / f"{uuid.uuid4()}.png"
                await _download_image(msg["url"], image_file)
                msg["path"] = str(image_file)
            data.append(msg)
        if not data or not str(_load_message(data)).strip():
            raise ValueError("自动消息内容不能为空")
        old_data = cls._load_data(scene)
        with cls._message_file(scene).open("w", encoding="utf8") as f:
            json.dump({"message": data}, f, ensure_ascii=False, indent=4)
        # 新配置写入成功后再清理旧图片，避免下载或写入失败导致旧配置也不可用。
        cls._cleanup_images(old_data)
        return _load_message(data)

    @classmethod
    def delete_message(cls, scene: GreetingScene) -> UniMessage | None:
        """删除指定场景的自动消息。"""
        data = cls._load_data(scene)
        if not data:
            return None
        file = cls._message_file(scene)
        old_message = cls.get_message(scene)
        if file.exists():
            file.unlink()
        cls._cleanup_images(data)
        return old_message

    @classmethod
    def _cleanup_images(cls, data: dict | None) -> None:
        """清理消息配置引用的本地图片。"""
        if not data:
            return
        for msg in data.get("message", []):
            if msg.get("type") != "image" or not msg.get("path"):
                continue
            image_path = Path(msg["path"])
            if image_path.exists():
                image_path.unlink()

    @classmethod
    async def send_friend_greeting(cls, bot: Bot, user_id: str) -> bool:
        """向新好友发送自定义欢迎消息。"""
        message = cls.get_message(GreetingScene.FRIEND)
        if not message:
            return False
        try:
            await _send_message(bot, str(user_id), None, message)
            _get_logger().info("发送好友自动欢迎消息", "自动介绍", session=user_id)
            return True
        except Exception as e:
            _get_logger().error(
                "发送好友自动欢迎消息失败", "自动介绍", target=user_id, e=e
            )
            return False

    @classmethod
    async def send_group_greeting(cls, bot: Bot, group_id: str) -> bool:
        """向 Bot 新加入的群发送自定义入群介绍。"""
        message = cls.get_message(GreetingScene.GROUP)
        if not message:
            return False
        try:
            await _send_message(bot, None, str(group_id), message)
            _get_logger().info("发送入群自动介绍消息", "自动介绍", session=group_id)
            return True
        except Exception as e:
            _get_logger().error(
                "发送入群自动介绍消息失败", "自动介绍", target=group_id, e=e
            )
            return False
