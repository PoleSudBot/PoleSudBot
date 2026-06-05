from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import sys
import types

import httpx
from jinja2 import Environment, FileSystemLoader
import nonebot
from PIL import Image
import pytest

nonebot.init()


class _FakeLogger:
    @staticmethod
    def debug(*_args, **_kwargs):
        return None

    @staticmethod
    def info(*_args, **_kwargs):
        return None

    @staticmethod
    def warning(*_args, **_kwargs):
        return None

    @staticmethod
    def error(*_args, **_kwargs):
        return None


class _FakeAsyncHttpx:
    @staticmethod
    @asynccontextmanager
    async def temporary_client(**_kwargs):
        raise AssertionError("AsyncHttpx.temporary_client should be monkeypatched")

    @staticmethod
    async def post(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.post should be monkeypatched")

    @staticmethod
    async def get_json(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.get_json should be monkeypatched")

    @staticmethod
    async def get_content(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.get_content should be monkeypatched")

    @staticmethod
    async def post_json(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.post_json should be monkeypatched")


async def _fake_render_template(*_args, **_kwargs):
    return b"rendered"


services_module = types.ModuleType("zhenxun.services")
services_module.__path__ = [str(Path(__file__).parents[1] / "zhenxun" / "services")]
log_module = types.ModuleType("zhenxun.services.log")
log_module.logger = _FakeLogger
http_utils_module = types.ModuleType("zhenxun.utils.http_utils")
http_utils_module.AsyncHttpx = _FakeAsyncHttpx
ui_module = types.ModuleType("zhenxun.ui")
ui_module.__path__ = [str(Path(__file__).parents[1] / "zhenxun" / "ui")]
ui_module.render_template = _fake_render_template
sys.modules.setdefault("zhenxun.services", services_module)
sys.modules["zhenxun.services.log"] = log_module
sys.modules["zhenxun.utils.http_utils"] = http_utils_module
sys.modules["zhenxun.ui"] = ui_module

import zhenxun.plugins.pictrace as plugin_module
from zhenxun.plugins.pictrace import clients as clients_module
from zhenxun.plugins.pictrace import config as config_module
from zhenxun.plugins.pictrace import image_input as image_input_module
from zhenxun.plugins.pictrace import renderer as renderer_module
from zhenxun.plugins.pictrace import sender as sender_module
from zhenxun.plugins.pictrace.clients import (
    AniListClient,
    AnimeTraceClient,
    SauceNAOClient,
    SearchClientError,
    SerpApiGoogleLensClient,
    TraceMoeClient,
)
from zhenxun.plugins.pictrace.config import PicSearchSettings
from zhenxun.plugins.pictrace.models import (
    ImageInput,
    SauceNAOResult,
    SearchCandidate,
    SearchPresentation,
    SearchResultItem,
    SearchSection,
)
from zhenxun.plugins.pictrace.sender import should_fallback_plain
from zhenxun.plugins.pictrace.workflow import (
    PicSearchClients,
    search_anime,
    search_character,
    search_picture,
)
from zhenxun.utils.exception import RenderingError


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _settings(**kwargs) -> PicSearchSettings:
    data = {
        "saucenao_api_key": "sauce-key",
        "serp_api_key": "serp-key",
        "hide_thumbnail": True,
    }
    data.update(kwargs)
    return PicSearchSettings(**data)


def _image(url: str | None = "https://gchat.qpic.cn/test.jpg") -> ImageInput:
    return ImageInput(content=b"image-bytes", url=url)


def _jpeg_image(width: int = 10, height: int = 10) -> ImageInput:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (80, 120, 180)).save(buffer, format="JPEG")
    return ImageInput(content=buffer.getvalue(), url="https://gchat.qpic.cn/test.jpg")


def test_image_size_limit_rejects_raw_bytes_and_local_file(tmp_path):
    big_content = b"x" * (1024 * 1024 + 1)

    with pytest.raises(ValueError, match="图片大小超过 1MB"):
        image_input_module._ensure_content_size(big_content, 1)

    path = tmp_path / "big.jpg"
    path.write_bytes(big_content)

    with pytest.raises(ValueError, match="图片大小超过 1MB"):
        image_input_module._read_limited_file(path, 1)


@pytest.mark.asyncio
async def test_url_image_download_rejects_content_length(monkeypatch):
    class FakeStream:
        def __init__(self):
            self.headers = {"content-length": str(1024 * 1024 + 1)}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            raise AssertionError("content-length should reject before body download")

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return FakeStream()

    @asynccontextmanager
    async def fake_temporary_client(**_kwargs):
        yield FakeClient()

    monkeypatch.setattr(
        image_input_module.AsyncHttpx,
        "temporary_client",
        fake_temporary_client,
    )

    with pytest.raises(ValueError, match="图片大小超过 1MB"):
        await image_input_module._download_limited_image("https://image.example", 1)


@pytest.mark.asyncio
async def test_url_image_download_rejects_stream_over_limit(monkeypatch):
    class FakeStream:
        def __init__(self):
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"x" * (700 * 1024)
            yield b"x" * (700 * 1024)

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return FakeStream()

    @asynccontextmanager
    async def fake_temporary_client(**_kwargs):
        yield FakeClient()

    monkeypatch.setattr(
        image_input_module.AsyncHttpx,
        "temporary_client",
        fake_temporary_client,
    )

    with pytest.raises(ValueError, match="图片大小超过 1MB"):
        await image_input_module._download_limited_image("https://image.example", 1)


def test_parse_bool_handles_string_values(monkeypatch):
    def fake_get_config(_module, key, default):
        values = {
            "GOOGLE_LENS_SAFE_SEARCH": "false",
            "GOOGLE_LENS_HIDE_THUMBNAIL": "0",
            "HIDE_THUMBNAIL": "off",
            "TRACEMOE_HIDE_ADULT_MEDIA": "no",
            "ANIMETRACE_SHOW_MEDIA": "yes",
            "FORWARD_SEARCH_RESULT": "true",
        }
        return values.get(key, default)

    from zhenxun.configs.config import Config

    monkeypatch.setattr(Config, "get_config", fake_get_config)

    settings = config_module.load_settings()

    assert settings.google_lens_safe_search is False
    assert settings.google_lens_hide_thumbnail is False
    assert settings.hide_thumbnail is False
    assert settings.tracemoe_hide_adult_media is False
    assert settings.animetrace_show_media is True
    assert settings.forward_search_result is True


@pytest.mark.asyncio
async def test_saucenao_parser_handles_common_dynamic_fields(monkeypatch):
    async def fake_post(*_args, **_kwargs):
        return FakeResponse(
            {
                "header": {
                    "status": 0,
                    "short_remaining": "3",
                    "long_remaining": "99",
                    "query_image_display": "/image.jpg",
                },
                "results": [
                    {
                        "header": {
                            "similarity": "88.5",
                            "thumbnail": "https://thumb/pixiv.jpg",
                            "index_name": "Pixiv Images",
                            "hidden": 0,
                        },
                        "data": {
                            "pixiv_id": "123",
                            "title": "pixiv title",
                            "member_name": "artist",
                            "characters": "初音ミク",
                        },
                    },
                    {
                        "header": {
                            "similarity": "86",
                            "thumbnail": "https://thumb/twitter.jpg",
                            "index_name": "Twitter",
                            "hidden": 1,
                        },
                        "data": {
                            "tweet_id": "456",
                            "twitter_user_handle": "tw_user",
                            "source": "tweet title",
                            "ext_urls": [
                                "https://danbooru.donmai.us/post/show/456",
                                "https://danbooru.donmai.us/posts/456",
                                "https://www.pixiv.net/artworks/789",
                                "https://gelbooru.com/index.php?page=post&s=view&id=999",
                            ],
                        },
                    },
                    {
                        "header": {
                            "similarity": "84",
                            "thumbnail": "https://thumb/ext.jpg",
                            "index_name": "Danbooru",
                            "hidden": 0,
                        },
                        "data": {
                            "creator": ["foo", "bar"],
                            "ext_urls": ["https://example.com/source"],
                        },
                    },
                ],
            }
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await SauceNAOClient().search(_image(), _settings())

    assert result.quota.short_remaining == 3
    assert result.quota.long_remaining == 99
    assert result.search_url.endswith("https://saucenao.com/image.jpg")
    assert result.items[0].url == "https://www.pixiv.net/artworks/123"
    assert result.items[0].links == ["https://www.pixiv.net/artworks/123"]
    assert result.items[0].author == "artist"
    assert result.items[0].metadata["Pixiv ID"] == "123"
    assert result.items[0].metadata["角色"] == "初音ミク"
    assert result.items[0].source == "Pixiv"
    assert result.items[1].url == "https://x.com/tw_user/status/456"
    assert result.items[1].links == [
        "https://x.com/tw_user/status/456",
        "https://www.pixiv.net/artworks/789",
        "https://danbooru.donmai.us/posts/456",
        "https://gelbooru.com/index.php?page=post&s=view&id=999",
    ]
    assert result.items[1].source == "X"
    assert result.items[1].hidden is True
    assert result.items[2].author == "foo, bar"
    assert result.items[2].url == "https://example.com/source"


@pytest.mark.asyncio
async def test_saucenao_api_error_is_user_facing(monkeypatch):
    async def fake_post(*_args, **_kwargs):
        return FakeResponse({"header": {"status": -1, "message": "bad key"}})

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    with pytest.raises(SearchClientError, match="bad key"):
        await SauceNAOClient().search(_image(), _settings())


class FakeSauceClient:
    def __init__(self, similarity: float | None):
        self.similarity = similarity

    async def search(self, *_args, **_kwargs):
        if self.similarity is None:
            return SauceNAOResult(items=[])
        return SauceNAOResult(
            items=[
                SearchResultItem(
                    title="sauce",
                    url="https://sauce.example",
                    similarity=self.similarity,
                )
            ]
        )


class FakeLensClient:
    def __init__(self):
        self.calls = 0

    async def search(self, *_args, **_kwargs):
        self.calls += 1
        return [SearchResultItem(title="lens", url="https://lens.example")]


class FakeSauceLinkClient:
    async def search(self, *_args, **_kwargs):
        return SauceNAOResult(
            items=[
                SearchResultItem(
                    title="vocaloid",
                    similarity=88,
                    links=[
                        "https://x.com/user/status/1",
                        "https://www.pixiv.net/artworks/1",
                        "https://danbooru.donmai.us/posts/1",
                        "https://gelbooru.com/index.php?page=post&s=view&id=1",
                    ],
                )
            ],
            search_url="https://saucenao.com/search.php?url=test",
        )


@pytest.mark.asyncio
async def test_picture_workflow_uses_only_saucenao_when_confident():
    lens = FakeLensClient()
    presentation = await search_picture(
        _image(),
        settings=_settings(),
        clients=PicSearchClients(
            saucenao=FakeSauceClient(80),
            google_lens=lens,
        ),
    )

    assert lens.calls == 0
    assert [section.title for section in presentation.sections] == ["SauceNAO"]
    assert presentation.sections[0].items[0].metadata["预览"] == "已按配置隐藏"


@pytest.mark.asyncio
async def test_picture_workflow_uses_saucenao_and_lens_when_mid_similarity():
    lens = FakeLensClient()
    presentation = await search_picture(
        _image(),
        settings=_settings(),
        clients=PicSearchClients(
            saucenao=FakeSauceClient(65),
            google_lens=lens,
        ),
    )

    assert lens.calls == 1
    assert [section.title for section in presentation.sections] == [
        "SauceNAO",
        "Google Lens",
    ]


@pytest.mark.asyncio
async def test_picture_workflow_hides_low_similarity_saucenao_result():
    lens = FakeLensClient()
    presentation = await search_picture(
        _image(),
        settings=_settings(),
        clients=PicSearchClients(
            saucenao=FakeSauceClient(50),
            google_lens=lens,
        ),
    )

    assert lens.calls == 1
    assert [section.title for section in presentation.sections] == ["Google Lens"]
    assert "低于 60" in presentation.notices[0]


@pytest.mark.asyncio
async def test_picture_workflow_puts_booru_links_after_blank_line():
    presentation = await search_picture(
        _image(),
        settings=_settings(),
        clients=PicSearchClients(saucenao=FakeSauceLinkClient()),
    )

    assert presentation.link_lines == [
        "[1] SauceNAO - vocaloid\n"
        "https://x.com/user/status/1\n"
        "https://www.pixiv.net/artworks/1\n"
        "\n"
        "https://danbooru.donmai.us/posts/1\n"
        "https://gelbooru.com/index.php?page=post&s=view&id=1",
        "SauceNAO 搜索页面\nhttps://saucenao.com/search.php?url=test",
    ]


@pytest.mark.asyncio
async def test_serpapi_requires_public_image_url():
    with pytest.raises(SearchClientError, match="公网图片 URL"):
        await SerpApiGoogleLensClient().search(None, _settings())


@pytest.mark.asyncio
async def test_serpapi_enables_safe_search_and_shows_lens_thumbnails(monkeypatch):
    async def fake_get_json(_url, **kwargs):
        assert kwargs["params"]["safe"] == "active"
        return {
            "visual_matches": [
                {
                    "title": "lens result",
                    "link": "https://lens.example",
                    "source": "example",
                    "thumbnail": "https://lens/thumb.jpg",
                }
            ]
        }

    monkeypatch.setattr(clients_module.AsyncHttpx, "get_json", fake_get_json)

    result = await SerpApiGoogleLensClient().search(
        _image().url, _settings(google_lens_hide_thumbnail=False)
    )

    assert result[0].thumbnail_visible is True
    assert result[0].hidden is False


@pytest.mark.asyncio
async def test_serpapi_manual_thumbnail_hide_does_not_mark_nsfw(monkeypatch):
    async def fake_get_json(_url, **_kwargs):
        return {
            "visual_matches": [
                {
                    "title": "lens result",
                    "link": "https://lens.example",
                    "thumbnail": "https://lens/thumb.jpg",
                }
            ]
        }

    monkeypatch.setattr(clients_module.AsyncHttpx, "get_json", fake_get_json)

    result = await SerpApiGoogleLensClient().search(
        _image().url, _settings(google_lens_hide_thumbnail=True)
    )

    assert result[0].thumbnail_visible is False
    assert result[0].hidden is False
    assert result[0].metadata["预览"] == "已按配置隐藏"


@pytest.mark.asyncio
async def test_serpapi_status_error_has_specific_message(monkeypatch):
    async def fake_get_json(_url, **_kwargs):
        request = httpx.Request("GET", clients_module.SERPAPI_SEARCH_URL)
        raise httpx.HTTPStatusError(
            "429",
            request=request,
            response=httpx.Response(429, request=request),
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "get_json", fake_get_json)

    with pytest.raises(SearchClientError, match="额度"):
        await SerpApiGoogleLensClient().search(_image().url, _settings())


@pytest.mark.asyncio
async def test_tracemoe_uses_anilist_cache_and_hides_adult_media(monkeypatch):
    calls = {"anilist": 0}

    async def fake_post(url, **_kwargs):
        if url == clients_module.TRACEMOE_SEARCH_URL:
            return FakeResponse(
                {
                    "error": "",
                    "frameCount": 1,
                    "result": [
                        {
                            "anilist": 1,
                            "filename": "anime-file",
                            "episode": 3,
                            "from": 12.34,
                            "to": 14.56,
                            "similarity": 0.876,
                            "image": "https://trace/image.jpg",
                            "video": "https://trace/video.mp4",
                        }
                    ],
                }
            )
        if url == clients_module.ANILIST_GRAPHQL_URL:
            calls["anilist"] += 1
            return FakeResponse(
                {
                    "data": {
                        "Media": {
                            "siteUrl": "https://anilist.co/anime/1",
                            "isAdult": True,
                            "title": {"native": "成人番"},
                            "coverImage": {"large": "https://cover"},
                        }
                    }
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    client = TraceMoeClient(AniListClient())
    first = await client.search(_image(), _settings())
    second = await client.search(_image(), _settings())

    assert calls["anilist"] == 1
    assert first[0].title == "成人番"
    assert first[0].hidden is True
    assert first[0].thumbnail_visible is False
    assert first[0].metadata["时间"] == "0:12 - 0:14"
    assert "预览视频" not in first[0].metadata
    assert second[0].title == "成人番"


@pytest.mark.asyncio
async def test_tracemoe_hides_media_when_anilist_lookup_fails(monkeypatch):
    async def fake_post(url, **_kwargs):
        if url == clients_module.TRACEMOE_SEARCH_URL:
            return FakeResponse(
                {
                    "error": "",
                    "result": [
                        {
                            "anilist": 2,
                            "filename": "unknown-adult-risk",
                            "episode": 1,
                            "from": 1,
                            "to": 2,
                            "similarity": 0.8,
                            "image": "https://trace/image.jpg",
                            "video": "https://trace/video.mp4",
                        }
                    ],
                }
            )
        if url == clients_module.ANILIST_GRAPHQL_URL:
            raise httpx.HTTPStatusError(
                "429",
                request=httpx.Request("POST", url),
                response=httpx.Response(429, request=httpx.Request("POST", url)),
            )
        raise AssertionError(url)

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await TraceMoeClient(AniListClient()).search(_image(), _settings())

    assert result[0].hidden is True
    assert result[0].thumbnail_visible is False
    assert "预览视频" not in result[0].metadata
    assert "成人状态未知" in result[0].metadata["风险提示"]


@pytest.mark.asyncio
async def test_tracemoe_allows_adult_media_when_config_disabled(monkeypatch):
    async def fake_post(url, **_kwargs):
        if url == clients_module.TRACEMOE_SEARCH_URL:
            return FakeResponse(
                {
                    "error": "",
                    "result": [
                        {
                            "anilist": 4,
                            "filename": "adult-visible",
                            "episode": 1,
                            "from": 1,
                            "to": 2,
                            "similarity": 0.8,
                            "image": "https://trace/image.jpg",
                            "video": "https://trace/video.mp4",
                        }
                    ],
                }
            )
        if url == clients_module.ANILIST_GRAPHQL_URL:
            return FakeResponse(
                {
                    "data": {
                        "Media": {
                            "siteUrl": "https://anilist.co/anime/4",
                            "isAdult": True,
                            "title": {"native": "成人番"},
                        }
                    }
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await TraceMoeClient(AniListClient()).search(
        _image(), _settings(tracemoe_hide_adult_media=False)
    )

    assert result[0].hidden is False
    assert result[0].thumbnail_visible is True
    assert result[0].links == [
        "https://anilist.co/anime/4",
        "https://trace/video.mp4",
    ]
    assert result[0].metadata["风险提示"] == "成人内容"
    assert "预览视频" not in result[0].metadata


@pytest.mark.asyncio
async def test_tracemoe_anilist_null_data_does_not_abort(monkeypatch):
    async def fake_post(url, **_kwargs):
        if url == clients_module.TRACEMOE_SEARCH_URL:
            return FakeResponse(
                {
                    "error": "",
                    "result": [
                        {
                            "anilist": 5,
                            "filename": "fallback-file",
                            "episode": 1,
                            "from": 1,
                            "to": 2,
                            "similarity": 0.8,
                        }
                    ],
                }
            )
        if url == clients_module.ANILIST_GRAPHQL_URL:
            return FakeResponse({"data": None, "errors": [{"message": "not found"}]})
        raise AssertionError(url)

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await TraceMoeClient(AniListClient()).search(_image(), _settings())

    assert result[0].title == "fallback-file"
    assert result[0].hidden is True
    assert "成人状态未知" in result[0].metadata["风险提示"]


@pytest.mark.asyncio
async def test_tracemoe_default_anilist_cache_is_shared(monkeypatch):
    calls = {"anilist": 0}
    clients_module._SHARED_ANILIST_CLIENT._cache.clear()

    async def fake_post(url, **_kwargs):
        if url == clients_module.TRACEMOE_SEARCH_URL:
            return FakeResponse(
                {
                    "error": "",
                    "result": [
                        {
                            "anilist": 99,
                            "filename": "shared-cache",
                            "episode": 1,
                            "from": 1,
                            "to": 2,
                            "similarity": 0.8,
                        }
                    ],
                }
            )
        if url == clients_module.ANILIST_GRAPHQL_URL:
            calls["anilist"] += 1
            return FakeResponse(
                {
                    "data": {
                        "Media": {
                            "siteUrl": "https://anilist.co/anime/99",
                            "isAdult": False,
                            "title": {"native": "共享缓存"},
                        }
                    }
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    first = await TraceMoeClient().search(_image(), _settings())
    second = await TraceMoeClient().search(_image(), _settings())

    assert calls["anilist"] == 1
    assert first[0].title == "共享缓存"
    assert second[0].title == "共享缓存"


@pytest.mark.asyncio
async def test_tracemoe_accepts_embedded_anilist_adult_field(monkeypatch):
    async def fake_post(url, **_kwargs):
        assert url == clients_module.TRACEMOE_SEARCH_URL
        return FakeResponse(
            {
                "error": "",
                "result": [
                    {
                        "anilist": {
                            "id": 3,
                            "title": {"native": "内置标题"},
                            "isAdult": True,
                        },
                        "filename": "fallback-file",
                        "episode": 1,
                        "from": 1,
                        "to": 2,
                        "similarity": 0.8,
                        "image": "https://trace/image.jpg",
                        "video": "https://trace/video.mp4",
                    }
                ],
            }
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await TraceMoeClient(AniListClient()).search(_image(), _settings())

    assert result[0].title == "内置标题"
    assert result[0].links == ["https://anilist.co/anime/3"]
    assert result[0].hidden is True


@pytest.mark.asyncio
async def test_tracemoe_formats_hour_long_time(monkeypatch):
    async def fake_post(url, **_kwargs):
        assert url == clients_module.TRACEMOE_SEARCH_URL
        return FakeResponse(
            {
                "error": "",
                "result": [
                    {
                        "anilist": None,
                        "filename": "long-scene",
                        "episode": 1,
                        "from": 3661.9,
                        "to": 3728.1,
                        "similarity": 0.8,
                    }
                ],
            }
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await TraceMoeClient(AniListClient()).search(_image(), _settings())

    assert result[0].metadata["时间"] == "1:01:01 - 1:02:08"


@pytest.mark.asyncio
async def test_animetrace_timeout_returns_user_facing_error(monkeypatch):
    calls = 0

    async def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    with pytest.raises(SearchClientError, match="识角色 API 当前不可用"):
        await AnimeTraceClient().search(_image(), _settings())
    assert calls == 2


@pytest.mark.asyncio
async def test_animetrace_accepts_success_code_and_outputs_text(monkeypatch):
    async def fake_post(_url, **kwargs):
        assert "files" not in kwargs
        assert base64.b64decode(kwargs["data"]["base64"]) == b"image-bytes"
        assert kwargs["data"]["is_multi"] == 1
        assert kwargs["data"]["ai_detect"] == 1
        assert kwargs["data"]["model"] == clients_module.ANIMETRACE_PRIMARY_MODEL
        return FakeResponse(
            {
                "code": 17720,
                "ai": True,
                "trace_id": "trace-1",
                "data": [
                    {
                        "box": [0, 0, 1, 1],
                        "character": [
                            {"character": "角色A", "work": "作品A"},
                            {"character": "角色B", "work": "作品B"},
                        ]
                    }
                ],
            }
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await AnimeTraceClient().search(_image(), _settings())

    assert result[0].title == "角色A"
    assert result[0].source == clients_module.ANIMETRACE_PRIMARY_MODEL
    assert result[0].author == "作品A"
    assert result[0].thumbnail_visible is True
    assert result[0].thumbnail_url.startswith("data:image/")
    assert result[0].candidates == [
        SearchCandidate(title="角色A", subtitle="作品A"),
        SearchCandidate(title="角色B", subtitle="作品B"),
    ]
    assert "候选数量" not in result[0].metadata
    assert "最可能作品" not in result[0].metadata
    assert "模型" not in result[0].metadata
    assert "Trace ID" not in result[0].metadata
    assert "Box ID" not in result[0].metadata
    assert result[0].metadata["AI 检测"] == "可能是 AI 绘图"


@pytest.mark.asyncio
async def test_animetrace_falls_back_to_stable_model(monkeypatch):
    models = []

    async def fake_post(_url, **kwargs):
        model = kwargs["data"]["model"]
        models.append(model)
        if model == clients_module.ANIMETRACE_PRIMARY_MODEL:
            return FakeResponse({"code": 17702, "msg": "busy"})
        return FakeResponse(
            {
                "code": 0,
                "data": [
                    {
                        "box": [0, 0, 1, 1],
                        "character": [{"character": "角色C", "work": "作品C"}],
                    }
                ],
            }
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await AnimeTraceClient().search(_image(), _settings())

    assert models == [
        clients_module.ANIMETRACE_PRIMARY_MODEL,
        clients_module.ANIMETRACE_FALLBACK_MODEL,
    ]
    assert result[0].title == "角色C"
    assert result[0].source == clients_module.ANIMETRACE_FALLBACK_MODEL


@pytest.mark.asyncio
async def test_animetrace_status_code_has_specific_message(monkeypatch):
    calls = 0

    async def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse({"code": 17708, "msg": "too many"})

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    with pytest.raises(SearchClientError, match="角色可能太多"):
        await AnimeTraceClient().search(_image(), _settings())
    assert calls == 1


@pytest.mark.asyncio
async def test_animetrace_usage_limit_does_not_fallback(monkeypatch):
    calls = 0

    async def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse({"code": 17728, "msg": "limit"})

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    with pytest.raises(SearchClientError, match="使用次数已达上限"):
        await AnimeTraceClient().search(_image(), _settings())
    assert calls == 1


@pytest.mark.asyncio
async def test_animetrace_crops_preview_from_box(monkeypatch):
    async def fake_post(_url, **_kwargs):
        return FakeResponse(
            {
                "code": 0,
                "data": [
                    {
                        "box": [0, 0, 0.5, 0.5],
                        "character": [{"character": "角色D", "work": "作品D"}],
                    }
                ],
            }
        )

    monkeypatch.setattr(clients_module.AsyncHttpx, "post", fake_post)

    result = await AnimeTraceClient().search(_jpeg_image(), _settings())
    image_payload = result[0].thumbnail_url.split(",", 1)[1]
    cropped = Image.open(BytesIO(base64.b64decode(image_payload)))

    assert cropped.size == (5, 5)


class FakeAnimeTraceClient:
    async def search(self, *_args, **_kwargs):
        return [
            SearchResultItem(
                title="角色A",
                candidates=[
                    SearchCandidate(title="角色A", subtitle="作品A"),
                    SearchCandidate(title="角色B", subtitle="作品B"),
                ],
            )
        ]


class FakeTraceMoeVideoClient:
    async def search(self, *_args, **_kwargs):
        return [
            SearchResultItem(
                title="番剧A",
                links=[
                    "https://anilist.co/anime/1",
                    "https://trace/video.mp4",
                ],
                metadata={"集数": "1", "时间": "0:01"},
            )
        ]


@pytest.mark.asyncio
async def test_anime_workflow_keeps_preview_video_only_in_link_text():
    presentation = await search_anime(
        _image(),
        settings=_settings(),
        clients=PicSearchClients(tracemoe=FakeTraceMoeVideoClient()),
    )

    item = presentation.sections[0].items[0]

    assert "预览视频" not in item.metadata
    assert "https://trace/video.mp4" in presentation.build_link_text()


@pytest.mark.asyncio
async def test_character_workflow_outputs_copyable_results_not_empty_links():
    presentation = await search_character(
        _image(),
        settings=_settings(),
        clients=PicSearchClients(animetrace=FakeAnimeTraceClient()),
    )

    assert presentation.link_lines == [
        "识角色实际结果\n[1] 角色A\n1. 角色A / 作品A\n2. 角色B / 作品B"
    ]
    assert "无可用链接" not in presentation.build_link_text()


@pytest.mark.asyncio
async def test_searching_notice_recall_uses_receipt_message_id():
    class FakeBot:
        def __init__(self):
            self.deleted = []

        async def delete_msg(self, *, message_id):
            self.deleted.append(message_id)

    receipt = types.SimpleNamespace(msg_ids=[{"message_id": "123"}])
    bot = FakeBot()

    await plugin_module._recall_searching_notice(bot, receipt)

    assert plugin_module._extract_receipt_message_id(receipt) == "123"
    assert bot.deleted == [123]


def test_forward_fallback_only_allows_small_message_count():
    assert should_fallback_plain(2) is True
    assert should_fallback_plain(3) is False


def test_result_template_renders_dataclass_items_without_method_collision():
    # 模板会引用主题共享字体 CSS，测试 loader 需要模拟真实渲染器的主题搜索路径。
    env = Environment(
        loader=FileSystemLoader(
            [
                str(renderer_module.TEMPLATE_PATH.parent),
                str(Path(__file__).parents[1] / "resources" / "themes" / "psb"),
            ]
        ),
        autoescape=True,
    )
    env.globals["asset"] = lambda path: path
    template = env.get_template(renderer_module.TEMPLATE_PATH.name)
    presentation = SearchPresentation(
        title="搜图结果",
        notices=["SauceNAO 未找到可用结果，已切换 Google Lens。"],
        sections=[
            SearchSection(
                title="Google Lens",
                subtitle="视觉相似网页结果",
                items=[
                    SearchResultItem(
                        title="lens title",
                        url="https://lens.example",
                        source="example",
                        metadata={"来源": "测试"},
                    )
                ],
            ),
            SearchSection(
                title="AnimeTrace",
                subtitle="动画角色识别",
                items=[
                    SearchResultItem(
                        title="候选角色",
                        source=clients_module.ANIMETRACE_PRIMARY_MODEL,
                        candidates=[
                            SearchCandidate(title="候选角色", subtitle="候选作品"),
                            SearchCandidate(title="候选角色B", subtitle="候选作品B"),
                        ],
                    )
                ],
            )
        ],
    )

    html = template.render(result=presentation)

    assert "Google Lens" in html
    assert "lens title" in html
    assert "来源：测试" in html
    assert "第一候选" not in html
    assert "候选角色" in html
    assert "候选角色B" in html
    assert 'candidate-index">1' in html
    assert 'candidate-index">2' in html
    assert "candidate-primary" in html
    assert "thumb-model" not in html
    assert "section-title-wrap" in html
    assert clients_module.ANIMETRACE_PRIMARY_MODEL in html
    assert "Results Generated by PoleSudBot" in html
    assert "https://lens.example" not in html
    assert "background: #edf1f5" not in html
    assert "border: 0" in html


@pytest.mark.asyncio
async def test_send_presentation_uses_forward_when_render_succeeds(monkeypatch):
    async def fake_render_search_result(_presentation):
        return b"rendered-image"

    sent = {}

    async def fake_send(self, **kwargs):
        sent["message"] = self
        sent["kwargs"] = kwargs

    monkeypatch.setattr(
        sender_module,
        "render_search_result",
        fake_render_search_result,
    )
    monkeypatch.setattr(sender_module.UniMessage, "send", fake_send)

    bot = types.SimpleNamespace(self_id="2956637281")
    event = types.SimpleNamespace()
    presentation = SearchPresentation(
        title="搜图结果",
        link_lines=["[1] Google Lens - lens title\nhttps://lens.example"],
    )

    await sender_module.send_presentation(bot, event, presentation, _settings())

    reference = next(
        segment
        for segment in sent["message"]
        if isinstance(segment, sender_module.Reference)
    )
    assert len(reference.children) == 2
    assert str(reference.children[0].content) == "[image]"
    assert "https://lens.example" in str(reference.children[1].content)
    assert sent["kwargs"]["target"] is event
    assert sent["kwargs"]["bot"] is bot
    assert sent["kwargs"]["fallback"] is sender_module.FallbackStrategy.forbid


@pytest.mark.asyncio
async def test_build_result_messages_skips_empty_link_text(monkeypatch):
    async def fake_render_search_result(_presentation):
        return b"rendered-image"

    monkeypatch.setattr(
        sender_module,
        "render_search_result",
        fake_render_search_result,
    )

    messages = await sender_module._build_result_messages(
        SearchPresentation(title="识角色结果")
    )

    assert len(messages) == 1
    assert str(messages[0]) == "[image]"


@pytest.mark.asyncio
async def test_render_failure_fallback_text_includes_error_details(monkeypatch):
    async def fake_render_search_result(_presentation):
        raise RenderingError("render failed")

    monkeypatch.setattr(
        sender_module,
        "render_search_result",
        fake_render_search_result,
    )

    messages = await sender_module._build_result_messages(
        SearchPresentation(
            title="搜番结果",
            notices=["全局提示"],
            sections=[
                SearchSection(
                    title="TraceMoe",
                    notices=["区块提示"],
                    error="TraceMoe 请求失败，请稍后再试。",
                )
            ],
        )
    )

    text = str(messages[0])

    assert "搜番结果" in text
    assert "全局提示" in text
    assert "TraceMoe：TraceMoe 请求失败，请稍后再试。" in text
    assert "区块提示" in text
