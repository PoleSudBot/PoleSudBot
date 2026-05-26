from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import ClassVar

import pytest


class _Text:
    def __init__(self, text: str) -> None:
        # 仅模拟测试中需要的文本段行为。
        self.text = text

    def __str__(self) -> str:
        return self.text


class _Image:
    def __init__(self, *, url: str | None = None, path: str | None = None) -> None:
        # 保存 url/path 以模拟 alconna 图片段序列化与反序列化。
        self.url = url
        self.path = path

    def __str__(self) -> str:
        return "[图片]"


class _Reply:
    def __init__(self, msg=None) -> None:
        # 模拟引用段，保存欢迎语时应只用它定位原消息，不能持久化为发送内容。
        self.msg = msg

    def __str__(self) -> str:
        return "[引用消息]"


class _FakeUniMessage(list):
    def __init__(self, items=None) -> None:
        super().__init__(items or [])

    def dump(self, _media_save_dir=None):
        # 将 fake 消息段转换成 data_source 持久化使用的列表结构。
        result = []
        for item in self:
            if isinstance(item, _Text):
                result.append({"type": "text", "text": item.text})
            elif isinstance(item, _Image):
                data = {"type": "image"}
                if item.url:
                    data["url"] = item.url
                if item.path:
                    data["path"] = item.path
                result.append(data)
            elif isinstance(item, _Reply):
                result.append({"type": "reply", "id": "1000"})
        return result

    def load(self, data):
        # 从持久化结构恢复 fake 消息段，便于断言文本与图片路径。
        items = []
        for item in data:
            if item["type"] == "text":
                items.append(_Text(item["text"]))
            elif item["type"] == "image":
                items.append(_Image(path=item.get("path"), url=item.get("url")))
        return _FakeUniMessage(items)

    def __str__(self) -> str:
        return "".join(str(item) for item in self)


class _FakeLogger:
    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class _FakeAsyncHttpx:
    downloads: ClassVar[list[tuple[str, Path]]] = []

    @classmethod
    async def download_file(cls, url: str, path: str | Path):
        # 写入文件模拟下载完成，便于验证删除时会清理图片。
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
        cls.downloads.append((url, path))
        return True


class _FakePlatformUtils:
    sends: ClassVar[list[tuple[object, str | None, str | None, object]]] = []

    @classmethod
    async def send_message(cls, bot, user_id, group_id, message):
        cls.sends.append((bot, user_id, group_id, message))
        return object()


class _FakeAutoGreetingManager:
    friend_calls: ClassVar[list[tuple[object, str]]] = []
    group_calls: ClassVar[list[tuple[object, str]]] = []

    @classmethod
    async def send_friend_greeting(cls, bot, user_id: str):
        # 记录好友欢迎调用，验证手动同意请求后接入了新服务。
        cls.friend_calls.append((bot, user_id))
        return True

    @classmethod
    async def send_group_greeting(cls, bot, group_id: str):
        # 记录入群介绍调用，验证通过强制拉群保护后才发送。
        cls.group_calls.append((bot, group_id))
        return True


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


async def _fake_download_image(url: str, path: Path):
    # 写入文件模拟下载完成，便于验证删除时会清理图片。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    _FakeAsyncHttpx.downloads.append((url, path))
    return True


async def _fake_send_message(bot, user_id, group_id, message):
    # 记录发送参数，验证好友/群场景目标不同。
    _FakePlatformUtils.sends.append((bot, user_id, group_id, message))
    return object()


