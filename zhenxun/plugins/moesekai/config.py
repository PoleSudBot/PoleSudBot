from __future__ import annotations

from functools import lru_cache
import os
import sys
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

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
    SERVERS,
    normalize_deck_difficulty,
    normalize_live_type,
)

MASTER_DATASET_KEYS = (
    "events",
    "virtualLives",
    "cards",
    "gameCharacters",
    "honors",
    "honorGroups",
    "stamps",
    "musics",
)

DEFAULT_MASTER_SOURCE_ORDER = ["8823", "haruki", "sekai-viewer"]
DEFAULT_ASSET_SOURCE_ORDER = [
    "uni",
    "haruki-main",
    "haruki-jp-dedicated",
    "legacy-viewer",
]
DEFAULT_PROFILE_STATIC_ASSET_BASES = [
    "https://raw.githubusercontent.com/Exmeaning/Exmeaning-Image-hosting/main",
    "https://cdn.jsdelivr.net/gh/Exmeaning/Exmeaning-Image-hosting@main",
]
LEGACY_PROFILE_TOKEN_DEFAULT = (
    "0357a6c752a7cb080bce495891911d0da722a907eecfc4d28b0ff952375466b0"
)


def _dataset_paths(prefix: str = "") -> dict[str, str]:
    return {dataset: f"{prefix}{dataset}.json" for dataset in MASTER_DATASET_KEYS}


DEFAULT_MASTER_SOURCE_FAMILIES: dict[str, dict[str, Any]] = {
    "8823": {
        "auto_probe": True,
        "version_path": "versions.json",
        "version_field": "data_version",
        "datasets": _dataset_paths(),
        "regions": {
            "jp": ("kotori8823", "sekai-master-db", "master"),
            "cn": ("kotori8823", "sekai-sc-master-db", "master"),
            "tw": ("kotori8823", "sekai-tc-master-db", "master"),
        },
    },
    "haruki": {
        "auto_probe": True,
        "version_path": "versions/current_version.json",
        "version_field": "dataVersion",
        "datasets": _dataset_paths("master/"),
        "regions": {
            "jp": ("Team-Haruki", "haruki-sekai-master", "main"),
            "cn": ("Team-Haruki", "haruki-sekai-sc-master", "main"),
            "tw": ("Team-Haruki", "haruki-sekai-tc-master", "main"),
        },
    },
    "sekai-viewer": {
        "auto_probe": False,
        "version_path": "versions.json",
        "version_field": "dataVersion",
        "datasets": _dataset_paths(),
        "regions": {
            "jp": ("Sekai-World", "sekai-master-db-diff", "main"),
            "cn": ("Sekai-World", "sekai-master-db-cn-diff", "main"),
            "tw": ("Sekai-World", "sekai-master-db-tc-diff", "main"),
        },
    },
}


def _infer_datasets_from_events_path(events_path: str) -> dict[str, str]:
    normalized = events_path.strip().strip("/")
    prefix = ""
    if "/" in normalized:
        prefix = normalized.rsplit("/", 1)[0].strip("/")
        prefix = f"{prefix}/" if prefix else ""
    return {dataset: f"{prefix}{dataset}.json" for dataset in MASTER_DATASET_KEYS}


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


class MasterSourceConfig(BaseModel):
    name: str
    region: Literal["cn", "jp", "tw"]
    family: str = "custom"
    auto_probe: bool = True
    owner: str = ""
    repo: str = ""
    branch: str = "main"
    base_url: str = ""
    version_path: str = ""
    version_field: str = "dataVersion"
    datasets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        if payload.get("version_url") and not payload.get("version_path"):
            payload["version_path"] = payload["version_url"]
        if payload.get("events_url") and not payload.get("datasets"):
            payload["datasets"] = _infer_datasets_from_events_path(payload["events_url"])
        if payload.get("events_path") and not payload.get("datasets"):
            payload["datasets"] = _infer_datasets_from_events_path(payload["events_path"])
        payload.setdefault("family", "custom")
        family = str(payload.get("family") or "").strip() or "custom"
        inferred_family = family
        if inferred_family == "custom":
            inferred_name = str(payload.get("name") or "").strip().rsplit("-", 1)[0]
            if inferred_name in DEFAULT_MASTER_SOURCE_FAMILIES:
                inferred_family = inferred_name
        if "auto_probe" not in payload or payload.get("auto_probe") is None:
            payload["auto_probe"] = DEFAULT_MASTER_SOURCE_FAMILIES.get(
                inferred_family,
                {},
            ).get("auto_probe", True)
        payload.setdefault("base_url", "")
        payload.setdefault("datasets", {})
        return payload

    @field_validator("datasets")
    @classmethod
    def _normalize_datasets(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            str(key): str(path).strip()
            for key, path in value.items()
            if key and str(path).strip()
        }

    @staticmethod
    def _build_url(base_url: str, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))

    @property
    def raw_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.owner and self.repo:
            return f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}"
        return ""

    @property
    def version_url(self) -> str:
        return self._build_url(self.raw_base_url, self.version_path)

    @property
    def revision_api_url(self) -> str | None:
        if self.owner and self.repo:
            return f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{self.branch}"
        return None

    def dataset_path(self, dataset: str) -> str | None:
        return self.datasets.get(dataset)

    def dataset_url(self, dataset: str) -> str:
        path = self.dataset_path(dataset)
        if not path:
            raise KeyError(f"{self.name} 未配置数据集 {dataset}")
        return self._build_url(self.raw_base_url, path)

    @property
    def events_url(self) -> str:
        return self.dataset_url("events")

    @property
    def events_path(self) -> str:
        return self.dataset_path("events") or ""


