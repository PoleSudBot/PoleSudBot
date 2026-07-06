from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from zhenxun.utils.exception import RenderingError

from ..config import get_settings
from ..constants import MODULE_NAME
from ..providers import asset_provider, master_data_provider
from ..providers.profile import (
    ProfileAssetError,
    ProfileProcessingError,
    ProfileProcessor,
    ProfileRenderError,
    profile_provider,
    profile_static_asset_provider,
)
from .runtime import AsyncHttpx, logger

_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "profile" / "index.html"
)
_PROFILE_DATA_DIR = _TEMPLATE_PATH.parent / "data"
_PROFILE_ATTR_ICON_DIR = _PROFILE_DATA_DIR / "attr"
_PROFILE_UNIT_ICON_DIR = _PROFILE_DATA_DIR / "icon"
_HONOR_DECO_MAP = {
    "low": None,
    "middle": "honor_feather_re.svg",
    "high": "honor_flower.svg",
    "highest": "honor_star.svg",
}
_HONOR_MIDDLE_MAP = {
    "low": None,
    "middle": "middle_feather.svg",
    "high": "middle_flower.svg",
    "highest": "middle_star.svg",
}
_ATTR_ICON_MAP = {
    "cute": _PROFILE_ATTR_ICON_DIR / "cute.png",
    "cool": _PROFILE_ATTR_ICON_DIR / "cool.png",
    "pure": _PROFILE_ATTR_ICON_DIR / "pure.png",
    "happy": _PROFILE_ATTR_ICON_DIR / "happy.png",
    "mysterious": _PROFILE_ATTR_ICON_DIR / "mysterious.png",
}
_CHAR_NAMES = {
    1: "一歌",
    2: "咲希",
    3: "穗波",
    4: "志步",
    5: "实乃理",
    6: "遥",
    7: "爱莉",
    8: "雫",
    9: "心羽",
    10: "杏",
    11: "彰人",
    12: "冬弥",
    13: "司",
    14: "笑梦",
    15: "宁宁",
    16: "类",
    17: "奏",
    18: "真冬",
    19: "绘名",
    20: "瑞希",
    21: "Miku",
    22: "Rin",
    23: "Len",
    24: "Luka",
    25: "MEIKO",
    26: "KAITO",
}
_CHAR_COLORS = {
    "1": "#33aaee",
    "2": "#ffdd44",
    "3": "#ee6666",
    "4": "#BBDD22",
    "5": "#FFCCAA",
    "6": "#99CCFF",
    "7": "#ffaacc",
    "8": "#99EEDD",
    "9": "#ff6699",
    "10": "#00BBDD",
    "11": "#ff7722",
    "12": "#0077DD",
    "13": "#FFBB00",
    "14": "#FF66BB",
    "15": "#33DD99",
    "16": "#BB88EE",
    "17": "#bb6688",
    "18": "#8888CC",
    "19": "#CCAA88",
    "20": "#DDAACC",
    "21": "#33ccbb",
    "22": "#ffcc11",
    "23": "#FFEE11",
    "24": "#FFBBCC",
    "25": "#DD4444",
    "26": "#3366CC",
}
_NAME_COLOR_RE = re.compile(r"^<#(?P<color>[0-9a-fA-F]{3}|[0-9a-fA-F]{6})>")


def _shade_color(color: str, percent: int) -> str:
    text = str(color or "#33ccbb").lstrip("#")
    if len(text) != 6:
        return "#2ab3a3"
    value = int(text, 16)
    amount = round(2.55 * percent)
    red = max(0, min(255, (value >> 16) + amount))
    green = max(0, min(255, ((value >> 8) & 0xFF) + amount))
    blue = max(0, min(255, (value & 0xFF) + amount))
    return f"#{(red << 16 | green << 8 | blue):06x}"


def _theme_color(character_id: int) -> str:
    return _CHAR_COLORS.get(str(character_id), "#33ccbb")


def _path_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.absolute().as_uri()


def _png_data_uri(payload: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(payload).decode('ascii')}"


def _svg_data_uri(svg_content: str, color: str) -> str:
    fixed = svg_content.replace("var(--honor-theme)", color)
    return f"data:image/svg+xml;charset=utf-8,{quote(fixed)}"


def _base_chibi_name(character_id: int, support_unit: str) -> str:
    if character_id <= 0:
        return "1"
    if character_id == 21:
        suffix_map = {
            "light_sound": "-1",
            "idol": "-2",
            "street": "-3",
            "theme_park": "-4",
            "school_refusal": "-5",
        }
        return f"21{suffix_map.get(support_unit, '')}"
    return str(character_id)


