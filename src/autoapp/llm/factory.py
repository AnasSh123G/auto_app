"""Build the right ``LLMClient`` for a task from the ``llm`` settings."""

from __future__ import annotations

import os
from collections.abc import Mapping

from autoapp.llm.base import TASKS, LLMClient, LLMError
from autoapp.llm.settings import API_KEY_ENV, LLMSettings


class LLMConfigError(LLMError):
    """The LLM configuration cannot be used (e.g. a missing API key)."""


def _missing_message(provider: str) -> str:
    return f"{API_KEY_ENV[provider]} is not set (needed for llm provider '{provider}')"


def build_client(
    settings: LLMSettings, task: str, env: Mapping[str, str] = os.environ
) -> LLMClient:
    """Resolve provider/model/effort for ``task`` and construct its client."""
    provider, model, effort = settings.resolve(task)
    key = (env.get(API_KEY_ENV[provider]) or "").strip()
    if not key:
        raise LLMConfigError(_missing_message(provider))
    if provider == "anthropic":
        from autoapp.llm.anthropic_client import AnthropicClient

        return AnthropicClient(model=model, api_key=key, effort=effort)
    if provider == "google":
        from autoapp.llm.gemini_client import GeminiClient

        return GeminiClient(model=model, api_key=key, effort=effort)
    if provider == "openrouter":
        from autoapp.llm.openrouter_client import OpenRouterClient

        return OpenRouterClient(model=model, api_key=key, effort=effort)
    raise LLMConfigError(f"unknown llm provider '{provider}'")


def check_keys(settings: LLMSettings, env: Mapping[str, str] = os.environ) -> list[str]:
    """Names of all missing/empty API key env vars needed by any task (for startup validation)."""
    missing: list[str] = []
    for task in TASKS:
        provider = settings.resolve(task)[0]
        var = API_KEY_ENV[provider]
        if not (env.get(var) or "").strip() and var not in missing:
            missing.append(var)
    return missing
