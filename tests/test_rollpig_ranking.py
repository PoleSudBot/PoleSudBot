import importlib.util
import json
from pathlib import Path
import sys
import types
import uuid

import pytest

ROLLPIG_PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "zhenxun"
    / "plugins"
    / "nonebot_plugin_rollpig"
)
ROLLPIG_DATA_MANAGER_PATH = ROLLPIG_PLUGIN_DIR / "data_manager.py"
ROLLPIG_PLUGIN_INIT_PATH = ROLLPIG_PLUGIN_DIR / "__init__.py"
ROLLPIG_RANKING_PATH = ROLLPIG_PLUGIN_DIR / "ranking.py"


class FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class FakeMatcher:
    def handle(self):
        def decorator(func):
            return func

        return decorator

    async def finish(self, *_args, **_kwargs):
        return None

    async def send(self, *_args, **_kwargs):
        return None


class FakeMessageSegment:
    @staticmethod
    def reply(_message_id):
        return ""

    @staticmethod
    def image(_data):
        return ""


class FakePluginMetadata:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeScheduler:
    def scheduled_job(self, *_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


def load_module_from_path(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_ranking_module = load_module_from_path(
    f"rollpig_ranking_test_{uuid.uuid4().hex}",
    ROLLPIG_RANKING_PATH,
)
PigKingEntry = _ranking_module.PigKingEntry
sort_pig_king_rankings = _ranking_module.sort_pig_king_rankings


def load_rollpig_data_manager_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    seed_data: dict | None = None,
):
    data_file = tmp_path / "pig_data.json"
    if seed_data is not None:
        data_file.write_text(
            json.dumps(seed_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.__path__ = []
    fake_nonebot_log = types.ModuleType("nonebot.log")
    fake_nonebot_log.logger = FakeLogger()
    fake_nonebot.log = fake_nonebot_log

    monkeypatch.setitem(sys.modules, "nonebot", fake_nonebot)
    monkeypatch.setitem(sys.modules, "nonebot.log", fake_nonebot_log)
    monkeypatch.setitem(
        sys.modules,
        "nonebot_plugin_localstore",
        types.SimpleNamespace(get_plugin_data_file=lambda _name: data_file),
    )

    package_name = f"rollpig_testpkg_{uuid.uuid4().hex}"
    fake_package = types.ModuleType(package_name)
    fake_package.__path__ = [str(ROLLPIG_DATA_MANAGER_PATH.parent)]
    fake_runtime = types.ModuleType(f"{package_name}.runtime")
    fake_runtime.resolve_roast_cooldown_seconds = lambda: 8 * 60 * 60

    monkeypatch.setitem(sys.modules, package_name, fake_package)
    monkeypatch.setitem(sys.modules, f"{package_name}.runtime", fake_runtime)

    module = load_module_from_path(
        f"{package_name}.data_manager",
        ROLLPIG_DATA_MANAGER_PATH,
    )
    return module, data_file


def load_rollpig_plugin_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_store: object,
    fake_data_manager: object,
    group_members: list[object],
):
    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.__path__ = []
    fake_nonebot_log = types.ModuleType("nonebot.log")
    fake_nonebot_log.logger = FakeLogger()
    fake_nonebot.log = fake_nonebot_log
    fake_nonebot.on_command = lambda *_args, **_kwargs: FakeMatcher()
    fake_nonebot.require = lambda *_args, **_kwargs: None
    fake_nonebot.get_driver = lambda: types.SimpleNamespace(
        config=types.SimpleNamespace(superusers=set())
    )

    def _raise_no_bot():
        raise ValueError("no bot")

    fake_nonebot.get_bot = _raise_no_bot

    fake_onebot = types.ModuleType("nonebot.adapters.onebot.v11")
    fake_onebot.Event = type("Event", (), {})
    fake_onebot.GroupMessageEvent = type(
        "GroupMessageEvent",
        (fake_onebot.Event,),
        {},
    )
    fake_onebot.Bot = type("Bot", (), {})
    fake_onebot.Message = str
    fake_onebot.MessageSegment = FakeMessageSegment

    fake_params = types.ModuleType("nonebot.params")
    fake_params.CommandArg = lambda: None

    async def _superuser(_bot, _event):
        return False

    fake_permission = types.ModuleType("nonebot.permission")
    fake_permission.SUPERUSER = _superuser

    fake_plugin = types.ModuleType("nonebot.plugin")
    fake_plugin.PluginMetadata = FakePluginMetadata

    fake_htmlrender = types.ModuleType("nonebot_plugin_htmlrender")
    fake_htmlrender.template_to_pic = lambda *_args, **_kwargs: b""

    fake_apscheduler = types.ModuleType("nonebot_plugin_apscheduler")
    fake_apscheduler.scheduler = FakeScheduler()

    fake_zhenxun = types.ModuleType("zhenxun")
    fake_zhenxun.__path__ = []
    fake_zhenxun_services = types.ModuleType("zhenxun.services")
    fake_zhenxun_services.__path__ = []
    fake_zhenxun_utils = types.ModuleType("zhenxun.utils")
    fake_zhenxun_utils.__path__ = []

    fake_avatar_service_module = types.ModuleType("zhenxun.services.avatar_service")
    fake_avatar_service_module.avatar_service = types.SimpleNamespace(
        get_avatar_path=lambda *_args, **_kwargs: None
    )
    fake_group_settings_module = types.ModuleType(
        "zhenxun.services.group_settings_service"
    )
    fake_group_settings_module.group_settings_service = types.SimpleNamespace(
        set_key_value=lambda *_args, **_kwargs: None
    )

    async def _get_group_member_list(_bot, _group_id):
        return list(group_members)

    fake_platform_module = types.ModuleType("zhenxun.utils.platform")
    fake_platform_module.PlatformUtils = types.SimpleNamespace(
        get_group_member_list=_get_group_member_list
    )

    fake_nonebot_adapters = types.ModuleType("nonebot.adapters")
    fake_nonebot_adapters.__path__ = []
    fake_nonebot_onebot = types.ModuleType("nonebot.adapters.onebot")
    fake_nonebot_onebot.__path__ = []

    for name, module in {
        "nonebot": fake_nonebot,
        "nonebot.log": fake_nonebot_log,
        "nonebot.adapters": fake_nonebot_adapters,
        "nonebot.adapters.onebot": fake_nonebot_onebot,
        "nonebot.params": fake_params,
        "nonebot.permission": fake_permission,
        "nonebot.plugin": fake_plugin,
        "nonebot.adapters.onebot.v11": fake_onebot,
        "nonebot_plugin_htmlrender": fake_htmlrender,
        "nonebot_plugin_apscheduler": fake_apscheduler,
        "zhenxun": fake_zhenxun,
        "zhenxun.services": fake_zhenxun_services,
        "zhenxun.services.avatar_service": fake_avatar_service_module,
        "zhenxun.services.group_settings_service": fake_group_settings_module,
        "zhenxun.utils": fake_zhenxun_utils,
        "zhenxun.utils.platform": fake_platform_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    package_name = f"rollpig_testpkg_{uuid.uuid4().hex}"

    fake_config = types.ModuleType(f"{package_name}.config")
    fake_config.Config = type("Config", (), {})
    fake_config.GroupSettings = type("GroupSettings", (), {})
    fake_config.MODULE_NAME = "nonebot_plugin_rollpig"
    fake_config.get_proxy = lambda: None
    fake_config.get_storage_backend = lambda: "local"

    fake_roast_manager = types.ModuleType(f"{package_name}.roast_manager")
    fake_roast_manager.roast_manager = object()

    fake_runtime = types.ModuleType(f"{package_name}.runtime")
    fake_runtime.is_daily_summary_push_enabled = lambda *_args, **_kwargs: True
    fake_runtime.is_group_rollpig_enabled = lambda *_args, **_kwargs: True
    fake_runtime.resolve_roast_cooldown_seconds = lambda: 8 * 60 * 60

    fake_store_module = types.ModuleType(f"{package_name}.store")
    fake_store_module.__path__ = []
    fake_store_module.store = fake_store

    fake_store_cloud = types.ModuleType(f"{package_name}.store.cloud")
    fake_store_cloud.CloudStoreError = type("CloudStoreError", (Exception,), {})

    fake_store_models = types.ModuleType(f"{package_name}.store.models")
    fake_store_models.RoastEvent = type("RoastEvent", (), {})

    fake_summary = types.ModuleType(f"{package_name}.summary_service")
    fake_summary.build_daily_summary = lambda *_args, **_kwargs: {}

    fake_texts = types.ModuleType(f"{package_name}.texts")
    fake_texts.TOMORROW_TEXTS = [""]
    fake_texts.FOOD_PIG_IDS = set()
    fake_texts.HUMAN_PIG_ID = "human"
    fake_texts.EATEN_PIG_ID = "eaten"
    fake_texts.FORCE_ROAST_KEYWORDS = []
    fake_texts.SUPER_FORCE_ROAST_KEYWORD = "super"
    fake_texts.TODAY_ROAST_HUMAN_BLOCK_TEXTS = [""]
    fake_texts.TODAY_ROAST_EATEN_BLOCK_TEXTS = [""]
    fake_texts.TODAY_ROAST_FOOD_BLOCK_TEXTS = [""]
    fake_texts.TARGET_HUMAN_BLOCK_TEXTS = [""]
    fake_texts.TARGET_EATEN_BLOCK_TEXTS = [""]
    fake_texts.TARGET_FOOD_BLOCK_TEXTS = [""]
    fake_texts.BACKFIRE_HUMAN_TEXTS = ["{attacker}{target}"]
    fake_texts.BACKFIRE_EATEN_TEXTS = ["{attacker}{target}"]
    fake_texts.BACKFIRE_FOOD_TEXTS = ["{attacker}{target}"]
    fake_texts.BACKFIRE_NO_PIG_TEXTS = ["{attacker}{target}"]
    fake_texts.BACKFIRE_GENERIC_TEXTS = ["{attacker}{target}"]
    fake_texts.ESCAPE_TEXTS = ["{attacker}{target}"]
    fake_texts.SUPER_FORCE_ROAST_PREFIX_TEXTS = ["{target}"]
    fake_texts.FORCE_ROAST_PREFIX_TEXTS = ["{target}"]
    fake_texts.FORCE_ROAST_LIMIT_TEXTS = ["{operator}{target}"]
    fake_texts.ROAST_BOT_TEXTS = [""]
    fake_texts.DAILY_SUMMARY_EMPTY_TEXTS = [""]
    fake_texts.DAILY_SUMMARY_HEADER = ""
    fake_texts.DAILY_SUMMARY_FOOTER = ""
    fake_texts.PROTECTION_BLOCK_TEXTS = [""]
    fake_texts.PROTECTION_BREAK_TEXTS = [""]
    fake_texts.RANDOM_ROAST_INTRO_TEXTS = [""]

    fake_data_manager_module = types.ModuleType(f"{package_name}.data_manager")
    fake_data_manager_module.get_data_manager = lambda: fake_data_manager

    monkeypatch.setitem(sys.modules, f"{package_name}.config", fake_config)
    monkeypatch.setitem(sys.modules, f"{package_name}.ranking", _ranking_module)
    monkeypatch.setitem(
        sys.modules,
        f"{package_name}.roast_manager",
        fake_roast_manager,
    )
    monkeypatch.setitem(sys.modules, f"{package_name}.runtime", fake_runtime)
    monkeypatch.setitem(sys.modules, f"{package_name}.store", fake_store_module)
    monkeypatch.setitem(sys.modules, f"{package_name}.store.cloud", fake_store_cloud)
    monkeypatch.setitem(sys.modules, f"{package_name}.store.models", fake_store_models)
    monkeypatch.setitem(sys.modules, f"{package_name}.summary_service", fake_summary)
    monkeypatch.setitem(sys.modules, f"{package_name}.texts", fake_texts)
    monkeypatch.setitem(
        sys.modules,
        f"{package_name}.data_manager",
        fake_data_manager_module,
    )

    spec = importlib.util.spec_from_file_location(
        package_name,
        ROLLPIG_PLUGIN_INIT_PATH,
        submodule_search_locations=[str(ROLLPIG_PLUGIN_DIR)],
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


def test_migrate_old_data_adds_collection_progress(monkeypatch, tmp_path):
    module, data_file = load_rollpig_data_manager_module(
        monkeypatch,
        tmp_path,
        seed_data={
            "history": {},
            "group_rolls": {},
            "collection": {
                "10001": ["pig", "black-pig"],
                "10002": ["pig"],
            },
            "usage": {},
            "force_usage": {},
            "daily_events": {},
            "protected": {},
        },
    )

    manager = module.PigDataManager()

    assert manager.get_collection_progress("10001") == {
        "count": 2,
        "reached_at": None,
    }
    assert manager.get_collection_progress("10002") == {
        "count": 1,
        "reached_at": None,
    }

    saved = json.loads(data_file.read_text("utf-8"))
    assert saved["collection_progress"]["10001"] == {"count": 2, "reached_at": None}
    assert saved["collection_progress"]["10002"] == {"count": 1, "reached_at": None}


@pytest.mark.asyncio
async def test_new_unique_pig_refreshes_collection_progress(monkeypatch, tmp_path):
    module, _data_file = load_rollpig_data_manager_module(
        monkeypatch,
        tmp_path,
        seed_data={
            "history": {},
            "group_rolls": {},
            "collection": {"10001": ["pig"]},
            "collection_progress": {"10001": {"count": 1, "reached_at": 10.0}},
            "usage": {},
            "force_usage": {},
            "daily_events": {},
            "protected": {},
        },
    )
    manager = module.PigDataManager()
    monkeypatch.setattr(module.time, "time", lambda: 1234.5)

    pig_id, created = await manager.get_or_create_today_pig(
        "10001",
        "black-pig",
        date_str="2026-04-22",
    )

    assert pig_id == "black-pig"
    assert created is True
    assert manager.get_user_collection("10001") == ["pig", "black-pig"]
    assert manager.get_collection_progress("10001") == {
        "count": 2,
        "reached_at": 1234.5,
    }


@pytest.mark.asyncio
async def test_duplicate_pig_does_not_refresh_collection_progress(
    monkeypatch,
    tmp_path,
):
    module, _data_file = load_rollpig_data_manager_module(
        monkeypatch,
        tmp_path,
        seed_data={
            "history": {},
            "group_rolls": {},
            "collection": {"10001": ["pig"]},
            "collection_progress": {"10001": {"count": 1, "reached_at": 10.0}},
            "usage": {},
            "force_usage": {},
            "daily_events": {},
            "protected": {},
        },
    )
    manager = module.PigDataManager()
    monkeypatch.setattr(module.time, "time", lambda: 9999.9)

    pig_id, created = await manager.get_or_create_today_pig(
        "10001",
        "pig",
        date_str="2026-04-22",
    )

    assert pig_id == "pig"
    assert created is True
    assert manager.get_user_collection("10001") == ["pig"]
    assert manager.get_collection_progress("10001") == {
        "count": 1,
        "reached_at": 10.0,
    }


def test_sort_prefers_earlier_reached_at_for_same_count():
    rankings = sort_pig_king_rankings(
        [
            PigKingEntry(
                user_id="10001",
                display_name="later-user",
                collection_count=7,
                reached_at=200.0,
            ),
            PigKingEntry(
                user_id="20002",
                display_name="earlier-user",
                collection_count=7,
                reached_at=100.0,
            ),
        ]
    )

    assert [entry.user_id for entry in rankings] == ["20002", "10001"]


def test_sort_falls_back_to_user_id_when_progress_is_missing():
    rankings = sort_pig_king_rankings(
        [
            PigKingEntry(
                user_id="20002",
                display_name="with-progress",
                collection_count=7,
                reached_at=100.0,
            ),
            PigKingEntry(
                user_id="10001",
                display_name="without-progress",
                collection_count=7,
                reached_at=None,
            ),
        ]
    )

    assert [entry.user_id for entry in rankings] == ["10001", "20002"]


@pytest.mark.asyncio
async def test_group_rankings_only_query_member_progress(monkeypatch):
    class FakeManager:
        def __init__(self):
            self.progress_calls: list[str] = []

        def get_all_collection_progress(self):
            raise AssertionError(
                "group ranking should not scan all collection progress"
            )

        def get_collection_progress(self, user_id: str):
            self.progress_calls.append(user_id)
            progress_map = {
                "10001": {"count": 2, "reached_at": 200.0},
                "20002": {"count": 2, "reached_at": 100.0},
            }
            return progress_map.get(user_id)

    class FakeStore:
        async def get_user_collection(self, user_id: str):
            collections = {
                "10001": ["pig", "black-pig"],
                "20002": ["pig", "white-pig"],
                "30003": [],
            }
            return list(collections.get(user_id, []))

    members = [
        types.SimpleNamespace(user_id="10001", card="", name="later-user"),
        types.SimpleNamespace(user_id="20002", card="", name="earlier-user"),
        types.SimpleNamespace(user_id="30003", card="", name="empty-user"),
    ]
    manager = FakeManager()
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=FakeStore(),
        fake_data_manager=manager,
        group_members=members,
    )

    rankings = await module.build_group_pig_rankings(object(), "123456")

    assert [entry.user_id for entry in rankings] == ["20002", "10001"]
    assert manager.progress_calls == ["10001", "20002"]
