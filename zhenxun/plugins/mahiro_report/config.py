from pydantic import BaseModel

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

MODULE_NAME = "mahiro_report"
DEFAULT_FETCH_TIME = "06:00"
DEFAULT_SEND_TIME = "09:01"
REPORT_PATH = DATA_PATH / "mahiro_report"
REPORT_PATH.mkdir(parents=True, exist_ok=True)


def parse_schedule_time(value: str) -> tuple[int, int]:
    text = value.strip()
    if ":" not in text:
        raise ValueError("时间格式错误，请使用 HH:MM。")

    hour_str, minute_str = text.split(":", maxsplit=1)
    if not (hour_str.isdigit() and minute_str.isdigit()):
        raise ValueError("时间格式错误，请使用 HH:MM。")

    hour = int(hour_str)
    minute = int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间超出有效范围。")
    return hour, minute


def _parse_configured_schedule_time(key: str, default: str) -> tuple[int, int]:
    raw_value = Config.get_config(MODULE_NAME, key, default)
    try:
        return parse_schedule_time(str(raw_value))
    except ValueError as e:
        logger.warning(
            f"真寻日报配置 {key}={raw_value!r} 无效，将回退到默认值 {default}",
            e=e,
        )
        return parse_schedule_time(default)


def _time_to_minutes(value: tuple[int, int]) -> int:
    return value[0] * 60 + value[1]


def get_fetch_time() -> tuple[int, int]:
    return _parse_configured_schedule_time("FETCH_TIME", DEFAULT_FETCH_TIME)


def get_send_time(fetch_time: tuple[int, int] | None = None) -> tuple[int, int]:
    effective_fetch_time = fetch_time or get_fetch_time()
    send_time = _parse_configured_schedule_time("SEND_TIME", DEFAULT_SEND_TIME)
    if _time_to_minutes(send_time) >= _time_to_minutes(effective_fetch_time):
        return send_time

    default_send_time = parse_schedule_time(DEFAULT_SEND_TIME)
    logger.warning(
        "真寻日报配置 SEND_TIME 早于 FETCH_TIME，将回退到默认发送时间 "
        f"{DEFAULT_SEND_TIME}"
    )
    if _time_to_minutes(default_send_time) >= _time_to_minutes(effective_fetch_time):
        return default_send_time

    logger.warning(
        "默认 SEND_TIME 仍早于 FETCH_TIME，将发送时间回退到 FETCH_TIME "
        f"{effective_fetch_time[0]:02d}:{effective_fetch_time[1]:02d}"
    )
    return effective_fetch_time


class Hitokoto(BaseModel):
    id: int
    """id"""
    uuid: str
    """uuid"""
    hitokoto: str
    """一言"""
    type: str
    """类型"""
    from_who: str | None
    """作者"""
    creator: str
    """创建者"""
    creator_uid: int
    """创建者id"""
    reviewer: int
    """审核者"""
    commit_from: str
    """提交来源"""
    created_at: str
    """创建日期"""
    length: int
    """长度"""


class SixDataTo(BaseModel):
    news: list[str]
    """新闻"""
    tip: str
    """tip"""
    updated: str
    """更新日期"""
    link: str
    """链接"""
    cover: str
    """图片"""


class SixData(BaseModel):
    code: int
    """状态码"""
    message: str
    """返回内容"""
    data: SixDataTo
    """数据"""


class WeekDay(BaseModel):
    en: str
    """英文"""
    cn: str
    """中文"""
    ja: str
    """日本称呼"""
    id: int
    """ID"""


class AnimeItem(BaseModel):
    name: str
    name_cn: str
    images: dict | None

    @property
    def image(self) -> str:
        return self.images["large"] if self.images else ""


class Anime(BaseModel):
    weekday: WeekDay
    items: list[AnimeItem]
