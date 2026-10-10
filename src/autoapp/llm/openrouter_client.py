"""OpenRouter adapter (``openai`` SDK pointed at OpenRouter)."""

from __future__ import annotations

import json
from typing import Any, TypeVar

import openai
from pydantic import BaseModel

from autoapp.llm._json import join_system, parse_model, parse_retry_after, strict_schema
from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMError,
    LLMInvalidRequestError,
    LLMTransientError,
    Usage,
)

T = TypeVar("T", bound=BaseModel)

BASE_URL = "https://openrouter.ai/api/v1"


def map_error(exc: Exception) -> LLMError | None:
    """Translate an ``openai`` SDK exception into a base error; ``None`` if unknown."""
    if isinstance(exc, openai.APIStatusError):
        code = exc.status_code
        text = f"OpenRouter error {code}: {exc.message}"
        if code in (401, 403):
            return LLMAuthError(text)
        if code in (408, 429) or code >= 500:
            return LLMTransientError(text, retry_after=parse_retry_after(exc.response.headers))
        return LLMInvalidRequestError(text)
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return LLMTransientError(f"OpenRouter connection problem: {exc}")
    return None


def _rejects_json_schema(exc: Exception) -> bool:
    """True if a 400 says the model/provider does not support ``json_schema`` response format."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    text = f"{exc.message} {exc.body}".lower()
    return "response_format" in text or "json_schema" in text or "structured output" in text


class OpenRouterClient:
    """``LLMClient`` for any model reachable through OpenRouter."""

    provider = "openrouter"

    def __init__(
        self, *, model: str, api_key: str, effort: str | None = None, client: Any | None = None
    ) -> None:
        self.model = model
        self.effort = effort
        # max_retries=0: retrying is done once, by autoapp.llm.retry.
        self._client = (
            client
            if client is not None
            else openai.OpenAI(base_url=BASE_URL, api_key=api_key, max_retries=0)
        )

    def _raw(self, messages: list[dict[str, Any]], **extra: Any) -> Any:
        """One SDK call, SDK exceptions unmapped."""
        if self.effort:
            extra["extra_body"] = {"reasoning": {"effort": self.effort}}
        return self._client.chat.completions.create(model=self.model, messages=messages, **extra)

    def _create(self, messages: list[dict[str, Any]], **extra: Any) -> Any:
        """One SDK call with SDK exceptions mapped to base errors."""
        try:
            return self._raw(messages, **extra)
        except Exception as exc:
            mapped = map_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    def _usage(self, response: Any, task: str) -> Usage:
        """``input_tokens`` = prompt tokens including cached ones (OpenAI/OpenRouter convention)."""
        u = getattr(response, "usage", None)
        details = getattr(u, "prompt_tokens_details", None)
        return Usage(
            provider=self.provider,
            model=self.model,
            task=task,
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            cached_input_tokens=getattr(details, "cached_tokens", 0) or 0,
            cache_write_tokens=getattr(details, "cache_write_tokens", 0) or 0,
        )

    @staticmethod
    def _content(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        return choices[0].message.content if choices else None

    def _system_message(self, system: list[Block]) -> dict[str, Any]:
        """System message; Anthropic models behind OpenRouter get a ``cache_control`` marker on the stable prefix."""
        if self.model.startswith("anthropic/"):
            stable = [b for b in system if b.stable]
            rest = [b for b in system if not b.stable]
            parts: list[dict[str, Any]] = []
            if stable:
                parts.append(
                    {
                        "type": "text",
                        "text": "\n\n".join(b.text for b in stable),
                        "cache_control": {"type": "ephemeral"},
                    }
                )
            if rest:
                parts.append({"type": "text", "text": "\n\n".join(b.text for b in rest)})
            if parts:
                return {"role": "system", "content": parts}
        return {"role": "system", "content": join_system(system)}

    def structured(
        self, *, task: str, system: list[Block], user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        json_schema = {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)}
        messages = [self._system_message(system), {"role": "user", "content": user}]
        try:
            response = self._raw(
                messages, response_format={"type": "json_schema", "json_schema": json_schema}
            )
        except Exception as exc:
            if not _rejects_json_schema(exc):
                mapped = map_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc
            # Model without json_schema support: json_object mode, schema in the system prompt.
            fallback = (
                join_system(system)
                + "\n\nReply with a single JSON object that conforms exactly to this JSON schema, "
                "with no other text:\n"
                + json.dumps(schema.model_json_schema())
            )
            response = self._create(
                [{"role": "system", "content": fallback}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
            )
        usage = self._usage(response, task)
        return parse_model(self._content(response), schema, usage), usage

    def text(
        self, *, task: str, system: list[Block], user: str, max_tokens: int = 4000
    ) -> tuple[str, Usage]:
        messages = [self._system_message(system), {"role": "user", "content": user}]
        response = self._create(messages, max_tokens=max_tokens)
        return (self._content(response) or ""), self._usage(response, task)
