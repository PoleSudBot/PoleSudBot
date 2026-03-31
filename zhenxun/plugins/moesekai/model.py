from __future__ import annotations

import os
import sys

from tortoise import fields

_TEST_MODE = bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules

if not _TEST_MODE:
    try:
        from zhenxun.services.db_context import Model
    except Exception:
        from tortoise.models import Model
else:
    from tortoise.models import Model


class MoeSekaiUserSettings(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    platform = fields.CharField(32)
    user_id = fields.CharField(128)
    default_server = fields.CharField(8, null=True)
    allow_share_profile = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "moesekai_user_settings"
        table_description = "MoeSekai 用户设置"
        unique_together = (("platform", "user_id"),)


class MoeSekaiBinding(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    platform = fields.CharField(32)
    user_id = fields.CharField(128)
    server = fields.CharField(8)
    game_id = fields.CharField(32)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "moesekai_bindings"
        table_description = "MoeSekai 绑定信息"
        unique_together = (("platform", "user_id", "server"),)
        indexes = (("server", "game_id"),)


class MoeSekaiBlacklistEntry(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    target_type = fields.CharField(16)
    server = fields.CharField(8, null=True)
    target_value = fields.CharField(128)
    created_by = fields.CharField(128)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "moesekai_blacklist_entries"
        table_description = "MoeSekai 黑名单"
        unique_together = (("target_type", "server", "target_value"),)


class MoeSekaiAliasEntry(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    target_type = fields.CharField(16)
    target_value = fields.CharField(64)
    alias = fields.CharField(128)
    alias_normalized = fields.CharField(128)
    scope = fields.CharField(128, default="global")
    created_by = fields.CharField(128)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "moesekai_alias_entries"
        table_description = "MoeSekai 通用别名表"
        unique_together = (("target_type", "alias_normalized", "scope"),)
        indexes = (
            ("target_type", "target_value"),
            ("target_type", "scope"),
        )


class MoeSekaiGroupFeatureToggle(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    platform = fields.CharField(32)
    group_id = fields.CharField(128)
    feature_name = fields.CharField(32)
    server = fields.CharField(8)
    enabled = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "moesekai_group_feature_toggles"
        table_description = "MoeSekai 群功能开关"
        unique_together = (("platform", "group_id", "feature_name", "server"),)


class MoeSekaiUserFeatureSubscription(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    platform = fields.CharField(32)
    group_id = fields.CharField(128)
    user_id = fields.CharField(128)
    feature_name = fields.CharField(32)
    server = fields.CharField(8)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "moesekai_user_feature_subscriptions"
        table_description = "MoeSekai 用户功能订阅"
        unique_together = (("platform", "group_id", "user_id", "feature_name", "server"),)


class MoeSekaiNotificationRecord(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    platform = fields.CharField(32)
    group_id = fields.CharField(128)
    feature_name = fields.CharField(32)
    server = fields.CharField(8)
    record_key = fields.CharField(128)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "moesekai_notification_records"
        table_description = "MoeSekai 提醒幂等记录"
        unique_together = (("platform", "group_id", "feature_name", "server", "record_key"),)
