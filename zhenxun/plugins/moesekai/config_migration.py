from __future__ import annotations

import os
import sys
from typing import Any

from zhenxun.services.sekai_resource.config import (
    DEFAULT_MASTER_SOURCE_ORDER,
    MASTER_DATASET_KEYS,
    MasterSourceConfig,
    _merge_master_sources_config,
    build_default_master_sources,
)
from zhenxun.services.sekai_resource.config import (
    REGISTER_CONFIGS as RESOURCE_REGISTER_CONFIGS,
)
from zhenxun.services.sekai_resource.constants import (
    MODULE_NAME as RESOURCE_MODULE_NAME,
)

from .config import LEGACY_PROFILE_TOKEN_DEFAULT
from .constants import MODULE_NAME

_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules

if not _TEST_MODE:
    try:
        from zhenxun.configs.config import Config
    except Exception:  # pragma: no cover - runtime fallback
        Config = None  # type: ignore[assignment]
else:  # pragma: no cover - test fallback
    Config = None  # type: ignore[assignment]


_SENTINEL = object()
_LEGACY_PROFILE_TEMPLATE_SUFFIXES = (
    "/profile/{server}/{game_id}?token={token}",
    "/profile/{server}/{game_id}/?token={token}",
)
_LEGACY_KEYS_TO_REMOVE = (
    "MOESEKAI_PROFILE_URL_TEMPLATES",
    "MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS",
)
_RESOURCE_DEFAULTS = {
    config.key: config.default_value for config in RESOURCE_REGISTER_CONFIGS
}
_RESOURCE_SIMPLE_KEY_MIGRATIONS = (
    ("MOESEKAI_MASTER_SOURCE_ORDER", "SEKAI_RESOURCE_MASTER_SOURCE_ORDER"),
    ("MOESEKAI_MASTER_CHECK_MODE", "SEKAI_RESOURCE_MASTER_CHECK_MODE"),
    (
        "MOESEKAI_ASSET_MISS_CACHE_TTL_SECONDS",
        "SEKAI_RESOURCE_ASSET_MISS_CACHE_TTL_SECONDS",
    ),
    ("MOESEKAI_GITHUB_TOKEN", "SEKAI_RESOURCE_GITHUB_TOKEN"),
    ("MOESEKAI_ASSET_SOURCE_ORDER", "SEKAI_RESOURCE_ASSET_SOURCE_ORDER"),
    ("MOESEKAI_AUDIO_FORMAT_PRIORITY", "SEKAI_RESOURCE_AUDIO_FORMAT_PRIORITY"),
)


def _get_raw_config(
    key: str,
    default: Any = _SENTINEL,
    *,
    module: str = MODULE_NAME,
) -> Any:
    if not Config:
        return default
    return Config.get_config(module, key, default, build_model=False)


def _set_config_value(
    key: str,
    value: Any,
    *,
    module: str = MODULE_NAME,
) -> None:
    if not Config:
        return
    Config.set_config(module, key, value, auto_save=False)


def _delete_config_key(key: str, *, module: str = MODULE_NAME) -> bool:
    if not Config:
        return False
    changed = False
    module_group = getattr(Config, "_data", {}).get(module)
    if module_group and key in module_group.configs:
        module_group.configs.pop(key, None)
        changed = True
    simple_group = getattr(Config, "_simple_data", {}).get(module)
    if isinstance(simple_group, dict) and key in simple_group:
        simple_group.pop(key, None)
        changed = True
    return changed