def _module(name: str, **attrs):
    # 创建临时模块对象，供目标文件导入其外部依赖。
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _load_auto_greeting_module(tmp_path: Path):
    # 直接加载服务模块，避免依赖真实 NoneBot 与项目全局 DATA_PATH。
    module_path = (
        Path(__file__).resolve().parents[1]
        / "zhenxun"
        / "utils"
        / "manager"
        / "auto_greeting_manager.py"
    )
    spec = importlib.util.spec_from_file_location(
        "auto_greeting_under_test", module_path
    )
    if not spec or not spec.loader:
        raise RuntimeError("无法加载 auto_greeting 测试模块")
    module = importlib.util.module_from_spec(spec)
    stubs = {
        "nonebot.adapters": _module("nonebot.adapters", Bot=type("Bot", (), {})),
        "nonebot_plugin_alconna": _module(
            "nonebot_plugin_alconna",
            UniMessage=_FakeUniMessage,
            UniMsg=_FakeUniMessage,
        ),
        "zhenxun.configs.path_config": _module(
            "zhenxun.configs.path_config", DATA_PATH=tmp_path
        ),
        "zhenxun.services.log": _module(
            "zhenxun.services.log", logger=_FakeLogger()
        ),
        "zhenxun.utils.http_utils": _module(
            "zhenxun.utils.http_utils", AsyncHttpx=_FakeAsyncHttpx
        ),
        "zhenxun.utils.platform": _module(
            "zhenxun.utils.platform", PlatformUtils=_FakePlatformUtils
        ),
    }
    previous_modules = {name: sys.modules.get(name) for name in stubs}
    try:
        sys.modules.update(stubs)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    _FakeAsyncHttpx.downloads.clear()
    _FakePlatformUtils.sends.clear()
    module._download_image = _fake_download_image
    module._send_message = _fake_send_message
    module._get_logger = _FakeLogger
    module._load_message = _FakeUniMessage().load
    return module


@pytest.mark.asyncio
async def test_save_read_and_delete_friend_greeting_with_image(tmp_path: Path):
    auto_greeting = _load_auto_greeting_module(tmp_path)
    message = _FakeUniMessage([_Text("你好"), _Image(url="https://example.com/a.png")])

    saved = await auto_greeting.AutoGreetingManager.save_message(
        auto_greeting.GreetingScene.FRIEND, message
    )

    assert str(saved) == "你好[图片]"
    assert _FakeAsyncHttpx.downloads[0][0] == "https://example.com/a.png"
    image_path = _FakeAsyncHttpx.downloads[0][1]
    assert image_path.exists()
    loaded = auto_greeting.AutoGreetingManager.get_message(
        auto_greeting.GreetingScene.FRIEND
    )
    assert str(loaded) == "你好[图片]"

    deleted = auto_greeting.AutoGreetingManager.delete_message(
        auto_greeting.GreetingScene.FRIEND
    )

    assert str(deleted) == "你好[图片]"
    assert not image_path.exists()
    assert (
        auto_greeting.AutoGreetingManager.get_message(
            auto_greeting.GreetingScene.FRIEND
        )
        is None
    )


@pytest.mark.asyncio
async def test_save_group_greeting_replaces_old_image_after_new_config_is_written(
    tmp_path: Path,
):
    auto_greeting = _load_auto_greeting_module(tmp_path)
    old_message = _FakeUniMessage([_Text("旧"), _Image(url="https://example.com/old.png")])
    new_message = _FakeUniMessage([_Text("新"), _Image(url="https://example.com/new.png")])
    await auto_greeting.AutoGreetingManager.save_message(
        auto_greeting.GreetingScene.GROUP, old_message
    )
    old_image_path = _FakeAsyncHttpx.downloads[-1][1]

    await auto_greeting.AutoGreetingManager.save_message(
        auto_greeting.GreetingScene.GROUP, new_message
    )

    assert not old_image_path.exists()
    assert str(
        auto_greeting.AutoGreetingManager.get_message(
            auto_greeting.GreetingScene.GROUP
        )
    ) == "新[图片]"


@pytest.mark.asyncio
async def test_save_message_ignores_reply_segment_and_blank_text(tmp_path: Path):
    auto_greeting = _load_auto_greeting_module(tmp_path)
    message = _FakeUniMessage([_Reply(), _Text("   "), _Text("欢迎")])

    saved = await auto_greeting.AutoGreetingManager.save_message(
        auto_greeting.GreetingScene.FRIEND, message
    )

    assert str(saved) == "欢迎"
    data = auto_greeting.AutoGreetingManager._load_data(
        auto_greeting.GreetingScene.FRIEND
    )
    assert data == {"message": [{"type": "text", "text": "欢迎"}]}


