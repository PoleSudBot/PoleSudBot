from __future__ import annotations

from functools import lru_cache
import os
import sys
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from zhenxun.services.sekai_resource.config import (
    DEFAULT_ASSET_SOURCE_ORDER,
    DEFAULT_MASTER_SOURCE_ORDER,
    DEFAULT_MASTER_SOURCES,
    MASTER_DATASET_KEYS,
    MasterSourceConfig,
    _merge_master_sources_config,
    build_default_master_sources,
)

_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


class _CompatConfig:
    @staticmethod
    def get_config(_module: str, _key: str, default: Any = None) -> Any:
        return default


if not _TEST_MODE:
    try:
        from zhenxun.configs.config import Config
    except Exception:
        Config = _CompatConfig()  # type: ignore[assignment]
else:
    Config = _CompatConfig()  # type: ignore[assignment]

if not _TEST_MODE:
    try:
        from zhenxun.configs.utils.models import RegisterConfig
    except Exception:

        class RegisterConfig(BaseModel):
            key: str
            value: Any
            module: str | None = None
            help: str | None = None
            default_value: Any | None = None
            type: object = None
            arg_parser: Any = None
else:

    class RegisterConfig(BaseModel):
        key: str
        value: Any
        module: str | None = None
        help: str | None = None
        default_value: Any | None = None
        type: object = None
        arg_parser: Any = None


from .constants import (
    MODULE_NAME,
    normalize_deck_difficulty,
    normalize_live_type,
)

DEFAULT_PROFILE_STATIC_ASSET_BASES = [
    "https://raw.githubusercontent.com/Exmeaning/Exmeaning-Image-hosting/main",
    "https://cdn.jsdelivr.net/gh/Exmeaning/Exmeaning-Image-hosting@main",
]
LEGACY_PROFILE_TOKEN_DEFAULT = (
    "0357a6c752a7cb080bce495891911d0da722a907eecfc4d28b0ff952375466b0"
)


def _normalize_ranking_api_base_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "https://rk.exmeaning.com"
    if not text.startswith(("http://", "https://")):
        text = f"https://{text.lstrip('/')}"
    split = urlsplit(text)
    host = split.netloc.lower()
    path = split.path.strip("/")
    normalized = f"{split.scheme or 'https'}://{host}"
    if path:
        normalized = f"{normalized}/{path}"
    return normalized.rstrip("/")


