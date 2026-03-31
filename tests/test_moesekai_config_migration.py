from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from zhenxun.plugins.moesekai.config import (
    LEGACY_PROFILE_TOKEN_DEFAULT,
    REGISTER_CONFIGS,
    build_default_master_sources,
)
from zhenxun.plugins.moesekai.config_migration import migrate_legacy_plugin_config


class _FakeConfig:
    def __init__(self, payload: dict[str, object]):
        self._data = {
            "moesekai": SimpleNamespace(
                configs={
                    key: SimpleNamespace(value=value)
                    for key, value in payload.items()
                }
            )
        }
        self._simple_data = {"moesekai": dict(payload)}
        self.saved = False

    def get_config(self, module: str, key: str, default=None, *, build_model: bool = True):
        group = self._data.get(module)
        if not group:
            return default
        config = group.configs.get(key)
        if not config:
            return default
        return config.value

    def set_config(self, module: str, key: str, value, auto_save: bool = False):
        group = self._data.setdefault(module, SimpleNamespace(configs={}))
        group.configs[key] = SimpleNamespace(value=value)
        self._simple_data.setdefault(module, {})[key] = value
        if auto_save:
            self.saved = True

    def save(self, *args, **kwargs):
        self.saved = True


def _legacy_default_master_sources() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for source in build_default_master_sources():
        result.append(
            {
                "name": source.name,
                "region": source.region,
                "version_url": source.version_url,
                "events_url": source.events_url,
                "version_field": source.version_field,
            }
        )
    return result


def test_register_configs_do_not_expose_legacy_keys():
    config_keys = {config.key for config in REGISTER_CONFIGS}
    assert "MOESEKAI_PROFILE_URL_TEMPLATES" not in config_keys
    assert "MOESEKAI_MASTER_SOURCES" not in config_keys
    assert "MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS" not in config_keys


def test_migrate_legacy_plugin_config_normalizes_defaults(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_config = _FakeConfig(
        {
            "MOESEKAI_RANKING_API_BASE": "https://sekaibangdan.exmeaning.com/public",
            "MOESEKAI_PROFILE_TOKEN": LEGACY_PROFILE_TOKEN_DEFAULT,
            "MOESEKAI_PROFILE_URL_TEMPLATES": [
                "https://sekaiprofile.exmeaning.com/profile/{server}/{game_id}?token={token}"
            ],
            "MOESEKAI_MASTER_SOURCES": _legacy_default_master_sources(),
            "MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS": 600,
        }
    )

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.config_migration.Config",
        fake_config,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.config_migration._sync_config_files",
        lambda: fake_config.save(),
    )

    changed = migrate_legacy_plugin_config()

    assert changed is True
    assert (
        fake_config.get_config("moesekai", "MOESEKAI_RANKING_API_BASE")
        == "https://sekaibangdan.exmeaning.com/public"
    )
    assert fake_config.get_config("moesekai", "MOESEKAI_PROFILE_TOKEN") == ""
    assert fake_config.get_config("moesekai", "MOESEKAI_PROFILE_BASES") == [
        "https://sekaiprofile.exmeaning.com"
    ]
    assert fake_config.get_config("moesekai", "MOESEKAI_PROFILE_URL_TEMPLATES", None) is None
    assert fake_config.get_config("moesekai", "MOESEKAI_MASTER_SOURCES", None) is None
    assert fake_config.get_config("moesekai", "MOESEKAI_MASTER_CHECK_INTERVAL_SECONDS") == 600
    assert (
        fake_config.get_config("moesekai", "MOESEKAI_MASTER_AUTO_CHECK_INTERVAL_SECONDS", None)
        is None
    )
    assert fake_config.saved is True


def test_migrate_legacy_plugin_config_keeps_custom_master_sources(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_config = _FakeConfig(
        {
            "MOESEKAI_MASTER_SOURCES": [
                {
                    "name": "custom-jp",
                    "region": "jp",
                    "family": "custom",
                    "base_url": "https://example.com/master",
                    "version_path": "versions.json",
                    "version_field": "dataVersion",
                    "datasets": {
                        "events": "events.json",
                        "virtualLives": "virtualLives.json",
                        "cards": "cards.json",
                        "gameCharacters": "gameCharacters.json",
                        "stamps": "stamps.json",
                        "musics": "musics.json",
                    },
                }
            ]
        }
    )

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.config_migration.Config",
        fake_config,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.config_migration._sync_config_files",
        lambda: fake_config.save(),
    )

    changed = migrate_legacy_plugin_config()

    assert changed is True
    migrated_sources = fake_config.get_config("moesekai", "MOESEKAI_MASTER_SOURCES")
    assert isinstance(migrated_sources, list)
    assert migrated_sources[0]["name"] == "custom-jp"
    assert migrated_sources[0]["family"] == "custom"


def test_migrate_legacy_plugin_config_keeps_explicit_profile_token_without_legacy_template(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_config = _FakeConfig(
        {
            "MOESEKAI_PROFILE_TOKEN": LEGACY_PROFILE_TOKEN_DEFAULT,
            "MOESEKAI_PROFILE_BASES": ["https://sekaiprofile.exmeaning.com"],
        }
    )

    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.config_migration.Config",
        fake_config,
    )
    monkeypatch.setattr(
        "zhenxun.plugins.moesekai.config_migration._sync_config_files",
        lambda: fake_config.save(),
    )

    changed = migrate_legacy_plugin_config()

    assert changed is False
    assert (
        fake_config.get_config("moesekai", "MOESEKAI_PROFILE_TOKEN")
        == LEGACY_PROFILE_TOKEN_DEFAULT
    )
