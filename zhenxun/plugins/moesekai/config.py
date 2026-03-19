from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urljoin

from pydantic import BaseModel, Field, field_validator, model_validator

from zhenxun.configs.config import Config
from zhenxun.configs.utils.models import RegisterConfig

from .constants import MODULE_NAME, normalize_deck_difficulty, normalize_live_type


class MasterSourceConfig(BaseModel):
    name: str
    region: Literal["cn", "jp", "tw"]
    base_url: str = ""
    version_path: str
    events_path: str
    version_field: str

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = value.copy()
        if payload.get("version_url") and not payload.get("version_path"):
            payload["version_path"] = payload["version_url"]
        if payload.get("events_url") and not payload.get("events_path"):
            payload["events_path"] = payload["events_url"]
        payload.setdefault("base_url", "")
        return payload

    @staticmethod
    def _build_url(base_url: str, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))

    @property
    def version_url(self) -> str:
        return self._build_url(self.base_url, self.version_path)

    @property
    def events_url(self) -> str:
        return self._build_url(self.base_url, self.events_path)


DEFAULT_MASTER_SOURCES = [
    MasterSourceConfig(
        name="sekai-viewer-jp",
        region="jp",
        base_url="https://raw.githubusercontent.com/Sekai-World/sekai-master-db-diff/main",
        version_path="versions.json",
        events_path="events.json",
        version_field="dataVersion",
    ),
    MasterSourceConfig(
        name="sekai-viewer-cn",
        region="cn",
        base_url="https://raw.githubusercontent.com/Sekai-World/sekai-master-db-cn-diff/main",
        version_path="versions.json",
        events_path="events.json",
        version_field="dataVersion",
    ),
    MasterSourceConfig(
        name="sekai-viewer-tw",
        region="tw",
        base_url="https://raw.githubusercontent.com/Sekai-World/sekai-master-db-tc-diff/main",
        version_path="versions.json",
        events_path="events.json",
        version_field="dataVersion",
    ),
    MasterSourceConfig(
        name="haruki-jp",
        region="jp",
        base_url="https://raw.githubusercontent.com/Team-Haruki/haruki-sekai-master/main",
        version_path="versions/current_version.json",
        events_path="master/events.json",
        version_field="dataVersion",
    ),
    MasterSourceConfig(
        name="haruki-cn",
        region="cn",
        base_url="https://raw.githubusercontent.com/Team-Haruki/haruki-sekai-sc-master/main",
        version_path="versions/current_version.json",
        events_path="master/events.json",
        version_field="dataVersion",
    ),
    MasterSourceConfig(
        name="haruki-tw",
        region="tw",
        base_url="https://raw.githubusercontent.com/Team-Haruki/haruki-sekai-tc-master/main",
        version_path="versions/current_version.json",
        events_path="master/events.json",
        version_field="dataVersion",
    ),
    MasterSourceConfig(
        name="8823-jp",
        region="jp",
        base_url="https://raw.githubusercontent.com/kotori8823/sekai-master-db/master",
        version_path="versions.json",
        events_path="events.json",
        version_field="data_version",
    ),
    MasterSourceConfig(
        name="8823-cn",
        region="cn",
        base_url="https://raw.githubusercontent.com/kotori8823/sekai-sc-master-db/master",
        version_path="versions.json",
        events_path="events.json",
        version_field="data_version",
    ),
    MasterSourceConfig(
        name="8823-tw",
        region="tw",
        base_url="https://raw.githubusercontent.com/kotori8823/sekai-tc-master-db/master",
        version_path="versions.json",
        events_path="events.json",
        version_field="data_version",
    ),
]


def _merge_master_sources_config(raw_sources: Any) -> list[MasterSourceConfig]:
    configured_sources = [
        item if isinstance(item, MasterSourceConfig) else MasterSourceConfig.model_validate(item)
        for item in (raw_sources or [])
    ]
    configured_map = {item.name: item for item in configured_sources}
    merged = [
        configured_map.pop(default_source.name, default_source)
        for default_source in DEFAULT_MASTER_SOURCES
    ]
    merged.extend(configured_map.values())
    return merged


