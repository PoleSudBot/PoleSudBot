from zhenxun.plugins.emoji_like.emoji_map import (
    extract_emoji_query,
    lookup_emoji_id,
    normalize_emoji_key,
)


def test_lookup_unicode_emoji_id():
    result = lookup_emoji_id("👍")

    assert result is not None
    assert result.normalized == "👍"
    assert result.emoji_id == "128077"


def test_lookup_variation_selector_emoji_id():
    result = lookup_emoji_id("㊗️")

    assert result is not None
    assert result.normalized == "㊗"
    assert result.emoji_id == "12951"


def test_lookup_heart_with_variation_selector():
    result = lookup_emoji_id("❤️")

    assert result is not None
    assert result.normalized == "❤"
    assert result.emoji_id == "10084"


def test_lookup_symbol_emoji_not_in_old_static_table():
    result = lookup_emoji_id("☀️")

    assert result is not None
    assert result.normalized == "☀"
    assert result.emoji_id == "9728"


def test_lookup_text_alias_returns_none():
    assert lookup_emoji_id("祝福") is None


def test_lookup_plain_text_single_character_returns_none():
    assert lookup_emoji_id("祝") is None


def test_lookup_combined_emoji_returns_none():
    assert lookup_emoji_id("👍🏻") is None
    assert lookup_emoji_id("👨‍👩‍👧‍👦") is None
    assert lookup_emoji_id("🇯🇵") is None
    assert lookup_emoji_id("7️⃣") is None


def test_normalize_emoji_key_removes_variation_selector():
    assert normalize_emoji_key("㊗️") == "㊗"


def test_extract_bare_emoji_query():
    result = extract_emoji_query("㊗️")

    assert result is not None
    assert result.emoji_id == "12951"


def test_extract_prefixed_emoji_query():
    result = extract_emoji_query("贴㊗️")

    assert result is not None
    assert result.emoji_id == "12951"


def test_extract_prefixed_emoji_query_with_space():
    result = extract_emoji_query("贴 ㊗️")

    assert result is not None
    assert result.emoji_id == "12951"


def test_extract_unsupported_query_returns_none():
    assert extract_emoji_query("") is None
    assert extract_emoji_query("祝福") is None
    assert extract_emoji_query("贴祝福") is None
    assert extract_emoji_query("👍🏻") is None
