"""Helpers shared by the provider adapters."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from autoapp.llm.base import Block, LLMOutputError, Usage

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def join_system(blocks: list[Block]) -> str:
    """Concatenate system blocks, stable ones first (order otherwise preserved).

    Stable text first and byte-identical keeps provider-side prefix caching effective.
    """
    ordered = [b for b in blocks if b.stable] + [b for b in blocks if not b.stable]
    return "\n\n".join(b.text for b in ordered)


def parse_model[T: BaseModel](text: str | None, schema: type[T], usage: Usage) -> T:
    """Validate JSON ``text`` against ``schema``; raise ``LLMOutputError`` (with usage) on failure."""
    if text is None or not text.strip():
        raise LLMOutputError("the model returned an empty response", usage=usage)
    m = _FENCE.match(text)
    body = m.group(1) if m else text
    try:
        return schema.model_validate_json(body)
    except ValidationError as exc:
        raise LLMOutputError(f"response does not match the schema: {exc}", usage=usage) from exc


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON schema of ``schema`` made compatible with strict structured outputs.

    Every object gets ``additionalProperties: false`` and all its properties required;
    ``default`` keywords are dropped (not allowed in strict mode).
    """
    out = copy.deepcopy(schema.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                props = node.get("properties", {})
                node["properties"] = props
                node["required"] = list(props)
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(out)
    return out


def parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    """Seconds to wait from ``retry-after-ms`` / ``retry-after`` headers, if present."""
    if not headers:
        return None
    try:
        ms = headers.get("retry-after-ms")
        if ms is not None:
            return max(float(ms) / 1000, 0.0)
        value = headers.get("retry-after")
    except (AttributeError, ValueError):
        return None
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)