class MoeSekaiSettings(BaseModel):
    profile_token: str = ""
    profile_render_mode: Literal["internal_first", "screenshot_only"] = "internal_first"
    profile_api_token: str = ""
    profile_api_base_jp: str = "https://api.unipjsk.com/api/user/%7Buser_id%7D"
    profile_api_base_cn: str = (
        "https://public-api.haruki.seiunx.com/sekai-api/v5/api/cn"
    )
    profile_api_base_tw: str = (
        "https://public-api.haruki.seiunx.com/sekai-api/v5/api/tw"
    )
    profile_static_asset_bases: list[str] = Field(
        default_factory=lambda: DEFAULT_PROFILE_STATIC_ASSET_BASES.copy()
    )
    profile_announcement_url: str = ""
    profile_bases: list[str] = Field(
        default_factory=lambda: ["https://sekaiprofile.exmeaning.com"]
    )
    profile_url_templates: list[str] = Field(default_factory=list)
    site_bases: list[str] = Field(
        default_factory=lambda: [
            "https://snowyviewer.exmeaning.com",
            "https://pjsk.moe",
        ]
    )
    ranking_api_base: str = "https://rk.exmeaning.com"
    ranking_screenshot_templates: list[str] = Field(
        default_factory=lambda: [
            "https://sekairanking.exmeaning.com/{server_path}simple"
        ]
    )
    ranking_history_screenshot_templates: list[str] = Field(
        default_factory=lambda: [
            "https://sekairanking.exmeaning.com/{server_path}event/{event_id}"
        ]
    )
    screenshot_quality: int = 85
    screenshot_retry_times: int = 3
    screenshot_retry_delay_seconds: int = 3
    screenshot_timeout_seconds: int = 45
    profile_viewport_width: int = 625
    ranking_viewport_width: int = 850
    deck_viewport_width: int = 750
    deck_top_crop: int = 75
    story_top_crop: int = 125
    character_top_crop: int = 100
    deck_wait_timeout_seconds: int = 120
    deck_default_music_id: int = 74
    deck_default_difficulty: str = "expert"
    deck_default_live_type: str = "multi"
    deck_strongest_default_music_id: int = 141
    deck_strongest_default_difficulty: str = "append"
    deck_challenge_default_music_id: int = 540
    deck_challenge_default_difficulty: str = "master"
    cache_mode: str = "REDIS"
    cache_ttl_seconds: int = 600
    character_cache_ttl_seconds: int = 1_209_600
    master_notify_superusers: bool = True
    new_card_auto_asset_timeout_seconds: float = 8.0
    new_card_media_fetch_concurrency: int = 4
    new_card_send_timeout_seconds: float = 12.0
    new_card_send_delay_min_seconds: float = 0.2
    new_card_send_delay_max_seconds: float = 0.8
    new_card_plain_card_max_estimated_bytes: int = 41_943_040
    new_card_plain_stamp_max_estimated_bytes: int = 10_485_760
    new_card_abort_after_consecutive_failures: int = 2
    new_card_forward_max_nodes_per_batch: int = 4
    new_card_forward_max_estimated_bytes: int = 10_485_760
    new_card_fallback_delay_min_seconds: float = 1.5
    new_card_fallback_delay_max_seconds: float = 3.0
    new_card_fallback_abort_after_consecutive_failures: int = 2
    alias_global_editor_groups: list[str] = Field(default_factory=list)
    alias_sync_interval_seconds: int = 21600
    theme_primary: str = "#FF6699"
    theme_primary_dark: str = "#E64D80"
    theme_text_dark: str = "#333333"
    theme_text_muted: str = "#888888"
    theme_text_light: str = "#FFFFFF"
    theme_bg_main: str = "#FFFFFF"
    theme_bg_card: str = "#FFFFFF"
    theme_border_light: str = "#F0F0F0"

    @field_validator(
        "profile_bases",
        "profile_url_templates",
        "profile_static_asset_bases",
        "site_bases",
        "ranking_screenshot_templates",
        "ranking_history_screenshot_templates",
    )
    @classmethod
    def _filter_empty_values(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if item and str(item).strip()]

    @field_validator("alias_global_editor_groups")
    @classmethod
    def _normalize_group_ids(cls, value: list[str | int]) -> list[str]:
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    @field_validator("screenshot_quality")
    @classmethod
    def _normalize_quality(cls, value: int) -> int:
        return max(1, min(100, value))

    @field_validator("profile_api_token")
    @classmethod
    def _normalize_github_token(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("screenshot_retry_times")
    @classmethod
    def _normalize_retry_times(cls, value: int) -> int:
        return max(1, min(5, value))

    @field_validator(
        "profile_viewport_width",
        "ranking_viewport_width",
        "deck_viewport_width",
    )
    @classmethod
    def _normalize_viewport_width(cls, value: int) -> int:
        return max(320, value)

    @field_validator("deck_top_crop", "story_top_crop", "character_top_crop")
    @classmethod
    def _normalize_crop_pixels(cls, value: int) -> int:
        return max(0, value)

    @field_validator("character_cache_ttl_seconds")
    @classmethod
    def _normalize_ttl_seconds(cls, value: int) -> int:
        return max(0, value)

    @field_validator(
        "deck_default_difficulty",
        "deck_strongest_default_difficulty",
        "deck_challenge_default_difficulty",
    )
    @classmethod
    def _normalize_difficulty(cls, value: str) -> str:
        return normalize_deck_difficulty(value) or "hard"

    @field_validator("deck_default_live_type")
    @classmethod
    def _normalize_live_type(cls, value: str) -> str:
        return normalize_live_type(value) or "multi"

    @field_validator("ranking_api_base")
    @classmethod
    def _normalize_ranking_api_base(cls, value: str) -> str:
        return _normalize_ranking_api_base_url(value)


def _build_profile_url_templates(settings: MoeSekaiSettings) -> list[str]:
    if settings.profile_url_templates:
        return settings.profile_url_templates
    return [
        f"{base.rstrip('/')}/profile/{{server}}/{{game_id}}"
        for base in settings.profile_bases
    ]


def _build_register_defaults() -> MoeSekaiSettings:
    return MoeSekaiSettings()


REGISTER_DEFAULTS = _build_register_defaults()

REGISTER_CONFIGS = [
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_TOKEN",
        value=REGISTER_DEFAULTS.profile_token,
        default_value=REGISTER_DEFAULTS.profile_token,
        help="MoeSekai 档案鉴权 Token；留空时不会拼接 token 参数",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_RENDER_MODE",
        value=REGISTER_DEFAULTS.profile_render_mode,
        default_value=REGISTER_DEFAULTS.profile_render_mode,
        help=(
            "个人档案渲染模式：internal_first 为内部生成优先，"
            "screenshot_only 为仅网页截图"
        ),
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_API_TOKEN",
        value=REGISTER_DEFAULTS.profile_api_token,
        default_value=REGISTER_DEFAULTS.profile_api_token,
        help="个人档案原始 profile API 的 X-Haruki-Sekai-Token；留空时不携带",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_API_BASE_JP",
        value=REGISTER_DEFAULTS.profile_api_base_jp,
        default_value=REGISTER_DEFAULTS.profile_api_base_jp,
        help="日服 profile API 基础地址；支持 {user_id} 占位符",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_API_BASE_CN",
        value=REGISTER_DEFAULTS.profile_api_base_cn,
        default_value=REGISTER_DEFAULTS.profile_api_base_cn,
        help="国服 profile API 基础地址；支持 {user_id} 占位符",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_API_BASE_TW",
        value=REGISTER_DEFAULTS.profile_api_base_tw,
        default_value=REGISTER_DEFAULTS.profile_api_base_tw,
        help="台服 profile API 基础地址；支持 {user_id} 占位符",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_STATIC_ASSET_BASES",
        value=list(REGISTER_DEFAULTS.profile_static_asset_bases),
        default_value=list(REGISTER_DEFAULTS.profile_static_asset_bases),
        help="个人档案静态资源源站列表，按顺序回退",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_ANNOUNCEMENT_URL",
        value=REGISTER_DEFAULTS.profile_announcement_url,
        default_value=REGISTER_DEFAULTS.profile_announcement_url,
        help="个人档案公告 JSON 地址；留空时不渲染公告",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_BASES",
        value=list(REGISTER_DEFAULTS.profile_bases),
        default_value=list(REGISTER_DEFAULTS.profile_bases),
        help="个人档案页面基础地址列表，默认会自动拼成 /profile/{server}/{game_id}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SITE_BASES",
        value=list(REGISTER_DEFAULTS.site_bases),
        default_value=list(REGISTER_DEFAULTS.site_bases),
        help="MoeSekai 网页站点列表，按顺序尝试，主要用于组卡/剧情/角色截图",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_API_BASE",
        value=REGISTER_DEFAULTS.ranking_api_base,
        default_value=REGISTER_DEFAULTS.ranking_api_base,
        help="榜线预测 API 基础地址",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_SCREENSHOT_TEMPLATES",
        value=list(REGISTER_DEFAULTS.ranking_screenshot_templates),
        default_value=list(REGISTER_DEFAULTS.ranking_screenshot_templates),
        help="ycx 榜线截图 URL 模板列表，支持 {server} {server_path}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_HISTORY_SCREENSHOT_TEMPLATES",
        value=list(REGISTER_DEFAULTS.ranking_history_screenshot_templates),
        default_value=list(REGISTER_DEFAULTS.ranking_history_screenshot_templates),
        help="ycx 历史活动截图 URL 模板列表，支持 {server} {server_path} {event_id}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_QUALITY",
        value=REGISTER_DEFAULTS.screenshot_quality,
        default_value=REGISTER_DEFAULTS.screenshot_quality,
        help="截图质量，1-100，越高越清晰但越慢",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_VIEWPORT_WIDTH",
        value=REGISTER_DEFAULTS.profile_viewport_width,
        default_value=REGISTER_DEFAULTS.profile_viewport_width,
        help="个人档案截图宽度（CSS viewport width）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_VIEWPORT_WIDTH",
        value=REGISTER_DEFAULTS.ranking_viewport_width,
        default_value=REGISTER_DEFAULTS.ranking_viewport_width,
        help="ycx 榜线截图宽度（CSS viewport width）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_VIEWPORT_WIDTH",
        value=REGISTER_DEFAULTS.deck_viewport_width,
        default_value=REGISTER_DEFAULTS.deck_viewport_width,
        help="组卡截图宽度（CSS viewport width）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_TOP_CROP",
        value=REGISTER_DEFAULTS.deck_top_crop,
        default_value=REGISTER_DEFAULTS.deck_top_crop,
        help="组卡截图顶部裁剪高度（CSS 像素）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_STORY_TOP_CROP",
        value=REGISTER_DEFAULTS.story_top_crop,
        default_value=REGISTER_DEFAULTS.story_top_crop,
        help="活动剧情截图顶部裁剪高度（CSS 像素）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_CHARACTER_TOP_CROP",
        value=REGISTER_DEFAULTS.character_top_crop,
        default_value=REGISTER_DEFAULTS.character_top_crop,
        help="角色页面截图顶部裁剪高度（CSS 像素）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_RETRY_TIMES",
        value=REGISTER_DEFAULTS.screenshot_retry_times,
        default_value=REGISTER_DEFAULTS.screenshot_retry_times,
        help="截图失败重试次数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_RETRY_DELAY_SECONDS",
        value=REGISTER_DEFAULTS.screenshot_retry_delay_seconds,
        default_value=REGISTER_DEFAULTS.screenshot_retry_delay_seconds,
        help="截图失败后下一次重试的等待秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_TIMEOUT_SECONDS",
        value=REGISTER_DEFAULTS.screenshot_timeout_seconds,
        default_value=REGISTER_DEFAULTS.screenshot_timeout_seconds,
        help="单次截图总超时秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_WAIT_TIMEOUT_SECONDS",
        value=REGISTER_DEFAULTS.deck_wait_timeout_seconds,
        default_value=REGISTER_DEFAULTS.deck_wait_timeout_seconds,
        help="组卡等待网页计算完成的超时秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_DEFAULT_MUSIC_ID",
        value=REGISTER_DEFAULTS.deck_default_music_id,
        default_value=REGISTER_DEFAULTS.deck_default_music_id,
        help="活动组卡默认歌曲 ID",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_DEFAULT_DIFFICULTY",
        value=REGISTER_DEFAULTS.deck_default_difficulty,
        default_value=REGISTER_DEFAULTS.deck_default_difficulty,
        help="活动组卡默认难度",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_DEFAULT_LIVE_TYPE",
        value=REGISTER_DEFAULTS.deck_default_live_type,
        default_value=REGISTER_DEFAULTS.deck_default_live_type,
        help="活动组卡默认 Live 类型",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_STRONGEST_DEFAULT_MUSIC_ID",
        value=REGISTER_DEFAULTS.deck_strongest_default_music_id,
        default_value=REGISTER_DEFAULTS.deck_strongest_default_music_id,
        help="最强组卡默认歌曲 ID",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_STRONGEST_DEFAULT_DIFFICULTY",
        value=REGISTER_DEFAULTS.deck_strongest_default_difficulty,
        default_value=REGISTER_DEFAULTS.deck_strongest_default_difficulty,
        help="最强组卡默认难度",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_CHALLENGE_DEFAULT_MUSIC_ID",
        value=REGISTER_DEFAULTS.deck_challenge_default_music_id,
        default_value=REGISTER_DEFAULTS.deck_challenge_default_music_id,
        help="挑战组卡默认歌曲 ID",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_CHALLENGE_DEFAULT_DIFFICULTY",
        value=REGISTER_DEFAULTS.deck_challenge_default_difficulty,
        default_value=REGISTER_DEFAULTS.deck_challenge_default_difficulty,
        help="挑战组卡默认难度",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_CACHE_MODE",
        value=REGISTER_DEFAULTS.cache_mode,
        default_value=REGISTER_DEFAULTS.cache_mode,
        help="插件缓存模式：REDIS / MEMORY / NONE",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_CACHE_TTL_SECONDS",
        value=REGISTER_DEFAULTS.cache_ttl_seconds,
        default_value=REGISTER_DEFAULTS.cache_ttl_seconds,
        help="插件缓存过期秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_CHARACTER_CACHE_TTL_SECONDS",
        value=REGISTER_DEFAULTS.character_cache_ttl_seconds,
        default_value=REGISTER_DEFAULTS.character_cache_ttl_seconds,
        help="查角色本地截图缓存过期秒数，默认 14 天",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_NOTIFY_SUPERUSERS",
        value=REGISTER_DEFAULTS.master_notify_superusers,
        default_value=REGISTER_DEFAULTS.master_notify_superusers,
        help="主数据自动更新成功后是否通知超级用户",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_AUTO_ASSET_TIMEOUT_SECONDS",
        value=REGISTER_DEFAULTS.new_card_auto_asset_timeout_seconds,
        default_value=REGISTER_DEFAULTS.new_card_auto_asset_timeout_seconds,
        help="自动新卡提醒单个资源抓取超时秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_MEDIA_FETCH_CONCURRENCY",
        value=REGISTER_DEFAULTS.new_card_media_fetch_concurrency,
        default_value=REGISTER_DEFAULTS.new_card_media_fetch_concurrency,
        help="自动新卡提醒资源抓取并发数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_SEND_TIMEOUT_SECONDS",
        value=REGISTER_DEFAULTS.new_card_send_timeout_seconds,
        default_value=REGISTER_DEFAULTS.new_card_send_timeout_seconds,
        help="自动新卡提醒单批发送超时秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_SEND_DELAY_MIN_SECONDS",
        value=REGISTER_DEFAULTS.new_card_send_delay_min_seconds,
        default_value=REGISTER_DEFAULTS.new_card_send_delay_min_seconds,
        help="自动新卡提醒普通消息之间的最小发送间隔秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_SEND_DELAY_MAX_SECONDS",
        value=REGISTER_DEFAULTS.new_card_send_delay_max_seconds,
        default_value=REGISTER_DEFAULTS.new_card_send_delay_max_seconds,
        help="自动新卡提醒普通消息之间的最大发送间隔秒数",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_PLAIN_CARD_MAX_ESTIMATED_BYTES",
        value=REGISTER_DEFAULTS.new_card_plain_card_max_estimated_bytes,
        default_value=REGISTER_DEFAULTS.new_card_plain_card_max_estimated_bytes,
        help="自动新卡提醒单条卡图消息的估算字节上限",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_PLAIN_STAMP_MAX_ESTIMATED_BYTES",
        value=REGISTER_DEFAULTS.new_card_plain_stamp_max_estimated_bytes,
        default_value=REGISTER_DEFAULTS.new_card_plain_stamp_max_estimated_bytes,
        help="自动新卡提醒单条表情消息的估算字节上限",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_NEW_CARD_ABORT_AFTER_CONSECUTIVE_FAILURES",
        value=REGISTER_DEFAULTS.new_card_abort_after_consecutive_failures,
        default_value=REGISTER_DEFAULTS.new_card_abort_after_consecutive_failures,
        help="自动新卡提醒连续失败多少次就中止该群本轮发送",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_ALIAS_GLOBAL_EDITOR_GROUPS",
        value=list(REGISTER_DEFAULTS.alias_global_editor_groups),
        default_value=list(REGISTER_DEFAULTS.alias_global_editor_groups),
        help="允许编辑全局别名的群号白名单",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_ALIAS_SYNC_INTERVAL_SECONDS",
        value=REGISTER_DEFAULTS.alias_sync_interval_seconds,
        default_value=REGISTER_DEFAULTS.alias_sync_interval_seconds,
        help="歌曲别名同步间隔秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_PRIMARY",
        value=REGISTER_DEFAULTS.theme_primary,
        default_value=REGISTER_DEFAULTS.theme_primary,
        help="MoeSekai 主题主色调",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_PRIMARY_DARK",
        value=REGISTER_DEFAULTS.theme_primary_dark,
        default_value=REGISTER_DEFAULTS.theme_primary_dark,
        help="MoeSekai 主题主色调（深色）",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_TEXT_DARK",
        value=REGISTER_DEFAULTS.theme_text_dark,
        default_value=REGISTER_DEFAULTS.theme_text_dark,
        help="MoeSekai 卡片主文本颜色",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_TEXT_MUTED",
        value=REGISTER_DEFAULTS.theme_text_muted,
        default_value=REGISTER_DEFAULTS.theme_text_muted,
        help="MoeSekai 卡片次要文本颜色",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_TEXT_LIGHT",
        value=REGISTER_DEFAULTS.theme_text_light,
        default_value=REGISTER_DEFAULTS.theme_text_light,
        help="MoeSekai 卡片反色文本颜色",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_BG_MAIN",
        value=REGISTER_DEFAULTS.theme_bg_main,
        default_value=REGISTER_DEFAULTS.theme_bg_main,
        help="MoeSekai 页面主背景颜色",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_BG_CARD",
        value=REGISTER_DEFAULTS.theme_bg_card,
        default_value=REGISTER_DEFAULTS.theme_bg_card,
        help="MoeSekai 卡片背景颜色",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_THEME_BORDER_LIGHT",
        value=REGISTER_DEFAULTS.theme_border_light,
        default_value=REGISTER_DEFAULTS.theme_border_light,
        help="MoeSekai 卡片描边颜色",
        type=str,
    ),
]


