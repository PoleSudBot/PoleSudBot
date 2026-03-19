from __future__ import annotations

from tortoise import fields

from zhenxun.services.db_context import Model


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
