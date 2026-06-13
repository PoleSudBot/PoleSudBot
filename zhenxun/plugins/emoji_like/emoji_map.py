from __future__ import annotations

from dataclasses import dataclass
import unicodedata

VARIATION_SELECTOR_16 = "\ufe0f"
COMMAND_PREFIX = "贴"


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
    """只允许单个 Unicode 符号码位，避免把中文、字母或组合序列误转成 id。"""

    return len(value) == 1 and unicodedata.category(value).startswith("S")


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


def extract_emoji_query(raw_text: str) -> EmojiLookupResult | None:
    """从回复消息文本中识别裸 emoji 或兼容入口“贴<emoji>”。"""

    text = raw_text.strip()
    if not text:
        return None

    result = lookup_emoji_id(text)
    if result is not None:
        return result

    if text.startswith(COMMAND_PREFIX):
        return lookup_emoji_id(text.removeprefix(COMMAND_PREFIX).strip())
    return None