def _is_high_rarity(rarity: str) -> bool:
    text = str(rarity or "").lower()
    return "4" in text or "birthday" in text


def _build_chibi_candidate_group(member: dict[str, Any]) -> list[str]:
    card_id = int(member.get("cardId") or 0)
    character_id = int(member.get("characterId") or 0)
    support_unit = str(member.get("supportUnit") or "")
    rarity = str(member.get("rarity") or "")
    relative_fallbacks: list[str] = []

    if _is_high_rarity(rarity) and card_id > 0:
        relative_fallbacks.append(f"costume_icons/{card_id}.png")

    base_name = _base_chibi_name(character_id, support_unit)
    relative_fallbacks.extend(
        [
            f"base_chibis/{base_name}.png",
            f"base_chibis/{base_name}.webp",
        ]
    )
    if base_name != str(character_id) and character_id > 0:
        relative_fallbacks.extend(
            [
                f"base_chibis/{character_id}.png",
                f"base_chibis/{character_id}.webp",
            ]
        )
    relative_fallbacks.extend(["base_chibis/1.webp", "base_chibis/1.png"])
    return relative_fallbacks


def _profile_static_group_key(candidates: list[str]) -> tuple[str, ...]:
    return tuple(str(item).strip("/") for item in candidates if str(item).strip("/"))


async def _prefetch_profile_static_assets(
    members: list[Any],
    honors: list[Any],
) -> None:
    groups: list[list[str]] = [["credits.json"]]
    for member in members:
        if isinstance(member, dict):
            groups.append(_build_chibi_candidate_group(member))
    for honor in honors:
        if not isinstance(honor, dict):
            continue
        rarity = str(honor.get("rarity") or "low").strip() or "low"
        for filename in (_HONOR_DECO_MAP.get(rarity), _HONOR_MIDDLE_MAP.get(rarity)):
            if filename:
                groups.append([f"honor_assets/{filename}"])

    # 相同候选组只预取一次；实际渲染阶段仍保留缺图兜底和报错语义。
    deduped_groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        key = _profile_static_group_key(group)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped_groups.append(group)
    await profile_static_asset_provider.ensure_many_local_paths(deduped_groups)


def _normalize_star_view(rarity: str) -> tuple[int, bool]:
    text = str(rarity or "").lower()
    if "birthday" in text:
        return 0, True
    for char in text:
        if char.isdigit():
            return int(char), False
    return 1, False


def _split_display_name_color(value: Any) -> tuple[str, str]:
    # Suite 昵称可能带前置颜色标签，模板需要把颜色和实际昵称分开渲染。
    text = str(value or "").strip() or "Unknown"
    match = _NAME_COLOR_RE.match(text)
    if not match:
        return text, ""
    color = match.group("color")
    if len(color) == 3:
        color = "".join(part * 2 for part in color)
    name = text[match.end() :] or text
    return name, f"#{color.lower()}"


def _build_credits_list(
    deck: dict[str, Any],
    credits_data: dict[str, Any],
) -> list[dict[str, str]]:
    members = deck.get("members") if isinstance(deck, dict) else []
    if not isinstance(members, list) or not isinstance(credits_data, dict):
        return []

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for member in members:
        if not isinstance(member, dict):
            continue
        card_id = str(member.get("cardId") or "").strip()
        if not card_id or card_id in seen:
            continue
        credit = credits_data.get(card_id)
        if not isinstance(credit, dict):
            continue
        source_type = str(credit.get("source_type") or "").strip()
        if source_type not in {"original", "trace"}:
            continue
        result.append(
            {
                "cardId": card_id,
                "author": str(credit.get("author") or "").strip(),
                "type": source_type,
            }
        )
        seen.add(card_id)
    return result


