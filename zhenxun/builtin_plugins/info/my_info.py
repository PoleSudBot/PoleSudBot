from datetime import datetime, timedelta

from nonebot_plugin_uninfo import Uninfo
from tortoise.expressions import RawSQL
from tortoise.functions import Count

from zhenxun import ui
from zhenxun.builtin_plugins.sign_in.config import lik2level, lik2relation
from zhenxun.models.chat_history import ChatHistory
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.level_user import LevelUser
from zhenxun.models.sign_user import SignUser
from zhenxun.models.statistics import Statistics
from zhenxun.models.user_console import UserConsole
from zhenxun.services import avatar_service
from zhenxun.utils.platform import PlatformUtils

EMPTY_VALUE = "-"


def get_level_progress(impression: float) -> tuple[int, float, float, float]:
    """获取当前好感度等级区间与进度"""
    thresholds = list(lik2level.keys())
    level = int(lik2level[thresholds[-1]])
    next_impression = float(thresholds[-2])
    previous_impression = float(thresholds[-1])

    for index, threshold in enumerate(thresholds):
        if impression >= threshold:
            level = int(lik2level[threshold])
            previous_impression = float(threshold)
            if index > 0:
                next_impression = float(thresholds[index - 1])
            else:
                next_impression = impression
            break

    denominator = next_impression - previous_impression
    progress = (
        100.0
        if denominator <= 0
        else min(100.0, ((impression - previous_impression) / denominator) * 100)
    )
    return level, next_impression, previous_impression, progress


def format_decimal(value: float) -> str:
    """格式化小数，避免界面出现多余的尾随零"""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_rank(rank: int | None) -> str:
    """格式化排行展示值"""
    return f"#{rank}" if rank else EMPTY_VALUE


def get_acquaintance_days(create_time: datetime | None, now: datetime) -> int | str:
    """根据用户建档时间计算相识天数"""
    if not create_time:
        return EMPTY_VALUE
    return max((now.date() - create_time.date()).days + 1, 1)


async def get_rank(
    model,
    user_id: str,
    order_field: str,
    group_id: str | None = None,
) -> int | None:
    """按指定字段计算用户排行"""
    query = model
    if group_id:
        # 群排行只在当前群成员范围内计算，避免群成员缓存缺失时误退化成总排行。
        user_id_list = await GroupInfoUser.filter(group_id=group_id).values_list(
            "user_id", flat=True
        )
        if not user_id_list:
            return None
        query = query.filter(user_id__in=user_id_list)
    ranked_user_ids = (
        await query.annotate().order_by(f"-{order_field}").values_list(
            "user_id", flat=True
        )
    )
    if user_id not in ranked_user_ids:
        return None
    return ranked_user_ids.index(user_id) + 1


async def get_activity_rank(
    model,
    user_id: str,
    group_id: str | None,
    start_time: datetime,
    end_time: datetime,
) -> int | None:
    """计算用户在当前群过去七日的活动排行"""
    if not group_id:
        return None
    # 群排行必须限定在当前群消息/调用记录内，避免私聊和跨群数据混入趋势摘要。
    ranked_user_ids = (
        await model.filter(
            group_id=group_id,
            create_time__gte=start_time,
            create_time__lt=end_time,
        )
        .annotate(count=Count("id"))
        .group_by("user_id")
        .order_by("-count")
        .values_list("user_id", flat=True)
    )
    if user_id not in ranked_user_ids:
        return None
    return ranked_user_ids.index(user_id) + 1


async def get_activity_history(
    user_id: str, group_id: str | None
) -> tuple[list[str], list[int], list[int], datetime, datetime]:
    """获取用户近期发言和调用记录

    参数:
        user_id: 用户id
        group_id: 群id

    返回:
        tuple[list[str], list[int], list[int], datetime, datetime]:
            日期列表, 发言次数列表, 调用次数列表, 查询开始时间, 查询结束时间

    """
    now = datetime.now()
    today_start = datetime.combine(now.date(), datetime.min.time())
    filter_start = today_start - timedelta(days=7)

    # 趋势图固定取过去 7 个完整自然日，避免当天尚未结束导致尾部异常下跌。
    chat_date_list = (
        await ChatHistory.filter(
            user_id=user_id,
            group_id=group_id,
            direction="in",
            create_time__gte=filter_start,
            create_time__lt=today_start,
        )
        .annotate(date=RawSQL("DATE(create_time)"), count=Count("id"))
        .group_by("date")
        .values("date", "count")
    )
    call_date_list = (
        await Statistics.filter(
            user_id=user_id,
            group_id=group_id,
            create_time__gte=filter_start,
            create_time__lt=today_start,
        )
        .annotate(date=RawSQL("DATE(create_time)"), count=Count("id"))
        .group_by("date")
        .values("date", "count")
    )
    chat_date2cnt = {str(item["date"]): item["count"] for item in chat_date_list}
    call_date2cnt = {str(item["date"]): item["count"] for item in call_date_list}

    chart_dates: list[str] = []
    chat_count_list: list[int] = []
    call_count_list: list[int] = []
    for offset in range(7, 0, -1):
        current_date = (today_start - timedelta(days=offset)).date()
        date_str = str(current_date)
        chart_dates.append(date_str[5:])
        chat_count_list.append(chat_date2cnt.get(date_str, 0))
        call_count_list.append(call_date2cnt.get(date_str, 0))
    return chart_dates, chat_count_list, call_count_list, filter_start, today_start