def _normalize_compare_value(value: Any) -> Any:
    if isinstance(value, MasterSourceConfig):
        return _normalize_compare_value(value.model_dump())
    if isinstance(value, list | tuple):
        return [_normalize_compare_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _normalize_compare_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    return value


def _has_explicit_resource_config(key: str) -> bool:
    key = key.upper()
    simple_group = getattr(Config, "_simple_data", {}).get(RESOURCE_MODULE_NAME)
    if isinstance(simple_group, dict) and key in simple_group:
        return True
    current = _get_raw_config(key, module=RESOURCE_MODULE_NAME)
    if current is _SENTINEL:
        return False
    default = _RESOURCE_DEFAULTS.get(key, _SENTINEL)
    if default is _SENTINEL:
        return True
    return _normalize_compare_value(current) != _normalize_compare_value(default)


def _derive_profile_bases(profile_templates: Any) -> list[str]:
    if not isinstance(profile_templates, list):
        return []
    result: list[str] = []
    for item in profile_templates:
        text = str(item or "").strip()
        if not text:
            return []
        suffix = next(
            (
                candidate
                for candidate in _LEGACY_PROFILE_TEMPLATE_SUFFIXES
                if text.endswith(candidate)
            ),
            None,
        )
        if not suffix:
            return []
        base = text[: -len(suffix)].rstrip("/")
        if not base:
            return []
        if base not in result:
            result.append(base)
    return result


def _source_signature(source: MasterSourceConfig) -> tuple[Any, ...]:
    dataset_urls = tuple(
        source.dataset_url(dataset) if source.dataset_path(dataset) else ""
        for dataset in MASTER_DATASET_KEYS
    )
    return (
        source.name,
        source.region,
        source.auto_probe,
        source.version_url,
        source.version_field,
        dataset_urls,
    )


def _replace_equivalent_default_sources(
    sources: list[MasterSourceConfig],
    *,
    default_sources: list[MasterSourceConfig],
) -> list[MasterSourceConfig]:
    default_by_name = {source.name: source for source in default_sources}
    result: list[MasterSourceConfig] = []
    for source in sources:
        default_source = default_by_name.get(source.name)
        if default_source and _source_signature(source) == _source_signature(
            default_source
        ):
            result.append(default_source)
        else:
            family = source.family
            if family == "custom":
                inferred = source.name.rsplit("-", 1)[0]
                if inferred in DEFAULT_MASTER_SOURCE_ORDER:
                    family = inferred
            result.append(
                source.model_copy(
                    update={
                        "family": family,
                    }
                )
            )
    return result


def _is_default_source_list(sources: list[MasterSourceConfig]) -> bool:
    default_sources = build_default_master_sources(DEFAULT_MASTER_SOURCE_ORDER)
    normalized = _replace_equivalent_default_sources(
        sources,
        default_sources=default_sources,
    )
    if len(normalized) != len(default_sources):
        return False
    return all(
        _source_signature(left) == _source_signature(right)
        and left.family == right.family
        and left.owner == right.owner
        and left.repo == right.repo
        and left.branch == right.branch
        and left.auto_probe == right.auto_probe
        and left.version_path == right.version_path
        and left.datasets == right.datasets
        for left, right in zip(normalized, default_sources, strict=False)
    )


def _migrate_resource_value(
    resource_key: str,
    value: Any,
) -> bool:
    if _has_explicit_resource_config(resource_key):
        return False
    _set_config_value(resource_key, value, module=RESOURCE_MODULE_NAME)
    return True


def _migrate_resource_master_sources(raw_master_sources: Any) -> bool:
    if raw_master_sources is _SENTINEL:
        return False
    if _has_explicit_resource_config("SEKAI_RESOURCE_MASTER_SOURCES"):
        return False

    default_sources = build_default_master_sources(DEFAULT_MASTER_SOURCE_ORDER)
    merged_sources = _merge_master_sources_config(
        raw_master_sources,
        default_sources=default_sources,
    )
    if _is_default_source_list(merged_sources):
        return False

    migrated_sources = _replace_equivalent_default_sources(
        merged_sources,
        default_sources=default_sources,
    )
    _set_config_value(
        "SEKAI_RESOURCE_MASTER_SOURCES",
        [source.model_dump() for source in migrated_sources],
        module=RESOURCE_MODULE_NAME,
    )
    return True


def _migrate_resource_configs() -> bool:
    changed = False

    raw_master_sources = _get_raw_config("MOESEKAI_MASTER_SOURCES")
    changed = _migrate_resource_master_sources(raw_master_sources) or changed
    changed = _delete_config_key("MOESEKAI_MASTER_SOURCES") or changed

    legacy_interval = _get_raw_config("MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS")
    legacy_auto_interval = _get_raw_config(
        "MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS"
    )
    interval_value = (
        legacy_interval if legacy_interval is not _SENTINEL else legacy_auto_interval
    )
    if interval_value is not _SENTINEL:
        changed = (
            _migrate_resource_value(
                "SEKAI_RESOURCE_MASTER_CHECK_INTERVAL_SECONDS",
                interval_value,
            )
            or changed
        )
    changed = _delete_config_key("MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS") or changed

    for legacy_key, resource_key in _RESOURCE_SIMPLE_KEY_MIGRATIONS:
        legacy_value = _get_raw_config(legacy_key)
        if legacy_value is _SENTINEL:
            continue
        changed = (
            _migrate_resource_value(
                resource_key,
                legacy_value,
            )
            or changed
        )
        changed = _delete_config_key(legacy_key) or changed

    return changed


def _sync_config_files() -> None:
    if not Config:
        return
    Config.save()
    try:
        from zhenxun.builtin_plugins.init.init_config import _generate_simple_config

        _generate_simple_config([])
    except Exception:
        Config.save(save_simple_data=True)


def migrate_legacy_plugin_config() -> bool:
    if not Config:
        return False
    changed = False

    profile_templates = _get_raw_config("MOESEKAI_PROFILE_URL_TEMPLATES")
    profile_token = _get_raw_config("MOESEKAI_PROFILE_TOKEN")
    if (
        profile_token == LEGACY_PROFILE_TOKEN_DEFAULT
        and profile_templates is not _SENTINEL
    ):
        _set_config_value("MOESEKAI_PROFILE_TOKEN", "")
        changed = True
    derived_bases = _derive_profile_bases(profile_templates)
    if derived_bases:
        current_bases = _get_raw_config("MOESEKAI_PROFILE_BASES")
        if current_bases is _SENTINEL or current_bases in (
            None,
            [],
            ["https://sekaiprofile.exmeaning.com"],
        ):
            _set_config_value("MOESEKAI_PROFILE_BASES", derived_bases)
            changed = True
        changed = _delete_config_key("MOESEKAI_PROFILE_URL_TEMPLATES") or changed

    # 资源类配置迁移到共享服务命名空间，避免新插件复用时仍依赖 MoeSekai。
    changed = _migrate_resource_configs() or changed

    for key in _LEGACY_KEYS_TO_REMOVE:
        if key == "MOESEKAI_PROFILE_URL_TEMPLATES" and derived_bases:
            continue
        changed = _delete_config_key(key) or changed

    if changed:
        _sync_config_files()
    return changed
