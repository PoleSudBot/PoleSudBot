import asyncio
import base64
import hashlib
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
ROLLPIG_CONFIG_PATH = ROLLPIG_PLUGIN_DIR / "config.py"
ROLLPIG_PLUGIN_INIT_PATH = ROLLPIG_PLUGIN_DIR / "__init__.py"
ROLLPIG_RANKING_PATH = ROLLPIG_PLUGIN_DIR / "ranking.py"
ROLLPIG_RESOURCE_MANAGER_PATH = ROLLPIG_PLUGIN_DIR / "resource_manager.py"
ROLLPIG_ROAST_MANAGER_PATH = ROLLPIG_PLUGIN_DIR / "roast_manager.py"
ROLLPIG_RUNTIME_PATH = ROLLPIG_PLUGIN_DIR / "runtime.py"
VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "iZk9HQAAAABJRU5ErkJggg=="
)


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


class FakeHTTPResponse:
    def __init__(self, content: bytes = b"", json_data=None):
        self.content = content
        self._json_data = json_data

    def raise_for_status(self):
        return None

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.content.decode("utf-8"))


class FakeHTTPClient:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.request_urls: list[str] = []

    async def get(self, url: str):
        self.request_urls.append(url)
        return FakeHTTPResponse(self.payloads[url])


class FakeAsyncClientContext:
    def __init__(self, client: FakeHTTPClient):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return None


