from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, UnidentifiedImageError

from zhenxun.services.log import logger
from zhenxun.utils.exception import AllURIsFailedError
from zhenxun.utils.http_utils import AsyncHttpx

from .config import PicSearchSettings
from .models import (
    AniListInfo,
    ImageInput,
    SauceNAOQuota,
    SauceNAOResult,
    SearchCandidate,
    SearchResultItem,
)

SAUCENAO_SEARCH_URL = "https://saucenao.com/search.php"
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"
TRACEMOE_SEARCH_URL = "https://api.trace.moe/search"
ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
ANIMETRACE_SEARCH_URL = "https://api.animetrace.com/v1/search"
ANIMETRACE_PRIMARY_MODEL = "animetrace_high_beta"
ANIMETRACE_FALLBACK_MODEL = "pre_stable"
ANIMETRACE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}

ANIMETRACE_STATUS_MESSAGES = {
    17701: "图片太大啦，压缩后再试试吧。",
    17702: "识角色服务现在有点忙，请稍后再试。",
    17703: "识角色请求参数异常，请换张图片再试。",
    17704: "识角色 API 正在维护，请稍后再试。",
    17705: "图片格式暂不支持，请换成常见图片格式再试。",
    17706: "识角色服务内部错误，请稍后再试。",
    17707: "识角色服务内部错误，请稍后再试。",
    17708: "图里角色可能太多了，请裁剪后再试。",
    17722: "识角色服务下载图片失败，请换张图片再试。",
    17728: "识角色服务使用次数已达上限，请稍后再试。",
    17731: "识角色服务现在有点忙，请稍后再试。",
    404: "识角色接口路径异常，请检查 API 状态。",
}

ANILIST_QUERY = """
query ($id: Int) {
  Media (id: $id, type: ANIME) {
    id
    siteUrl
    title {
      native
      romaji
      english
    }
    coverImage {
      large
    }
    isAdult
  }
}
"""


class SearchClientError(Exception):
    """外部搜索服务的可展示错误。"""

    def __init__(self, user_message: str, *, retryable: bool = False):
        super().__init__(user_message)
        self.user_message = user_message
        self.retryable = retryable


def _http_status_from_exception(error: BaseException) -> int | None:
    """从 AsyncHttpx 的包装异常中提取 HTTP 状态码。"""

    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    if isinstance(error, AllURIsFailedError):
        for exc in reversed(error.exceptions):
            if status := _http_status_from_exception(exc):
                return status
    if cause := getattr(error, "__cause__", None):
        return _http_status_from_exception(cause)
    return None


def _request_error(
    error: BaseException,
    *,
    status_template: str,
    fallback: str,
) -> SearchClientError:
    """把可预期网络错误统一转成可发送给用户的提示。"""

    if status := _http_status_from_exception(error):
        return SearchClientError(status_template.format(status=status))
    return SearchClientError(fallback)


def _serpapi_request_error(error: BaseException) -> SearchClientError:
    """把 SerpAPI 常见鉴权、限流和服务错误转成明确提示。"""

    status = _http_status_from_exception(error)
    if status in (401, 403):
        return SearchClientError("Google Lens 鉴权失败，请检查 SERP_API_KEY。")
    if status == 429:
        return SearchClientError("Google Lens 请求额度已用尽或触发限流，请稍后再试。")
    if status is not None and status >= 500:
        return SearchClientError("Google Lens 服务暂时不可用，请稍后再试。")
    if status is not None:
        return SearchClientError(f"Google Lens 请求失败：HTTP {status}")
    return SearchClientError("Google Lens 请求失败，请稍后再试。")