@pytest.mark.asyncio
async def test_get_reply_message_returns_referenced_content(tmp_path: Path):
    auto_greeting = _load_auto_greeting_module(tmp_path)
    quoted_message = _FakeUniMessage([_Text("引用欢迎")])

    async def _fake_fetch_reply(_event, _bot):
        # 模拟 alconna 从事件中取到被引用消息。
        return _Reply(quoted_message)

    def _fake_build_reply_message(raw_message, _bot):
        # 测试只关心引用内容会被转交给保存流程，适配器转换由真实库负责。
        return raw_message

    auto_greeting._fetch_reply = _fake_fetch_reply
    auto_greeting._build_reply_message = _fake_build_reply_message

    message = await auto_greeting.AutoGreetingManager.get_reply_message(
        object(), object()
    )

    assert str(message) == "引用欢迎"


@pytest.mark.asyncio
async def test_send_skips_when_message_is_not_configured(tmp_path: Path):
    auto_greeting = _load_auto_greeting_module(tmp_path)

    sent = await auto_greeting.AutoGreetingManager.send_friend_greeting(
        object(), "10001"
    )

    assert sent is False
    assert _FakePlatformUtils.sends == []


@pytest.mark.asyncio
async def test_send_friend_and_group_greeting_targets_configured_scene(tmp_path: Path):
    auto_greeting = _load_auto_greeting_module(tmp_path)
    bot = object()
    await auto_greeting.AutoGreetingManager.save_message(
        auto_greeting.GreetingScene.FRIEND, _FakeUniMessage([_Text("好友")])
    )
    await auto_greeting.AutoGreetingManager.save_message(
        auto_greeting.GreetingScene.GROUP, _FakeUniMessage([_Text("群")])
    )

    assert await auto_greeting.AutoGreetingManager.send_friend_greeting(bot, "10001")
    assert await auto_greeting.AutoGreetingManager.send_group_greeting(bot, "20001")

    assert _FakePlatformUtils.sends[0][1:3] == ("10001", None)
    assert str(_FakePlatformUtils.sends[0][3]) == "好友"
    assert _FakePlatformUtils.sends[1][1:3] == (None, "20001")
    assert str(_FakePlatformUtils.sends[1][3]) == "群"


class _FakeModel:
    def __init_subclass__(cls, **kwargs):
        # 模拟 Tortoise Model 作为基类时的最小行为。
        super().__init_subclass__(**kwargs)


class _FakeFields:
    @staticmethod
    def IntField(**_kwargs):
        return None

    @staticmethod
    def CharEnumField(*_args, **_kwargs):
        return None

    @staticmethod
    def CharField(*_args, **_kwargs):
        return None


class _FakeRequest:
    def __init__(self):
        self.request_type = "friend"
        self.flag = "flag-1"
        self.user_id = "10001"
        self.handle_type = None
        self.saved_fields: list[list[str]] = []

    async def save(self, update_fields=None):
        self.saved_fields.append(update_fields)


class _FakeRequestBot:
    def __init__(self) -> None:
        self.set_friend_requests: list[dict] = []

    async def set_friend_add_request(self, **kwargs):
        self.set_friend_requests.append(kwargs)


