import pytest

from zhenxun.plugins.emoji_like.service import apply_emoji_likes


class FakeBot:
    def __init__(self):
        self.calls = []

    async def call_api(self, api: str, **data):
        self.calls.append((api, data))


@pytest.mark.asyncio
async def test_apply_emoji_like_calls_napcat_api():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, 12345, "㊗️")

    assert [result.emoji_id for result in results] == ["12951"]
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "12951"},
        )
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_supports_dynamic_unicode_codepoint():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, "abc", "❤️")

    assert [result.emoji_id for result in results] == ["10084"]
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": "abc", "emoji_id": "10084"},
        )
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_calls_napcat_api_for_multiple_emojis():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, 12345, "㊗️❤️")

    assert [result.emoji_id for result in results] == ["12951", "10084"]
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "12951"},
        ),
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "10084"},
        ),
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_calls_napcat_api_for_legacy_emojis():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, 12345, "©️®️")

    assert [result.emoji_id for result in results] == ["169", "174"]
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "169"},
        ),
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "174"},
        ),
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_supports_prefixed_numeric_id():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, 12345, "贴 128077 10084")

    assert [result.emoji_id for result in results] == ["128077", "10084"]
    assert bot.calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "128077"},
        ),
        (
            "set_msg_emoji_like",
            {"message_id": 12345, "emoji_id": "10084"},
        ),
    ]


@pytest.mark.asyncio
async def test_apply_emoji_like_text_returns_empty_without_calling_api():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, 12345, "祝福")

    assert results == []
    assert bot.calls == []


@pytest.mark.asyncio
async def test_apply_emoji_like_combined_emoji_returns_empty_without_calling_api():
    bot = FakeBot()

    results = await apply_emoji_likes(bot, 12345, "👍🏻")

    assert results == []
    assert bot.calls == []
