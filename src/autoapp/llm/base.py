"""Provider-neutral LLM interface.

Every LLM call in the app goes through an ``LLMClient``. Adapters for Anthropic,
Google and OpenRouter implement it; ``FakeLLM`` implements it for tests.

Prompts are split into ``Block``s so adapters can mark the stable prefix
(rules, profile, CV) as cacheable and keep variable content (the job ad) after it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

TASKS = ("evaluate", "write", "form_fill", "form_find", "alert_extract")

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Block:
    """One piece of the system prompt.

    ``stable`` blocks must be byte-identical across calls of a run so providers can
    cache them; put them first.
    """

    text: str
    stable: bool = False


@dataclass
class Usage:
    """Token usage of one call, as reported by the provider."""

    provider: str
    model: str
    task: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class LLMError(Exception):
    """Base class for LLM failures."""


class LLMAuthError(LLMError):
    """Credentials were rejected (HTTP 401/403). Never retried; stops LLM work for the run."""


class LLMInvalidRequestError(LLMError):
    """The request itself is wrong (HTTP 400/404/422). Never retried."""


class LLMTransientError(LLMError):
    """Rate limit or server-side failure (HTTP 429/5xx, timeouts). Retried with backoff."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMOutputError(LLMError):
    """The response could not be parsed or validated against the requested schema.

    ``usage`` carries the tokens the failed attempt consumed so they still count.
    """

    def __init__(self, message: str, *, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class LLMCallLimitReached(LLMError):
    """The per-run call limit is exhausted; remaining items wait for the next run."""


@runtime_checkable
class LLMClient(Protocol):
    """What the rest of the app needs from a model provider."""

    provider: str
    model: str

    def structured(
        self, *, task: str, system: list[Block], user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        """Return an instance of ``schema`` parsed from the model's JSON output."""
        ...

    def text(
        self, *, task: str, system: list[Block], user: str, max_tokens: int = 4000
    ) -> tuple[str, Usage]:
        """Return free text."""
        ...


class UsageRecorder(Protocol):
    """Persists usage rows; the database layer provides the real implementation."""

    def record(self, usage: Usage) -> None: ...


class MemoryUsageRecorder:
    """Keeps usage in memory (tests, dry runs)."""

    def __init__(self) -> None:
        self.rows: list[Usage] = []

    def record(self, usage: Usage) -> None:
        self.rows.append(usage)


class UsageMeter:
    """Enforces the per-run call and cost limits and records every call's usage.

    Callers ask ``check()`` before a call and ``add()`` after it. The meter is
    shared by all clients of a run, so the limits cover evaluate, write,
    form_fill and form_find together. The cost limit (``max_cost``, USD) uses the
    price of each call's model (``cost_of``); a call whose model has no price adds
    nothing and its model is listed in ``unpriced``, so the limit cannot see it.
    """

    def __init__(self, max_calls: int | None, recorder: UsageRecorder | None = None, *,
                 max_cost: float | None = None, cost_of: Callable[[Usage], float | None] | None = None) -> None:
        if max_calls is not None and max_calls < 0:
            raise ValueError("max_calls must be >= 0")
        if max_cost is not None and max_cost < 0:
            raise ValueError("max_cost must be >= 0")
        self.max_calls = max_calls
        self.max_cost = max_cost
        self.cost_of = cost_of
        self.recorder = recorder or MemoryUsageRecorder()
        self.calls = 0
        self.cost = 0.0
        self.unpriced: set[str] = set()
        self.usages: list[Usage] = []

    @property
    def remaining(self) -> int | None:
        return None if self.max_calls is None else max(self.max_calls - self.calls, 0)

    @property
    def cost_exhausted(self) -> bool:
        return self.max_cost is not None and self.cost >= self.max_cost

    @property
    def exhausted(self) -> bool:
        return (self.max_calls is not None and self.calls >= self.max_calls) or self.cost_exhausted

    def check(self) -> None:
        if self.cost_exhausted:
            raise LLMCallLimitReached(
                f"AI cost limit of ${self.max_cost:.2f} per run reached (${self.cost:.2f} spent); "
                "remaining items wait for the next run"
            )
        if self.exhausted:
            raise LLMCallLimitReached(
                f"LLM call limit of {self.max_calls} per run reached; remaining items wait for the next run"
            )

    def add(self, usage: Usage) -> None:
        self.calls += 1
        if self.cost_of is not None:
            cost = self.cost_of(usage)
            if cost is None:
                self.unpriced.add(usage.model)
            else:
                self.cost += cost
        self.usages.append(usage)
        self.recorder.record(usage)