def build_default_master_sources(
    order: list[str] | tuple[str, ...] | None = None,
) -> list[MasterSourceConfig]:
    family_order = list(order or DEFAULT_MASTER_SOURCE_ORDER)
    sources: list[MasterSourceConfig] = []
    for family_name in family_order:
        family = DEFAULT_MASTER_SOURCE_FAMILIES.get(family_name)
        if not family:
            continue
        for region in SERVERS:
            owner, repo, branch = family["regions"][region]
            sources.append(
                MasterSourceConfig(
                    name=f"{family_name}-{region}",
                    family=family_name,
                    auto_probe=bool(family.get("auto_probe", True)),
                    region=region,
                    owner=owner,
                    repo=repo,
                    branch=branch,
                    version_path=family["version_path"],
                    version_field=family["version_field"],
                    datasets=family["datasets"].copy(),
                )
            )
    return sources


DEFAULT_MASTER_SOURCES = build_default_master_sources()


def _merge_master_sources_config(
    raw_sources: Any,
    *,
    default_sources: list[MasterSourceConfig] | None = None,
) -> list[MasterSourceConfig]:
    configured_sources = [
        item if isinstance(item, MasterSourceConfig) else MasterSourceConfig.model_validate(item)
        for item in (raw_sources or [])
    ]
    configured_map = {item.name: item for item in configured_sources}
    merged = [
        configured_map.pop(default_source.name, default_source)
        for default_source in (default_sources or DEFAULT_MASTER_SOURCES)
    ]
    merged.extend(configured_map.values())
    return merged