class MoeSekaiSettings(BaseModel):
    profile_token: str = ""
    profile_url_templates: list[str] = Field(
        default_factory=lambda: [
            "https://sekaiprofile.exmeaning.com/profile/{server}/{game_id}?token={token}"
        ]
    )
    site_bases: list[str] = Field(
        default_factory=lambda: [
            "https://snowyviewer.exmeaning.com",
            "https://pjsk.moe",
        ]
    )
    ranking_api_base: str = "https://sekaibangdan.exmeaning.com"
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
    deck_viewport_width: int = 650
    deck_top_crop: int = 100
    deck_wait_timeout_seconds: int = 120
    deck_default_music_id: int = 226
    deck_default_difficulty: str = "hard"
    deck_default_live_type: str = "multi"
    cache_mode: str = "REDIS"
    cache_ttl_seconds: int = 600
    master_sources: list[MasterSourceConfig] = Field(
        default_factory=lambda: DEFAULT_MASTER_SOURCES.copy()
    )
    master_auto_check_interval_seconds: int = 600
    master_notify_superusers: bool = True

    @field_validator(
        "site_bases",
        "profile_url_templates",
        "ranking_screenshot_templates",
        "ranking_history_screenshot_templates",
    )
    @classmethod
    def _filter_empty_values(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("screenshot_quality")
    @classmethod
    def _normalize_quality(cls, value: int) -> int:
        return max(1, min(100, value))

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

    @field_validator("deck_top_crop")
    @classmethod
    def _normalize_deck_top_crop(cls, value: int) -> int:
        return max(0, value)

    @field_validator("deck_default_difficulty")
    @classmethod
    def _normalize_difficulty(cls, value: str) -> str:
        return normalize_deck_difficulty(value) or "hard"

    @field_validator("deck_default_live_type")
    @classmethod
    def _normalize_live_type(cls, value: str) -> str:
        return normalize_live_type(value) or "multi"


REGISTER_CONFIGS = [
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_TOKEN",
        value="",
        default_value="",
        help="MoeSekai 档案鉴权 Token",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_URL_TEMPLATES",
        value=[item for item in MoeSekaiSettings().profile_url_templates],
        default_value=[item for item in MoeSekaiSettings().profile_url_templates],
        help="档案截图 URL 模板列表，按顺序尝试，支持 {server} {game_id} {token}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SITE_BASES",
        value=[item for item in MoeSekaiSettings().site_bases],
        default_value=[item for item in MoeSekaiSettings().site_bases],
        help="MoeSekai 主站点列表，按顺序尝试，主要用于组卡截图",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_API_BASE",
        value=MoeSekaiSettings().ranking_api_base,
        default_value=MoeSekaiSettings().ranking_api_base,
        help="榜线预测 API 基础地址",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_SCREENSHOT_TEMPLATES",
        value=[item for item in MoeSekaiSettings().ranking_screenshot_templates],
        default_value=[item for item in MoeSekaiSettings().ranking_screenshot_templates],
        help="ycx 榜线截图 URL 模板列表，支持 {server} {server_path}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_HISTORY_SCREENSHOT_TEMPLATES",
        value=[item for item in MoeSekaiSettings().ranking_history_screenshot_templates],
        default_value=[item for item in MoeSekaiSettings().ranking_history_screenshot_templates],
        help="ycx 历史活动截图 URL 模板列表，支持 {server} {server_path} {event_id}",
        type=list[str],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_QUALITY",
        value=MoeSekaiSettings().screenshot_quality,
        default_value=MoeSekaiSettings().screenshot_quality,
        help="截图质量，1-100，越高越清晰但越慢",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_PROFILE_VIEWPORT_WIDTH",
        value=MoeSekaiSettings().profile_viewport_width,
        default_value=MoeSekaiSettings().profile_viewport_width,
        help="个人档案截图宽度（CSS viewport width）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_RANKING_VIEWPORT_WIDTH",
        value=MoeSekaiSettings().ranking_viewport_width,
        default_value=MoeSekaiSettings().ranking_viewport_width,
        help="ycx 榜线截图宽度（CSS viewport width）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_VIEWPORT_WIDTH",
        value=MoeSekaiSettings().deck_viewport_width,
        default_value=MoeSekaiSettings().deck_viewport_width,
        help="活动组卡截图宽度（CSS viewport width）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_TOP_CROP",
        value=MoeSekaiSettings().deck_top_crop,
        default_value=MoeSekaiSettings().deck_top_crop,
        help="活动组卡截图顶部裁剪高度（CSS 像素）",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_RETRY_TIMES",
        value=MoeSekaiSettings().screenshot_retry_times,
        default_value=MoeSekaiSettings().screenshot_retry_times,
        help="截图失败重试次数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_RETRY_DELAY_SECONDS",
        value=MoeSekaiSettings().screenshot_retry_delay_seconds,
        default_value=MoeSekaiSettings().screenshot_retry_delay_seconds,
        help="截图失败后下一次重试的等待秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_SCREENSHOT_TIMEOUT_SECONDS",
        value=MoeSekaiSettings().screenshot_timeout_seconds,
        default_value=MoeSekaiSettings().screenshot_timeout_seconds,
        help="单次截图总超时秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_WAIT_TIMEOUT_SECONDS",
        value=MoeSekaiSettings().deck_wait_timeout_seconds,
        default_value=MoeSekaiSettings().deck_wait_timeout_seconds,
        help="活动组卡等待网页计算完成的超时秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_DEFAULT_MUSIC_ID",
        value=MoeSekaiSettings().deck_default_music_id,
        default_value=MoeSekaiSettings().deck_default_music_id,
        help="活动组卡默认歌曲 ID",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_DEFAULT_DIFFICULTY",
        value=MoeSekaiSettings().deck_default_difficulty,
        default_value=MoeSekaiSettings().deck_default_difficulty,
        help="活动组卡默认难度",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_DECK_DEFAULT_LIVE_TYPE",
        value=MoeSekaiSettings().deck_default_live_type,
        default_value=MoeSekaiSettings().deck_default_live_type,
        help="活动组卡默认 Live 类型",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_CACHE_MODE",
        value=MoeSekaiSettings().cache_mode,
        default_value=MoeSekaiSettings().cache_mode,
        help="插件缓存模式：REDIS / MEMORY / NONE",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_CACHE_TTL_SECONDS",
        value=MoeSekaiSettings().cache_ttl_seconds,
        default_value=MoeSekaiSettings().cache_ttl_seconds,
        help="插件缓存过期秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_SOURCES",
        value=[item.model_dump() for item in DEFAULT_MASTER_SOURCES],
        default_value=[item.model_dump() for item in DEFAULT_MASTER_SOURCES],
        help="主数据源配置列表",
        type=list[MasterSourceConfig],
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS",
        value=MoeSekaiSettings().master_auto_check_interval_seconds,
        default_value=MoeSekaiSettings().master_auto_check_interval_seconds,
        help="主数据自动检查间隔秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MOESEKAI_MASTER_NOTIFY_SUPERUSERS",
        value=MoeSekaiSettings().master_notify_superusers,
        default_value=MoeSekaiSettings().master_notify_superusers,
        help="主数据自动更新成功后是否通知超级用户",
        type=bool,
    ),
]


