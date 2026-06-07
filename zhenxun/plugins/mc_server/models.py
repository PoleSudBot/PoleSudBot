from __future__ import annotations

from tortoise import fields

from zhenxun.services.db_context import Model


class McServer(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    platform = fields.CharField(32, default="qq")
    group_id = fields.CharField(128)
    name = fields.CharField(128, default="默认服务器")
    identity_key = fields.CharField(255, default="")
    preset_name = fields.CharField(128, default="")
    host = fields.CharField(255)
    port = fields.IntField(default=25565)
    rcon_host = fields.CharField(255, default="")
    rcon_port = fields.IntField(default=25575)
    rcon_password = fields.TextField(default="")
    log_path = fields.TextField(default="")
    bluemap_base_url = fields.TextField(default="")
    bluemap_map_ids = fields.JSONField(default=list)
    join_notify_enabled = fields.BooleanField(default=False)
    conn_notify_enabled = fields.BooleanField(default=False)
    chat_bridge_enabled = fields.BooleanField(default=False)
    online = fields.BooleanField(default=False)
    disconnected = fields.BooleanField(default=False)
    failed_count = fields.IntField(default=0)
    last_error = fields.TextField(default="")
    last_checked_at = fields.DatetimeField(null=True)
    last_sampled_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_server"
        table_description = "MC服务器群绑定"
        unique_together = (("platform", "group_id"),)
        indexes = (("platform", "group_id"), ("host", "port"), ("identity_key",))

    @classmethod
    async def _run_script(cls):
        return [
            "ALTER TABLE mc_server ADD COLUMN identity_key VARCHAR(255) DEFAULT '';",
            "ALTER TABLE mc_server ADD COLUMN preset_name VARCHAR(128) DEFAULT '';",
        ]


class McServerGroupBinding(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="group_bindings")
    platform = fields.CharField(32, default="qq")
    group_id = fields.CharField(128)
    join_notify_enabled = fields.BooleanField(default=False)
    conn_notify_enabled = fields.BooleanField(default=False)
    chat_bridge_enabled = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_server_group_binding"
        table_description = "MC服务器群绑定关系"
        unique_together = (("platform", "group_id"),)
        indexes = (("server_id",), ("platform", "group_id"))


class McSeason(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="seasons")
    name = fields.CharField(128, default="默认周目")
    started_at = fields.DatetimeField()
    ended_at = fields.DatetimeField(null=True)
    active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_season"
        table_description = "MC服务器周目"
        indexes = (("server_id", "active"), ("started_at",))


class McPlayer(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="players")
    name = fields.CharField(64)
    uuid = fields.CharField(64, default="")
    first_seen_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_player"
        table_description = "MC玩家"
        unique_together = (("server", "name"),)
        indexes = (("server_id", "name"), ("uuid",))


class McQqBinding(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="qq_bindings")
    player = fields.ForeignKeyField("models.McPlayer", related_name="qq_bindings")
    platform = fields.CharField(32, default="qq")
    qq_id = fields.CharField(128)
    created_by = fields.CharField(128, default="")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_qq_binding"
        table_description = "MC玩家与QQ绑定"
        unique_together = (("server", "platform", "qq_id"), ("server", "player"))
        indexes = (("platform", "qq_id"),)


class McOnlineSession(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="online_sessions")
    season = fields.ForeignKeyField(
        "models.McSeason", related_name="online_sessions", null=True
    )
    player = fields.ForeignKeyField("models.McPlayer", related_name="online_sessions")
    player_name = fields.CharField(64)
    qq_id = fields.CharField(128, default="")
    started_at = fields.DatetimeField()
    ended_at = fields.DatetimeField(null=True)
    source = fields.CharField(32, default="log")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_online_session"
        table_description = "MC玩家在线时间段"
        indexes = (("server_id", "started_at"), ("server_id", "player_name"))


class McPlayerCountSample(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="count_samples")
    season = fields.ForeignKeyField(
        "models.McSeason", related_name="count_samples", null=True
    )
    online_count = fields.IntField(default=0)
    max_players = fields.IntField(default=0)
    captured_at = fields.DatetimeField()
    source = fields.CharField(32, default="status")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "mc_player_count_sample"
        table_description = "MC在线人数采样"
        indexes = (("server_id", "captured_at"),)


class McLogCursor(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="log_cursors")
    path = fields.TextField()
    inode = fields.CharField(128, default="")
    offset = fields.BigIntField(default=0)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "mc_log_cursor"
        table_description = "MC日志读取游标"
        unique_together = (("server", "path"),)


class McChatDedup(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    server = fields.ForeignKeyField("models.McServer", related_name="chat_dedup")
    digest = fields.CharField(64)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "mc_chat_dedup"
        table_description = "MC聊天同步去重"
        unique_together = (("server", "digest"),)
        indexes = (("created_at",),)
