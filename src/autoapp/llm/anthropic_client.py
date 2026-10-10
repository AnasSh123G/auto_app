"""Anthropic (Claude) adapter.

Structured output uses ``client.messages.parse(output_format=<pydantic model>)``,
which constrains the response to the schema server-side. The stable system blocks
get a ``cache_control`` breakpoint so the CV/profile prefix is cached across a run.

Usage is normalised to the app-wide convention (see ``autoapp.llm.cost``):
``input_tokens`` counts *all* input tokens including cache reads and writes, and
``cached_input_tokens`` is the cache-read part. Anthropic reports ``input_tokens``
excluding both, so they are added back here.
"""

from __future__ import annotations

from typing import Any

import anthropic
import pydantic

from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMInvalidRequestError,
    LLMOutputError,
    LLMTransientError,
    T,
    Usage,
)

STRUCTURED_MAX_TOKENS = 16000

# Models that reject the ``effort`` setting.
_NO_EFFORT_PREFIXES = ("claude-haiku-4", "claude-3")


def _system_param(system: list[Block]) -> list[dict[str, Any]]:
    """Text blocks in order, with one cache breakpoint after the last stable block."""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": b.text} for b in system if b.text]
    stable_idx = [i for i, b in enumerate(b for b in system if b.text) if b.stable]
    if stable_idx:
        blocks[stable_idx[-1]]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _retry_after(err: anthropic.APIStatusError) -> float | None:
    try:
        value = err.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _map_error(err: Exception) -> Exception:
    if isinstance(err, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LLMAuthError(f"Anthropic rejected the credentials: {err}")
    if isinstance(err, anthropic.RateLimitError):
        return LLMTransientError(f"Anthropic rate limit: {err}", retry_after=_retry_after(err))
    if isinstance(err, anthropic.APITimeoutError | anthropic.APIConnectionError):
        return LLMTransientError(f"Anthropic connection problem: {err}")
    if isinstance(err, anthropic.APIStatusError):
        if err.status_code >= 500 or err.status_code in (408, 409, 429, 529):
            return LLMTransientError(f"Anthropic server error {err.status_code}: {err}", retry_after=_retry_after(err))
        return LLMInvalidRequestError(f"Anthropic rejected the request ({err.status_code}): {err}")
    return err


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, *, model: str, api_key: str, effort: str | None = None, client: Any | None = None) -> None:
        self.model = model
        self.effort = effort
        # Retries are done by autoapp.llm.retry so every attempt is metered; disable the SDK's own.
        self._client = client or anthropic.Anthropic(api_key=api_key, max_retries=0)

    def _usage(self, task: str, response: Any) -> Usage:
        u = getattr(response, "usage", None)
        cache_read = (getattr(u, "cache_read_input_tokens", None) or 0) if u else 0
        cache_write = (getattr(u, "cache_creation_input_tokens", None) or 0) if u else 0
        return Usage(
            provider=self.provider,
            model=self.model,
            task=task,
            input_tokens=((getattr(u, "input_tokens", 0) or 0) if u else 0) + cache_read + cache_write,
            output_tokens=(getattr(u, "output_tokens", 0) or 0) if u else 0,
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    def _common(self, system: list[Block], user: str, max_tokens: int) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": _system_param(system),
            "messages": [{"role": "user", "content": user}],
        }
        if self.effort and not self.model.startswith(_NO_EFFORT_PREFIXES):
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    @staticmethod
    def _check_stop(response: Any, usage: Usage) -> None:
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise LLMOutputError("Claude declined to answer this request (stop_reason=refusal)", usage=usage)
        if stop == "max_tokens":
            raise LLMOutputError("The response was cut off at max_tokens before it was complete", usage=usage)

    def structured(self, *, task: str, system: list[Block], user: str, schema: type[T]) -> tuple[T, Usage]:
        try:
            response = self._client.messages.parse(
                **self._common(system, user, STRUCTURED_MAX_TOKENS), output_format=schema
            )
        except (pydantic.ValidationError, ValueError) as err:
            # The SDK validates while parsing; we have no response object and thus no usage here.
            raise LLMOutputError(f"Response did not match the schema: {err}") from err
        except anthropic.AnthropicError as err:
            raise _map_error(err) from err
        usage = self._usage(task, response)
        self._check_stop(response, usage)
        for block in response.content:
            parsed = getattr(block, "parsed_output", None)
            if getattr(block, "type", None) == "text" and parsed is not None:
                if isinstance(parsed, schema):
                    return parsed, usage
                try:
                    return schema.model_validate(parsed), usage
                except pydantic.ValidationError as err:
                    raise LLMOutputError(f"Response did not match the schema: {err}", usage=usage) from err
        raise LLMOutputError("Response contained no structured output", usage=usage)

    def text(self, *, task: str, system: list[Block], user: str, max_tokens: int = 4000) -> tuple[str, Usage]:
        try:
            response = self._client.messages.create(**self._common(system, user, max_tokens))
        except anthropic.AnthropicError as err:
            raise _map_error(err) from err
        usage = self._usage(task, response)
        self._check_stop(response, usage)
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        return text, usage