_SENTINEL = object()


def _get_compat_config(
    key: str,
    default: Any,
    *,
    legacy_keys: list[str] | None = None,
) -> Any:
    value = Config.get_config(MODULE_NAME, key, _SENTINEL)
    if value is not _SENTINEL:
        return value
    for legacy_key in legacy_keys or []:
        legacy_value = Config.get_config(MODULE_NAME, legacy_key, _SENTINEL)
        if legacy_value is not _SENTINEL:
            return legacy_value
    return default


@lru_cache(maxsize=1)
def get_settings() -> MoeSekaiSettings:
    defaults = MoeSekaiSettings()
    payload = {
        "profile_token": _get_compat_config(
            "MOESEKAI_PROFILE_TOKEN",
            defaults.profile_token,
        ),
        "profile_render_mode": _get_compat_config(
            "MOESEKAI_PROFILE_RENDER_MODE",
            defaults.profile_render_mode,
        ),
        "profile_api_token": _get_compat_config(
            "MOESEKAI_PROFILE_API_TOKEN",
            defaults.profile_api_token,
        ),
        "profile_api_base_jp": _get_compat_config(
            "MOESEKAI_PROFILE_API_BASE_JP",
            defaults.profile_api_base_jp,
        ),
        "profile_api_base_cn": _get_compat_config(
            "MOESEKAI_PROFILE_API_BASE_CN",
            defaults.profile_api_base_cn,
        ),
        "profile_api_base_tw": _get_compat_config(
            "MOESEKAI_PROFILE_API_BASE_TW",
            defaults.profile_api_base_tw,
        ),
        "profile_static_asset_bases": _get_compat_config(
            "MOESEKAI_PROFILE_STATIC_ASSET_BASES",
            defaults.profile_static_asset_bases,
        ),
        "profile_announcement_url": _get_compat_config(
            "MOESEKAI_PROFILE_ANNOUNCEMENT_URL",
            defaults.profile_announcement_url,
        ),
        "profile_bases": _get_compat_config(
            "MOESEKAI_PROFILE_BASES",
            defaults.profile_bases,
        ),
        "profile_url_templates": _get_compat_config(
            "MOESEKAI_PROFILE_URL_TEMPLATES",
            defaults.profile_url_templates,
        ),
        "site_bases": _get_compat_config(
            "MOESEKAI_SITE_BASES",
            defaults.site_bases,
        ),
        "ranking_api_base": _get_compat_config(
            "MOESEKAI_RANKING_API_BASE",
            defaults.ranking_api_base,
        ),
        "ranking_screenshot_templates": _get_compat_config(
            "MOESEKAI_RANKING_SCREENSHOT_TEMPLATES",
            defaults.ranking_screenshot_templates,
        ),
        "ranking_history_screenshot_templates": _get_compat_config(
            "MOESEKAI_RANKING_HISTORY_SCREENSHOT_TEMPLATES",
            defaults.ranking_history_screenshot_templates,
        ),
        "screenshot_quality": _get_compat_config(
            "MOESEKAI_SCREENSHOT_QUALITY",
            defaults.screenshot_quality,
        ),
        "profile_viewport_width": _get_compat_config(
            "MOESEKAI_PROFILE_VIEWPORT_WIDTH",
            defaults.profile_viewport_width,
        ),
        "ranking_viewport_width": _get_compat_config(
            "MOESEKAI_RANKING_VIEWPORT_WIDTH",
            defaults.ranking_viewport_width,
        ),
        "deck_viewport_width": _get_compat_config(
            "MOESEKAI_DECK_VIEWPORT_WIDTH",
            defaults.deck_viewport_width,
        ),
        "deck_top_crop": _get_compat_config(
            "MOESEKAI_DECK_TOP_CROP",
            defaults.deck_top_crop,
        ),
        "story_top_crop": _get_compat_config(
            "MOESEKAI_STORY_TOP_CROP",
            defaults.story_top_crop,
        ),
        "character_top_crop": _get_compat_config(
            "MOESEKAI_CHARACTER_TOP_CROP",
            defaults.character_top_crop,
        ),
        "screenshot_retry_times": _get_compat_config(
            "MOESEKAI_SCREENSHOT_RETRY_TIMES",
            defaults.screenshot_retry_times,
        ),
        "screenshot_retry_delay_seconds": _get_compat_config(
            "MOESEKAI_SCREENSHOT_RETRY_DELAY_SECONDS",
            defaults.screenshot_retry_delay_seconds,
        ),
        "screenshot_timeout_seconds": _get_compat_config(
            "MOESEKAI_SCREENSHOT_TIMEOUT_SECONDS",
            defaults.screenshot_timeout_seconds,
        ),
        "deck_wait_timeout_seconds": _get_compat_config(
            "MOESEKAI_DECK_WAIT_TIMEOUT_SECONDS",
            defaults.deck_wait_timeout_seconds,
        ),
        "deck_default_music_id": _get_compat_config(
            "MOESEKAI_DECK_DEFAULT_MUSIC_ID",
            defaults.deck_default_music_id,
        ),
        "deck_default_difficulty": _get_compat_config(
            "MOESEKAI_DECK_DEFAULT_DIFFICULTY",
            defaults.deck_default_difficulty,
        ),
        "deck_default_live_type": _get_compat_config(
            "MOESEKAI_DECK_DEFAULT_LIVE_TYPE",
            defaults.deck_default_live_type,
        ),
        "deck_strongest_default_music_id": _get_compat_config(
            "MOESEKAI_DECK_STRONGEST_DEFAULT_MUSIC_ID",
            defaults.deck_strongest_default_music_id,
        ),
        "deck_strongest_default_difficulty": _get_compat_config(
            "MOESEKAI_DECK_STRONGEST_DEFAULT_DIFFICULTY",
            defaults.deck_strongest_default_difficulty,
        ),
        "deck_challenge_default_music_id": _get_compat_config(
            "MOESEKAI_DECK_CHALLENGE_DEFAULT_MUSIC_ID",
            defaults.deck_challenge_default_music_id,
        ),
        "deck_challenge_default_difficulty": _get_compat_config(
            "MOESEKAI_DECK_CHALLENGE_DEFAULT_DIFFICULTY",
            defaults.deck_challenge_default_difficulty,
        ),
        "cache_mode": _get_compat_config(
            "MOESEKAI_CACHE_MODE",
            defaults.cache_mode,
        ),
        "cache_ttl_seconds": _get_compat_config(
            "MOESEKAI_CACHE_TTL_SECONDS",
            defaults.cache_ttl_seconds,
        ),
        "character_cache_ttl_seconds": _get_compat_config(
            "MOESEKAI_CHARACTER_CACHE_TTL_SECONDS",
            defaults.character_cache_ttl_seconds,
        ),
        "master_notify_superusers": _get_compat_config(
            "MOESEKAI_MASTER_NOTIFY_SUPERUSERS",
            defaults.master_notify_superusers,
        ),
        "new_card_auto_asset_timeout_seconds": _get_compat_config(
            "MOESEKAI_NEW_CARD_AUTO_ASSET_TIMEOUT_SECONDS",
            defaults.new_card_auto_asset_timeout_seconds,
        ),
        "new_card_media_fetch_concurrency": _get_compat_config(
            "MOESEKAI_NEW_CARD_MEDIA_FETCH_CONCURRENCY",
            defaults.new_card_media_fetch_concurrency,
        ),
        "new_card_send_timeout_seconds": _get_compat_config(
            "MOESEKAI_NEW_CARD_SEND_TIMEOUT_SECONDS",
            defaults.new_card_send_timeout_seconds,
        ),
        "new_card_send_delay_min_seconds": _get_compat_config(
            "MOESEKAI_NEW_CARD_SEND_DELAY_MIN_SECONDS",
            defaults.new_card_send_delay_min_seconds,
            legacy_keys=["MOESEKAI_NEW_CARD_FALLBACK_DELAY_MIN_SECONDS"],
        ),
        "new_card_send_delay_max_seconds": _get_compat_config(
            "MOESEKAI_NEW_CARD_SEND_DELAY_MAX_SECONDS",
            defaults.new_card_send_delay_max_seconds,
            legacy_keys=["MOESEKAI_NEW_CARD_FALLBACK_DELAY_MAX_SECONDS"],
        ),
        "new_card_plain_card_max_estimated_bytes": _get_compat_config(
            "MOESEKAI_NEW_CARD_PLAIN_CARD_MAX_ESTIMATED_BYTES",
            defaults.new_card_plain_card_max_estimated_bytes,
            legacy_keys=["MOESEKAI_NEW_CARD_FORWARD_MAX_ESTIMATED_BYTES"],
        ),
        "new_card_plain_stamp_max_estimated_bytes": _get_compat_config(
            "MOESEKAI_NEW_CARD_PLAIN_STAMP_MAX_ESTIMATED_BYTES",
            defaults.new_card_plain_stamp_max_estimated_bytes,
            legacy_keys=["MOESEKAI_NEW_CARD_FORWARD_MAX_ESTIMATED_BYTES"],
        ),
        "new_card_abort_after_consecutive_failures": _get_compat_config(
            "MOESEKAI_NEW_CARD_ABORT_AFTER_CONSECUTIVE_FAILURES",
            defaults.new_card_abort_after_consecutive_failures,
            legacy_keys=["MOESEKAI_NEW_CARD_FALLBACK_ABORT_AFTER_CONSECUTIVE_FAILURES"],
        ),
        "alias_global_editor_groups": _get_compat_config(
            "MOESEKAI_ALIAS_GLOBAL_EDITOR_GROUPS",
            defaults.alias_global_editor_groups,
        ),
        "alias_sync_interval_seconds": _get_compat_config(
            "MOESEKAI_ALIAS_SYNC_INTERVAL_SECONDS",
            defaults.alias_sync_interval_seconds,
        ),
    }
    settings = MoeSekaiSettings.model_validate(payload)
    settings.profile_url_templates = _build_profile_url_templates(settings)
    return settings


def refresh_settings() -> MoeSekaiSettings:
    get_settings.cache_clear()
    return get_settings()


__all__ = [
    "DEFAULT_ASSET_SOURCE_ORDER",
    "DEFAULT_MASTER_SOURCES",
    "DEFAULT_MASTER_SOURCE_ORDER",
    "DEFAULT_PROFILE_STATIC_ASSET_BASES",
    "LEGACY_PROFILE_TOKEN_DEFAULT",
    "MASTER_DATASET_KEYS",
    "REGISTER_CONFIGS",
    "MasterSourceConfig",
    "MoeSekaiSettings",
    "_merge_master_sources_config",
    "build_default_master_sources",
    "get_settings",
    "refresh_settings",
]
