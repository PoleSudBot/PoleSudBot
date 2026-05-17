from __future__ import annotations

# ruff: noqa: I001

from contextlib import asynccontextmanager
from pathlib import Path
import sys
import types

from arclet.alconna import Alconna, Args, CommandMeta, MultiVar
import nonebot
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
    async def post_json(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.post_json should be monkeypatched")

    @staticmethod
    async def post(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.post should be monkeypatched")

    @staticmethod
    async def get_json(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.get_json should be monkeypatched")

    @staticmethod
    async def get_content(*_args, **_kwargs):
        raise AssertionError("AsyncHttpx.get_content should be monkeypatched")


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

from zhenxun.plugins.pictrace import renderer as renderer_module
from zhenxun.plugins.pictrace import sender as sender_module
from zhenxun.plugins.pictrace import tagger as tagger_module
from zhenxun.plugins.pictrace.config import PicSearchSettings
from zhenxun.plugins.pictrace.models import ImageInput, ImageTag, ImageTagResult
from zhenxun.plugins.pictrace.tagger import (
    ImageTagClient,
    ImageTagClientError,
    build_request_headers,
    build_wd14_payload,
    image_to_data_url,
    parse_wd14_payload,
)

from nonebot_plugin_alconna import Text
from nonebot_plugin_alconna.uniseg import Image as UniImage


def _settings(**kwargs) -> PicSearchSettings:
    data = {
        "tagger_api_url": "https://tagger.example/api",
        "tagger_model": "wd14-vit",
        "tagger_confidence_threshold": 0.5,
        "tagger_result_limit": 30,
        "tagger_hf_token": "",
    }
    data.update(kwargs)
    return PicSearchSettings(**data)


def _image() -> ImageInput:
    return ImageInput(content=b"image-bytes", url=None, mimetype="image/png")


def test_command_aliases_match_expected_names():
    alc = Alconna(
        "识别tag",
        Args["parts?", MultiVar(Text | UniImage)],
        meta=CommandMeta(strict=False, compact=True),
    )
    for alias in ("tag识别", "图片tag", "鉴赏图片"):
        alc.shortcut(alias, command="识别tag")

    assert alc.parse("识别tag").matched is True
    assert alc.parse("tag识别").matched is True
    assert alc.parse("图片tag").matched is True
    assert alc.parse("鉴赏图片").matched is True
    assert alc.parse("danbooru tag").matched is False
    assert alc.parse("wd14tag").matched is False


def _wd14_payload():
    return {
        "data": [
            {
                "label": "general",
                "confidences": [
                    {"label": "general", "confidence": 0.9},
                    {"label": "sensitive", "confidence": 0.2},
                ],
            },
            "1girl, solo, smile",
            {
                "label": "1girl",
                "confidences": [
                    {"label": "solo", "confidence": 0.8},
                    {"label": "1girl", "confidence": 0.95},
                    {"label": "smile", "confidence": 0.7},
                    {"label": "", "confidence": 0.99},
                    {"label": "bad", "confidence": "nan"},
                ],
            },
        ]
    }


def test_parse_wd14_payload_splits_ratings_and_tags():
    result = parse_wd14_payload(
        _wd14_payload(), threshold=0.5, model="wd14-vit", result_limit=2
    )

    assert [tag.name for tag in result.ratings] == ["general", "sensitive"]
    assert [tag.name for tag in result.tags] == ["1girl", "solo"]
    assert result.total_count == 3
    assert result.build_copy_text() == "tags: 1girl,solo"


def test_parse_wd14_payload_falls_back_to_text_tags():
    payload = {"data": [{"confidences": []}, "1girl, solo", {"confidences": []}]}

    result = parse_wd14_payload(
        payload, threshold=0.5, model="wd14-vit", result_limit=30
    )

    assert [tag.name for tag in result.tags] == ["1girl", "solo"]
    assert all(tag.score == 0.5 for tag in result.tags)


def test_parse_wd14_payload_rejects_unknown_shape():
    with pytest.raises(ImageTagClientError, match="返回格式异常"):
        parse_wd14_payload([], threshold=0.5, model="wd14-vit", result_limit=30)


def test_build_wd14_payload_uses_data_url_and_model():
    settings = _settings(
        tagger_model="wd14-convnext",
        tagger_confidence_threshold=0.42,
    )

    payload = build_wd14_payload(_image(), settings)

    assert payload["fn_index"] == 0
    assert payload["data"][0].startswith("data:image/png;base64,")
    assert payload["data"][1] == "wd14-convnext"
    assert payload["data"][2] == 0.42


def test_build_request_headers_uses_optional_hf_token():
    assert build_request_headers(_settings()) == {}
    assert build_request_headers(_settings(tagger_hf_token="hf_x")) == {
        "Authorization": "Bearer hf_x"
    }


def test_image_to_data_url_encodes_bytes():
    assert image_to_data_url(_image()).startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_client_posts_to_wd14_api_and_limits_results(monkeypatch):
    async def fake_post_json(url, **kwargs):
        assert url == "https://tagger.example/api"
        assert kwargs["headers"] == {"Authorization": "Bearer hf_token"}
        assert kwargs["json"]["data"][1] == "wd14-vit"
        assert kwargs["json"]["data"][2] == 0.5
        return _wd14_payload()

    monkeypatch.setattr(tagger_module.AsyncHttpx, "post_json", fake_post_json)

    result = await ImageTagClient().recognize(
        _image(),
        _settings(
            tagger_result_limit=2,
            tagger_hf_token="hf_token",
        ),
    )

    assert [tag.name for tag in result.tags] == ["1girl", "solo"]
    assert result.total_count == 3


@pytest.mark.asyncio
async def test_client_empty_result_is_user_facing(monkeypatch):
    async def fake_post_json(*_args, **_kwargs):
        return {"data": [{"confidences": []}, "", {"confidences": []}]}

    monkeypatch.setattr(tagger_module.AsyncHttpx, "post_json", fake_post_json)

    with pytest.raises(ImageTagClientError, match="未识别到高于当前阈值的 tag"):
        await ImageTagClient().recognize(_image(), _settings())


@pytest.mark.asyncio
async def test_renderer_builds_data_uri(monkeypatch):
    captured = {}

    async def fake_render_template(path, data, **kwargs):
        captured["path"] = path
        captured["data"] = data
        captured["kwargs"] = kwargs
        return b"rendered-image"

    monkeypatch.setattr(renderer_module, "render_template", fake_render_template)

    result = ImageTagResult(
        tags=[ImageTag("solo", 0.8)],
        ratings=[ImageTag("general", 0.9)],
        threshold=0.5,
        model="wd14-vit",
        total_count=1,
    )

    rendered = await renderer_module.render_tag_result(_image(), result)

    assert rendered == b"rendered-image"
    assert captured["path"] == renderer_module.TAG_RESULT_TEMPLATE_PATH
    assert captured["data"]["image_data_uri"].startswith("data:image/png;base64,")
    assert captured["kwargs"]["clip_selector"] == ".page"


@pytest.mark.asyncio
async def test_sender_fallbacks_to_text_when_rendering_fails(monkeypatch):
    sent = []

    async def fake_render_tag_result(*_args, **_kwargs):
        from zhenxun.utils.exception import RenderingError

        raise RenderingError("boom")

    class FakeUniMessage:
        def __init__(self, content):
            self.content = content

        async def send(self, *args, **kwargs):
            sent.append((self.content, args, kwargs))

    monkeypatch.setattr(sender_module, "render_tag_result", fake_render_tag_result)
    monkeypatch.setattr(
        sender_module.MessageUtils,
        "build_message",
        lambda content: FakeUniMessage(content),
    )

    result = ImageTagResult(
        tags=[ImageTag("solo", 0.8)],
        ratings=[],
        threshold=0.5,
        model="wd14-vit",
        total_count=1,
    )

    await sender_module.send_tag_result(
        bot=types.SimpleNamespace(self_id="10000"),
        event=object(),
        image=_image(),
        result=result,
        settings=_settings(forward_tagger_result=True),
    )

    assert len(sent) == 1
    assert "图片 tag 识别结果" in sent[0][0]
    assert "solo 80.0%" in sent[0][0]


@pytest.mark.asyncio
async def test_sender_uses_forward_when_render_succeeds(monkeypatch):
    async def fake_render_tag_result(*_args, **_kwargs):
        return b"rendered-image"

    sent = {}

    async def fake_send(self, **kwargs):
        sent["message"] = self
        sent["kwargs"] = kwargs

    monkeypatch.setattr(sender_module, "render_tag_result", fake_render_tag_result)
    monkeypatch.setattr(sender_module.UniMessage, "send", fake_send)

    result = ImageTagResult(
        tags=[ImageTag("solo", 0.8)],
        ratings=[],
        threshold=0.5,
        model="wd14-vit",
        total_count=1,
    )

    bot = types.SimpleNamespace(self_id="2956637281")
    event = types.SimpleNamespace()

    await sender_module.send_tag_result(
        bot=bot,
        event=event,
        image=_image(),
        result=result,
        settings=_settings(forward_tagger_result=True),
    )

    reference = next(
        segment
        for segment in sent["message"]
        if isinstance(segment, sender_module.Reference)
    )
    assert len(reference.children) == 2
    assert str(reference.children[0].content) == "[image]"
    assert "tags: solo" in str(reference.children[1].content)
    assert sent["kwargs"]["target"] is event
    assert sent["kwargs"]["bot"] is bot
    assert sent["kwargs"]["fallback"] is sender_module.FallbackStrategy.forbid