class FakePluginMetadata:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeScheduler:
    def scheduled_job(self, *_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


class FakeDriver:
    config = types.SimpleNamespace(superusers=set())

    def on_startup(self, func):
        return func

    def on_shutdown(self, func):
        return func


class FakeResourceManager:
    def __init__(self):
        self.pig_list = [
            {"id": "pig", "name": "普通小猪"},
            {"id": "human", "name": "人类"},
            {"id": "eaten", "name": "吃掉了"},
            {"id": "sold-out", "name": "卖掉了"},
            {"id": "cake-pig", "name": "蛋糕猪"},
            {"id": "guest-human", "name": "客人人类"},
            {"id": "auctioned-pig", "name": "拍卖猪"},
        ]
        self.rules = {
            "food_pigs": ["cake-pig"],
            "human_pigs": ["guest-human"],
            "eaten_pigs": ["eaten"],
            "sold_pigs": ["sold-out", "auctioned-pig"],
            "roast_excluded_pigs": [],
        }

    def reload(self):
        return None

    def find_image_file(self, _pig_id):
        return None

    def get_pig_by_id(self, pig_id):
        return next(
            (item for item in self.pig_list if item["id"] == pig_id),
            None,
        )

    def get_rule_ids(self, key):
        return list(self.rules.get(key, []))

    async def sync_from_remote(self, *, force=False):
        return types.SimpleNamespace(
            updated=force,
            skipped=not force,
            resource_version="test",
            message="小猪资源同步完成：test" if force else "资源已是最新版本",
        )

    async def sync_all(self, *, force=False, wait_if_busy=True):
        public_result = await self.sync_from_remote(force=force)
        private_result = types.SimpleNamespace(
            updated=False,
            skipped=True,
            resource_version="",
            message="",
        )
        return public_result, private_result


class FakePigProgress:
    def __init__(self, copies: int = 0, first_obtained_at: str | None = None):
        self.copies = copies
        self.first_obtained_at = first_obtained_at


class FakeDrawState:
    def __init__(
        self,
        pig_ids: list[str],
        progress: dict[str, FakePigProgress],
        duplicate_streak: int = 0,
    ):
        self.pig_ids = pig_ids
        self.progress = progress
        self.duplicate_streak = duplicate_streak

    def copies_of(self, pig_id: str) -> int:
        item = self.progress.get(pig_id)
        return int(item.copies) if item else 0


class FakeDailyRollResult:
    def __init__(
        self,
        pig_id: str,
        created: bool,
        is_new_pig: bool = False,
        previous_copies: int = 0,
        copies: int = 0,
        previous_duplicate_streak: int = 0,
        duplicate_streak: int = 0,
    ):
        self.pig_id = pig_id
        self.created = created
        self.is_new_pig = is_new_pig
        self.previous_copies = previous_copies
        self.copies = copies
        self.previous_duplicate_streak = previous_duplicate_streak
        self.duplicate_streak = duplicate_streak

    def __iter__(self):
        yield self.pig_id
        yield self.created


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
    fake_runtime.rollpig_date_str = (
        lambda offset_days=0: f"2026-04-{22 + offset_days:02d}"
    )
    fake_runtime.rollpig_today = lambda: __import__("datetime").date(2026, 4, 22)
    fake_store_package = types.ModuleType(f"{package_name}.store")
    fake_store_package.__path__ = []
    fake_store_models = types.ModuleType(f"{package_name}.store.models")
    fake_store_models.DailyRollResult = FakeDailyRollResult
    fake_store_models.DrawState = FakeDrawState
    fake_store_models.PigProgress = FakePigProgress

    monkeypatch.setitem(sys.modules, package_name, fake_package)
    monkeypatch.setitem(sys.modules, f"{package_name}.runtime", fake_runtime)
    monkeypatch.setitem(sys.modules, f"{package_name}.store", fake_store_package)
    monkeypatch.setitem(sys.modules, f"{package_name}.store.models", fake_store_models)

    module = load_module_from_path(
        f"{package_name}.data_manager",
        ROLLPIG_DATA_MANAGER_PATH,
    )
    return module, data_file


def load_rollpig_resource_manager_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.__path__ = []
    fake_nonebot_log = types.ModuleType("nonebot.log")
    fake_nonebot_log.logger = FakeLogger()
    fake_nonebot.log = fake_nonebot_log

    package_name = f"rollpig_resource_testpkg_{uuid.uuid4().hex}"
    fake_package = types.ModuleType(package_name)
    fake_package.__path__ = [str(ROLLPIG_PLUGIN_DIR)]

    fake_config = types.ModuleType(f"{package_name}.config")
    fake_config.get_proxy = lambda: None
    fake_config.get_resource_manifest_url = lambda: None
    fake_config.get_resource_max_file_size = lambda: 10 * 1024 * 1024
    fake_config.get_resource_sync_enabled = lambda: False
    fake_config.get_resource_sync_timeout = lambda: 10.0
    fake_config.get_private_resource_manifest_url = lambda: None
    fake_config.get_private_resource_token = lambda: None

    fake_localstore = types.ModuleType("nonebot_plugin_localstore")
    fake_localstore.get_plugin_data_dir = lambda: tmp_path / "cache"

    monkeypatch.setitem(sys.modules, "nonebot", fake_nonebot)
    monkeypatch.setitem(sys.modules, "nonebot.log", fake_nonebot_log)
    monkeypatch.setitem(sys.modules, "nonebot_plugin_localstore", fake_localstore)
    monkeypatch.setitem(sys.modules, package_name, fake_package)
    monkeypatch.setitem(sys.modules, f"{package_name}.config", fake_config)

    module = load_module_from_path(
        f"{package_name}.resource_manager",
        ROLLPIG_RESOURCE_MANAGER_PATH,
    )
    return module


def load_rollpig_config_module(
    monkeypatch: pytest.MonkeyPatch,
    config_values: dict,
):
    fake_zhenxun = types.ModuleType("zhenxun")
    fake_zhenxun.__path__ = []
    fake_configs = types.ModuleType("zhenxun.configs")
    fake_configs.__path__ = []
    fake_config_module = types.ModuleType("zhenxun.configs.config")
    fake_config_module.Config = types.SimpleNamespace(
        get=lambda _module_name: dict(config_values)
    )

    monkeypatch.setitem(sys.modules, "zhenxun", fake_zhenxun)
    monkeypatch.setitem(sys.modules, "zhenxun.configs", fake_configs)
    monkeypatch.setitem(sys.modules, "zhenxun.configs.config", fake_config_module)

    return load_module_from_path(
        f"rollpig_config_test_{uuid.uuid4().hex}",
        ROLLPIG_CONFIG_PATH,
    )


def load_rollpig_runtime_module(monkeypatch: pytest.MonkeyPatch):
    fake_nonebot_log = types.ModuleType("nonebot.log")
    fake_nonebot_log.logger = FakeLogger()

    fake_zhenxun = types.ModuleType("zhenxun")
    fake_zhenxun.__path__ = []
    fake_services = types.ModuleType("zhenxun.services")
    fake_services.__path__ = []
    fake_group_settings = types.ModuleType("zhenxun.services.group_settings_service")
    fake_group_settings.group_settings_service = types.SimpleNamespace(
        get_all_for_plugin=lambda *_args, **_kwargs: None
    )

    package_name = f"rollpig_runtime_testpkg_{uuid.uuid4().hex}"
    fake_package = types.ModuleType(package_name)
    fake_package.__path__ = [str(ROLLPIG_PLUGIN_DIR)]
    fake_config = types.ModuleType(f"{package_name}.config")
    fake_config.GroupSettings = type("GroupSettings", (), {})
    fake_config.MODULE_NAME = "nonebot_plugin_rollpig"
    fake_config.get_roast_cooldown_hours = lambda: 8.0

    for name, module in {
        "nonebot.log": fake_nonebot_log,
        "zhenxun": fake_zhenxun,
        "zhenxun.services": fake_services,
        "zhenxun.services.group_settings_service": fake_group_settings,
        package_name: fake_package,
        f"{package_name}.config": fake_config,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return load_module_from_path(
        f"{package_name}.runtime",
        ROLLPIG_RUNTIME_PATH,
    )


def load_rollpig_roast_manager_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    roast_file = tmp_path / "roast_library.json"
    fake_localstore = types.ModuleType("nonebot_plugin_localstore")
    fake_localstore.get_plugin_data_file = lambda _name: roast_file

    fake_zhenxun = types.ModuleType("zhenxun")
    fake_zhenxun.__path__ = []
    fake_services = types.ModuleType("zhenxun.services")
    fake_services.__path__ = []
    fake_llm = types.ModuleType("zhenxun.services.llm")
    fake_llm.LLMMessage = types.SimpleNamespace(
        system=lambda text: ("system", text),
        user=lambda text: ("user", text),
    )
    fake_llm.generate = lambda *_args, **_kwargs: None
    fake_log = types.ModuleType("zhenxun.services.log")
    fake_log.logger = FakeLogger()

    package_name = f"rollpig_roast_testpkg_{uuid.uuid4().hex}"
    fake_package = types.ModuleType(package_name)
    fake_package.__path__ = [str(ROLLPIG_PLUGIN_DIR)]
    fake_config = types.ModuleType(f"{package_name}.config")
    fake_config.get_ai_enabled = lambda: False
    fake_config.get_llm_model_name = lambda: None

    for name, module in {
        "nonebot_plugin_localstore": fake_localstore,
        "zhenxun": fake_zhenxun,
        "zhenxun.services": fake_services,
        "zhenxun.services.llm": fake_llm,
        "zhenxun.services.log": fake_log,
        package_name: fake_package,
        f"{package_name}.config": fake_config,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    module = load_module_from_path(
        f"{package_name}.roast_manager",
        ROLLPIG_ROAST_MANAGER_PATH,
    )
    return module, roast_file


def build_manifest_meta(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


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
    fake_nonebot.get_driver = lambda: FakeDriver()

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
    fake_config.DEFAULT_RESOURCE_MANIFEST_URL = (
        "https://pig.felislab.cc/resources/rollpig/manifest.json"
    )
    fake_config.DEFAULT_PRIVATE_RESOURCE_MANIFEST_URL = (
        "https://pig.felislab.cc/resources/rollpig-pjsk/manifest.json"
    )
    fake_config.GroupSettings = type("GroupSettings", (), {})
    fake_config.MODULE_NAME = "nonebot_plugin_rollpig"
    fake_config.get_proxy = lambda: None
    fake_config.get_storage_backend = lambda: "local"
    fake_config.get_growth_max_expert_level = lambda: 5
    fake_config.get_growth_pity_weight_cap = lambda: 4.0
    fake_config.get_growth_pity_weight_step = lambda: 0.5
    fake_config.get_resource_sync_enabled = lambda: False
    fake_config.get_resource_sync_interval_hours = lambda: 24
    fake_config.get_resource_sync_on_startup = lambda: True
    fake_config.get_private_resource_manifest_url = lambda: None
    fake_config.get_private_resource_token = lambda: None

    fake_resource_manager = types.ModuleType(f"{package_name}.resource_manager")
    fake_resource_manager.pig_resource_manager = FakeResourceManager()

    fake_roast_manager = types.ModuleType(f"{package_name}.roast_manager")
    fake_roast_manager.roast_manager = object()

    fake_runtime = types.ModuleType(f"{package_name}.runtime")
    fake_runtime.is_daily_summary_push_enabled = lambda *_args, **_kwargs: True
    fake_runtime.is_group_rollpig_enabled = lambda *_args, **_kwargs: True
    fake_runtime.rollpig_date_str = (
        lambda offset_days=0: f"2026-04-{22 + offset_days:02d}"
    )
    fake_runtime.rollpig_today = lambda: __import__("datetime").date(2026, 4, 22)
    fake_runtime.resolve_roast_cooldown_seconds = lambda: 8 * 60 * 60

    fake_store_module = types.ModuleType(f"{package_name}.store")
    fake_store_module.__path__ = []
    fake_store_module.store = fake_store

    fake_store_cloud = types.ModuleType(f"{package_name}.store.cloud")
    fake_store_cloud.CloudStoreError = type("CloudStoreError", (Exception,), {})

    fake_store_models = types.ModuleType(f"{package_name}.store.models")
    fake_store_models.DailyRollResult = FakeDailyRollResult
    fake_store_models.DrawState = FakeDrawState
    fake_store_models.PigProgress = FakePigProgress
    fake_store_models.RoastEvent = type("RoastEvent", (), {})

    fake_summary = types.ModuleType(f"{package_name}.summary_service")
    fake_summary.build_daily_summary = lambda *_args, **_kwargs: {}

    fake_texts = types.ModuleType(f"{package_name}.texts")
    fake_texts.TOMORROW_TEXTS = [""]
    fake_texts.DAILY_ROLL_NEW_PIG_TEXTS = ["new {pig} {level}"]
    fake_texts.DAILY_ROLL_DUPLICATE_LEVEL_UP_TEXTS = [
        "up {pig} {old_level} {new_level}"
    ]
    fake_texts.DAILY_ROLL_DUPLICATE_SAME_LEVEL_TEXTS = ["same {pig} {level}"]
    fake_texts.FOOD_PIG_IDS = set()
    fake_texts.HUMAN_PIG_ID = "human"
    fake_texts.EATEN_PIG_ID = "eaten"
    fake_texts.SOLD_PIG_ID = "sold-out"
    fake_texts.FORCE_ROAST_KEYWORDS = []
    fake_texts.SUPER_FORCE_ROAST_KEYWORD = "super"
    fake_texts.TODAY_ROAST_HUMAN_BLOCK_TEXTS = [""]
    fake_texts.TODAY_ROAST_EATEN_BLOCK_TEXTS = [""]
    fake_texts.TODAY_ROAST_SOLD_BLOCK_TEXTS = [""]
    fake_texts.TODAY_ROAST_FOOD_BLOCK_TEXTS = [""]
    fake_texts.TARGET_HUMAN_BLOCK_TEXTS = [""]
    fake_texts.TARGET_EATEN_BLOCK_TEXTS = [""]
    fake_texts.TARGET_SOLD_BLOCK_TEXTS = [""]
    fake_texts.TARGET_FOOD_BLOCK_TEXTS = [""]
    fake_texts.BACKFIRE_HUMAN_TEXTS = ["{attacker}{target}"]
    fake_texts.BACKFIRE_EATEN_TEXTS = ["{attacker}{target}"]
    fake_texts.BACKFIRE_SOLD_TEXTS = ["{attacker}{target}"]
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
        f"{package_name}.resource_manager",
        fake_resource_manager,
    )
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
    assert saved["pig_progress"]["10001"] == {
        "pig": {"copies": 1, "first_obtained_at": None},
        "black-pig": {"copies": 1, "first_obtained_at": None},
    }
    assert saved["draw_state"]["10001"] == {"duplicate_streak": 0}


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
    draw_state = manager.get_draw_state("10001")
    assert draw_state.copies_of("black-pig") == 1
    assert draw_state.duplicate_streak == 0


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
    draw_state = manager.get_draw_state("10001")
    assert draw_state.copies_of("pig") == 2
    assert draw_state.duplicate_streak == 1


@pytest.mark.asyncio
async def test_existing_daily_roll_does_not_increment_growth(monkeypatch, tmp_path):
    module, _data_file = load_rollpig_data_manager_module(
        monkeypatch,
        tmp_path,
        seed_data={
            "history": {"2026-04-22": {"10001": "pig"}},
            "group_rolls": {},
            "collection": {"10001": ["pig"]},
            "collection_progress": {"10001": {"count": 1, "reached_at": 10.0}},
            "pig_progress": {
                "10001": {"pig": {"copies": 3, "first_obtained_at": None}},
            },
            "draw_state": {"10001": {"duplicate_streak": 2}},
            "usage": {},
            "force_usage": {},
            "daily_events": {},
            "protected": {},
        },
    )
    manager = module.PigDataManager()

    result = await manager.get_or_create_today_pig(
        "10001",
        "black-pig",
        date_str="2026-04-22",
        group_id="20001",
    )

    assert result.pig_id == "pig"
    assert result.created is False
    assert result.copies == 3
    assert result.duplicate_streak == 2
    assert manager.get_draw_state("10001").copies_of("pig") == 3
    assert manager.get_group_rolls("20001", "2026-04-22") == {"10001": "pig"}


@pytest.mark.asyncio
async def test_broken_pig_data_enters_write_protection(monkeypatch, tmp_path):
    module, data_file = load_rollpig_data_manager_module(monkeypatch, tmp_path)
    data_file.write_text("{ broken json", encoding="utf-8")

    manager = module.PigDataManager()

    assert manager.get_daily_rolls("2026-04-22") == {}
    assert data_file.read_text("utf-8") == "{ broken json"
    assert list(tmp_path.glob("pig_data.json.broken.*.bak"))
    with pytest.raises(RuntimeError, match="拒绝写入"):
        await manager.set_today_pig("10001", "pig")
    assert data_file.read_text("utf-8") == "{ broken json"


def test_broken_pig_data_recovers_from_backup(monkeypatch, tmp_path):
    module, data_file = load_rollpig_data_manager_module(monkeypatch, tmp_path)
    backup_data = {
        "history": {"2026-04-22": {"10001": "pig"}},
        "group_rolls": {},
        "collection": {"10001": ["pig"]},
        "collection_progress": {},
        "pig_progress": {},
        "draw_state": {},
        "usage": {},
        "force_usage": {},
        "daily_events": {},
        "protected": {},
    }
    data_file.write_text("{ broken json", encoding="utf-8")
    data_file.with_name("pig_data.json.bak").write_text(
        json.dumps(backup_data, ensure_ascii=False),
        encoding="utf-8",
    )

    manager = module.PigDataManager()

    assert manager.get_today_pig("10001", "2026-04-22") == "pig"
    saved = json.loads(data_file.read_text("utf-8"))
    assert saved["history"]["2026-04-22"]["10001"] == "pig"
    assert json.loads(data_file.with_name("pig_data.json.bak").read_text("utf-8")) == backup_data


@pytest.mark.asyncio
async def test_pig_data_save_rotates_backups(monkeypatch, tmp_path):
    module, data_file = load_rollpig_data_manager_module(
        monkeypatch,
        tmp_path,
        seed_data={
            "history": {},
            "group_rolls": {},
            "collection": {},
            "collection_progress": {},
            "pig_progress": {},
            "draw_state": {},
            "usage": {},
            "force_usage": {},
            "daily_events": {},
            "protected": {},
        },
    )
    original = data_file.read_text("utf-8")
    manager = module.PigDataManager()

    await manager.set_today_pig("10001", "pig", group_id="20001")

    assert data_file.with_name("pig_data.json.bak").read_text("utf-8") == original
    saved = json.loads(data_file.read_text("utf-8"))
    assert saved["history"]["2026-04-22"]["10001"] == "pig"


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


def test_pigsty_growth_summary_keeps_repeat_and_streak_notes(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )
    draw_state = FakeDrawState(
        pig_ids=["pig", "black-pig"],
        progress={
            "pig": FakePigProgress(copies=3, first_obtained_at="2026-04-20T00:00:00Z"),
            "black-pig": FakePigProgress(copies=1, first_obtained_at=None),
        },
        duplicate_streak=2,
    )

    stats = module.build_pigsty_growth_stats(draw_state)
    notes = module.build_pigsty_growth_notes(draw_state)

    assert {"label": "最高等级", "value": "EX Lv. 2"} in stats
    assert any("本命猪" in note and "EX Lv. 2" in note for note in notes)
    assert any("高等级小猪" in note and "EX Lv.2" in note for note in notes)
    assert all("×3" not in note for note in notes)
    assert any("连续重复：2 次" in note for note in notes)


def test_pigsty_footer_matches_upstream_summary_copy(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )

    assert module.build_my_pigsty_footer(0) == "发送「今日小猪」开始收集。"
    assert (
        module.build_my_pigsty_footer(1)
        == "完整图鉴图还在施工，先把成长进度记牢。"
    )


@pytest.mark.asyncio
async def test_pighub_refresh_prefers_new_api_and_builds_urls(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )

    class FakePigHubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            assert url == module.PIGHUB_API_URLS[0]
            return FakeHTTPResponse(
                json_data={
                    "code": 0,
                    "data": [
                        {
                            "title": "空格猪",
                            "image_url": "https://pighub.top/images/space pig.png",
                        }
                    ],
                }
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_: FakePigHubClient())

    assert await module.ensure_pighub_images_loaded()
    assert module.pighub_images[0]["thumbnail"].endswith("space pig.png")
    assert (
        module.build_pighub_image_url(module.pighub_images[0])
        == "https://pighub.top/images/space%20pig.png"
    )
    assert (
        module.build_pighub_image_url({"thumbnail": "pig.png"})
        == "https://pighub.top/data/pig.png"
    )
    assert (
        module.build_pighub_image_url({"thumbnail": "/data/fancy pig.png"})
        == "https://pighub.top/data/fancy%20pig.png"
    )


@pytest.mark.asyncio
async def test_pighub_refresh_falls_back_to_old_api(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )
    requested_urls: list[str] = []

    class FakePigHubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            requested_urls.append(url)
            if url == module.PIGHUB_API_URLS[0]:
                raise RuntimeError("new api down")
            return FakeHTTPResponse(
                json_data={"images": [{"title": "旧猪", "thumbnail": "old.png"}]}
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_: FakePigHubClient())

    assert await module.ensure_pighub_images_loaded()
    assert requested_urls == module.PIGHUB_API_URLS
    assert module.pighub_images[0]["title"] == "旧猪"


@pytest.mark.asyncio
async def test_pighub_refresh_reuses_active_task(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )
    calls = 0
    release = asyncio.Event()

    async def slow_refresh():
        nonlocal calls
        calls += 1
        await release.wait()
        module.pighub_images = [{"title": "并发猪", "thumbnail": "pig.png"}]
        module.pighub_last_loaded = module.time.time()
        return True

    monkeypatch.setattr(module, "refresh_pighub_images", slow_refresh)

    first = asyncio.create_task(module.ensure_pighub_images_loaded())
    second = asyncio.create_task(module.ensure_pighub_images_loaded())
    await asyncio.sleep(0)
    release.set()

    assert await first
    assert await second
    assert calls == 1


def test_rollpig_resource_json_accepts_utf8_bom(monkeypatch, tmp_path):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    resource_file = tmp_path / "pig.json"
    resource_file.write_text(
        '\ufeff[{"id": "bom-pig", "name": "BOM"}]', encoding="utf-8"
    )

    assert manager._read_pig_json(resource_file) == [
        {"id": "bom-pig", "name": "BOM"}
    ]


@pytest.mark.asyncio
async def test_rollpig_resource_sync_accepts_decodable_image_mismatch(
    monkeypatch,
    tmp_path,
):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    staging_dir = tmp_path / "staging"
    (staging_dir / "images").mkdir(parents=True)
    pig_json = b'[{"id": "pig", "name": "Pig"}]'
    client = FakeHTTPClient(
        {
            "https://example.com/resources/pig.json": pig_json,
            "https://example.com/resources/images/pig.png": VALID_PNG_BYTES,
        }
    )
    manifest = {
        "pig_json": build_manifest_meta("pig.json", pig_json),
        "images": [
            {
                "path": "images/pig.png",
                "filename": "pig.png",
                "size": len(VALID_PNG_BYTES) + 1,
                "sha256": "0" * 64,
            }
        ],
    }

    report = await manager._download_manifest_files(
        client,
        manifest_url="https://example.com/resources/manifest.json",
        manifest=manifest,
        staging_dir=staging_dir,
        max_size=1024 * 1024,
    )

    assert staging_dir.joinpath("images", "pig.png").read_bytes() == VALID_PNG_BYTES
    assert report.accepted_mismatches == ["images/pig.png"]
    assert report.skipped == []


@pytest.mark.asyncio
async def test_rollpig_resource_sync_skips_invalid_image_mismatch(
    monkeypatch,
    tmp_path,
):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    staging_dir = tmp_path / "staging"
    (staging_dir / "images").mkdir(parents=True)
    pig_json = b'[{"id": "pig", "name": "Pig"}]'
    client = FakeHTTPClient(
        {
            "https://example.com/resources/pig.json": pig_json,
            "https://example.com/resources/images/pig.png": b"<html>error</html>",
        }
    )
    manifest = {
        "pig_json": build_manifest_meta("pig.json", pig_json),
        "images": [
            {
                "path": "images/pig.png",
                "filename": "pig.png",
                "size": 1,
                "sha256": "0" * 64,
            }
        ],
    }

    report = await manager._download_manifest_files(
        client,
        manifest_url="https://example.com/resources/manifest.json",
        manifest=manifest,
        staging_dir=staging_dir,
        max_size=1024 * 1024,
    )

    assert not staging_dir.joinpath("images", "pig.png").exists()
    assert report.accepted_mismatches == []
    assert report.skipped == ["images/pig.png"]


def test_rollpig_partial_resource_state_keeps_retrying(monkeypatch, tmp_path):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    manager._activate_staging_dir(
        staging_dir,
        "v1",
        image_report=module.ImageSyncReport(
            accepted_mismatches=[],
            skipped=["images/pig.png"],
        ),
    )

    state = json.loads(module.STATE_FILE.read_text(encoding="utf-8"))
    assert state["partial"] is True
    assert state["skipped_images"] == ["images/pig.png"]
    assert manager._read_state_version() == "cache"


@pytest.mark.asyncio
async def test_rollpig_resource_sync_force_refreshes_same_version(
    monkeypatch,
    tmp_path,
):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    module.CACHE_ROOT.mkdir(parents=True)
    module.STATE_FILE.write_text(
        json.dumps({"resource_version": "v1"}, ensure_ascii=False),
        encoding="utf-8",
    )
    pig_json = b'[{"id": "pig", "name": "Pig"}]'
    manifest = {
        "resource_version": "v1",
        "pig_json": build_manifest_meta("pig.json", pig_json),
        "images": [],
    }
    client = FakeHTTPClient(
        {
            "https://example.com/resources/manifest.json": json.dumps(
                manifest
            ).encode("utf-8"),
            "https://example.com/resources/pig.json": pig_json,
        }
    )

    monkeypatch.setattr(
        module,
        "get_resource_manifest_url",
        lambda: "https://example.com/resources/manifest.json",
    )
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_: FakeAsyncClientContext(client),
    )

    result = await manager.sync_from_remote(force=True)

    assert result.updated
    assert "https://example.com/resources/pig.json" in client.request_urls


@pytest.mark.asyncio
async def test_rollpig_resource_sync_busy_background_skips(monkeypatch, tmp_path):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    entered = 0
    release = asyncio.Event()

    async def slow_public_sync(*, force=False):
        nonlocal entered
        entered += 1
        await release.wait()
        return module.ResourceSyncResult(
            updated=False,
            skipped=True,
            message="done",
        )

    async def private_sync(*, force=False):
        return module.ResourceSyncResult(updated=False, skipped=True, message="")

    monkeypatch.setattr(manager, "_sync_from_remote_unlocked", slow_public_sync)
    monkeypatch.setattr(manager, "_sync_private_from_remote_unlocked", private_sync)

    first_task = asyncio.create_task(manager.sync_all(force=True))
    await asyncio.sleep(0)
    busy_result, _ = await manager.sync_all(force=True, wait_if_busy=False)
    release.set()
    await first_task

    assert entered == 1
    assert busy_result.skipped
    assert busy_result.message == "已有资源同步任务运行中"


def test_rollpig_public_resource_sync_defaults_to_upstream(monkeypatch):
    default_module = load_rollpig_config_module(monkeypatch, {})
    disabled_url_module = load_rollpig_config_module(
        monkeypatch,
        {"RESOURCE_MANIFEST_URL": "   "},
    )
    custom_module = load_rollpig_config_module(
        monkeypatch,
        {"RESOURCE_MANIFEST_URL": "https://example.com/public.json"},
    )

    assert default_module.get_resource_sync_enabled()
    assert (
        default_module.get_resource_manifest_url()
        == default_module.DEFAULT_RESOURCE_MANIFEST_URL
    )
    assert disabled_url_module.get_resource_manifest_url() is None
    assert custom_module.get_resource_manifest_url() == "https://example.com/public.json"


def test_rollpig_private_resource_url_can_be_disabled(monkeypatch):
    default_module = load_rollpig_config_module(monkeypatch, {})
    disabled_module = load_rollpig_config_module(
        monkeypatch,
        {"PRIVATE_RESOURCE_MANIFEST_URL": "   "},
    )
    custom_module = load_rollpig_config_module(
        monkeypatch,
        {"PRIVATE_RESOURCE_MANIFEST_URL": "https://example.com/private.json"},
    )

    assert (
        default_module.get_private_resource_manifest_url()
        == default_module.DEFAULT_PRIVATE_RESOURCE_MANIFEST_URL
    )
    assert disabled_module.get_private_resource_manifest_url() is None
    assert (
        custom_module.get_private_resource_manifest_url()
        == "https://example.com/private.json"
    )


def test_rollpig_date_str_uses_business_timezone(monkeypatch):
    module = load_rollpig_runtime_module(monkeypatch)
    real_datetime = module.datetime.datetime

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return real_datetime(
                2026,
                4,
                22,
                0,
                30,
                tzinfo=tz,
            )

    monkeypatch.setattr(module.datetime, "datetime", FixedDateTime)

    assert module.rollpig_date_str() == "2026-04-22"
    assert module.rollpig_date_str(-1) == "2026-04-21"
    assert module.rollpig_date_str(1) == "2026-04-23"


@pytest.mark.asyncio
async def test_roast_library_save_is_atomic_and_locked(monkeypatch, tmp_path):
    module, roast_file = load_rollpig_roast_manager_module(monkeypatch, tmp_path)
    manager = module.RoastManager()

    await asyncio.gather(
        manager._save_new_text("pig", "food", "{k} text"),
        manager._save_new_text("pig", "food", "{v} text"),
    )

    saved = json.loads(roast_file.read_text("utf-8"))
    assert set(saved["pig"]["food"]) == {"{k}text", "{v}text"}
    assert not list(tmp_path.glob("*.tmp"))


def test_rollpig_resource_merge_retains_legacy_ids_and_images(monkeypatch, tmp_path):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    builtin_dir = tmp_path / "builtin"
    builtin_image_dir = builtin_dir / "image"
    staging_dir = tmp_path / "staging"
    (staging_dir / "images").mkdir(parents=True)
    builtin_image_dir.mkdir(parents=True)

    builtin_dir.joinpath("pig.json").write_text(
        json.dumps(
            [
                {"id": "old-pig", "name": "旧猪"},
                {"id": "same-pig", "name": "旧同名"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    builtin_dir.joinpath("pig_rules.json").write_text(
        json.dumps({"food_pigs": ["old-pig"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    builtin_image_dir.joinpath("old-pig.png").write_bytes(b"legacy-image")
    staging_dir.joinpath("pig.json").write_text(
        json.dumps(
            [
                {"id": "same-pig", "name": "新同名"},
                {"id": "new-pig", "name": "新猪"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    staging_dir.joinpath("pig_rules.json").write_text(
        json.dumps({"sold_pigs": ["new-pig"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "BUILTIN_PIG_JSON", builtin_dir / "pig.json")
    monkeypatch.setattr(module, "BUILTIN_RULES_JSON", builtin_dir / "pig_rules.json")
    monkeypatch.setattr(module, "BUILTIN_IMAGE_DIR", builtin_image_dir)
    manager = module.RollPigResourceManager()

    manager._merge_with_existing_snapshot(staging_dir)

    merged_pigs = json.loads(staging_dir.joinpath("pig.json").read_text("utf-8"))
    merged_rules = json.loads(staging_dir.joinpath("pig_rules.json").read_text("utf-8"))

    # 云端缺失旧 ID 时仍保留旧资源，防止本地账本历史 pig_id 渲染失效。
    assert [item["id"] for item in merged_pigs] == ["same-pig", "new-pig", "old-pig"]
    assert merged_pigs[0]["name"] == "新同名"
    assert merged_rules["food_pigs"] == ["old-pig"]
    assert merged_rules["sold_pigs"] == ["new-pig"]
    assert staging_dir.joinpath("images", "old-pig.png").read_bytes() == b"legacy-image"


def test_rollpig_private_overlay_adds_pigs_rules_and_image_priority(
    monkeypatch,
    tmp_path,
):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    public_image_dir = tmp_path / "public_images"
    private_dir = tmp_path / "private"
    private_image_dir = private_dir / "images"
    public_image_dir.mkdir()
    private_image_dir.mkdir(parents=True)
    public_image_dir.joinpath("public-pig.png").write_bytes(b"public")
    private_image_dir.joinpath("private-pig.png").write_bytes(b"private")
    private_dir.joinpath("pig.json").write_text(
        json.dumps([{"id": "private-pig", "name": "私有猪"}], ensure_ascii=False),
        encoding="utf-8",
    )
    private_dir.joinpath("pig_rules.json").write_text(
        json.dumps({"sold_pigs": ["private-pig"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    manager.pig_list = [{"id": "public-pig", "name": "公有猪"}]
    manager.pig_map = {"public-pig": manager.pig_list[0]}
    manager.rules = {
        "food_pigs": [],
        "human_pigs": [],
        "eaten_pigs": [],
        "sold_pigs": [],
        "roast_excluded_pigs": [],
    }
    manager.image_dirs = [public_image_dir]

    manager._apply_private_overlay(private_dir, resource_version="private-v1")

    assert manager.get_pig_by_id("private-pig")["name"] == "私有猪"
    assert manager.get_rule_ids("sold_pigs") == ["private-pig"]
    assert manager.find_image_file("private-pig") == (
        private_image_dir / "private-pig.png"
    )
    assert manager.find_image_file("public-pig") == public_image_dir / "public-pig.png"
    assert manager.resource_version.endswith("+private-v1")


def test_rollpig_private_overlay_rejects_duplicate_pig_ids(monkeypatch, tmp_path):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    private_dir.joinpath("pig.json").write_text(
        json.dumps([{"id": "public-pig", "name": "重复猪"}], ensure_ascii=False),
        encoding="utf-8",
    )

    manager.pig_list = [{"id": "public-pig", "name": "公有猪"}]
    manager.pig_map = {"public-pig": manager.pig_list[0]}
    manager.rules = {key: [] for key in module.RULE_KEYS}
    manager.image_dirs = []

    with pytest.raises(ValueError, match="pig_overrides"):
        manager._apply_private_overlay(private_dir, resource_version="private-v1")


def test_rollpig_private_overlay_explicit_overrides(monkeypatch, tmp_path):
    module = load_rollpig_resource_manager_module(monkeypatch, tmp_path)
    manager = module.RollPigResourceManager()
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    private_dir.joinpath("pig.json").write_text("[]", encoding="utf-8")
    private_dir.joinpath("pig_overrides.json").write_text(
        json.dumps(
            [{"id": "public-pig", "name": "覆盖猪", "analysis": "已覆盖"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager.pig_list = [{"id": "public-pig", "name": "公有猪"}]
    manager.pig_map = {"public-pig": manager.pig_list[0]}
    manager.rules = {key: [] for key in module.RULE_KEYS}
    manager.image_dirs = []

    manager._apply_private_overlay(private_dir, resource_version="private-v1")

    assert manager.get_pig_by_id("public-pig")["name"] == "覆盖猪"
    assert manager.get_pig_by_id("public-pig")["analysis"] == "已覆盖"


def test_rollpig_rules_extend_special_shape_ids(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )
    module.pig_resource_manager.rules = {
        "food_pigs": ["cake-pig"],
        "human_pigs": ["guest-human"],
        "eaten_pigs": ["missing-pig"],
        "sold_pigs": ["auctioned-pig"],
        "roast_excluded_pigs": [],
    }

    assert module.is_food_pig({"id": "cake-pig"})
    assert module.is_human_pig({"id": "guest-human"})
    assert module.is_eaten_pig({"id": "missing-pig"})
    assert module.is_sold_pig({"id": "auctioned-pig"})
    assert module.is_sold_pig({"id": "sold-out"})


def test_builtin_special_rule_ids_have_pig_entries(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )

    # pig_rules.json 只做本地分类；缺条目会让账本 ID 被当作异常数据。
    for key in ("food_pigs", "human_pigs", "eaten_pigs", "sold_pigs"):
        assert all(
            module.get_pig_by_id(pig_id) for pig_id in module._read_rule_ids(key)
        )


def test_backfire_roast_skips_terminal_shapes(monkeypatch):
    module = load_rollpig_plugin_module(
        monkeypatch,
        fake_store=object(),
        fake_data_manager=object(),
        group_members=[],
    )
    module.pig_resource_manager.rules = {
        "food_pigs": ["food-pig"],
        "human_pigs": [],
        "eaten_pigs": [],
        "sold_pigs": [],
        "roast_excluded_pigs": [],
    }

    assert module.can_backfire_roast({"id": "pig"})
    assert not module.can_backfire_roast({"id": "human"})
    assert not module.can_backfire_roast({"id": "eaten"})
    assert not module.can_backfire_roast({"id": "sold-out"})
    assert not module.can_backfire_roast({"id": "food-pig"})
