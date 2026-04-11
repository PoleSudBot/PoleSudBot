import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

from zhdate import ZhDate

from zhenxun import ui
from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .config import REPORT_PATH, Anime, Hitokoto, SixData, get_fetch_time
from .date import get_festivals_dates


class Report:
    hitokoto_url = "https://v1.hitokoto.cn/?c=a"
    alapi_url = "https://v3.alapi.cn/api/zaobao"
    six_url = "https://60s.viki.moe/v2/60s"  # 如域名无法访问，可使用公共实例: https://docs.60s-api.viki.moe/7306811m0
    bili_url = "https://s.search.bilibili.com/main/hotword"
    it_url = "https://www.ithome.com/rss/"
    anime_url = "https://api.bgm.tv/calendar"

    week = {  # noqa: RUF012
        0: "一",
        1: "二",
        2: "三",
        3: "四",
        4: "五",
        5: "六",
        6: "日",
    }

    @classmethod
    def _now(cls) -> datetime:
        return datetime.now()

    @classmethod
    def _get_report_file(cls, report_date: date) -> Path:
        return REPORT_PATH / f"{report_date}.png"

    @classmethod
    def _get_fetch_anchor(cls, now: datetime) -> datetime:
        hour, minute = get_fetch_time()
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @classmethod
    def _get_primary_report_date(cls, now: datetime) -> date:
        if now < cls._get_fetch_anchor(now):
            return now.date() - timedelta(days=1)
        return now.date()

    @classmethod
    def _build_report_datetime(cls, now: datetime, report_date: date) -> datetime:
        return datetime.combine(report_date, now.time())

    @classmethod
    def _select_report_date(
        cls, now: datetime, *, force_refresh: bool = False
    ) -> date:
        report_date = cls._get_primary_report_date(now)
        if now >= cls._get_fetch_anchor(now):
            return report_date

        report_file = cls._get_report_file(report_date)
        if cls._is_cache_fresh(
            report_file, report_date, now, force_refresh=force_refresh
        ):
            return report_date
        return now.date()

    @classmethod
    def _is_cache_fresh(
        cls,
        file: Path,
        report_date: date,
        now: datetime,
        *,
        force_refresh: bool = False,
    ) -> bool:
        if force_refresh or not file.exists():
            return False

        if report_date != now.date():
            return True

        fetch_anchor = cls._get_fetch_anchor(now)
        if now < fetch_anchor:
            return True

        file_mtime = datetime.fromtimestamp(file.stat().st_mtime)
        return file_mtime >= fetch_anchor

    @classmethod
    def _clear_report_cache(cls):
        for file in REPORT_PATH.iterdir():
            if file.is_file():
                file.unlink()

    @classmethod
    def get_visible_report_date(cls, now: datetime | None = None) -> date:
        current = now or cls._now()
        return cls._select_report_date(current)

    @classmethod
    def get_visible_report_file(cls, now: datetime | None = None) -> Path:
        report_date = cls.get_visible_report_date(now)
        return cls._get_report_file(report_date)

    @classmethod
    async def _render_report_image(cls, report_time: datetime) -> bytes:
        zhdata = ZhDate.from_datetime(report_time)
        hitokoto, bili, six, it, anime = await asyncio.gather(
            *[
                cls.get_hitokoto(),
                cls.get_bili(),
                cls.get_six(),
                cls.get_it(),
                cls.get_anime(report_time),
            ]
        )
        data = {
            "data_festival": get_festivals_dates(report_time.date()),
            "data_hitokoto": hitokoto,
            "data_bili": bili,
            "data_six": six,
            "data_anime": anime,
            "data_it": it,
            "week": cls.week[report_time.weekday()],
            "date": report_time.date(),
            "zh_date": zhdata.chinese().split()[0][5:],
            "full_show": Config.get_config("mahiro_report", "full_show"),
        }
        template_path = Path(__file__).parent / "mahiro_report" / "main.html"
        component = ui.template(template_path, data=data)
        return await ui.render(
            component, viewport={"width": 578, "height": 1885}, wait=2
        )

    @classmethod
    async def get_report_image(cls, force_refresh: bool = False) -> Path:
        """获取数据"""
        now = cls._now()
        report_date = cls._select_report_date(now, force_refresh=force_refresh)
        file = cls._get_report_file(report_date)
        if cls._is_cache_fresh(
            file, report_date, now, force_refresh=force_refresh
        ):
            return file

        cls._clear_report_cache()
        report_time = cls._build_report_datetime(now, report_date)
        image_bytes = await cls._render_report_image(report_time)
        file.write_bytes(image_bytes)
        return file

    @classmethod
    async def get_hitokoto(cls) -> str:
        """获取今日一言"""
        try:
            res = await AsyncHttpx.get(cls.hitokoto_url)
            data = Hitokoto(**res.json())
            return data.hitokoto
        except Exception as e:
            logger.error(f"获取今日一言失败: {e}")
            return "获取今日一言失败 QAQ"

    @classmethod
    async def get_bili(cls) -> list[str]:
        """获取B站热点"""
        try:
            res = await AsyncHttpx.get(cls.bili_url)
            data = res.json()
            return [item["keyword"] for item in data["list"]]
        except Exception as e:
            logger.error(f"获取B站热点失败: {e}")
            return ["获取B站热点失败 QAQ"]

    @classmethod
    async def get_alapi_data(cls) -> list[str]:
        """获取alapi数据"""
        token = Config.get_config("alapi", "ALAPI_TOKEN")  # 从配置中获取alapi
        payload = {"token": token, "format": "json"}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            res = await AsyncHttpx.post(cls.alapi_url, data=payload, headers=headers)
            if res.status_code != 200:
                return ["Error: Unable to fetch data"]
            data = res.json()
            news_items = data.get("data", {}).get("news", [])
            return news_items[:11] if len(news_items) > 11 else news_items
        except Exception as e:
            logger.error(f"获取alapi数据失败: {e}")
            return ["获取alapi数据失败 QAQ"]

    @classmethod
    async def get_six(cls) -> list[str]:
        """获取60S读世界"""
        if Config.get_config("alapi", "ALAPI_TOKEN"):
            return await cls.get_alapi_data()
        try:
            res = await AsyncHttpx.get(cls.six_url)
            data = SixData(**res.json())
            return data.data.news[:11] if len(data.data.news) > 11 else data.data.news
        except Exception as e:
            logger.error(f"获取60S读世界失败: {e}")
            return ["获取60S读世界失败 QAQ"]

    @classmethod
    async def get_it(cls) -> list[str]:
        """获取IT资讯"""
        try:
            res = await AsyncHttpx.get(cls.it_url)
            root = ET.fromstring(res.text)
            titles = []
            for item in root.findall("./channel/item"):
                title_element = item.find("title")
                if title_element is not None:
                    titles.append(title_element.text)
            return titles[:11] if len(titles) > 11 else titles
        except Exception as e:
            logger.error(f"获取IT资讯失败: {e}")
            return ["获取IT资讯失败 QAQ"]

    @classmethod
    async def get_anime(
        cls, report_time: datetime | None = None
    ) -> list[tuple[str, str]]:
        """获取今日新番"""
        try:
            res = await AsyncHttpx.get(cls.anime_url)
            data_list = []
            effective_time = report_time or cls._now()
            week = effective_time.weekday()
            try:
                anime = Anime(**res.json()[week])
            except IndexError:
                anime = Anime(**res.json()[-1])
            data_list.extend(
                (data.name_cn or data.name, data.image) for data in anime.items
            )
            return data_list[:8] if len(data_list) > 8 else data_list
        except Exception as e:
            logger.error(f"获取今日新番失败: {e}")
            return [("获取今日新番失败 QAQ", "")]
