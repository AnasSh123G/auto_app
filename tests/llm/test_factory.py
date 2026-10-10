import sys
import types

import pytest

from autoapp.llm.base import LLMError
from autoapp.llm.factory import LLMConfigError, build_client, check_keys
from autoapp.llm.gemini_client import GeminiClient
from autoapp.llm.openrouter_client import OpenRouterClient
from autoapp.llm.settings import LLMSettings

ENV = {"GOOGLE_API_KEY": "g", "OPENROUTER_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}


def test_per_task_override_and_default_fallback(monkeypatch):
    class StubAnthropic:
        provider = "anthropic"

        def __init__(self, *, model, api_key, effort=None, client=None):
            self.model, self.api_key, self.effort = model, api_key, effort

    module = types.ModuleType("autoapp.llm.anthropic_client")
    module.AnthropicClient = StubAnthropic
    monkeypatch.setitem(sys.modules, "autoapp.llm.anthropic_client", module)

    settings = LLMSettings(
        provider="anthropic",
        model="claude-sonnet-5-5",
        tasks={"evaluate": {"provider": "google", "model": "gemini-3.8-flash", "effort": "high"}},
    )
    write = build_client(settings, "write", ENV)
    assert isinstance(write, StubAnthropic)
    assert (write.model, write.api_key, write.effort) == ("claude-sonnet-5-5", "a", "medium")
    evaluate = build_client(settings, "evaluate", ENV)
    assert isinstance(evaluate, GeminiClient)
    assert (evaluate.model, evaluate.effort) == ("gemini-3.8-flash", "high")


def test_openrouter_branch():
    settings = LLMSettings(provider="openrouter", model="openai/gpt-6-luna")
    client = build_client(settings, "form_fill", ENV)
    assert isinstance(client, OpenRouterClient)
    assert client.provider == "openrouter" and client.effort == "low"


def test_missing_google_key_names_the_variable():
    settings = LLMSettings(provider="google", model="gemini-3.8-flash")
    with pytest.raises(LLMConfigError, match="GOOGLE_API_KEY is not set") as info:
        build_client(settings, "evaluate", {})
    assert "'google'" in str(info.value)
    assert isinstance(info.value, LLMError)


def test_empty_key_counts_as_missing():
    settings = LLMSettings(provider="openrouter", model="m")
    with pytest.raises(LLMConfigError, match="OPENROUTER_API_KEY"):
        build_client(settings, "write", {"OPENROUTER_API_KEY": "  "})


def test_check_keys_lists_all_missing_across_tasks():
    settings = LLMSettings(
        provider="anthropic",
        model="claude-sonnet-5-5",
        tasks={
            "evaluate": {"provider": "google", "model": "gemini-3.8-flash"},
            "write": {"provider": "openrouter", "model": "openai/gpt-6-luna"},
        },
    )
    assert check_keys(settings, {}) == ["GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"]
    assert check_keys(settings, {"GOOGLE_API_KEY": "x"}) == ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"]
    assert check_keys(settings, ENV) == []
    # only the default provider is used when no task overrides exist
    assert check_keys(LLMSettings(provider="google", model="m"), {}) == ["GOOGLE_API_KEY"]
