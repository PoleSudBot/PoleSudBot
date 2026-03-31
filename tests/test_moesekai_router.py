from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.adapters.results import MoeImageTextMessage
from zhenxun.plugins.moesekai.commands import router as router_module


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[object, Message]] = []

    async def send(self, event, message: Message):
        self.calls.append((event, message))
        return None


class FakeEvent:
    def __init__(self, message_id: int = 12345) -> None:
        self.message_id = message_id


@pytest.mark.asyncio
async def test_send_result_uses_single_native_onebot_message_for_image_text():
    bot = FakeBot()
    event = FakeEvent()

    await router_module._send_result(
        MoeImageTextMessage(
            image_bytes=b"manga-image",
            text="B站链接：https://www.bilibili.com/opus/1183974551994761216",
        ),
        bot=bot,
        event=event,
    )

    assert len(bot.calls) == 1
    sent_event, sent_message = bot.calls[0]
    assert sent_event is event
    assert isinstance(sent_message, Message)
    assert sent_message[0].type == "reply"
    assert sent_message[1].type == "image"
    assert sent_message[2].type == "text"
    assert sent_message[2].data["text"].startswith("\nB站链接：")
    assert MessageSegment.image(b"manga-image").data["file"] == sent_message[1].data["file"]
