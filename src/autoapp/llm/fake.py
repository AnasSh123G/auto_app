"""Scripted ``LLMClient`` for tests."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from autoapp.llm.base import Block, LLMOutputError, Usage

T = TypeVar("T", bound=BaseModel)

# A script item: an object to return, an exception to raise, or a callable
# ``(task, system, user, schema) -> object`` (it may raise).
ScriptItem = Any


class FakeLLM:
    """Returns scripted results per task, in order, and records every call.

    ``script`` maps a task name to a list of items. Each call to that task consumes
    one item: an exception instance is raised, a callable is invoked with
    ``(task, system, user, schema)``, a dict is validated against the schema (a mismatch
    raises ``LLMOutputError``), anything else is returned as is. A task whose
    list is exhausted (or that has none) raises ``AssertionError``.
    """

    def __init__(
        self,
        script: dict[str, list[ScriptItem]] | None = None,
        *,
        provider: str = "fake",
        model: str = "fake-model",
        input_tokens: int = 100,
        output_tokens: int = 20,
    ) -> None:
        self.provider = provider
        self.model = model
        self.script = {task: list(items) for task, items in (script or {}).items()}
        self.calls: list[tuple[str, list[Block], str]] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def _next(self, task: str, system: list[Block], user: str, schema: type[BaseModel] | None) -> Any:
        self.calls.append((task, system, user))
        queue = self.script.get(task)
        if not queue:
            raise AssertionError(f"FakeLLM script for task {task!r} is exhausted (call #{len(self.calls)})")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            if isinstance(item, LLMOutputError) and item.usage is None:
                item.usage = self._usage(task)
            raise item
        if callable(item) and not isinstance(item, (BaseModel, type)):
            item = item(task, system, user, schema)
        return item

    def _usage(self, task: str) -> Usage:
        return Usage(
            provider=self.provider,
            model=self.model,
            task=task,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )

    def calls_for(self, task: str) -> list[tuple[str, list[Block], str]]:
        """Recorded calls of one task."""
        return [c for c in self.calls if c[0] == task]

    def structured(
        self, *, task: str, system: list[Block], user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        obj = self._next(task, system, user, schema)
        usage = self._usage(task)
        if isinstance(obj, dict):
            try:
                obj = schema.model_validate(obj)
            except ValidationError as exc:
                raise LLMOutputError(f"response does not match the schema: {exc}", usage=usage) from exc
        return obj, usage

    def text(
        self, *, task: str, system: list[Block], user: str, max_tokens: int = 4000
    ) -> tuple[str, Usage]:
        return str(self._next(task, system, user, None)), self._usage(task)
