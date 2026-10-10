"""Cost estimation from recorded token usage and a per-model price table.

Token convention: ``Usage.input_tokens`` is the TOTAL input including cached tokens,
and ``Usage.cached_input_tokens`` is the cached subset of it. Adapters normalise to this:

- Anthropic reports ``input_tokens`` EXCLUDING cache reads/writes, so its adapter adds
  ``cache_read_input_tokens`` (and cache creation tokens) to ``input_tokens``.
- OpenAI / OpenRouter (``prompt_tokens``) and Gemini (``prompt_token_count``) already
  include cached tokens, so they are used as is.

Cost = (input - cached) * input_price + cached * cached_price + output * output_price,
per 1M tokens. The cached price falls back to the input price when unset. Cache-write
tokens are not priced separately (negligible; they stay billed as normal input).
Costs are computed at report time, so a corrected price fixes history too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from ruamel.yaml import YAML

from autoapp.llm.base import Usage
from autoapp.llm.settings import Price

UNKNOWN_COST = "unknown (no price configured)"


def load_default_prices() -> tuple[dict[str, Price], str | None]:
    """Parse the shipped ``prices.yaml``; returns ``(prices, checked_date)``."""
    text = resources.files("autoapp.llm").joinpath("prices.yaml").read_text(encoding="utf-8")
    data: Any = YAML(typ="safe").load(text)
    prices = {
        model: Price(
            input=entry["input"], output=entry["output"], cached_input=entry.get("cached_input")
        )
        for model, entry in data["models"].items()
    }
    checked = data.get("checked")
    return prices, str(checked) if checked is not None else None


@dataclass
class CostSummary:
    """Estimated cost per task and in total; ``None`` means unknown, never zero."""

    per_task: dict[str, float | None] = field(default_factory=dict)
    total: float | None = 0.0
    unknown_models: set[str] = field(default_factory=set)


class PriceTable:
    """Shipped prices plus user overrides, keyed by model ID."""

    def __init__(self, prices: Mapping[str, Price], checked: str | None = None) -> None:
        self.prices = dict(prices)
        self.checked = checked

    @classmethod
    def load(cls, overrides: Mapping[str, Price] | None = None,
             fetched: Mapping[str, Price] | None = None) -> PriceTable:
        """Shipped prices, then prices fetched from OpenRouter, then ``llm.prices`` from the configuration."""
        prices, checked = load_default_prices()
        prices.update(fetched or {})
        prices.update(overrides or {})
        return cls(prices, checked)

    @classmethod
    def for_workspace(cls, ws, overrides: Mapping[str, Price] | None = None) -> PriceTable:  # noqa: ANN001
        from autoapp.llm.openrouter_prices import load_cached

        return cls.load(overrides, load_cached(ws.price_cache_path)[0])

    def price_of(self, model: str) -> Price | None:
        return self.prices.get(model)

    def cost_of(self, usage: Usage) -> float | None:
        """USD cost of one call, or ``None`` when the model has no price."""
        price = self.price_of(usage.model)
        if price is None:
            return None
        cached = min(max(usage.cached_input_tokens, 0), usage.input_tokens)
        uncached = usage.input_tokens - cached
        cached_price = price.cached_input if price.cached_input is not None else price.input
        return (
            uncached * price.input + cached * cached_price + usage.output_tokens * price.output
        ) / 1_000_000

    def summarize(self, usages: Iterable[Usage]) -> CostSummary:
        """Cost per task and in total. A task (or the total) with any unpriced call is ``None``."""
        summary = CostSummary()
        for u in usages:
            cost = self.cost_of(u)
            if cost is None:
                summary.unknown_models.add(u.model)
                summary.per_task[u.task] = None
                summary.total = None
                continue
            if u.task not in summary.per_task:
                summary.per_task[u.task] = 0.0
            if summary.per_task[u.task] is not None:
                summary.per_task[u.task] += cost  # type: ignore[operator]
            if summary.total is not None:
                summary.total += cost
        return summary


def format_cost(value: float | None) -> str:
    """``"$0.0123"``, or the unknown marker for ``None``."""
    return UNKNOWN_COST if value is None else f"${value:.4f}"
