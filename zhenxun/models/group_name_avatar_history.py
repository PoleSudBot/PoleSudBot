from tortoise import fields

from zhenxun.services.db_context import Model


class GroupNameAvatarHistory(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    platform = fields.CharField(255, default="qq", description="平台")
    """所属平台"""
    user_id = fields.CharField(255, description="用户id")
    """用户id"""
    avatar_hash = fields.CharField(64, description="头像hash")
    """压缩后头像文件的sha256 hash"""
    source = fields.CharField(32, default="cache_refresh", description="来源")
    """记录来源"""
    record_time = fields.DatetimeField(auto_now_add=True, description="记录时间")
    """记录时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "group_name_avatar_history"
        table_description = "群成员历史头像记录表"
        indexes = [  # noqa: RUF012
            ("platform", "user_id", "record_time"),
            ("platform", "user_id", "avatar_hash"),
        ]
