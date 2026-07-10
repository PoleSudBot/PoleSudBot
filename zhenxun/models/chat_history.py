from datetime import datetime, timedelta
from typing import Literal
from typing_extensions import Self

from tortoise import fields, timezone
from tortoise.functions import Count

from zhenxun.configs.config import BotConfig
from zhenxun.services.db_context import Model


class ChatHistory(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255)
    """用户id"""
    group_id = fields.CharField(255, null=True)
    """群聊id"""
    text = fields.TextField(null=True)
    """文本内容"""
    plain_text = fields.TextField(null=True)
    """纯文本"""
    create_time = fields.DatetimeField(default=timezone.now)
    """消息发生时间"""
    bot_id = fields.CharField(255, null=True)
    """bot记录id"""
    platform = fields.CharField(255, null=True)
    """平台"""
    direction = fields.CharField(16, default="in")
    """消息方向，in为用户消息，out为Bot消息"""
    message_id = fields.CharField(255, null=True)
    """平台消息id"""
    message_type = fields.CharField(32, null=True)
    """消息类型，group/private等"""
    segments = fields.JSONField(null=True)
    """结构化消息段"""
    segment_types = fields.JSONField(null=True)
    """消息段类型列表"""
    reply_to_message_id = fields.CharField(255, null=True)
    """引用消息id"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "chat_history"
        table_description = "聊天记录数据表"
        indexes = (
            ("group_id", "create_time", "id"),
            ("bot_id", "create_time"),
            ("user_id", "group_id", "create_time"),
            ("platform", "bot_id", "message_id"),
            ("direction", "create_time"),
        )

    @classmethod
    async def get_group_msg_rank(
        cls,
        gid: str | None,
        limit: int = 10,
        order: str = "DESC",
        date_scope: tuple[datetime, datetime] | None = None,
    ) -> list[Self]:
        """获取排行数据

        参数:
            gid: 群号
            limit: 获取数量
            order: 排序类型，desc，des
            date_scope: 日期范围
        """
        o = "-" if order == "DESC" else ""
        query = (
            cls.filter(group_id=gid, direction="in")
            if gid
            else cls.filter(direction="in")
        )
        if date_scope:
            query = query.filter(create_time__range=date_scope)
        return list(
            await query.annotate(count=Count("user_id"))
            .order_by(f"{o}count")
            .group_by("user_id")
            .limit(limit)
            .values_list("user_id", "count")
        )  # type: ignore

    @classmethod
    async def get_group_first_msg_datetime(
        cls, group_id: str | None
    ) -> datetime | None:
        """获取群第一条记录消息时间

        参数:
            group_id: 群组id
        """
        if group_id:
            message = (
                await cls.filter(group_id=group_id, direction="in")
                .order_by("create_time")
                .first()
            )
        else:
            message = await cls.filter(direction="in").order_by("create_time").first()
        return message.create_time if message else None

    @classmethod
    async def get_message(
        cls,
        uid: str,
        gid: str,
        type_: Literal["user", "group"],
        msg_type: Literal["private", "group"] | None = None,
        days: int | tuple[datetime, datetime] | None = None,
    ) -> list[Self]:
        """获取消息查询query

        参数:
            uid: 用户id
            gid: 群聊id
            type_: 类型，私聊或群聊
            msg_type: 消息类型，用户或群聊
            days: 限制日期
        """
        if type_ == "user":
            query = cls.filter(user_id=uid, direction="in")
            if msg_type == "private":
                query = query.filter(group_id__isnull=True)
            elif msg_type == "group":
                query = query.filter(group_id__not_isnull=True)
        else:
            query = cls.filter(group_id=gid, direction="in")
            if uid:
                query = query.filter(user_id=uid)
        if days:
            if isinstance(days, int):
                query = query.filter(
                    create_time__gte=datetime.now() - timedelta(days=days)
                )
            elif isinstance(days, tuple):
                query = query.filter(create_time__range=days)
        return await query.all()  # type: ignore

    @classmethod
    async def _run_script(cls):
        db_type = (BotConfig.get_sql_type() or "").lower()
        scripts = [
            # 旧表可能仍使用 user_qq，先保持兼容重命名再追加新字段。
            "ALTER TABLE chat_history RENAME COLUMN user_qq TO user_id;",
        ]

        if "postgres" in db_type:
            scripts.extend(
                [
                    # PostgreSQL 可原地放宽旧字段约束，避免私聊或空文本记录写入失败。
                    "ALTER TABLE chat_history ALTER group_id DROP NOT NULL;",
                    "ALTER TABLE chat_history ALTER text DROP NOT NULL;",
                    "ALTER TABLE chat_history ALTER plain_text DROP NOT NULL;",
                    "ALTER TABLE chat_history "
                    "ALTER COLUMN user_id TYPE character varying(255);",
                    "ALTER TABLE chat_history "
                    "ALTER COLUMN group_id TYPE character varying(255);",
                    "ALTER TABLE chat_history ADD COLUMN bot_id "
                    "character varying(255);",
                    "ALTER TABLE chat_history ADD COLUMN platform "
                    "character varying(255);",
                    "ALTER TABLE chat_history ADD COLUMN direction "
                    "character varying(16) DEFAULT 'in';",
                    "ALTER TABLE chat_history ADD COLUMN message_id "
                    "character varying(255);",
                    "ALTER TABLE chat_history ADD COLUMN message_type "
                    "character varying(32);",
                    "ALTER TABLE chat_history ADD COLUMN segments JSON;",
                    "ALTER TABLE chat_history ADD COLUMN segment_types JSON;",
                    "ALTER TABLE chat_history ADD COLUMN reply_to_message_id "
                    "character varying(255);",
                ]
            )
        elif "mysql" in db_type:
            scripts.extend(
                [
                    # MySQL 使用 MODIFY COLUMN 完成旧字段放宽和类型对齐。
                    "ALTER TABLE chat_history MODIFY COLUMN user_id "
                    "VARCHAR(255) NOT NULL;",
                    "ALTER TABLE chat_history MODIFY COLUMN group_id "
                    "VARCHAR(255) NULL;",
                    "ALTER TABLE chat_history MODIFY COLUMN text TEXT NULL;",
                    "ALTER TABLE chat_history MODIFY COLUMN plain_text TEXT NULL;",
                    "ALTER TABLE chat_history ADD COLUMN bot_id VARCHAR(255);",
                    "ALTER TABLE chat_history ADD COLUMN platform VARCHAR(255);",
                    "ALTER TABLE chat_history ADD COLUMN direction "
                    "VARCHAR(16) DEFAULT 'in';",
                    "ALTER TABLE chat_history ADD COLUMN message_id VARCHAR(255);",
                    "ALTER TABLE chat_history ADD COLUMN message_type VARCHAR(32);",
                    "ALTER TABLE chat_history ADD COLUMN segments JSON;",
                    "ALTER TABLE chat_history ADD COLUMN segment_types JSON;",
                    "ALTER TABLE chat_history ADD COLUMN reply_to_message_id "
                    "VARCHAR(255);",
                ]
            )
        else:
            scripts.extend(
                [
                    # SQLite 不安全做原地约束重写，只追加兼容字段和幂等索引。
                    "ALTER TABLE chat_history ADD COLUMN bot_id VARCHAR(255);",
                    "ALTER TABLE chat_history ADD COLUMN platform VARCHAR(255);",
                    "ALTER TABLE chat_history ADD COLUMN direction "
                    "VARCHAR(16) DEFAULT 'in';",
                    "ALTER TABLE chat_history ADD COLUMN message_id VARCHAR(255);",
                    "ALTER TABLE chat_history ADD COLUMN message_type VARCHAR(32);",
                    "ALTER TABLE chat_history ADD COLUMN segments JSON;",
                    "ALTER TABLE chat_history ADD COLUMN segment_types JSON;",
                    "ALTER TABLE chat_history ADD COLUMN reply_to_message_id "
                    "VARCHAR(255);",
                ]
            )

        if "sqlite" in db_type:
            scripts.extend(
                [
                    "CREATE INDEX IF NOT EXISTS idx_chat_history_group_time_id "
                    "ON chat_history(group_id, create_time, id);",
                    "CREATE INDEX IF NOT EXISTS idx_chat_history_bot_time "
                    "ON chat_history(bot_id, create_time);",
                    "CREATE INDEX IF NOT EXISTS idx_chat_history_user_group_time "
                    "ON chat_history(user_id, group_id, create_time);",
                    "CREATE INDEX IF NOT EXISTS idx_chat_history_platform_bot_msg "
                    "ON chat_history(platform, bot_id, message_id);",
                    "CREATE INDEX IF NOT EXISTS idx_chat_history_direction_time "
                    "ON chat_history(direction, create_time);",
                ]
            )
        else:
            scripts.extend(
                [
                    "CREATE INDEX idx_chat_history_group_time_id "
                    "ON chat_history(group_id, create_time, id);",
                    "CREATE INDEX idx_chat_history_bot_time "
                    "ON chat_history(bot_id, create_time);",
                    "CREATE INDEX idx_chat_history_user_group_time "
                    "ON chat_history(user_id, group_id, create_time);",
                    "CREATE INDEX idx_chat_history_platform_bot_msg "
                    "ON chat_history(platform, bot_id, message_id);",
                    "CREATE INDEX idx_chat_history_direction_time "
                    "ON chat_history(direction, create_time);",
                ]
            )

        return scripts
