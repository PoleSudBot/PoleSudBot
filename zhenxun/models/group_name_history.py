from tortoise import fields

from zhenxun.services.db_context import Model


class GroupNameHistory(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    platform = fields.CharField(255, default="qq", description="平台")
    """所属平台"""
    group_id = fields.CharField(255, description="群聊id")
    """群聊id"""
    user_id = fields.CharField(255, description="用户id")
    """用户id"""
    name_type = fields.CharField(32, description="名称类型")
    """名称类型，group_card 表示群名片，qq_name 表示 QQ 名称"""
    display_name = fields.CharField(255, description="展示名称")
    """记录到的名称"""
    record_time = fields.DatetimeField(auto_now_add=True, description="记录时间")
    """记录时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "group_name_history"
        table_description = "群成员历史名称记录表"
        indexes = [  # noqa: RUF012
            ("platform", "group_id", "user_id", "name_type", "record_time"),
            ("group_id", "user_id"),
        ]