def _serpapi_payload_error(message: Any) -> SearchClientError:
    """把 SerpAPI 返回体里的 error 字段转成用户可读提示。"""

    text = _stringify(message).strip()
    lowered = text.lower()
    if "invalid api key" in lowered or "api key" in lowered:
        return SearchClientError("Google Lens 鉴权失败，请检查 SERP_API_KEY。")
    if "rate limit" in lowered or "quota" in lowered:
        return SearchClientError("Google Lens 请求额度已用尽或触发限流，请稍后再试。")
    return SearchClientError(f"Google Lens 搜索失败：{text or '未知错误'}")


def _animetrace_message(code: int | None, message: Any = None) -> str:
    """根据 AnimeTrace 文档状态码生成稳定、短句化的提示。"""

    if code in ANIMETRACE_STATUS_MESSAGES:
        return ANIMETRACE_STATUS_MESSAGES[code]
    if code is not None and code >= 500:
        return "识角色服务暂时不可用，请稍后再试。"
    if message_text := _stringify(message).strip():
        return f"识角色失败：{message_text}"
    return f"识角色失败：{code}" if code is not None else "识角色返回格式异常。"


def _is_animetrace_retryable_code(code: int | None) -> bool:
    """判断 AnimeTrace 错误是否值得切换模型重试。"""

    return code in {17702, 17706, 17707, 17731}


def _is_animetrace_success(payload: dict[str, Any]) -> bool:
    """兼容 AnimeTrace 新旧成功码，避免文档/服务端迁移期误判失败。"""

    code = payload.get("code")
    if code is None and isinstance(payload.get("data"), list):
        return True
    return str(code) in {"0", "17720", "200"}


def _image_data_uri(content: bytes, mimetype: str = "image/jpeg") -> str:
    """把图片 bytes 编码成模板可直接渲染的 data URI。"""

    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mimetype or 'image/jpeg'};base64,{encoded}"


def _crop_animetrace_preview(image: ImageInput, box: Any) -> str:
    """按 AnimeTrace 返回的 box 从原图裁出角色预览，失败时退回原图。"""

    fallback = _image_data_uri(image.content, image.mimetype)
    if not isinstance(box, list | tuple) or len(box) != 4:
        return fallback

    try:
        coords = [float(value) for value in box]
        with Image.open(BytesIO(image.content)) as base:
            width, height = base.size
            if max(coords) <= 1.5:
                left, top, right, bottom = (
                    coords[0] * width,
                    coords[1] * height,
                    coords[2] * width,
                    coords[3] * height,
                )
            else:
                left, top, right, bottom = coords

            left = max(0, min(width, int(left)))
            top = max(0, min(height, int(top)))
            right = max(left + 1, min(width, int(right)))
            bottom = max(top + 1, min(height, int(bottom)))

            # API 只给出识别框不返回媒体 URL，裁剪原图能避免额外托管图片，
            # 也比整张原图更容易让用户对照多个角色候选。
            cropped = base.convert("RGB").crop((left, top, right, bottom))
            output = BytesIO()
            cropped.save(output, format="JPEG", quality=90)
            return _image_data_uri(output.getvalue(), "image/jpeg")
    except (OSError, ValueError, UnidentifiedImageError):
        return fallback


