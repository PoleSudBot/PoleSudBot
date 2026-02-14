from tortoise import fields

from zhenxun.services.db_context import Model


class PjskBind(Model):
    """PJSK 游戏账号绑定表"""

    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增主键"""
    user_id = fields.CharField(255, unique=True)
    """用户QQ号"""
    server = fields.CharField(10, default="jp")
    """区服 (jp / cn / tw)"""
    pjsk_id = fields.CharField(50)
    """游戏内ID"""

    class Meta:
        table = "pjsk_bind"
        table_description = "PJSK游戏账号绑定表"

    @classmethod
    async def get_bind(cls, user_id: str):
        """查询用户绑定信息

        参数:
            user_id: QQ号

        返回:
            PjskBind | None: 绑定记录
        """
        return await cls.get_or_none(user_id=user_id)

    @classmethod
    async def set_bind(cls, user_id: str, server: str, pjsk_id: str):
        """新增或更新绑定

        参数:
            user_id: QQ号
            server: 区服
            pjsk_id: 游戏内ID

        返回:
            tuple[PjskBind, bool]: (记录, 是否新创建)
        """
        return await cls.update_or_create(
            defaults={"server": server, "pjsk_id": pjsk_id},
            user_id=user_id,
        )

    @classmethod
    async def del_bind(cls, user_id: str) -> bool:
        """删除绑定

        参数:
            user_id: QQ号

        返回:
            bool: 是否删除成功
        """
        count = await cls.filter(user_id=user_id).delete()
        return count > 0
