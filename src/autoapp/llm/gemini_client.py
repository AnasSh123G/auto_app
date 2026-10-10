"""Google Gemini adapter (``google-genai`` SDK)."""

from __future__ import annotations

from typing import Any, TypeVar

import httpx
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from autoapp.llm._json import join_system, parse_model, parse_retry_after
from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMError,
    LLMInvalidRequestError,
    LLMTransientError,
    Usage,
)

T = TypeVar("T", bound=BaseModel)

try:  # the SDK may raise httpx2 transport errors on newer versions
    import httpx2

    _TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (httpx.TransportError, httpx2.TransportError)
except ImportError:  # pragma: no cover
    _TRANSPORT_ERRORS = (httpx.TransportError,)

_THINKING = {
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}


def _retry_after(exc: genai_errors.APIError) -> float | None:
    """Retry hint from the response header or the ``RetryInfo.retryDelay`` error detail."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    value = parse_retry_after(headers) if headers is not None else None
    if value is not None:
        return value
    details = exc.details if isinstance(exc.details, dict) else {}
    for item in (details.get("error") or details).get("details", []) or []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


def map_error(exc: Exception) -> LLMError | None:
    """Translate an SDK / transport exception into a base error; ``None`` if it is not one we know."""
    if isinstance(exc, genai_errors.APIError):
        code = exc.code
        text = f"Gemini API error: {exc}"
        if code in (401, 403) or (code == 400 and "API_KEY_INVALID" in str(exc.details)):
            return LLMAuthError(text)
        if code == 429 or code == 408 or (isinstance(code, int) and code >= 500):
            return LLMTransientError(text, retry_after=_retry_after(exc))
        return LLMInvalidRequestError(text)
    if isinstance(exc, (*_TRANSPORT_ERRORS, TimeoutError, ConnectionError)):
        return LLMTransientError(f"Gemini connection problem: {exc}")
    return None


class GeminiClient:
    """``LLMClient`` for Gemini models via the Google GenAI SDK."""

    provider = "google"

    def __init__(
        self, *, model: str, api_key: str, effort: str | None = None, client: Any | None = None
    ) -> None:
        self.model = model
        self.effort = effort
        self._client = client if client is not None else _make_client(api_key)

    def _config(self, system: list[Block], **extra: Any) -> types.GenerateContentConfig:
        thinking = (
            types.ThinkingConfig(thinking_level=_THINKING[self.effort])
            if self.effort in _THINKING
            else None
        )
        return types.GenerateContentConfig(
            system_instruction=join_system(system) or None, thinking_config=thinking, **extra
        )

    def _generate(self, user: str, config: types.GenerateContentConfig) -> Any:
        try:
            return self._client.models.generate_content(model=self.model, contents=user, config=config)
        except Exception as exc:
            mapped = map_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    def _usage(self, response: Any, task: str) -> Usage:
        """``input_tokens`` = prompt tokens including cached ones (Gemini reports them that way);
        output includes thinking tokens."""
        m = getattr(response, "usage_metadata", None)
        return Usage(
            provider=self.provider,
            model=self.model,
            task=task,
            input_tokens=(getattr(m, "prompt_token_count", 0) or 0),
            output_tokens=(getattr(m, "candidates_token_count", 0) or 0)
            + (getattr(m, "thoughts_token_count", 0) or 0),
            cached_input_tokens=(getattr(m, "cached_content_token_count", 0) or 0),
        )

    def structured(
        self, *, task: str, system: list[Block], user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        config = self._config(
            system, response_mime_type="application/json", response_schema=schema
        )
        response = self._generate(user, config)
        usage = self._usage(response, task)
        return parse_model(getattr(response, "text", None), schema, usage), usage

    def text(
        self, *, task: str, system: list[Block], user: str, max_tokens: int = 4000
    ) -> tuple[str, Usage]:
        response = self._generate(user, self._config(system, max_output_tokens=max_tokens))
        return (getattr(response, "text", None) or ""), self._usage(response, task)


def _make_client(api_key: str) -> Any:
    from google import genai

    return genai.Client(api_key=api_key)
