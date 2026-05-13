from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any

import httpx

from zhenxun.services.sekai_resource.config import get_settings as get_resource_settings
from zhenxun.utils.exception import AllURIsFailedError

from ..adapters.runtime import AsyncHttpx, logger
from ..config import get_settings
from ..constants import MODULE_NAME, PROFILE_STATIC_ASSET_DIR, SERVER_SET, STATE_DIR
from ..storage import JsonStateStore, PathBinaryFileStore

_PROFILE_API_PLACEHOLDER_RE = re.compile(r"(?i)%7buser_id%7d")
_PROFILE_DIFF_ORDER = ("easy", "normal", "hard", "expert", "master", "append")
_PROFILE_STATIC_MISS_STORE = JsonStateStore(
    STATE_DIR / "profile_static_miss_cache.json"
)


class ProfileRenderError(RuntimeError):
    """个人档案内部渲染链路的预期失败。"""


class ProfileApiError(ProfileRenderError):
    """profile 原始接口请求或解析失败。"""


class ProfileProcessingError(ProfileRenderError):
    """profile 原始数据缺失关键字段，无法生成内部视图模型。"""


class ProfileAssetError(ProfileRenderError):
    """profile 静态资源缺失，无法继续内部渲染。"""


class ProfileProvider:
    @staticmethod
    def _normalize_base_url(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if not text.startswith(("http://", "https://")):
            text = f"https://{text.lstrip('/')}"
        return text.rstrip("/")

    @classmethod
    def build_profile_url(cls, base_url: str, user_id: str) -> str:
        normalized_base = cls._normalize_base_url(base_url)
        if not normalized_base:
            raise ProfileApiError("未配置 profile API 基础地址")

        # Uni 的上游路由要求保留 `%7Buser_id%7D` 这段路径哨兵，
        # 真正的用户 ID 仍需继续向后追加；如果直接替换，
        # 会拿到 `"internal server error"`。
        if _PROFILE_API_PLACEHOLDER_RE.search(normalized_base):
            if normalized_base.endswith("/profile"):
                return f"{normalized_base.removesuffix('/profile')}/{user_id}/profile"
            return f"{normalized_base}/{user_id}/profile"

        # 对普通的 `{user_id}` 自定义占位符仍保留替换支持，方便兼容其他镜像源。
        if "{user_id}" in normalized_base:
            resolved = normalized_base.replace("{user_id}", user_id)
            return (
                resolved
                if resolved.endswith("/profile")
                else f"{resolved.rstrip('/')}/profile"
            )

        return (
            normalized_base
            if normalized_base.endswith("/profile")
            else f"{normalized_base}/{user_id}/profile"
        )

    @staticmethod
    def _token_headers(token: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "MoeSekai/1.0",
        }
        if token:
            headers["X-Haruki-Sekai-Token"] = token
        return headers

    @staticmethod
    def _base_for_server(server: str) -> str:
        settings = get_settings()
        server_name = str(server).strip().lower()
        mapping = {
            "jp": settings.profile_api_base_jp,
            "cn": settings.profile_api_base_cn,
            "tw": settings.profile_api_base_tw,
        }
        return str(mapping.get(server_name, "") or "").strip()

    async def get_raw_profile(self, server: str, user_id: str) -> dict[str, Any]:
        normalized_server = str(server).strip().lower()
        if normalized_server not in SERVER_SET:
            raise ProfileApiError(f"不支持的区服: {server}")
        url = self.build_profile_url(
            self._base_for_server(normalized_server),
            str(user_id).strip(),
        )
        try:
            response = await AsyncHttpx.get(
                url,
                timeout=30,
                headers=self._token_headers(get_settings().profile_api_token),
            )
        except Exception as exc:
            raise ProfileApiError(
                f"profile API 请求失败: {normalized_server}/{user_id}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProfileApiError(
                f"profile API 返回的不是有效 JSON: {normalized_server}/{user_id}"
            ) from exc

        if not isinstance(payload, dict):
            raise ProfileApiError(
                f"profile API 返回格式异常: {normalized_server}/{user_id}"
            )
        return payload


class ProfileProcessor:
    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_str(value: Any, default: str = "") -> str:
        if value is None:
            return default
        return str(value)

    @classmethod
    def _parse_user_honor_record(cls, payload: Any) -> tuple[int, int] | None:
        if isinstance(payload, list) and len(payload) >= 2:
            return cls._as_int(payload[0]), cls._as_int(payload[1], 1)
        if isinstance(payload, dict):
            return (
                cls._as_int(payload.get("honorId")),
                cls._as_int(payload.get("level"), 1),
            )
        return None

    @classmethod
    def _build_level_map(cls, payloads: list[Any], *, id_key: str) -> dict[int, int]:
        result: dict[int, int] = {}
        for item in payloads:
            if not isinstance(item, dict):
                continue
            item_id = cls._as_int(item.get(id_key))
            if item_id <= 0:
                continue
            result[item_id] = cls._as_int(item.get("level"), 1)
        return result

    @classmethod
    def _card_lookup(cls, cards: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for item in cards:
            if not isinstance(item, dict):
                continue
            item_id = cls._as_int(item.get("id"))
            if item_id > 0:
                result[item_id] = item
        return result

    @classmethod
    def _honor_lookup(cls, honors: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for item in honors:
            if not isinstance(item, dict):
                continue
            item_id = cls._as_int(item.get("id"))
            if item_id > 0:
                result[item_id] = item
        return result

    @classmethod
    def _honor_group_lookup(
        cls,
        honor_groups: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for item in honor_groups:
            if not isinstance(item, dict):
                continue
            item_id = cls._as_int(item.get("id"))
            if item_id > 0:
                result[item_id] = item
        return result

    @classmethod
    def _resolve_honor_description(
        cls,
        honor: dict[str, Any],
        group: dict[str, Any] | None,
    ) -> str:
        levels = honor.get("levels")
        if isinstance(levels, list):
            for item in levels:
                if not isinstance(item, dict):
                    continue
                description = cls._as_str(item.get("description")).strip()
                if description:
                    return description
        if group:
            return cls._as_str(group.get("name")).strip()
        return ""

    @classmethod
    def _process_deck(
        cls,
        raw: dict[str, Any],
        card_map: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        user_deck = raw.get("userDeck") if isinstance(raw.get("userDeck"), dict) else {}
        deck = {
            "name": cls._as_str(user_deck.get("name")),
            "leader": cls._as_int(user_deck.get("leader")),
            "members": [],
        }
        member_ids = [
            cls._as_int(user_deck.get("member1")),
            cls._as_int(user_deck.get("member2")),
            cls._as_int(user_deck.get("member3")),
            cls._as_int(user_deck.get("member4")),
            cls._as_int(user_deck.get("member5")),
        ]

        user_card_map: dict[int, dict[str, Any]] = {}
        for item in raw.get("userCards") or []:
            if not isinstance(item, dict):
                continue
            card_id = cls._as_int(item.get("cardId"))
            if card_id > 0:
                user_card_map[card_id] = item

        for card_id in member_ids:
            if card_id <= 0:
                continue
            user_card = user_card_map.get(
                card_id,
                {
                    "cardId": card_id,
                    "level": 1,
                    "masterRank": 0,
                    "defaultImage": "original",
                },
            )
            master_card = card_map.get(card_id, {})
            deck["members"].append(
                {
                    "cardId": card_id,
                    "characterId": cls._as_int(master_card.get("characterId")),
                    "level": cls._as_int(user_card.get("level"), 1),
                    "masterRank": cls._as_int(user_card.get("masterRank")),
                    "defaultImage": cls._as_str(
                        user_card.get("defaultImage"),
                        "original",
                    ),
                    "isLeader": card_id == deck["leader"],
                    "rarity": cls._as_str(master_card.get("cardRarityType")),
                    "attr": cls._as_str(master_card.get("attr")),
                    "supportUnit": cls._as_str(master_card.get("supportUnit")),
                    "assetbundleName": cls._as_str(master_card.get("assetbundleName")),
                }
            )
        return deck

    @classmethod
    def _process_honors(
        cls,
        raw: dict[str, Any],
        honor_map: dict[int, dict[str, Any]],
        honor_group_map: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        honor_level_map: dict[int, int] = {}
        for item in raw.get("userHonors") or []:
            record = cls._parse_user_honor_record(item)
            if record:
                honor_level_map[record[0]] = record[1]

        bonds_level_map = cls._build_level_map(
            raw.get("userBondsHonors") or [],
            id_key="bondsHonorId",
        )

        result: list[dict[str, Any]] = []
        for item in raw.get("userProfileHonors") or []:
            if not isinstance(item, dict):
                continue
            honor_id = cls._as_int(item.get("honorId"))
            if honor_id <= 0:
                continue
            profile_type = cls._as_str(item.get("profileHonorType")).strip() or "normal"
            is_bonds = profile_type == "bonds"
            level = (
                bonds_level_map.get(honor_id, 1)
                if is_bonds
                else honor_level_map.get(honor_id, 1)
            )

            honor = honor_map.get(honor_id, {})
            group = honor_group_map.get(cls._as_int(honor.get("groupId")))
            honor_type = cls._as_str(group.get("honorType") if group else "")
            group_name = cls._as_str(group.get("name") if group else "")
            level_display = (
                group_name
                if honor_type in {"event", "sekai_echo"} and group_name
                else f"Lv.{level}"
            )

            result.append(
                {
                    "seq": cls._as_int(item.get("seq")),
                    "honorId": honor_id,
                    "name": cls._as_str(honor.get("name"), f"徽章 #{honor_id}"),
                    "level": level,
                    "levelDisplay": level_display,
                    "type": profile_type,
                    "rarity": cls._as_str(honor.get("honorRarity"), "low"),
                    "isBonds": is_bonds,
                    "assetbundleName": cls._as_str(honor.get("assetbundleName")),
                    "description": cls._resolve_honor_description(honor, group),
                }
            )

        return sorted(result, key=lambda item: item.get("seq", 0))

    @classmethod
    def _process_character_ranks(
        cls,
        raw: dict[str, Any],
    ) -> tuple[dict[int, int], int]:
        result: dict[int, int] = {}
        max_rank = 0
        top_character_id = 1
        for item in raw.get("userCharacters") or []:
            if not isinstance(item, dict):
                continue
            character_id = cls._as_int(item.get("characterId"))
            if character_id <= 0:
                continue
            rank = cls._as_int(item.get("characterRank"))
            result[character_id] = rank
            if rank > max_rank:
                max_rank = rank
                top_character_id = character_id
        return result, top_character_id

    @classmethod
    def _process_challenge_live(cls, raw: dict[str, Any]) -> dict[str, Any]:
        result = {
            "displayCharacterId": 0,
            "displayHighScore": 0,
            "characterStages": {},
        }
        solo_result = (
            raw.get("userChallengeLiveSoloResult")
            if isinstance(raw.get("userChallengeLiveSoloResult"), dict)
            else {}
        )
        result["displayCharacterId"] = cls._as_int(solo_result.get("characterId"))
        result["displayHighScore"] = cls._as_int(solo_result.get("highScore"))

        stages: dict[int, int] = {}
        for item in raw.get("userChallengeLiveSoloStages") or []:
            if not isinstance(item, dict):
                continue
            character_id = cls._as_int(item.get("characterId"))
            if character_id <= 0:
                continue
            rank = cls._as_int(item.get("rank"))
            if rank > stages.get(character_id, 0):
                stages[character_id] = rank
        result["characterStages"] = stages
        return result

    @classmethod
    def _process_music_stats(cls, raw: dict[str, Any]) -> list[dict[str, Any]]:
        stats_map: dict[str, dict[str, Any]] = {}
        for item in raw.get("userMusicDifficultyClearCount") or []:
            if not isinstance(item, dict):
                continue
            difficulty = cls._as_str(item.get("musicDifficultyType")).strip().lower()
            if difficulty:
                stats_map[difficulty] = item

        result: list[dict[str, Any]] = []
        for difficulty in _PROFILE_DIFF_ORDER:
            item = stats_map.get(difficulty, {})
            result.append(
                {
                    "difficulty": difficulty,
                    "clear": cls._as_int(item.get("liveClear")),
                    "fullCombo": cls._as_int(item.get("fullCombo")),
                    "allPerfect": cls._as_int(item.get("allPerfect")),
                }
            )
        return result

    @classmethod
    def process(
        cls,
        raw: dict[str, Any],
        *,
        cards: list[dict[str, Any]],
        honors: list[dict[str, Any]],
        honor_groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        if not user:
            raise ProfileProcessingError("profile 原始数据缺少 user")

        user_profile = (
            raw.get("userProfile") if isinstance(raw.get("userProfile"), dict) else {}
        )
        total_power = (
            raw.get("totalPower") if isinstance(raw.get("totalPower"), dict) else {}
        )
        top_score = (
            raw.get("userMultiLiveTopScoreCount")
            if isinstance(raw.get("userMultiLiveTopScoreCount"), dict)
            else {}
        )
        card_map = cls._card_lookup(cards)
        honor_map = cls._honor_lookup(honors)
        honor_group_map = cls._honor_group_lookup(honor_groups)
        character_ranks, top_character_id = cls._process_character_ranks(raw)

        return {
            "userId": cls._as_str(user.get("userId")),
            "name": cls._as_str(user.get("name")),
            "rank": cls._as_int(user.get("rank")),
            "word": cls._as_str(user_profile.get("word")),
            "twitterId": cls._as_str(user_profile.get("twitterId")),
            "totalPower": cls._as_int(total_power.get("totalPower")),
            "mvp": cls._as_int(top_score.get("mvp")),
            "superStar": cls._as_int(top_score.get("superStar")),
            "topCharacterId": top_character_id,
            "deck": cls._process_deck(raw, card_map),
            "honors": cls._process_honors(raw, honor_map, honor_group_map),
            "botHonors": [],
            "characterRanks": character_ranks,
            "challengeLive": cls._process_challenge_live(raw),
            "musicStats": cls._process_music_stats(raw),
        }


class ProfileStaticAssetProvider:
    def __init__(
        self,
        store: PathBinaryFileStore | None = None,
        miss_store: JsonStateStore | None = None,
    ) -> None:
        self._store = store or PathBinaryFileStore(PROFILE_STATIC_ASSET_DIR)
        self._miss_store = miss_store or _PROFILE_STATIC_MISS_STORE
        self._miss_state_cache: dict[str, float] | None = None

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        normalized = PurePosixPath(str(relative_path).strip("/"))
        if not normalized.parts:
            raise ValueError("relative_path 不能为空")
        return normalized.as_posix()

    @staticmethod
    def _build_url(base_url: str, relative_path: str) -> str:
        return f"{base_url.rstrip('/')}/{relative_path.lstrip('/')}"

    def _get_local_path(self, relative_path: str) -> Path | None:
        path = self._store.resolve_path(self._normalize_relative_path(relative_path))
        return path if path.is_file() else None

    @staticmethod
    def _normalize_miss_state(
        payload: Any,
        *,
        now: float,
    ) -> tuple[dict[str, float], bool]:
        if not isinstance(payload, dict):
            return {}, False
        changed = False
        result: dict[str, float] = {}
        for url, expire_at in payload.items():
            try:
                expire_ts = float(expire_at)
            except (TypeError, ValueError):
                changed = True
                continue
            if expire_ts <= now:
                changed = True
                continue
            result[str(url)] = expire_ts
        return result, changed

    def _load_miss_state(self) -> dict[str, float]:
        now = time.time()
        if self._miss_state_cache is None:
            payload = self._miss_store.load({})
            normalized, changed = self._normalize_miss_state(payload, now=now)
            self._miss_state_cache = normalized
            if changed:
                self._miss_store.save(normalized)
            return normalized

        expired = [
            url for url, expire_at in self._miss_state_cache.items() if expire_at <= now
        ]
        if not expired:
            return self._miss_state_cache
        for url in expired:
            self._miss_state_cache.pop(url, None)
        self._miss_store.save(self._miss_state_cache)
        return self._miss_state_cache

    def _record_missing(self, url: str) -> None:
        payload = self._load_miss_state()
        payload[url] = (
            time.time() + get_resource_settings().asset_miss_cache_ttl_seconds
        )
        self._miss_state_cache = payload
        self._miss_store.save(payload)

    def _clear_missing(self, url: str) -> None:
        payload = self._load_miss_state()
        if url in payload:
            payload.pop(url, None)
            self._miss_state_cache = payload
            self._miss_store.save(payload)

    async def ensure_local_path(
        self,
        relative_candidates: list[str],
        *,
        timeout: float = 20,
    ) -> Path | None:
        for relative_path in relative_candidates:
            normalized = self._normalize_relative_path(relative_path)
            if local_path := self._get_local_path(normalized):
                return local_path

            for base_url in get_settings().profile_static_asset_bases:
                candidate_url = self._build_url(base_url, normalized)
                if candidate_url in self._load_miss_state():
                    continue
                try:
                    content = await AsyncHttpx.get_content(
                        candidate_url,
                        timeout=timeout,
                    )
                except AllURIsFailedError as exc:
                    last_error = exc.exceptions[-1] if exc.exceptions else None
                    if (
                        isinstance(last_error, httpx.HTTPStatusError)
                        and last_error.response.status_code == 404
                    ):
                        self._record_missing(candidate_url)
                    continue
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        self._record_missing(candidate_url)
                    continue
                except httpx.RequestError:
                    continue
                if not content:
                    continue

                self._clear_missing(candidate_url)
                return self._store.save(normalized, content)
        return None

    async def get_json(self, relative_path: str) -> Any:
        path = await self.ensure_local_path([relative_path])
        if path is None:
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                f"MoeSekai profile 静态 JSON 解析失败: {relative_path}",
                MODULE_NAME,
                e=exc,
            )
            return {}

    async def get_text(self, relative_path: str) -> str | None:
        path = await self.ensure_local_path([relative_path])
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                f"MoeSekai profile 静态文本读取失败: {relative_path}",
                MODULE_NAME,
                e=exc,
            )
            return None

    async def get_costume_icon(self, card_id: int) -> Path | None:
        return await self.ensure_local_path([f"costume_icons/{card_id}.png"])

    async def get_base_chibi(self, base_name: str) -> Path | None:
        return await self.ensure_local_path(
            [
                f"base_chibis/{base_name}.png",
                f"base_chibis/{base_name}.webp",
            ]
        )

    async def get_honor_asset_svg(self, filename: str) -> str | None:
        return await self.get_text(f"honor_assets/{filename}")

    async def get_credits(self) -> dict[str, Any]:
        payload = await self.get_json("credits.json")
        return payload if isinstance(payload, dict) else {}


profile_provider = ProfileProvider()
profile_static_asset_provider = ProfileStaticAssetProvider()