async def _ensure_card_thumbnail_uri(server: str, member: dict[str, Any]) -> str:
    assetbundle_name = str(member.get("assetbundleName") or "").strip()
    card_id = member.get("cardId")
    if not assetbundle_name:
        raise ProfileProcessingError(f"卡牌 {card_id} 缺少 assetbundleName")

    prefer_after_training = str(member.get("defaultImage") or "") == "special_training"
    candidate_after_training = [prefer_after_training]
    if prefer_after_training:
        candidate_after_training.append(False)

    for after_training in candidate_after_training:
        local_path = asset_provider.get_card_image_local_path(
            server,
            assetbundle_name,
            after_training=after_training,
            thumbnail=True,
        )
        if local_path is not None:
            return local_path.absolute().as_uri()
        content = await asset_provider.get_card_image(
            server,
            assetbundle_name,
            after_training=after_training,
            thumbnail=True,
        )
        local_path = asset_provider.get_card_image_local_path(
            server,
            assetbundle_name,
            after_training=after_training,
            thumbnail=True,
        )
        if local_path is not None:
            return local_path.absolute().as_uri()
        if content:
            return _png_data_uri(content)

    raise ProfileAssetError(f"卡牌缩略图缺失: {card_id}")


async def _ensure_chibi_uri(member: dict[str, Any]) -> str:
    card_id = int(member.get("cardId") or 0)
    local_path = await profile_static_asset_provider.ensure_local_path(
        _build_chibi_candidate_group(member)
    )
    if local_path is None:
        raise ProfileAssetError(f"小人资源缺失: {card_id}")
    return local_path.absolute().as_uri()


async def _build_honor_view_model(
    honor: dict[str, Any],
    theme_color: str,
) -> dict[str, Any]:
    rarity = str(honor.get("rarity") or "low").strip() or "low"
    deco_uri = None
    middle_uri = None
    deco_file = _HONOR_DECO_MAP.get(rarity)
    middle_file = _HONOR_MIDDLE_MAP.get(rarity)

    if deco_file:
        svg_content = await profile_static_asset_provider.get_honor_asset_svg(deco_file)
        if svg_content:
            deco_uri = _svg_data_uri(svg_content, theme_color)
    if middle_file:
        svg_content = await profile_static_asset_provider.get_honor_asset_svg(
            middle_file
        )
        if svg_content:
            middle_uri = _svg_data_uri(svg_content, theme_color)

    capsule_classes: list[str] = []
    if rarity == "middle":
        capsule_classes.append("honor-feather")
    elif rarity == "high":
        capsule_classes.append("honor-flower")
    elif rarity == "highest":
        capsule_classes.append("honor-star")
    if not deco_file:
        capsule_classes.append("honor-simple")

    style = {}
    if not deco_file:
        style = {
            "background": (
                f"linear-gradient(90deg, {theme_color}18, "
                f"{theme_color}08, {theme_color}18)"
            ),
            "border": f"2px solid {theme_color}44",
        }
    elif middle_uri:
        style = {"background-image": f"url('{middle_uri}')"}

    return {
        **honor,
        "rarity": rarity,
        "decoUri": deco_uri,
        "middleUri": middle_uri,
        "capsuleClasses": " ".join(capsule_classes),
        "capsuleStyle": "; ".join(f"{key}: {value}" for key, value in style.items()),
        "isRankingHonor": str(honor.get("assetbundleName") or "").startswith(
            "honor_top_"
        ),
        "displayTag": str(honor.get("levelDisplay") or f"Lv.{honor.get('level') or 1}"),
    }


async def _load_announcement_html() -> str | None:
    announcement_url = str(get_settings().profile_announcement_url or "").strip()
    if not announcement_url:
        return None
    try:
        response = await AsyncHttpx.get(announcement_url, timeout=20)
    except Exception as exc:
        logger.warning("MoeSekai 个人档案公告加载失败", MODULE_NAME, e=exc)
        return None
    text = response.text.strip()

    def _extract_announcement_html(payload: Any) -> str | None:
        if isinstance(payload, str):
            content = payload.strip()
            if content[:1] in {"{", "["}:
                try:
                    return _extract_announcement_html(json.loads(content))
                except ValueError:
                    pass
            return content or None
        if not isinstance(payload, dict):
            return None
        if payload.get("enabled") is False:
            return None
        # 原版前端会先解析 JSON，再从公告字段中取最终可渲染内容。
        for key in ("content", "message", "text"):
            content = _extract_announcement_html(payload.get(key))
            if content:
                return content
        return None

    try:
        payload = response.json()
    except json.JSONDecodeError:
        return text or None
    except ValueError:
        return text or None

    return _extract_announcement_html(payload)