def _load_fg_request_module(fake_req: _FakeRequest | None):
    # 直接加载模型文件，避免真实数据库连接参与单元测试。
    module_path = (
        Path(__file__).resolve().parents[1] / "zhenxun" / "models" / "fg_request.py"
    )
    spec = importlib.util.spec_from_file_location("fg_request_under_test", module_path)
    if not spec or not spec.loader:
        raise RuntimeError("无法加载 fg_request 测试模块")
    module = importlib.util.module_from_spec(spec)
    request_handle_type = type(
        "RequestHandleType",
        (),
        {
            "APPROVE": "approve",
            "REFUSED": "refused",
            "IGNORE": "ignore",
            "EXPIRE": "expire",
        },
    )
    request_type = type("RequestType", (), {"FRIEND": "friend", "GROUP": "group"})

    class _BaseFgRequest(_FakeModel):
        @classmethod
        async def get_or_none(cls, **_kwargs):
            return fake_req

    stubs = {
        "nonebot.adapters": _module("nonebot.adapters", Bot=type("Bot", (), {})),
        "tortoise": _module("tortoise", fields=_FakeFields),
        "zhenxun.configs.config": _module(
            "zhenxun.configs.config",
            BotConfig=type("BotConfig", (), {"self_nickname": "真寻"}),
        ),
        "zhenxun.models.group_console": _module(
            "zhenxun.models.group_console", GroupConsole=type("GroupConsole", (), {})
        ),
        "zhenxun.services.db_context": _module(
            "zhenxun.services.db_context", Model=_BaseFgRequest
        ),
        "zhenxun.utils.common_utils": _module(
            "zhenxun.utils.common_utils",
            SqlUtils=type("SqlUtils", (), {"add_column": staticmethod(lambda *a: a)}),
        ),
        "zhenxun.utils.enum": _module(
            "zhenxun.utils.enum",
            RequestHandleType=request_handle_type,
            RequestType=request_type,
        ),
        "zhenxun.utils.exception": _module(
            "zhenxun.utils.exception",
            NotFoundError=type("NotFoundError", (Exception,), {}),
        ),
        "zhenxun.utils.manager.auto_greeting_manager": _module(
            "zhenxun.utils.manager.auto_greeting_manager",
            AutoGreetingManager=_FakeAutoGreetingManager,
        ),
    }
    previous_modules = {name: sys.modules.get(name) for name in stubs}
    try:
        sys.modules.update(stubs)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


@pytest.mark.asyncio
async def test_manual_friend_approve_sends_auto_greeting():
    fake_req = _FakeRequest()
    fg_request = _load_fg_request_module(fake_req)
    bot = _FakeRequestBot()
    _FakeAutoGreetingManager.friend_calls.clear()

    result = await fg_request.FgRequest._handle_request(bot, 1, "approve")

    assert result is fake_req
    assert fake_req.handle_type == "approve"
    assert bot.set_friend_requests == [{"flag": "flag-1", "approve": True}]
    assert _FakeAutoGreetingManager.friend_calls == [(bot, "10001")]


def _load_group_handle_module():
    # 加载 QQ 群事件数据层，测试 add_bot 的通过/强制退出分支。
    module_path = (
        Path(__file__).resolve().parents[1]
        / "zhenxun"
        / "builtin_plugins"
        / "platform"
        / "qq"
        / "group_handle"
        / "data_source.py"
    )
    spec = importlib.util.spec_from_file_location(
        "group_handle_under_test", module_path
    )
    if not spec or not spec.loader:
        raise RuntimeError("无法加载 group_handle 测试模块")
    module = importlib.util.module_from_spec(spec)
    force_add_group_error = type("ForceAddGroupError", (Exception,), {})
    force_add_group_error.get_group_id = lambda self: ""
    request_handle_type = type("RequestHandleType", (), {"IGNORE": "ignore"})
    stubs = {
        "nonebot.adapters": _module("nonebot.adapters", Bot=type("Bot", (), {})),
        "nonebot.exception": _module(
            "nonebot.exception", ActionFailed=type("ActionFailed", (Exception,), {})
        ),
        "nonebot_plugin_alconna": _module(
            "nonebot_plugin_alconna", At=object, UniMessage=_FakeUniMessage
        ),
        "nonebot_plugin_uninfo": _module(
            "nonebot_plugin_uninfo", Uninfo=type("Uninfo", (), {})
        ),
        "zhenxun.builtin_plugins.platform.qq.exception": _module(
            "zhenxun.builtin_plugins.platform.qq.exception",
            ForceAddGroupError=force_add_group_error,
        ),
        "zhenxun.configs.config": _module(
            "zhenxun.configs.config",
            Config=type(
                "Config",
                (),
                {
                    "get": staticmethod(
                        lambda _module: {
                            "flag": True,
                            "message": "拒绝",
                            "welcome_msg_cd": 5,
                        }
                    ),
                    "get_config": staticmethod(lambda *_args, **_kwargs: None),
                },
            ),
        ),
        "zhenxun.configs.path_config": _module(
            "zhenxun.configs.path_config",
            DATA_PATH=Path("/tmp"),
            IMAGE_PATH=Path("/tmp"),
        ),
        "zhenxun.models.fg_request": _module(
            "zhenxun.models.fg_request",
            FgRequest=type(
                "FgRequest", (), {"filter": staticmethod(lambda **_kwargs: None)}
            ),
        ),
        "zhenxun.models.group_console": _module(
            "zhenxun.models.group_console", GroupConsole=type("GroupConsole", (), {})
        ),
        "zhenxun.models.group_member_info": _module(
            "zhenxun.models.group_member_info",
            GroupInfoUser=type("GroupInfoUser", (), {}),
        ),
        "zhenxun.models.level_user": _module(
            "zhenxun.models.level_user", LevelUser=type("LevelUser", (), {})
        ),
        "zhenxun.models.plugin_info": _module(
            "zhenxun.models.plugin_info", PluginInfo=type("PluginInfo", (), {})
        ),
        "zhenxun.services.log": _module("zhenxun.services.log", logger=_FakeLogger()),
        "zhenxun.utils.common_utils": _module(
            "zhenxun.utils.common_utils", CommonUtils=type("CommonUtils", (), {})
        ),
        "zhenxun.utils.enum": _module(
            "zhenxun.utils.enum", RequestHandleType=request_handle_type
        ),
        "zhenxun.utils.manager.auto_greeting_manager": _module(
            "zhenxun.utils.manager.auto_greeting_manager",
            AutoGreetingManager=_FakeAutoGreetingManager,
        ),
        "zhenxun.utils.message": _module(
            "zhenxun.utils.message", MessageUtils=type("MessageUtils", (), {})
        ),
        "zhenxun.utils.platform": _module(
            "zhenxun.utils.platform", PlatformUtils=type("PlatformUtils", (), {})
        ),
        "zhenxun.utils.utils": _module(
            "zhenxun.utils.utils",
            FreqLimiter=type(
                "FreqLimiter",
                (),
                {"__init__": lambda self, _cd: None},
            ),
        ),
    }
    previous_modules = {name: sys.modules.get(name) for name in stubs}
    try:
        sys.modules.update(stubs)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module, force_add_group_error