class MoeSekaiSettings(BaseModel):
    profile_token: str = ""
    profile_render_mode: Literal["internal_first", "screenshot_only"] = "internal_first"
    profile_api_token: str = ""
    profile_api_base_jp: str = "https://api.unipjsk.com/api/user/%7Buser_id%7D"
    profile_api_base_cn: str = "https://public-api.haruki.seiunx.com/sekai-api/v5/api/cn"
    profile_api_base_tw: str = "https://public-api.haruki.seiunx.com/sekai-api/v5/api/tw"
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
    asset_miss_cache_ttl_seconds: int = 21_600
    master_source_order: list[str] = Field(
        default_factory=lambda: DEFAULT_MASTER_SOURCE_ORDER.copy()
    )
    master_sources: list[MasterSourceConfig] = Field(
        default_factory=lambda: DEFAULT_MASTER_SOURCES.copy()
    )
    master_check_interval_seconds: int = 180
    master_check_mode: Literal["revision", "version", "hybrid"] = "revision"
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
    github_token: str = ""
    alias_global_editor_groups: list[str] = Field(default_factory=list)
    alias_sync_interval_seconds: int = 21600
    asset_source_order: list[str] = Field(
        default_factory=lambda: DEFAULT_ASSET_SOURCE_ORDER.copy()
    )
    audio_format_priority: list[str] = Field(default_factory=lambda: ["mp3", "flac"])
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
        "master_source_order",
        "asset_source_order",
        "audio_format_priority",
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

    @field_validator("profile_api_token", "github_token")
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

    @field_validator("character_cache_ttl_seconds", "asset_miss_cache_ttl_seconds")
    @classmethod
    def _normalize_ttl_seconds(cls, value: int) -> int:
        return max(0, value)

    @field_validator("master_check_interval_seconds")
    @classmethod
    def _normalize_master_interval(cls, value: int) -> int:
        return max(1, value)

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

    @field_validator("master_sources")
    @classmethod
    def _normalize_master_sources(
        cls, value: list[MasterSourceConfig]
    ) -> list[MasterSourceConfig]:
        return _merge_master_sources_config(value)


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
        help="个人档案渲染模式：internal_first 为内部生成优先，screenshot_only 为仅网页截图",
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
        value=[item for item in REGISTER_DEFAULTS.profile_static_asset_bases],
        default_value=[item for item in REGISTER_DEFAULTS.profile_static_asset_bases],
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
        value=[item for item in REGISTER_DEFAULTS.profile_bases],
        default_value=[item for item in REGISTER_DEFAULTS.profile_bases],
        help="个人档案页面基础地址列表，默认会自动拼成 /profile/{server}/{game_id}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SITE_BASES",
        value=[item for item in REGISTER_DEFAULTS.site_bases],
        default_value=[item for item in REGISTER_DEFAULTS.site_bases],
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
        value=[item for item in REGISTER_DEFAULTS.ranking_screenshot_templates],
        default_value=[item for item in REGISTER_DEFAULTS.ranking_screenshot_templates],
        help="ycx 榜线截图 URL 模板列表，支持 {server} {server_path}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_HISTORY_SCREENSHOT_TEMPLATES",
        value=[item for item in REGISTER_DEFAULTS.ranking_history_screenshot_templates],
        default_value=[
            item for item in REGISTER_DEFAULTS.ranking_history_screenshot_templates
        ],
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
        key="MOESEKAI_ASSET_MISS_CACHE_TTL_SECONDS",
        value=REGISTER_DEFAULTS.asset_miss_cache_ttl_seconds,
        default_value=REGISTER_DEFAULTS.asset_miss_cache_ttl_seconds,
        help="Asset 404 负缓存秒数，默认 6 小时",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_SOURCE_ORDER",
        value=[item for item in REGISTER_DEFAULTS.master_source_order],
        default_value=[item for item in REGISTER_DEFAULTS.master_source_order],
        help="MasterData 源优先级，默认 8823 > haruki > sekai-viewer",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS",
        value=REGISTER_DEFAULTS.master_check_interval_seconds,
        default_value=REGISTER_DEFAULTS.master_check_interval_seconds,
        help="主数据完整探测周期秒数，自动更新仅轮询启用 auto_probe 的数据源",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_CHECK_MODE",
        value=REGISTER_DEFAULTS.master_check_mode,
        default_value=REGISTER_DEFAULTS.master_check_mode,
        help="兼容旧配置的保留项；自动更新现已统一按 version 判定",
        type=str,
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
        key="MOESEKAI_GITHUB_TOKEN",
        value=REGISTER_DEFAULTS.github_token,
        default_value=REGISTER_DEFAULTS.github_token,
        help="GitHub API Personal Access Token，用于提升主数据轮询额度",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_ALIAS_GLOBAL_EDITOR_GROUPS",
        value=[item for item in REGISTER_DEFAULTS.alias_global_editor_groups],
        default_value=[item for item in REGISTER_DEFAULTS.alias_global_editor_groups],
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
        key="MOESEKAI_ASSET_SOURCE_ORDER",
        value=[item for item in REGISTER_DEFAULTS.asset_source_order],
        default_value=[item for item in REGISTER_DEFAULTS.asset_source_order],
        help="资源源优先级，默认 uni > haruki-main > haruki-jp-dedicated > legacy-viewer",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_AUDIO_FORMAT_PRIORITY",
        value=[item for item in REGISTER_DEFAULTS.audio_format_priority],
        default_value=[item for item in REGISTER_DEFAULTS.audio_format_priority],
        help="音频格式优先级，默认 mp3 > flac",
        type=list[str],
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
        "asset_miss_cache_ttl_seconds": _get_compat_config(
            "MOESEKAI_ASSET_MISS_CACHE_TTL_SECONDS",
            defaults.asset_miss_cache_ttl_seconds,
        ),
        "master_source_order": _get_compat_config(
            "MOESEKAI_MASTER_SOURCE_ORDER",
            defaults.master_source_order,
        ),
        "master_sources": _get_compat_config(
            "MOESEKAI_MASTER_SOURCES",
            defaults.master_sources,
        ),
        "master_check_interval_seconds": _get_compat_config(
            "MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS",
            defaults.master_check_interval_seconds,
            legacy_keys=["MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS"],
        ),
        "master_check_mode": _get_compat_config(
            "MOESEKAI_MASTER_CHECK_MODE",
            defaults.master_check_mode,
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
        "github_token": _get_compat_config(
            "MOESEKAI_GITHUB_TOKEN",
            defaults.github_token,
        ),
        "alias_global_editor_groups": _get_compat_config(
            "MOESEKAI_ALIAS_GLOBAL_EDITOR_GROUPS",
            defaults.alias_global_editor_groups,
        ),
        "alias_sync_interval_seconds": _get_compat_config(
            "MOESEKAI_ALIAS_SYNC_INTERVAL_SECONDS",
            defaults.alias_sync_interval_seconds,
        ),
        "asset_source_order": _get_compat_config(
            "MOESEKAI_ASSET_SOURCE_ORDER",
            defaults.asset_source_order,
        ),
        "audio_format_priority": _get_compat_config(
            "MOESEKAI_AUDIO_FORMAT_PRIORITY",
            defaults.audio_format_priority,
        ),
    }
    settings = MoeSekaiSettings.model_validate(payload)
    settings.profile_url_templates = _build_profile_url_templates(settings)
    ordered_defaults = build_default_master_sources(settings.master_source_order)
    settings.master_sources = _merge_master_sources_config(
        settings.master_sources or ordered_defaults,
        default_sources=ordered_defaults,
    )
    return settings


def refresh_settings() -> MoeSekaiSettings:
    get_settings.cache_clear()
    return get_settings()
