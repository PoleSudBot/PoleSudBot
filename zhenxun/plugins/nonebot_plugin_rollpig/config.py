from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from zhenxun.configs.config import Config as ZhenxunConfig

MODULE_NAME = "nonebot_plugin_rollpig"
base_config = ZhenxunConfig.get(MODULE_NAME)


class GroupSettings(BaseModel):
    """rollpig 分群配置"""

    daily_summary_enabled: bool = Field(
        True,
        description="是否为当前群发送猪圈日报",
    )


class Config(BaseModel):
    """兼容 NoneBot 插件配置导入；运行时有效配置以 zhenxun 配置为准。"""

    AI_ENABLED: bool = False
    LLM_MODEL_NAME: Optional[str] = None
    ROAST_COOLDOWN_HOURS: float = 8.0
    STORAGE_BACKEND: str = "local"
    CLOUD_API_URL: Optional[str] = None
    CLOUD_TOKEN: Optional[str] = None
    CLOUD_TIMEOUT: float = 3.0
    CLOUD_STRICT_MODE: bool = True
    PROXY: Optional[str] = None

    # Legacy compatibility only. Runtime no longer uses these fields.
    rollpig_ai_enabled: Optional[bool] = None
    rollpig_deepseek_key: Optional[str] = None
    rollpig_deepseek_base: Optional[str] = None
    rollpig_model: Optional[str] = None
    rollpig_roast_cooldown_hours: Optional[float] = None
    rollpig_storage_backend: Optional[str] = None
    rollpig_cloud_api_url: Optional[str] = None
    rollpig_cloud_token: Optional[str] = None
    rollpig_cloud_timeout: Optional[float] = None
    rollpig_cloud_strict_mode: Optional[bool] = None
    rollpig_proxy: Optional[str] = None


def _get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    value = base_config.get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _get_float(key: str, default: float) -> float:
    raw_value = base_config.get(key, default)
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    value = base_config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def get_ai_enabled() -> bool:
    return _get_bool("AI_ENABLED", False)


def get_llm_model_name() -> Optional[str]:
    return _get_str("LLM_MODEL_NAME")


def get_roast_cooldown_hours() -> float:
    return _get_float("ROAST_COOLDOWN_HOURS", 8.0)


def get_storage_backend() -> str:
    return (_get_str("STORAGE_BACKEND", "local") or "local").strip().lower()


def get_cloud_api_url() -> Optional[str]:
    return _get_str("CLOUD_API_URL")


def get_cloud_token() -> Optional[str]:
    return _get_str("CLOUD_TOKEN")


def get_cloud_timeout() -> float:
    return _get_float("CLOUD_TIMEOUT", 3.0)


def get_cloud_strict_mode() -> bool:
    return _get_bool("CLOUD_STRICT_MODE", True)


def get_proxy() -> Optional[str]:
    return _get_str("PROXY")
