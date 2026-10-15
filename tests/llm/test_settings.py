import pytest
from pydantic import ValidationError

from autoapp.llm.settings import LLMSettings


def test_defaults_per_task():
    s = LLMSettings()
    assert s.resolve("evaluate") == ("anthropic", "claude-sonnet-5-5", "low")
    assert s.resolve("write") == ("anthropic", "claude-sonnet-5-5", "medium")
    assert s.resolve("judge")[:2] == s.resolve("evaluate")[:2]
    assert s.resolve("judge")[2] == "medium"


def test_task_override():
    s = LLMSettings(tasks={"evaluate": {"provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash"}})
    assert s.resolve("evaluate")[:2] == ("openrouter", "deepseek/deepseek-v4.1-flash")
    assert s.resolve("write")[:2] == ("anthropic", "claude-sonnet-5-5")


def test_other_provider_without_model_rejected():
    with pytest.raises(ValidationError):
        LLMSettings(tasks={"write": {"provider": "google"}})


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        LLMSettings(modle="typo")


def test_every_task_has_its_own_model_and_the_old_single_default_still_loads():
    s = LLMSettings(tasks={
        "evaluate": {"provider": "openrouter", "model": "openai/gpt-6-luna"},
        "write": {"provider": "openrouter", "model": "anthropic/claude-haiku-5.5", "effort": "high"},
        "form_fill": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
        "form_find": {"provider": "openrouter", "model": "openai/gpt-6-luna"},
        "judge": {"provider": "openrouter", "model": "openai/gpt-6-luna"}})
    assert [s.resolve(t) for t in ("evaluate", "write", "form_fill", "form_find", "judge")] == [
        ("openrouter", "openai/gpt-6-luna", "low"), ("openrouter", "anthropic/claude-haiku-5.5", "high"),
        ("anthropic", "claude-sonnet-5-5", "low"), ("openrouter", "openai/gpt-6-luna", "low"),
        ("openrouter", "openai/gpt-6-luna", "medium")]
    # the config of earlier versions: one default plus overrides for some tasks
    old = LLMSettings.model_validate({"provider": "openrouter", "model": "a/b", "effort": "medium",
                                      "tasks": {"evaluate": {"provider": "google", "model": "g"}}})
    assert old.resolve("evaluate") == ("google", "g", "medium")
    assert old.resolve("write") == ("openrouter", "a/b", "medium")
    assert old.resolve("form_fill") == ("openrouter", "a/b", "medium")
    assert old.resolve("form_find") == ("google", "g", "low")  # find form used to follow the evaluate model
    assert old.resolve("judge") == ("google", "g", "medium")
    assert not hasattr(old, "provider")


def test_a_task_needs_a_model_when_it_names_another_provider_than_its_default():
    with pytest.raises(ValidationError, match="needs a model"):
        LLMSettings.model_validate({"provider": "anthropic", "tasks": {"write": {"provider": "openrouter"}}})
    assert LLMSettings.model_validate({"model": "claude-x", "tasks": {"write": {"provider": "anthropic"}}}).resolve("write")[:2] == ("anthropic", "claude-x")
