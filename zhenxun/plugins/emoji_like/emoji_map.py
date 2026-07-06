from __future__ import annotations

from dataclasses import dataclass
import unicodedata

VARIATION_SELECTOR_16 = "\ufe0f"
COMMAND_PREFIX = "贴"
MAX_EMOJI_LIKE_COUNT = 5
MAX_UINT32 = 0xFFFFFFFF
ZERO_WIDTH_JOINER = "\u200d"
KEYCAP_COMBINING_MARK = "\u20e3"
EMOJI_MODIFIER_START = 0x1F3FB
EMOJI_MODIFIER_END = 0x1F3FF
REGIONAL_INDICATOR_START = 0x1F1E6
REGIONAL_INDICATOR_END = 0x1F1FF
LEGACY_SINGLE_CODEPOINT_EMOJIS = {"©", "®"}


@dataclass(frozen=True, slots=True)
class EmojiLookupResult:
    """表情解析结果，保留展示名方便失败或日志提示定位用户输入。"""

    query: str
    normalized: str
    emoji_id: str


def normalize_emoji_key(value: str) -> str:
    """把带变体选择符的 emoji 归一成可转换为单个 code point 的形式。"""

    return unicodedata.normalize("NFC", value.strip()).replace(
        VARIATION_SELECTOR_16, ""
    )


def _is_single_symbol_emoji(value: str) -> bool:
    """只允许单个 Unicode 符号码位，避免把普通符号误转成 id。"""

    # 低码点 legacy emoji 需要显式放行，否则 ©️/®️ 会被码点阈值误排除。
    if value in LEGACY_SINGLE_CODEPOINT_EMOJIS:
        return True
    return (
        len(value) == 1
        and ord(value) > 256
        and unicodedata.category(value).startswith("S")
    )


def _is_sequence_marker(value: str) -> bool:
    """识别肤色、国旗、键帽和 ZWJ 这类 NapCat 单个 id 无法可靠表达的序列组件。"""

    codepoint = ord(value)
    return (
        value in {ZERO_WIDTH_JOINER, KEYCAP_COMBINING_MARK}
        or EMOJI_MODIFIER_START <= codepoint <= EMOJI_MODIFIER_END
        or REGIONAL_INDICATOR_START <= codepoint <= REGIONAL_INDICATOR_END
    )


def lookup_emoji_id(raw_query: str) -> EmojiLookupResult | None:
    """把用户发送的单个 emoji 动态转换为 NapCat 需要的十进制 emoji_id。"""

    query = raw_query.strip()
    if not query:
        return None

    normalized = normalize_emoji_key(query)
    if not _is_single_symbol_emoji(normalized):
        return None
    return EmojiLookupResult(
        query=query,
        normalized=normalized,
        emoji_id=str(ord(normalized)),
    )


def lookup_emoji_ids(raw_query: str) -> list[EmojiLookupResult]:
    """把连续的单码位 emoji 拆成多个可贴表情结果。"""

    query = raw_query.strip()
    if not query:
        return []

    normalized = normalize_emoji_key(query)
    if not normalized or any(_is_sequence_marker(char) for char in normalized):
        return []

    results: list[EmojiLookupResult] = []
    for char in normalized:
        if not _is_single_symbol_emoji(char):
            return []
        results.append(
            EmojiLookupResult(
                query=char,
                normalized=char,
                emoji_id=str(ord(char)),
            )
        )
    return results


def lookup_numeric_emoji_id(raw_query: str) -> EmojiLookupResult | None:
    """只把“贴”前缀后的十进制数字解析为直通 emoji_id。"""

    query = raw_query.strip()
    if not query.isdecimal():
        return None

    emoji_int_id = int(query)
    if emoji_int_id > MAX_UINT32:
        return None
    return EmojiLookupResult(
        query=query,
        normalized=query,
        emoji_id=str(emoji_int_id),
    )


def extract_emoji_queries(raw_text: str) -> list[EmojiLookupResult]:
    """从回复消息文本中识别裸 emoji、兼容入口“贴<emoji>”或“贴 <数字>”。"""

    text = raw_text.strip()
    if not text:
        return []

    if not text.startswith(COMMAND_PREFIX):
        return lookup_emoji_ids(text)[:MAX_EMOJI_LIKE_COUNT]

    results: list[EmojiLookupResult] = []
    for token in text.removeprefix(COMMAND_PREFIX).strip().split():
        if result := lookup_numeric_emoji_id(token):
            results.append(result)
        else:
            results.extend(lookup_emoji_ids(token))
    # 限制单条消息触发的协议调用次数，避免长串 emoji 一次性刷出过多 API 请求。
    return results[:MAX_EMOJI_LIKE_COUNT]