async def get_user_info(
    session: Uninfo, user_id: str, group_id: str | None, nickname: str
) -> bytes:
    """获取用户个人信息

    参数:
        session: Uninfo
        user_id: 用户id
        group_id: 群id
        nickname: 用户昵称

    返回:
        bytes: 图片数据
    """
    platform = PlatformUtils.get_platform(session) or "qq"
    avatar_path = await avatar_service.get_avatar_path(platform, user_id)
    avatar_url = avatar_path.as_uri() if avatar_path else ""

    user = await UserConsole.get_user(user_id, platform)
    permission_level = await LevelUser.get_user_level(user_id, group_id)

    # 好感度缺失时保留等级起点，但具体值与排行显示占位符，避免误导为已有签到数据。
    sign_user = await SignUser.get_or_none(user_id=user_id)
    impression = float(sign_user.impression) if sign_user else 0.0
    sign_level, next_impression, _, favorability_progress = get_level_progress(
        impression
    )
    relation = lik2relation.get(str(sign_level), "未知")
    remaining_impression = max(0.0, next_impression - impression)

    chat_count = await ChatHistory.filter(
        user_id=user_id,
        group_id=group_id,
        direction="in",
    ).count()
    stat_count = await Statistics.filter(user_id=user_id, group_id=group_id).count()

    uid = f"{user.uid}".rjust(12, "0")
    uid_formatted = f"{uid[:4]} {uid[4:8]} {uid[8:]}"

    now = datetime.now()

    (
        chart_labels,
        chat_chart_data,
        call_chart_data,
        activity_start,
        activity_end,
    ) = await get_activity_history(user_id, group_id)
    chat_week_total = sum(chat_chart_data)
    call_week_total = sum(call_chart_data)

    # 排行复用签到与金币现有数据表，保持与对应排行榜命令一致的排序口径。
    favorability_group_rank = (
        await get_rank(SignUser, user_id, "impression", group_id)
        if sign_user
        else None
    )
    favorability_total_rank = (
        await get_rank(SignUser, user_id, "impression") if sign_user else None
    )
    gold_group_rank = await get_rank(UserConsole, user_id, "gold", group_id)
    gold_total_rank = await get_rank(UserConsole, user_id, "gold")
    chat_week_rank = await get_activity_rank(
        ChatHistory, user_id, group_id, activity_start, activity_end
    )
    call_week_rank = await get_activity_rank(
        Statistics, user_id, group_id, activity_start, activity_end
    )

    profile_data = {
        "page": {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "info": {
            "avatar_url": avatar_url,
            "nickname": nickname,
            "uid": uid_formatted,
        },
        "profile": {
            "permission_level": permission_level,
            "acquaintance_days": get_acquaintance_days(user.create_time, now),
            "acquaintance_since": user.create_time.strftime("%Y-%m-%d")
            if user.create_time
            else EMPTY_VALUE,
        },
        "stats": {
            "call_count": stat_count,
            "chat_count": chat_count,
        },
        "gold": {
            "value": user.gold,
            "group_rank": format_rank(gold_group_rank),
            "total_rank": format_rank(gold_total_rank),
        },
        "favorability": {
            "level": sign_level,
            "level_text": f"LV.{sign_level} {relation}",
            "current": format_decimal(impression) if sign_user else EMPTY_VALUE,
            "next_level_at": (
                format_decimal(next_impression) if sign_user else EMPTY_VALUE
            ),
            "progress": favorability_progress if sign_user else 0,
            "remaining": format_decimal(remaining_impression)
            if sign_user
            else EMPTY_VALUE,
            "group_rank": format_rank(favorability_group_rank),
            "total_rank": format_rank(favorability_total_rank),
        },
        "chart": {
            "labels": chart_labels,
            "chat_data": chat_chart_data,
            "call_data": call_chart_data,
        },
        "activity": {
            "chat_total": chat_week_total,
            "call_total": call_week_total,
            "chat_rank": format_rank(chat_week_rank),
            "call_rank": format_rank(call_week_rank),
        },
    }

    return await ui.render_template("pages/builtin/my_info", data=profile_data)
