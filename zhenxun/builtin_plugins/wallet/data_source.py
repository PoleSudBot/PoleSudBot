from nonebot_plugin_uninfo import Uninfo

from zhenxun import ui
from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.models.friend_user import FriendUser
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.user_console import UserConsole
from zhenxun.services import avatar_service
from zhenxun.ui.models import ImageCell, TextCell
from zhenxun.utils.platform import PlatformUtils

ICON_PATH = IMAGE_PATH / "_icon"

PLATFORM_PATH = {
    "dodo": ICON_PATH / "dodo.png",
    "discord": ICON_PATH / "discord.png",
    "kaiheila": ICON_PATH / "kook.png",
    "qq": ICON_PATH / "qq.png",
}


async def get_balance(user_id: str, platform: str | None = None) -> int:
    """获取用户钱包余额"""
    user = await UserConsole.get_user(user_id, platform)
    return user.gold


async def wallet_rank(session: Uninfo, group_id: str | None, num: int) -> bytes | str:
    """生成钱包排行榜"""
    query = UserConsole
    if group_id:
        # 群排行只统计当前群成员，避免群成员缓存为空时误展示全局榜。
        uid_list = await GroupInfoUser.filter(group_id=group_id).values_list(
            "user_id", flat=True
        )
        if uid_list:
            query = query.filter(user_id__in=uid_list)
        else:
            return "当前群还没有钱包数据哦..."

    user_list = await query.annotate().order_by("-gold").values_list("user_id", "gold")
    if not user_list:
        return "当前还没有人拥有金币哦..."

    user_id_list = [user[0] for user in user_list]
    if session.user.id in user_id_list:
        index = user_id_list.index(session.user.id) + 1
    else:
        index = "-1（未统计）"

    user_list = user_list[:num] if num < len(user_list) else user_list
    friend_user = await FriendUser.filter(user_id__in=user_id_list).values_list(
        "user_id", "user_name"
    )
    uid2name = {user[0]: user[1] for user in friend_user}
    if diff_id := set(user_id_list).difference(set(uid2name.keys())):
        group_user = await GroupInfoUser.filter(user_id__in=diff_id).values_list(
            "user_id", "user_name"
        )
        for group_member in group_user:
            uid2name[group_member[0]] = group_member[1]

    column_name = ["排名", "-", "名称", "金币", "平台"]
    data_list = []
    platform = PlatformUtils.get_platform(session)
    for i, user in enumerate(user_list):
        avatar_path = await avatar_service.get_avatar_path(platform, user[0])
        platform_path = PLATFORM_PATH.get(platform)
        data_list.append(
            [
                TextCell(content=f"{i + 1}"),
                ImageCell(
                    src=avatar_path.as_uri() if avatar_path else "", shape="circle"
                )
                if avatar_path
                else TextCell(content=""),
                TextCell(content=uid2name.get(user[0]) or user[0]),
                TextCell(content=str(user[1]), bold=True),
                ImageCell(src=platform_path.resolve().as_uri())
                if platform_path
                else TextCell(content=""),
            ]
        )

    if group_id:
        title = "钱包群组内排行"
        tip = f"你的排名在本群第 {index} 位哦!"
    else:
        title = "钱包全局排行"
        tip = f"你的排名在全局第 {index} 位哦!"

    table = ui.table(title, tip)
    table.set_headers(column_name).add_rows(data_list)
    return await ui.render(table)
