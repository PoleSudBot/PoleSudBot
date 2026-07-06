from zhenxun.plugins.emoji_like.emoji_map import (
    extract_emoji_queries,
    lookup_emoji_id,
    lookup_emoji_ids,
    lookup_numeric_emoji_id,
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


def test_lookup_low_codepoint_legacy_emoji():
    copyright_result = lookup_emoji_id("©️")
    registered_result = lookup_emoji_id("®️")

    assert copyright_result is not None
    assert copyright_result.normalized == "©"
    assert copyright_result.emoji_id == "169"
    assert registered_result is not None
    assert registered_result.normalized == "®"
    assert registered_result.emoji_id == "174"


def test_lookup_multiple_unicode_emoji_ids():
    results = lookup_emoji_ids("㊗️❤️")

    assert [result.emoji_id for result in results] == ["12951", "10084"]


def test_lookup_multiple_low_codepoint_legacy_emoji_ids():
    results = lookup_emoji_ids("©️®️")

    assert [result.emoji_id for result in results] == ["169", "174"]


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
    results = extract_emoji_queries("㊗️")

    assert [result.emoji_id for result in results] == ["12951"]


def test_extract_bare_multiple_emoji_queries():
    results = extract_emoji_queries("㊗️❤️")

    assert [result.emoji_id for result in results] == ["12951", "10084"]


def test_extract_bare_multiple_legacy_emoji_queries():
    results = extract_emoji_queries("©️®️")

    assert [result.emoji_id for result in results] == ["169", "174"]


def test_extract_bare_emoji_queries_caps_at_five():
    results = extract_emoji_queries("㊗️❤️©️®️☀️👍")

    assert [result.emoji_id for result in results] == [
        "12951",
        "10084",
        "169",
        "174",
        "9728",
    ]


def test_extract_prefixed_emoji_query():
    results = extract_emoji_queries("贴㊗️")

    assert [result.emoji_id for result in results] == ["12951"]


def test_extract_prefixed_emoji_query_with_space():
    results = extract_emoji_queries("贴 ㊗️")

    assert [result.emoji_id for result in results] == ["12951"]


def test_extract_prefixed_numeric_emoji_id():
    results = extract_emoji_queries("贴 128077 10084")

    assert [result.emoji_id for result in results] == ["128077", "10084"]


def test_extract_mixed_prefixed_emoji_and_numeric_id():
    results = extract_emoji_queries("贴 ㊗️ 128077 ❤️")

    assert [result.emoji_id for result in results] == ["12951", "128077", "10084"]


def test_extract_prefixed_mixed_queries_caps_at_five():
    results = extract_emoji_queries("贴 128077 10084 12951 ©️ ®️ ☀️")

    assert [result.emoji_id for result in results] == [
        "128077",
        "10084",
        "12951",
        "169",
        "174",
    ]


def test_extract_unsupported_query_returns_none():
    assert extract_emoji_queries("") == []
    assert extract_emoji_queries("祝福") == []
    assert extract_emoji_queries("贴祝福") == []
    assert extract_emoji_queries("👍🏻") == []


def test_bare_numeric_id_does_not_trigger():
    assert extract_emoji_queries("128077") == []


def test_lookup_numeric_emoji_id_rejects_overflow():
    assert lookup_numeric_emoji_id("4294967295") is not None
    assert lookup_numeric_emoji_id("4294967296") is None
