import pytest

from zhenxun.plugins.emoji_like.service import apply_emoji_like


class FakeBot:
    def __init__(self):
        self.calls = []

    async def call_api(self, api: str, **data):
        self.calls.append((api, data))


@pytest.mark.asyncio
async def test_apply_emoji_like_calls_napcat_api():
    bot = FakeBot()

    result = await apply_emoji_like(bot, 12345, "㊗️")

    assert result is not None
    assert result.emoji_id == "12951"
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "12951"},
        )
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_supports_dynamic_unicode_codepoint():
    bot = FakeBot()

    result = await apply_emoji_like(bot, "abc", "❤️")

    assert result is not None
    assert result.emoji_id == "10084"
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": "abc", "emoji_id": "10084"},
        )
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_text_returns_none_without_calling_api():
    bot = FakeBot()

    result = await apply_emoji_like(bot, 12345, "祝福")

    assert result is None
    assert bot.calls == []


@pytest.mark.asyncio
async def test_apply_emoji_like_combined_emoji_returns_none_without_calling_api():
    bot = FakeBot()

    result = await apply_emoji_like(bot, 12345, "👍🏻")

    assert result is None
    assert bot.calls == []