def _config_value(key: str, default: Any, value_type: Any = None) -> Any:
    return Config.get_config(MODULE_NAME, key, default, build_model=True) if value_type is None else Config.get_config(MODULE_NAME, key, default)


@lru_cache(maxsize=1)
def get_settings() -> MoeSekaiSettings:
    defaults = MoeSekaiSettings()
    payload = {
        "profile_token": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_PROFILE_TOKEN",
            defaults.profile_token,
        ),
        "profile_url_templates": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_PROFILE_URL_TEMPLATES",
            defaults.profile_url_templates,
        ),
        "site_bases": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_SITE_BASES",
            defaults.site_bases,
        ),
        "ranking_api_base": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_RANKING_API_BASE",
            defaults.ranking_api_base,
        ),
        "ranking_screenshot_templates": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_RANKING_SCREENSHOT_TEMPLATES",
            defaults.ranking_screenshot_templates,
        ),
        "ranking_history_screenshot_templates": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_RANKING_HISTORY_SCREENSHOT_TEMPLATES",
            defaults.ranking_history_screenshot_templates,
        ),
        "screenshot_quality": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_SCREENSHOT_QUALITY",
            defaults.screenshot_quality,
        ),
        "profile_viewport_width": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_PROFILE_VIEWPORT_WIDTH",
            defaults.profile_viewport_width,
        ),
        "ranking_viewport_width": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_RANKING_VIEWPORT_WIDTH",
            defaults.ranking_viewport_width,
        ),
        "deck_viewport_width": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_DECK_VIEWPORT_WIDTH",
            defaults.deck_viewport_width,
        ),
        "deck_top_crop": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_DECK_TOP_CROP",
            defaults.deck_top_crop,
        ),
        "screenshot_retry_times": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_SCREENSHOT_RETRY_TIMES",
            defaults.screenshot_retry_times,
        ),
        "screenshot_retry_delay_seconds": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_SCREENSHOT_RETRY_DELAY_SECONDS",
            defaults.screenshot_retry_delay_seconds,
        ),
        "screenshot_timeout_seconds": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_SCREENSHOT_TIMEOUT_SECONDS",
            defaults.screenshot_timeout_seconds,
        ),
        "deck_wait_timeout_seconds": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_DECK_WAIT_TIMEOUT_SECONDS",
            defaults.deck_wait_timeout_seconds,
        ),
        "deck_default_music_id": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_DECK_DEFAULT_MUSIC_ID",
            defaults.deck_default_music_id,
        ),
        "deck_default_difficulty": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_DECK_DEFAULT_DIFFICULTY",
            defaults.deck_default_difficulty,
        ),
        "deck_default_live_type": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_DECK_DEFAULT_LIVE_TYPE",
            defaults.deck_default_live_type,
        ),
        "cache_mode": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_CACHE_MODE",
            defaults.cache_mode,
        ),
        "cache_ttl_seconds": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_CACHE_TTL_SECONDS",
            defaults.cache_ttl_seconds,
        ),
        "master_sources": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_MASTER_SOURCES",
            defaults.master_sources,
        ),
        "master_auto_check_interval_seconds": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS",
            defaults.master_auto_check_interval_seconds,
        ),
        "master_notify_superusers": Config.get_config(
            MODULE_NAME,
            "MOESEKAI_MASTER_NOTIFY_SUPERUSERS",
            defaults.master_notify_superusers,
        ),
    }
    payload["master_sources"] = _merge_master_sources_config(payload["master_sources"])
    return MoeSekaiSettings.model_validate(payload)


def refresh_settings() -> MoeSekaiSettings:
    get_settings.cache_clear()
    return get_settings()
