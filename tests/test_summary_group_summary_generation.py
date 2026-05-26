from __future__ import annotations

from enum import Enum
from importlib import util as importlib_util
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1]
    / "zhenxun"
    / "plugins"
    / "zhenxun_plugin_summary_group"
)


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class _FakeBaseConfig:
    def __init__(self):
        self.values = {
            "SUMMARY_THINKING_MODE": "off",
            "SUMMARY_STRIP_THINKING_FALLBACK": True,
            "summary_output_type": "text",
        }

    def get(self, key: str, default=None):
        return self.values.get(key, default)


class _FakeReasoningEffort(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class _FakeReasoningConfig:
    def __init__(self, *, effort=None, budget_tokens=None, show_thoughts=None):
        self.effort = effort
        self.budget_tokens = budget_tokens
        self.show_thoughts = show_thoughts


class _FakeLLMGenerationConfig:
    def __init__(self):
        self.reasoning = None
        self.custom_params = None


class _FakeLLMException(Exception):
    pass


class _FakeLLMMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    @classmethod
    def system(cls, content: str):
        return cls("system", content)

    @classmethod
    def user(cls, content: str):
        return cls("user", content)


class _FakeModel:
    def __init__(
        self,
        *,
        api_type: str = "openai",
        provider_name: str = "Provider",
        model_name: str = "model",
    ):
        self.api_type = api_type
        self.provider_name = provider_name
        self.model_name = model_name


class _FakeSummaryConfig:
    @staticmethod
    def get_timeout() -> int:
        return 1


@pytest.fixture
def summary_generation(monkeypatch: pytest.MonkeyPatch):
    package_name = f"_summary_generation_test_{id(monkeypatch)}"
    utils_name = f"{package_name}.utils"
    base_config = _FakeBaseConfig()

    package = types.ModuleType(package_name)
    package.__path__ = [str(PLUGIN_ROOT)]
    package.base_config = base_config
    monkeypatch.setitem(sys.modules, package_name, package)

    utils_package = types.ModuleType(utils_name)
    utils_package.__path__ = [str(PLUGIN_ROOT / "utils")]
    monkeypatch.setitem(sys.modules, utils_name, utils_package)

    store_module = types.ModuleType(f"{package_name}.store")

    class FakeStore:
        @staticmethod
        def get_group_setting(*_args, **_kwargs):
            return None

    store_module.store = FakeStore()
    monkeypatch.setitem(sys.modules, f"{package_name}.store", store_module)

    config_module = types.ModuleType(f"{package_name}.config")
    config_module.summary_config = _FakeSummaryConfig()
    monkeypatch.setitem(sys.modules, f"{package_name}.config", config_module)

    message_processing_module = types.ModuleType(f"{utils_name}.message_processing")
    message_processing_module.serialize_messages_for_summary = lambda messages: ""
    monkeypatch.setitem(
        sys.modules,
        f"{utils_name}.message_processing",
        message_processing_module,
    )

    logger_module = types.ModuleType("zhenxun.services.log")
    logger_module.logger = _FakeLogger()
    services_module = types.ModuleType("zhenxun.services")
    services_module.__path__ = []
    llm_module = types.ModuleType("zhenxun.services.llm")
    llm_module.LLMException = _FakeLLMException
    llm_module.LLMGenerationConfig = _FakeLLMGenerationConfig
    llm_module.LLMMessage = _FakeLLMMessage
    llm_module.get_global_default_model_name = lambda: None
    llm_module.get_model_instance = None
    llm_module.list_available_models = lambda: []
    llm_config_module = types.ModuleType("zhenxun.services.llm.config")
    llm_config_module.__path__ = []
    llm_generation_module = types.ModuleType("zhenxun.services.llm.config.generation")
    llm_generation_module.ReasoningConfig = _FakeReasoningConfig
    llm_generation_module.ReasoningEffort = _FakeReasoningEffort
    monkeypatch.setitem(sys.modules, "zhenxun.services", services_module)
    monkeypatch.setitem(sys.modules, "zhenxun.services.llm", llm_module)
    monkeypatch.setitem(sys.modules, "zhenxun.services.llm.config", llm_config_module)
    monkeypatch.setitem(
        sys.modules,
        "zhenxun.services.llm.config.generation",
        llm_generation_module,
    )
    monkeypatch.setitem(sys.modules, "zhenxun.services.log", logger_module)

    spec = importlib_util.spec_from_file_location(
        f"{utils_name}.summary_generation",
        PLUGIN_ROOT / "utils" / "summary_generation.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, f"{utils_name}.summary_generation", module)
    spec.loader.exec_module(module)

    return module, base_config


@pytest.mark.asyncio
async def test_summary_prompt_does_not_request_visible_thinking(
    summary_generation, monkeypatch: pytest.MonkeyPatch
):
    module, _base_config = summary_generation
    captured: dict[str, object] = {}

    class FakeResponse:
        text = "最终总结"
        thought_text = "内部思考"

    class FakeContextModel(_FakeModel):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate_response(self, messages, config=None, timeout=None):
            captured["messages"] = messages
            captured["config"] = config
            captured["timeout"] = timeout
            return FakeResponse()

    async def fake_get_model_instance(_model_name):
        return FakeContextModel(
            api_type="gemini",
            provider_name="Gemini",
            model_name="gemini-3.5-flash",
        )

    monkeypatch.setattr(module, "get_model_instance", fake_get_model_instance)

    result = await module.messages_summary(
        target=SimpleNamespace(id="123", private=True),
        messages=[object()],
        model_name="Gemini/gemini-3.5-flash",
    )

    system_prompt = captured["messages"][0].content

    assert result.summary_text == "最终总结"
    assert "高强度的深度思考流程" not in system_prompt
    assert "并将你的思考内容放在" not in system_prompt
    assert "<think>" not in system_prompt
    assert "只输出最终总结" in system_prompt


def test_gemini_default_disables_visible_thoughts(summary_generation):
    module, _base_config = summary_generation
    model = _FakeModel(
        api_type="gemini",
        provider_name="Gemini",
        model_name="gemini-3.5-flash",
    )

    config = module._build_summary_generation_config(model, "Gemini/gemini-3.5-flash")

    assert config.reasoning is not None
    assert config.reasoning.show_thoughts is False
    assert config.reasoning.effort is None


def test_gemini_high_keeps_thoughts_hidden(summary_generation):
    module, base_config = summary_generation
    base_config.values["SUMMARY_THINKING_MODE"] = "high"
    model = _FakeModel(
        api_type="gemini",
        provider_name="Gemini",
        model_name="gemini-3.5-flash",
    )

    config = module._build_summary_generation_config(model, "Gemini/gemini-3.5-flash")

    assert config.reasoning is not None
    assert config.reasoning.effort == _FakeReasoningEffort.HIGH
    assert config.reasoning.show_thoughts is False


def test_deepseek_default_disables_thinking(summary_generation):
    module, _base_config = summary_generation
    model = _FakeModel(
        api_type="openai",
        provider_name="DeepSeek",
        model_name="deepseek-v4-flash",
    )

    config = module._build_summary_generation_config(
        model, "DeepSeek/deepseek-v4-flash"
    )

    assert config.custom_params == {"thinking": {"type": "disabled"}}


def test_deepseek_enabled_mode_keeps_answer_field_only(summary_generation):
    module, base_config = summary_generation
    base_config.values["SUMMARY_THINKING_MODE"] = "medium"
    model = _FakeModel(
        api_type="deepseek",
        provider_name="DeepSeek",
        model_name="deepseek-v4-flash",
    )

    config = module._build_summary_generation_config(
        model, "DeepSeek/deepseek-v4-flash"
    )

    assert config.custom_params == {"thinking": {"type": "enabled"}}


def test_strip_visible_thinking_handles_case_and_attributes(summary_generation):
    module, _base_config = summary_generation
    text = "开头<THINK data-x='1'>内部\n思考</THINK>结果</think>"

    assert module._strip_visible_thinking(text) == "开头结果"


def test_strip_visible_thinking_can_be_disabled(summary_generation):
    module, base_config = summary_generation
    base_config.values["SUMMARY_STRIP_THINKING_FALLBACK"] = False
    text = "<think>保留</think> 结果 "

    assert module._strip_visible_thinking(text) == "<think>保留</think> 结果"