def _to_int(value: Any) -> int | None:
    """将 API 返回的数字字段安全转为 int。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    """将 API 返回的数字字段安全转为 float。"""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_timestamp(value: float | None) -> str:
    """把 TraceMoe 秒数转换成用户更容易理解的分秒格式。"""

    if value is None:
        return "?"
    total_seconds = max(0, int(value))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_time_range(
    start: float | None,
    end: float | None,
    at: float | None,
) -> str:
    """按 TraceMoe 可用字段生成时间点或时间段文本。"""

    if start is not None and end is not None:
        return f"{_format_timestamp(start)} - {_format_timestamp(end)}"
    if at is not None:
        return _format_timestamp(at)
    if start is not None:
        return _format_timestamp(start)
    return "?"


def _stringify(value: Any) -> str:
    """把 SauceNAO 异构字段压平为可展示文本。"""

    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value)


def _first_value(data: dict[str, Any], keys: Iterable[str]) -> str:
    """按优先级从异构 data 字段里抽取第一个非空值。"""

    for key in keys:
        if value := _stringify(data.get(key)).strip():
            return value
    return ""


def _append_unique_url(urls: list[str], url: str) -> None:
    """把合法 URL 去重追加到列表。"""

    normalized = _normalize_source_url(url)
    if normalized and normalized not in urls:
        urls.append(normalized)


def _normalize_source_url(url: str) -> str:
    """规范化 SauceNAO 来源 URL，降低同一站点多种路径造成的重复。"""

    raw_url = url.strip()
    if not raw_url.startswith(("http://", "https://")):
        return ""

    parsed = urlsplit(raw_url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or parsed.path
    query = parsed.query

    if host in {"www.twitter.com", "twitter.com", "www.x.com"}:
        host = "x.com"

    # Danbooru 历史链接有 /post/show/{id} 和 /posts/{id} 两种形态；
    # 统一后才能避免文本链接里重复出现同一个帖子。
    if host == "danbooru.donmai.us":
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "post" and parts[1] == "show":
            path = f"/posts/{parts[2]}"
            query = ""
        elif len(parts) >= 2 and parts[0] == "posts":
            path = f"/posts/{parts[1]}"
            query = ""

    return urlunsplit((parsed.scheme, host, path, query, ""))


def is_supplemental_source_url(url: str) -> bool:
    """判断来源 URL 是否更适合放在链接清单的次要区域。"""

    host = urlsplit(url).netloc.lower()
    return any(
        marker in host
        for marker in (
            "danbooru.donmai.us",
            "gelbooru.com",
            "yande.re",
            "konachan.com",
            "konachan.net",
            "safebooru.org",
            "rule34.xxx",
            "i.pximg.net",
        )
    )


def _source_url_rank(url: str) -> int:
    """按用户打开价值给 SauceNAO 链接排序，原始来源优先于镜像站。"""

    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "pixiv.net" in host and "/artworks/" in path:
        return 0
    if host in {"x.com", "twitter.com", "www.twitter.com"}:
        return 1
    if "i.pximg.net" in host:
        return 3
    if is_supplemental_source_url(url):
        return 4
    return 2


def _site_label(url: str) -> str:
    """把主来源 URL 转成结果卡片里可读的站点徽标。"""

    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if "pixiv.net" in host or "pximg.net" in host:
        return "Pixiv"
    if host in {"x.com", "twitter.com"}:
        return "X"
    if "danbooru.donmai.us" in host:
        return "Danbooru"
    if "gelbooru.com" in host:
        return "Gelbooru"
    if "yande.re" in host:
        return "Yande.re"
    if "konachan" in host:
        return "Konachan"
    if "seiga.nicovideo.jp" in host:
        return "Nico Seiga"
    if "nijie.info" in host:
        return "Nijie"
    if "bcy.net" in host:
        return "BCY"
    if "getchu.com" in host:
        return "Getchu"
    return host.split(".")[0].title() if host else ""


def _ordered_ext_urls(data: dict[str, Any]) -> list[str]:
    """按用户更常用的站点优先级排序 SauceNAO ext_urls。"""

    ext_urls = data.get("ext_urls")
    if not isinstance(ext_urls, list):
        return []
    urls = [
        url
        for url in ext_urls
        if isinstance(url, str) and url.startswith(("http://", "https://"))
    ]

    return sorted(urls, key=_source_url_rank)


def _source_urls(data: dict[str, Any]) -> list[str]:
    """根据 SauceNAO index 常见字段构造完整来源链接列表。"""

    urls: list[str] = []
    if pixiv_id := data.get("pixiv_id"):
        _append_unique_url(urls, f"https://www.pixiv.net/artworks/{pixiv_id}")
    if tweet_id := data.get("tweet_id"):
        handle = str(data.get("twitter_user_handle") or "").lstrip("@")
        if handle:
            _append_unique_url(urls, f"https://x.com/{handle}/status/{tweet_id}")

    # ext_urls 经常同时包含 Pixiv/X/Danbooru，保留所有链接并让更常用来源排前面。
    for url in _ordered_ext_urls(data):
        _append_unique_url(urls, url)

    if danbooru_id := data.get("danbooru_id"):
        _append_unique_url(urls, f"https://danbooru.donmai.us/posts/{danbooru_id}")
    if seiga_id := data.get("seiga_id"):
        _append_unique_url(urls, f"https://seiga.nicovideo.jp/seiga/im{seiga_id}")
    if nijie_id := data.get("nijie_id"):
        _append_unique_url(urls, f"https://nijie.info/view.php?id={nijie_id}")
    if bcy_id := data.get("bcy_id"):
        _append_unique_url(urls, f"https://bcy.net/item/detail/{bcy_id}")
    if getchu_id := data.get("getchu_id"):
        _append_unique_url(urls, f"https://www.getchu.com/soft.phtml?id={getchu_id}")
    if pawoo_id := data.get("pawoo_id"):
        acct = data.get("pawoo_user_acct")
        if acct:
            _append_unique_url(urls, f"https://pawoo.net/@{acct}/{pawoo_id}")

    source = _stringify(data.get("source"))
    _append_unique_url(urls, source)
    primary_urls = [url for url in urls if not is_supplemental_source_url(url)]
    supplemental_urls = [url for url in urls if is_supplemental_source_url(url)]
    return primary_urls + supplemental_urls


def _parse_saucenao_item(payload: dict[str, Any]) -> SearchResultItem | None:
    """把 SauceNAO 单条结果压成插件统一模型。"""

    # 解析单条 header/data，缺关键字段时跳过这一条而不是让整次搜索失败。
    header = payload.get("header") if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(header, dict) or not isinstance(data, dict):
        return None

    similarity = _to_float(header.get("similarity"))
    if similarity is None:
        return None

    # SauceNAO 的 data 结构随 index_id 变化很大，只抽通用展示字段避免维护大而全模型。
    title = _first_value(
        data,
        (
            "title",
            "material",
            "jp_name",
            "eng_name",
            "source",
            "created_at",
        ),
    )
    author = _first_value(
        data,
        (
            "author",
            "member_name",
            "creator",
            "twitter_user_handle",
            "pawoo_user_display_name",
            "author_name",
            "user_name",
            "artist",
            "company",
        ),
    )
    # URL 优先走少量确定性的站点特例，再用 ext_urls/source 做宽松兜底。
    source_urls = _source_urls(data)
    source_url = source_urls[0] if source_urls else ""
    metadata = {}
    if pixiv_id := _stringify(data.get("pixiv_id")).strip():
        metadata["Pixiv ID"] = pixiv_id
    if characters := _first_value(data, ("characters", "character")):
        metadata["角色"] = characters

    return SearchResultItem(
        title=title or source_url or "未命名结果",
        url=source_url,
        links=source_urls,
        source=_site_label(source_url),
        author=author,
        similarity=similarity,
        thumbnail_url=_stringify(header.get("thumbnail")),
        hidden=bool(header.get("hidden", 0)),
        metadata=metadata,
    )


class SauceNAOClient:
    """SauceNAO JSON API 的最小客户端。"""

    async def search(
        self,
        image: ImageInput,
        settings: PicSearchSettings,
    ) -> SauceNAOResult:
        """上传图片到 SauceNAO 并解析统一结果。"""

        if not settings.saucenao_api_key:
            raise SearchClientError("未配置 SAUCENAO_API_KEY，无法使用 SauceNAO。")

        # SauceNAO 使用 bytes multipart 上传，避免让 SauceNAO 反向拉取
        # QQ/Tencent URL 失败。
        params = {
            "api_key": settings.saucenao_api_key,
            "output_type": 2,
            "numres": max(1, settings.saucenao_result_limit),
            "hide": min(max(settings.saucenao_nsfw_hide_level, 0), 3),
            "db": 999,
        }
        files = {
            "file": (
                image.filename,
                image.content,
                image.mimetype or "application/octet-stream",
            )
        }

        # 发起 API 请求并解析 JSON，HTTP/超时/JSON 错误都转换成用户可读提示。
        try:
            response = await AsyncHttpx.post(
                SAUCENAO_SEARCH_URL,
                params=params,
                files=files,
                timeout=30,
            )
            payload = response.json()
        except (httpx.HTTPError, AllURIsFailedError, ValueError) as e:
            raise _request_error(
                e,
                status_template="SauceNAO 请求失败：HTTP {status}",
                fallback="SauceNAO 请求失败，请稍后再试。",
            ) from e

        # SauceNAO 会把业务错误放在 header.status，不能只依赖 HTTP 状态码。
        header = payload.get("header") if isinstance(payload, dict) else {}
        if not isinstance(header, dict):
            raise SearchClientError("SauceNAO 返回格式异常。")

        status = _to_int(header.get("status"))
        message = _stringify(header.get("message"))
        if status is not None and status < 0:
            raise SearchClientError(f"SauceNAO 搜索失败：{message or status}")

        # 宽松解析每条结果，单条坏数据不影响其他候选继续展示。
        items = [
            item
            for raw in payload.get("results", [])
            if isinstance(raw, dict)
            if (item := _parse_saucenao_item(raw)) is not None
        ]
        items.sort(key=lambda item: item.similarity or 0, reverse=True)

        # 记录配额状态，低额度时提示维护者避免误判为插件故障。
        quota = SauceNAOQuota(
            short_remaining=_to_int(header.get("short_remaining")),
            long_remaining=_to_int(header.get("long_remaining")),
        )
        if quota.long_remaining is not None and quota.long_remaining < 10:
            logger.warning(
                f"SauceNAO 24h 剩余额度较低：{quota.long_remaining}",
                "搜图",
            )
        if quota.short_remaining is not None and quota.short_remaining < 1:
            logger.warning("SauceNAO 短时间额度已耗尽。", "搜图")

        # SauceNAO 返回的是站内相对展示路径，这里只作为人工复查入口展示。
        search_url = ""
        if query_image := header.get("query_image_display"):
            search_url = (
                f"https://saucenao.com/search.php?url=https://saucenao.com{query_image}"
            )

        return SauceNAOResult(items=items, quota=quota, search_url=search_url)


class SerpApiGoogleLensClient:
    """SerpAPI Google Lens 的最小客户端。"""

    async def search(
        self,
        image_url: str | None,
        settings: PicSearchSettings,
    ) -> list[SearchResultItem]:
        """使用公网图片 URL 查询 Google Lens。"""

        if not settings.serp_api_key:
            raise SearchClientError("未配置 SERP_API_KEY，无法使用 Google Lens。")
        if not image_url:
            raise SearchClientError(
                "Google Lens 需要公网图片 URL，本次图片没有可用 URL。"
            )

        # SerpAPI Google Lens 只接受公网 URL；v1 不 rehost，避免引入图片留存和清理策略。
        # safe 只是 Google 侧过滤参数，SerpAPI 不提供逐条 R18 分级，不能把普通缩略图
        # 标记成 NSFW 隐藏。
        params = {
            "engine": "google_lens",
            "url": image_url,
            "api_key": settings.serp_api_key,
            "safe": "active" if settings.google_lens_safe_search else "off",
        }
        # Lens 的 URL 不可达、额度耗尽和 5xx 都降级为该区块错误，不影响其他结果区块。
        try:
            payload = await AsyncHttpx.get_json(
                SERPAPI_SEARCH_URL,
                params=params,
                timeout=40,
                raise_on_failure=True,
            )
        except (httpx.HTTPError, AllURIsFailedError, ValueError) as e:
            raise _serpapi_request_error(e) from e

        if not isinstance(payload, dict):
            raise SearchClientError("Google Lens 返回格式异常。")
        if error := payload.get("error"):
            raise _serpapi_payload_error(error)

        # 只取视觉匹配结果并压成统一展示模型，避免把 SerpAPI 其它区块混进链接清单。
        results = []
        visual_matches = payload.get("visual_matches", [])
        if not isinstance(visual_matches, list):
            return results
        for raw in visual_matches[: settings.google_lens_result_limit]:
            if not isinstance(raw, dict):
                continue
            url = _stringify(raw.get("link"))
            title = _stringify(raw.get("title"))
            if not url and not title:
                continue
            metadata = {}
            if settings.google_lens_hide_thumbnail:
                metadata["预览"] = "已按配置隐藏"
            results.append(
                SearchResultItem(
                    title=title or url or "Google Lens 结果",
                    url=url,
                    links=[url] if url else [],
                    source=_stringify(raw.get("source")),
                    thumbnail_url=_stringify(raw.get("thumbnail")),
                    thumbnail_visible=not settings.google_lens_hide_thumbnail,
                    hidden=False,
                    metadata=metadata,
                )
            )
        return results


@dataclass(slots=True)
class _CachedAniListInfo:
    expires_at: float
    value: AniListInfo


class AniListClient:
    """带 TTL 缓存的 AniList GraphQL 客户端。"""

    def __init__(self):
        self._cache: dict[int, _CachedAniListInfo] = {}

    async def get_info(
        self,
        anilist_id: int,
        settings: PicSearchSettings,
    ) -> AniListInfo | None:
        """读取番剧信息并缓存，降低 TraceMoe 高频使用时的 GraphQL 压力。"""

        # AniList 信息变化很低频，TTL 缓存能显著减少 TraceMoe 高频使用时的
        # GraphQL 请求。
        now = time.monotonic()
        if cached := self._cache.get(anilist_id):
            if cached.expires_at > now:
                return cached.value
            self._cache.pop(anilist_id, None)

        # AniList 只是补全来源；失败时保留 TraceMoe 原始结果，不阻断搜番主流程。
        try:
            response = await AsyncHttpx.post(
                ANILIST_GRAPHQL_URL,
                json={"query": ANILIST_QUERY, "variables": {"id": anilist_id}},
                timeout=15,
            )
            payload = response.json()
        except (httpx.HTTPError, AllURIsFailedError, ValueError) as e:
            logger.warning(f"AniList 补全失败：{anilist_id} {e}", "搜番")
            return None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or payload.get("errors"):
            return None
        media = data.get("Media")
        if not isinstance(media, dict):
            return None

        titles = media.get("title") if isinstance(media.get("title"), dict) else {}
        title = (
            _stringify(titles.get("native"))
            or _stringify(titles.get("romaji"))
            or _stringify(titles.get("english"))
        )
        cover = (
            media.get("coverImage") if isinstance(media.get("coverImage"), dict) else {}
        )
        info = AniListInfo(
            title=title,
            is_adult=bool(media.get("isAdult")),
            cover_image=_stringify(cover.get("large")),
            site_url=_stringify(media.get("siteUrl")),
            raw=media,
        )
        ttl_seconds = max(settings.anilist_cache_ttl_hours, 1) * 3600
        self._cache[anilist_id] = _CachedAniListInfo(now + ttl_seconds, info)
        return info


_SHARED_ANILIST_CLIENT = AniListClient()


class TraceMoeClient:
    """TraceMoe 搜番 API 客户端。"""

    def __init__(self, anilist_client: AniListClient | None = None):
        self.anilist_client = anilist_client or _SHARED_ANILIST_CLIENT

    async def search(
        self,
        image: ImageInput,
        settings: PicSearchSettings,
    ) -> list[SearchResultItem]:
        """上传图片到 TraceMoe 并补全成人标记。"""

        # TraceMoe 使用图片 bytes 上传，避免远端二次拉取平台图片 URL 的不确定性。
        files = {"file": (image.filename, image.content, image.mimetype)}
        try:
            response = await AsyncHttpx.post(
                TRACEMOE_SEARCH_URL,
                params={"cutBorders": "true"},
                files=files,
                timeout=30,
            )
            payload = response.json()
        except (httpx.HTTPError, AllURIsFailedError, ValueError) as e:
            raise _request_error(
                e,
                status_template="TraceMoe 请求失败：HTTP {status}",
                fallback="TraceMoe 请求失败，请稍后再试。",
            ) from e

        if not isinstance(payload, dict):
            raise SearchClientError("TraceMoe 返回格式异常。")
        if payload.get("error"):
            raise SearchClientError(f"TraceMoe 搜索失败：{payload['error']}")

        # 逐条补 AniList 信息，成人内容默认只保留文本，避免在群聊里发送高风险预览媒体。
        items: list[SearchResultItem] = []
        for raw in payload.get("result", [])[: settings.tracemoe_result_limit]:
            if not isinstance(raw, dict):
                continue
            anilist_payload = raw.get("anilist")
            info: AniListInfo | None = None
            anilist_id = _to_int(anilist_payload)
            # trace.moe 支持 anilistInfo 时会把 anilist 从数字变成对象；这里兼容该
            # 形态，避免上游新增 isAdult 字段后仍重复请求 AniList。
            if isinstance(anilist_payload, dict):
                anilist_id = _to_int(anilist_payload.get("id"))
                titles = (
                    anilist_payload.get("title")
                    if isinstance(anilist_payload.get("title"), dict)
                    else {}
                )
                title = (
                    _stringify(titles.get("native"))
                    or _stringify(titles.get("romaji"))
                    or _stringify(titles.get("english"))
                )
                info = AniListInfo(
                    title=title,
                    is_adult=bool(anilist_payload.get("isAdult")),
                    site_url=(
                        f"https://anilist.co/anime/{anilist_id}" if anilist_id else ""
                    ),
                    raw=anilist_payload,
                )
            elif anilist_id:
                info = await self.anilist_client.get_info(anilist_id, settings)
            title = (info.title if info else "") or _stringify(raw.get("filename"))
            episode = _stringify(raw.get("episode")) or "?"
            start = _to_float(raw.get("from"))
            end = _to_float(raw.get("to"))
            at = _to_float(raw.get("at"))
            similarity = (_to_float(raw.get("similarity")) or 0) * 100
            is_adult = bool(info and info.is_adult)
            # AniList 补全失败时不能证明内容安全，按未知高风险处理，避免漏发成人预览。
            adult_state_unknown = bool(anilist_id and info is None)
            hide_media = settings.tracemoe_hide_adult_media and (
                is_adult or adult_state_unknown
            )
            thumbnail_visible = not hide_media
            if is_adult:
                risk_notice = "成人内容，已隐藏媒体" if hide_media else "成人内容"
            elif adult_state_unknown:
                risk_notice = (
                    "成人状态未知，已隐藏媒体" if hide_media else "成人状态未知"
                )
            else:
                risk_notice = ""
            preview_video = "" if hide_media else _stringify(raw.get("video"))
            links = [info.site_url] if info and info.site_url else []
            if preview_video:
                links.append(preview_video)
            items.append(
                SearchResultItem(
                    title=title or "TraceMoe 结果",
                    url=info.site_url if info else "",
                    links=links,
                    source="TraceMoe",
                    similarity=round(similarity, 2),
                    thumbnail_url=_stringify(raw.get("image")),
                    thumbnail_visible=thumbnail_visible,
                    hidden=hide_media,
                    metadata={
                        "集数": episode,
                        "时间": _format_time_range(start, end, at),
                        "风险提示": risk_notice,
                    },
                )
            )
        return items


class AnimeTraceClient:
    """AnimeTrace 识角色 API 客户端。"""

    async def _search_once(
        self,
        image: ImageInput,
        model: str,
    ) -> dict[str, Any]:
        """使用指定模型请求一次 AnimeTrace。"""

        data = {
            "is_multi": 1,
            "ai_detect": 1,
            "model": model,
            "base64": base64.b64encode(image.content).decode("ascii"),
        }
        try:
            response = await AsyncHttpx.post(
                ANIMETRACE_SEARCH_URL,
                headers=ANIMETRACE_HEADERS,
                data=data,
                timeout=10,
            )
            payload = response.json()
        except (httpx.HTTPError, AllURIsFailedError, ValueError) as e:
            status = _http_status_from_exception(e)
            if status is not None:
                raise SearchClientError(
                    _animetrace_message(status),
                    retryable=status >= 500,
                ) from e
            raise SearchClientError(
                "识角色 API 当前不可用，请稍后再试。", retryable=True
            ) from e

        if not isinstance(payload, dict):
            raise SearchClientError("AnimeTrace 返回格式异常。")
        if not _is_animetrace_success(payload):
            code = _to_int(payload.get("code"))
            raise SearchClientError(
                _animetrace_message(code, payload.get("msg") or payload.get("message")),
                retryable=_is_animetrace_retryable_code(code),
            )
        return payload

    async def search(
        self,
        image: ImageInput,
        settings: PicSearchSettings,
    ) -> list[SearchResultItem]:
        """上传图片到 AnimeTrace 并返回文本化候选。"""

        # 最高质量 beta 模型准确率更好，但服务波动时会失败；回退 pre_stable
        # 可以保留可用性，避免一次模型错误直接让命令失败。
        try:
            payload = await self._search_once(image, ANIMETRACE_PRIMARY_MODEL)
            used_model = ANIMETRACE_PRIMARY_MODEL
        except SearchClientError as primary_error:
            if not primary_error.retryable:
                raise
            logger.debug(
                f"AnimeTrace 主模型失败，尝试回退模型：{primary_error.user_message}",
                "识角色",
            )
            payload = await self._search_once(image, ANIMETRACE_FALLBACK_MODEL)
            used_model = ANIMETRACE_FALLBACK_MODEL

        # AnimeTrace 不返回预览图 URL，只返回识别框；这里把每个框裁成 data URI，
        # 让多个角色结果可对照，同时不引入临时图床。
        items: list[SearchResultItem] = []
        ai_notice = "可能是 AI 绘图" if payload.get("ai") else ""
        data_items = payload.get("data", [])
        if not isinstance(data_items, list):
            return items
        for raw in data_items[: settings.animetrace_result_limit]:
            if not isinstance(raw, dict):
                continue
            characters = raw.get("character")
            if not isinstance(characters, list) or not characters:
                continue
            first = characters[0] if isinstance(characters[0], dict) else {}
            name = _stringify(first.get("character"))
            work = _stringify(first.get("work"))
            candidates = [
                SearchCandidate(
                    title=_stringify(item.get("character")),
                    subtitle=_stringify(item.get("work")),
                )
                for item in characters[: settings.animetrace_result_limit]
                if isinstance(item, dict)
                and (_stringify(item.get("character")) or _stringify(item.get("work")))
            ]
            metadata = {}
            if ai_notice:
                metadata["AI 检测"] = ai_notice
            items.append(
                SearchResultItem(
                    title=name or "未知角色",
                    source=used_model,
                    author=work,
                    thumbnail_url=_crop_animetrace_preview(image, raw.get("box")),
                    thumbnail_visible=bool(settings.animetrace_show_media),
                    hidden=False,
                    metadata=metadata,
                    candidates=candidates,
                )
            )
        return items