async def _build_render_payload(server: str, game_id: str) -> dict[str, Any]:
    raw_profile = await profile_provider.get_raw_profile(server, game_id)
    cards, honors, honor_groups = await asyncio.gather(
        master_data_provider.get_cards(server),
        master_data_provider.get_honors(server),
        master_data_provider.get_honor_groups(server),
    )
    processed = ProfileProcessor.process(
        raw_profile,
        cards=cards,
        honors=honors,
        honor_groups=honor_groups,
    )
    top_character_id = int(processed.get("topCharacterId") or 1)

    leader = None
    members = processed["deck"].get("members", [])
    if isinstance(members, list):
        for item in members:
            if isinstance(item, dict) and item.get("isLeader"):
                leader = item
                break
        if leader is None and members:
            leader = members[0]
    if not isinstance(leader, dict):
        raise ProfileProcessingError("profile 编队为空")

    # 这里优先使用编队 leader 角色，和原版前端的主题色/页脚逻辑保持一致；
    # topCharacterId 只在 leader 缺失或字段异常时兜底，避免静态图和旧网页截图不一致。
    theme_character_id = int(leader.get("characterId") or top_character_id or 1)
    theme_color = _theme_color(theme_character_id)
    theme_dark = _shade_color(theme_color, -15)

    _, announcement_html = await asyncio.gather(
        _prefetch_profile_static_assets(members, processed.get("honors", [])),
        _load_announcement_html(),
    )
    credits_data = await profile_static_asset_provider.get_credits()

    avatar_uri = await _ensure_card_thumbnail_uri(server, leader)

    async def _build_member(member: dict[str, Any]) -> dict[str, Any]:
        star_count, is_birthday = _normalize_star_view(str(member.get("rarity") or ""))
        thumbnail_uri, chibi_uri = await asyncio.gather(
            _ensure_card_thumbnail_uri(server, member),
            _ensure_chibi_uri(member),
        )
        attr_key = str(member.get("attr") or "").strip().lower()
        attr_icon = _ATTR_ICON_MAP.get(attr_key)
        return {
            **member,
            "thumbnailUri": thumbnail_uri,
            "chibiUri": chibi_uri,
            "attrIconUri": _path_uri(attr_icon),
            "starCount": star_count,
            "isBirthday": is_birthday,
        }

    built_members = await asyncio.gather(
        *(_build_member(member) for member in members if isinstance(member, dict))
    )
    built_honors = await asyncio.gather(
        *(
            _build_honor_view_model(honor, theme_color)
            for honor in processed.get("honors", [])
            if isinstance(honor, dict)
        )
    )

    challenge_live = processed.get("challengeLive")
    challenge_info = None
    if (
        isinstance(challenge_live, dict)
        and int(challenge_live.get("displayCharacterId") or 0) > 0
    ):
        challenge_character_id = int(challenge_live.get("displayCharacterId") or 0)
        challenge_info = {
            "charName": _CHAR_NAMES.get(challenge_character_id, "挑战演出"),
            "score": f"{int(challenge_live.get('displayHighScore') or 0):,}",
        }

    display_name, display_name_color = _split_display_name_color(
        processed.get("name") or "Unknown"
    )

    return {
        "server": server,
        "serverCode": server.upper(),
        "gameId": game_id,
        "themeColor": theme_color,
        "themeLight": f"{theme_color}22",
        "themeDark": theme_dark,
        "avatarUri": avatar_uri,
        "processed": processed,
        "displayName": display_name,
        "displayNameColor": display_name_color,
        "displayRank": int(processed.get("rank") or 0),
        "displayPower": f"{int(processed.get('totalPower') or 0):,}",
        "displayWord": str(processed.get("word") or "这个人很懒，什么都没写~"),
        "challengeInfo": challenge_info,
        "honors": built_honors,
        "deckMembers": built_members,
        "creditsList": _build_credits_list(processed.get("deck", {}), credits_data),
        "musicStats": processed.get("musicStats", []),
        "footerCharacterName": _CHAR_NAMES.get(theme_character_id, "Unknown"),
        "announcementHtml": announcement_html,
        "backgroundRows": list(range(30)),
    }


async def render_profile_image(server: str, game_id: str) -> bytes:
    payload = await _build_render_payload(server, game_id)
    try:
        from zhenxun import ui

        return await ui.render_template(
            _TEMPLATE_PATH,
            payload,
            use_cache=False,
            is_page=True,
            viewport={"width": get_settings().profile_viewport_width, "height": 10},
            wait=150,
            disable_animations=True,
        )
    except RenderingError:
        raise
    except Exception as exc:
        raise ProfileRenderError("个人档案内部模板渲染失败") from exc