class _FakeGroup:
    def __init__(self, group_flag: int = 1) -> None:
        self.group_flag = group_flag
        self.saved_fields: list[list[str]] = []

    async def save(self, update_fields=None):
        self.saved_fields.append(update_fields)


class _FakeGroupBot:
    self_id = "10000"

    def __init__(self, superusers=None) -> None:
        self.config = type("Config", (), {"superusers": set(superusers or [])})()
        self.sent_group_messages: list[dict] = []
        self.left_groups: list[int] = []

    async def send_group_msg(self, **kwargs):
        self.sent_group_messages.append(kwargs)

    async def set_group_leave(self, **kwargs):
        self.left_groups.append(kwargs["group_id"])


@pytest.mark.asyncio
async def test_group_add_bot_sends_group_greeting_after_protection_passes(
    monkeypatch: pytest.MonkeyPatch,
):
    group_handle, _ = _load_group_handle_module()
    _FakeAutoGreetingManager.group_calls.clear()
    monkeypatch.setattr(
        group_handle.GroupManager, "_GroupManager__refresh_level", _noop
    )

    bot = _FakeGroupBot(superusers={"20001"})
    await group_handle.GroupManager.add_bot(bot, "20001", "30001", _FakeGroup())

    assert _FakeAutoGreetingManager.group_calls == [(bot, "30001")]
    assert bot.left_groups == []


@pytest.mark.asyncio
async def test_group_add_bot_does_not_send_group_greeting_when_force_leave(
    monkeypatch: pytest.MonkeyPatch,
):
    group_handle, force_add_group_error = _load_group_handle_module()
    _FakeAutoGreetingManager.group_calls.clear()

    class _FilterResult:
        async def update(self, **_kwargs):
            return None

    monkeypatch.setattr(
        group_handle.FgRequest,
        "filter",
        staticmethod(lambda **_kwargs: _FilterResult()),
    )
    bot = _FakeGroupBot()

    with pytest.raises(force_add_group_error):
        await group_handle.GroupManager.add_bot(bot, "20001", "30001", _FakeGroup(0))

    assert _FakeAutoGreetingManager.group_calls == []
    assert bot.left_groups == [30001]


async def _noop(*_args, **_kwargs):
    return None
