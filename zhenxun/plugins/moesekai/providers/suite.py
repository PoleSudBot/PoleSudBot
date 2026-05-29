from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from ..adapters.runtime import AsyncHttpx
from ..config import get_settings

SUITE_B30_FIELDS = (
    "upload_time",
    "userGamedata",
    "userMusicResults",
    "userDecks",
    "userCards",
)


class SuiteApiError(RuntimeError):
    """Suite API 请求或响应解析失败。"""


@dataclass(frozen=True)
class SuiteProfile:
    user_id: str
    name: str
    rank: int
    upload_time: int = 0
    avatar_uri: str = ""
    name_color: str = ""


@dataclass(frozen=True)
class SuiteB30Data:
    profile: SuiteProfile
    music_results: list[dict[str, Any]]
    user_decks: list[dict[str, Any]]
    user_cards: list[dict[str, Any]]
    default_deck_id: int
    source_url: str


class SuiteProvider:
    @staticmethod
    def build_url(
        pattern: str,
        server: str,
        game_id: str,
        *,
        fields: tuple[str, ...] = SUITE_B30_FIELDS,
    ) -> str:
        endpoint = str(pattern or "").strip()
        if not endpoint:
            raise SuiteApiError("未配置 Suite API 地址模板")
        replacements = {
            "{server}": quote(server),
            "{region}": quote(server),
            "{game_id}": quote(game_id),
            "{uid}": quote(game_id),
        }
        for placeholder, value in replacements.items():
            endpoint = endpoint.replace(placeholder, value)

        split = urlsplit(endpoint)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        # Haruki public suite 用 key 参数裁剪字段，避免拉全量卡牌数据拖慢 B30。
        query["key"] = ",".join(fields)
        return urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                urlencode(query),
                split.fragment,
            )
        )

    async def get_b30_data(self, server: str, game_id: str) -> SuiteB30Data:
        settings = get_settings()
        url = self.build_url(settings.suite_api_url_pattern, server, game_id)
        try:
            response = await AsyncHttpx.get(
                url,
                timeout=settings.suite_api_timeout_seconds,
            )
            # Suite 返回裸 JSON；这里显式解析，避开统一 get_json 对特殊响应的默认兜底。
            payload = response.json()
        except Exception as exc:
            raise SuiteApiError(f"Suite API 请求失败: {server}/{game_id}") from exc

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise SuiteApiError("Suite API 返回格式异常")

        user_gamedata = payload.get("userGamedata")
        if not isinstance(user_gamedata, dict):
            raise SuiteApiError("Suite API 返回缺少 userGamedata")
        music_results = payload.get("userMusicResults")
        if not isinstance(music_results, list):
            raise SuiteApiError("Suite API 返回缺少 userMusicResults")
        user_decks = payload.get("userDecks")
        if not isinstance(user_decks, list):
            user_decks = []
        user_cards = payload.get("userCards")
        if not isinstance(user_cards, list):
            user_cards = []

        profile_name, profile_color = _parse_colored_name(
            user_gamedata.get("name") or "未知玩家"
        )
        profile = SuiteProfile(
            user_id=str(user_gamedata.get("userId") or game_id),
            name=profile_name,
            rank=_as_int(user_gamedata.get("rank")),
            upload_time=_as_int(
                payload.get("upload_time") or payload.get("uploadTime")
            ),
            name_color=profile_color,
        )
        return SuiteB30Data(
            profile=profile,
            music_results=music_results,
            user_decks=user_decks,
            user_cards=user_cards,
            default_deck_id=_as_int(user_gamedata.get("deck")),
            source_url=url,
        )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_NAME_COLOR_RE = re.compile(r"^<#(?P<color>[0-9a-fA-F]{3}|[0-9a-fA-F]{6})>")


def _parse_colored_name(value: Any) -> tuple[str, str]:
    # Suite 昵称可能带 Moebot 风格的前置颜色标签，展示层需要名字和颜色分开使用。
    text = str(value or "").strip() or "未知玩家"
    match = _NAME_COLOR_RE.match(text)
    if not match:
        return text, ""
    color = match.group("color")
    if len(color) == 3:
        color = "".join(part * 2 for part in color)
    name = text[match.end() :] or text
    return name, f"#{color.lower()}"


suite_provider = SuiteProvider()
