from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest


class _FakeMatcher:
    def __init__(self):
        self.handler = None

    def handle(self):
        # 将 matcher 装饰器退化为原函数，测试只关注模块内纯逻辑。
        def decorator(func):
            self.handler = func
            return func

        return decorator


class _PluginMetadata:
    def __init__(self, **kwargs):
        # 保存元数据参数，满足插件模块初始化时的构造需求。
        self.__dict__.update(kwargs)


class _PluginExtraData:
    def __init__(self, **_kwargs):
        # 只保留 to_dict 接口，避免拉起真实配置层依赖。
        pass

    def to_dict(self):
        return {}


class _RegisterConfig:
    def __init__(self, **kwargs):
        # 保留配置声明字段，满足插件元数据构造。
        self.__dict__.update(kwargs)


class _FakeCache:
    def get(self, _key):
        return None

    def set(self, _key, _value):
        return None


class _FakeCacheRoot:
    @staticmethod
    def cache_dict(*_args, **_kwargs):
        return _FakeCache()


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class _FakeAutoGreetingManager:
    calls: ClassVar[list[tuple[object, str]]] = []

    @classmethod
    async def send_friend_greeting(cls, bot, user_id: str):
        # 记录调用参数，验证自动同意好友后会走新欢迎消息服务。
        cls.calls.append((bot, user_id))
        return True


class _FakeBanConsole:
    async def get_ban(**_kwargs):
        raise AssertionError("测试应显式 patch BanConsole.get_ban")

    async def safe_get_or_none(**_kwargs):
        raise AssertionError("测试应显式 patch BanConsole.safe_get_or_none")


def _module(name: str, **attrs):
    # 创建临时模块对象，供目标文件导入其外部依赖。
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _record_request_stubs() -> dict[str, ModuleType]:
    # 仅 stub record_request 初始化必需的依赖，不执行真实插件启动流程。
    action_failed = type("ActionFailed", (Exception,), {})
    friend_request_event = type("FriendRequestEvent", (), {})
    group_request_event = type("GroupRequestEvent", (), {})
    v11_bot = type("v11Bot", (), {})
    v12_bot = type("v12Bot", (), {})
    return {
        "nonebot": _module(
            "nonebot",
            on_message=lambda **_kwargs: _FakeMatcher(),
            on_request=lambda **_kwargs: _FakeMatcher(),
        ),
        "nonebot.adapters.onebot.v11": _module(
            "nonebot.adapters.onebot.v11",
            ActionFailed=action_failed,
            FriendRequestEvent=friend_request_event,
            GroupRequestEvent=group_request_event,
            Bot=v11_bot,
        ),
        "nonebot.adapters.onebot.v12": _module(
            "nonebot.adapters.onebot.v12",
            Bot=v12_bot,
        ),
        "nonebot.plugin": _module("nonebot.plugin", PluginMetadata=_PluginMetadata),
        "nonebot_plugin_session": _module(
            "nonebot_plugin_session", EventSession=type("EventSession", (), {})
        ),
        "zhenxun.configs.config": _module(
            "zhenxun.configs.config",
            BotConfig=SimpleNamespace(self_nickname="真寻"),
            Config=SimpleNamespace(get=lambda _module_name: {}),
        ),
        "zhenxun.configs.utils": _module(
            "zhenxun.configs.utils",
            PluginExtraData=_PluginExtraData,
            RegisterConfig=_RegisterConfig,
        ),
        "zhenxun.models.ban_console": _module(
            "zhenxun.models.ban_console", BanConsole=_FakeBanConsole
        ),
        "zhenxun.models.event_log": _module(
            "zhenxun.models.event_log", EventLog=type("EventLog", (), {})
        ),
        "zhenxun.models.fg_request": _module(
            "zhenxun.models.fg_request", FgRequest=type("FgRequest", (), {})
        ),
        "zhenxun.models.friend_user": _module(
            "zhenxun.models.friend_user", FriendUser=type("FriendUser", (), {})
        ),
        "zhenxun.models.group_console": _module(
            "zhenxun.models.group_console", GroupConsole=type("GroupConsole", (), {})
        ),
        "zhenxun.services.cache": _module(
            "zhenxun.services.cache", CacheRoot=_FakeCacheRoot
        ),
        "zhenxun.services.log": _module("zhenxun.services.log", logger=_FakeLogger()),
        "zhenxun.utils.enum": _module(
            "zhenxun.utils.enum",
            EventLogType=SimpleNamespace(KICK_BOT="kick_bot"),
            PluginType=SimpleNamespace(HIDDEN="hidden"),
            RequestHandleType=SimpleNamespace(APPROVE="approve", EXPIRE="expire"),
            RequestType=SimpleNamespace(FRIEND="friend", GROUP="group"),
        ),
        "zhenxun.utils.manager.auto_greeting_manager": _module(
            "zhenxun.utils.manager.auto_greeting_manager",
            AutoGreetingManager=_FakeAutoGreetingManager,
        ),
        "zhenxun.utils.platform": _module(
            "zhenxun.utils.platform", PlatformUtils=type("PlatformUtils", (), {})
        ),
    }


