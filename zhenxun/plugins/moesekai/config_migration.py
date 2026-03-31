from __future__ import annotations

import os
import sys
from typing import Any

from .config import (
    DEFAULT_MASTER_SOURCE_ORDER,
    LEGACY_PROFILE_TOKEN_DEFAULT,
    MASTER_DATASET_KEYS,
    MasterSourceConfig,
    _merge_master_sources_config,
    build_default_master_sources,
)
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


def _get_raw_config(key: str, default: Any = _SENTINEL) -> Any:
    if not Config:
        return default
    return Config.get_config(MODULE_NAME, key, default, build_model=False)


def _set_config_value(key: str, value: Any) -> None:
    if not Config:
        return
    Config.set_config(MODULE_NAME, key, value, auto_save=False)


def _delete_config_key(key: str) -> bool:
    if not Config:
        return False
    changed = False
    module_group = getattr(Config, "_data", {}).get(MODULE_NAME)
    if module_group and key in module_group.configs:
        module_group.configs.pop(key, None)
        changed = True
    simple_group = getattr(Config, "_simple_data", {}).get(MODULE_NAME)
    if isinstance(simple_group, dict) and key in simple_group:
        simple_group.pop(key, None)
        changed = True
    return changed


def _derive_profile_bases(profile_templates: Any) -> list[str]:
    if not isinstance(profile_templates, list):
        return []
    result: list[str] = []
    for item in profile_templates:
        text = str(item or "").strip()
        if not text:
            return []
        suffix = next(
            (candidate for candidate in _LEGACY_PROFILE_TEMPLATE_SUFFIXES if text.endswith(candidate)),
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
        if default_source and _source_signature(source) == _source_signature(default_source):
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
        and left.version_path == right.version_path
        and left.datasets == right.datasets
        for left, right in zip(normalized, default_sources, strict=False)
    )


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

    legacy_interval = _get_raw_config("MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS")
    if legacy_interval is not _SENTINEL:
        current_interval = _get_raw_config("MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS")
        if current_interval is _SENTINEL:
            _set_config_value("MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS", legacy_interval)
            changed = True
        changed = _delete_config_key("MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS") or changed

    raw_master_sources = _get_raw_config("MOESEKAI_MASTER_SOURCES")
    if raw_master_sources is not _SENTINEL:
        default_sources = build_default_master_sources(DEFAULT_MASTER_SOURCE_ORDER)
        merged_sources = _merge_master_sources_config(
            raw_master_sources,
            default_sources=default_sources,
        )
        if _is_default_source_list(merged_sources):
            changed = _delete_config_key("MOESEKAI_MASTER_SOURCES") or changed
        else:
            migrated_sources = _replace_equivalent_default_sources(
                merged_sources,
                default_sources=default_sources,
            )
            _set_config_value(
                "MOESEKAI_MASTER_SOURCES",
                [source.model_dump() for source in migrated_sources],
            )
            changed = True

    for key in _LEGACY_KEYS_TO_REMOVE:
        if key == "MOESEKAI_PROFILE_URL_TEMPLATES" and derived_bases:
            continue
        changed = _delete_config_key(key) or changed

    if changed:
        _sync_config_files()
    return changed
