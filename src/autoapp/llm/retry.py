"""Validation-and-retry wrapper around any ``LLMClient``.

One repair retry for invalid output, exponential backoff for transient errors,
no retry for auth / invalid-request errors. Every attempt that produced usage is
counted on the shared ``UsageMeter``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel

from autoapp.llm.base import (
    Block,
    LLMClient,
    LLMOutputError,
    LLMTransientError,
    Usage,
    UsageMeter,
)

BACKOFF_CAP_SECONDS = 60.0


def backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Delay before retry number ``attempt`` (0-based): 1, 2, 4, ... capped at 60 s; ``retry_after`` wins if larger."""
    delay = min(float(2**attempt), BACKOFF_CAP_SECONDS)
    if retry_after is not None and retry_after > delay:
        delay = retry_after
    return delay


def repair_prompt(user: str, error: LLMOutputError) -> str:
    """The user message of the single repair retry."""
    return (
        f"{user}\n\nYour previous answer was invalid: {error}. "
        "Reply again with JSON that matches the schema exactly."
    )


def _run[T](
    attempt_call: Callable[[str], tuple[T, Usage]],
    meter: UsageMeter,
    user: str,
    max_retries: int,
    sleep: Callable[[float], None],
) -> T:
    repaired = False
    transient_failures = 0
    current_user = user
    while True:
        meter.check()
        try:
            result, usage = attempt_call(current_user)
        except LLMOutputError as exc:
            if exc.usage is not None:
                meter.add(exc.usage)
            if repaired:
                raise
            repaired = True
            current_user = repair_prompt(user, exc)
            continue
        except LLMTransientError as exc:
            if transient_failures >= max_retries:
                raise
            sleep(backoff_delay(transient_failures, exc.retry_after))
            transient_failures += 1
            continue
        # LLMAuthError / LLMInvalidRequestError propagate untouched.
        meter.add(usage)
        return result


def call_structured[M: BaseModel](
    client: LLMClient,
    meter: UsageMeter,
    *,
    task: str,
    system: list[Block],
    user: str,
    schema: type[M],
    max_retries: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> M:
    """Structured call with the full retry policy. Returns the validated object."""
    return _run(
        lambda u: client.structured(task=task, system=system, user=u, schema=schema),
        meter,
        user,
        max_retries,
        sleep,
    )


def call_text(
    client: LLMClient,
    meter: UsageMeter,
    *,
    task: str,
    system: list[Block],
    user: str,
    max_tokens: int = 4000,
    max_retries: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Free-text call; transient errors are retried, there is no output validation."""
    return _run(
        lambda u: client.text(task=task, system=system, user=u, max_tokens=max_tokens),
        meter,
        user,
        max_retries,
        sleep,
    )