def _load_record_request_module():
    # 直接加载目标文件，避免导入 builtin_plugins 包时触发全量启动钩子。
    module_path = (
        Path(__file__).resolve().parents[1]
        / "zhenxun"
        / "builtin_plugins"
        / "record_request.py"
    )
    spec = importlib.util.spec_from_file_location(
        "record_request_under_test", module_path
    )
    if not spec or not spec.loader:
        raise RuntimeError("无法加载 record_request 测试模块")
    module = importlib.util.module_from_spec(spec)
    stubs = _record_request_stubs()
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


record_request = _load_record_request_module()


def _ban_record(
    *,
    user_id: str | None = None,
    group_id: str | None = None,
    duration: int = -1,
    operator: str = "1163272259",
    ban_reason: str | None = "测试封禁",
):
    # 构造最小 ban 记录对象，避免单测依赖真实数据库模型实例。
    return SimpleNamespace(
        user_id=user_id,
        group_id=group_id,
        duration=duration,
        operator=operator,
        ban_reason=ban_reason,
    )


class _FakeFriendRequestBot:
    self_id = "10000"

    def __init__(self) -> None:
        self.set_friend_requests: list[dict] = []

    async def get_stranger_info(self, user_id: int):
        # 模拟 OneBot 获取申请人信息。
        return {"user_id": user_id, "nickname": f"用户{user_id}"}

    async def set_friend_add_request(self, **kwargs):
        self.set_friend_requests.append(kwargs)


def test_format_ban_target_handles_user_group_and_combined_targets():
    assert record_request._format_ban_target(
        _ban_record(user_id="1001")
    ) == "用户 1001"
    assert record_request._format_ban_target(
        _ban_record(group_id="2001")
    ) == "群组 2001"
    assert record_request._format_ban_target(
        _ban_record(user_id="1001", group_id="2001")
    ) == "用户 1001 在群组 2001"


@pytest.mark.asyncio
async def test_auto_add_friend_sends_auto_greeting(monkeypatch: pytest.MonkeyPatch):
    bot = _FakeFriendRequestBot()
    event = SimpleNamespace(user_id=10001, flag="flag-1", comment="你好")
    session = SimpleNamespace(platform="qq")
    created_friends: list[dict] = []
    _FakeAutoGreetingManager.calls.clear()
    old_auto_add_friend = record_request.base_config.get("AUTO_ADD_FRIEND")
    record_request.base_config["AUTO_ADD_FRIEND"] = True

    async def fake_create(**kwargs):
        created_friends.append(kwargs)

    monkeypatch.setattr(
        record_request.FriendUser, "create", staticmethod(fake_create), raising=False
    )
    monkeypatch.setattr(record_request.random, "randint", lambda _start, _end: 0)
    monkeypatch.setattr(record_request.asyncio, "sleep", _fake_sleep)

    await record_request.friend_req.handler(bot, event, session)

    assert bot.set_friend_requests == [{"flag": "flag-1", "approve": True}]
    assert created_friends == [{"user_id": "10001", "user_name": "用户10001"}]
    assert _FakeAutoGreetingManager.calls == [(bot, "10001")]
    record_request.base_config["AUTO_ADD_FRIEND"] = old_auto_add_friend


async def _fake_sleep(_delay: int):
    return None


@pytest.mark.asyncio
async def test_build_permanent_ban_tip_filters_temporary_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
):
    records = {
        ("1001", ""): _ban_record(user_id="1001", ban_reason="刷屏"),
        ("1001", "2001"): _ban_record(
            user_id="1001", group_id="2001", duration=60
        ),
        ("", "2001"): _ban_record(group_id="2001", operator="0", ban_reason=None),
    }

    async def fake_get_request_ban_record(
        user_id: str | None, group_id: str | None
    ):
        return records.get((user_id or "", group_id or ""))

    monkeypatch.setattr(
        record_request, "_get_request_ban_record", fake_get_request_ban_record
    )

    tip = await record_request._build_permanent_ban_tip(
        ("1001", None),
        ("1001", "2001"),
        (None, "2001"),
        (None, "2001"),
    )

    assert "永久黑名单提示：" in tip
    assert "- 用户 1001 在黑名单中" in tip
    assert "封禁原因：刷屏" in tip
    assert "- 群组 2001 在黑名单中" in tip
    assert "封禁原因：无" in tip
    assert "用户 1001 在群组 2001" not in tip
    assert tip.count("- 群组 2001 在黑名单中") == 1


@pytest.mark.asyncio
async def test_get_request_ban_record_falls_back_to_null_user_group_ban(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, dict]] = []
    group_ban = _ban_record(group_id="2001")

    async def fake_get_ban(**kwargs):
        calls.append(("get_ban", kwargs))
        return None

    async def fake_safe_get_or_none(**kwargs):
        calls.append(("safe_get_or_none", kwargs))
        return group_ban

    monkeypatch.setattr(
        record_request.BanConsole, "get_ban", staticmethod(fake_get_ban)
    )
    monkeypatch.setattr(
        record_request.BanConsole,
        "safe_get_or_none",
        staticmethod(fake_safe_get_or_none),
    )

    assert await record_request._get_request_ban_record(None, "2001") is group_ban
    assert calls == [
        ("get_ban", {"user_id": None, "group_id": "2001"}),
        (
            "safe_get_or_none",
            {
                "user_id__isnull": True,
                "group_id": "2001",
                "clean_duplicates": False,
            },
        ),
    ]
