import pytest

from autoapp.llm.base import Usage
from autoapp.llm.cost import UNKNOWN_COST, PriceTable, format_cost
from autoapp.llm.settings import Price


def u(model, task, inp=0, out=0, cached=0):
    return Usage(
        provider="x", model=model, task=task, input_tokens=inp, output_tokens=out, cached_input_tokens=cached
    )


def test_shipped_table_has_documented_models_and_date():
    t = PriceTable.load()
    assert t.checked == "2026-10-07"
    for model in (
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-haiku-4-5",
        "claude-sonnet-5",
        "gemini-3.8-flash",
        "google/gemini-3.8-flash",
        "openai/gpt-6-luna",
        "deepseek/deepseek-v4.1-flash",
        "mistralai/mistral-large-4-0",
    ):
        assert t.price_of(model) is not None, model
    assert t.price_of("claude-sonnet-5-5") == Price(input=2.0, output=10.0, cached_input=0.2)


def test_cost_of_splits_cached_and_uncached_input():
    t = PriceTable.load()
    # design.md evaluation call: 3500 cached + 1500 uncached input, 1500 output
    cost = t.cost_of(u("claude-sonnet-5-5", "evaluate", inp=5000, out=1500, cached=3500))
    assert cost == pytest.approx((1500 * 2.0 + 3500 * 0.2 + 1500 * 10.0) / 1e6)
    assert cost == pytest.approx(0.0187)


def test_cached_price_falls_back_to_input_price():
    t = PriceTable({"m": Price(input=1.0, output=2.0)})
    assert t.cost_of(u("m", "evaluate", inp=1_000_000, cached=500_000)) == pytest.approx(1.0)


def test_overrides_replace_shipped_prices():
    t = PriceTable.load({"claude-sonnet-5-5": Price(input=1.0, output=1.0), "new": Price(input=3, output=4)})
    assert t.cost_of(u("claude-sonnet-5-5", "write", inp=1_000_000)) == pytest.approx(1.0)
    assert t.cost_of(u("new", "write", out=1_000_000)) == pytest.approx(4.0)


def test_priced_run_total_per_task_and_total():
    t = PriceTable.load()
    usages = [
        u("claude-sonnet-5-5", "evaluate", inp=1_000_000),
        u("claude-sonnet-5-5", "evaluate", out=100_000),
        u("claude-sonnet-5-5", "write", out=200_000),
    ]
    s = t.summarize(usages)
    assert s.per_task == {"evaluate": pytest.approx(3.0), "write": pytest.approx(2.0)}
    assert s.total == pytest.approx(5.0)
    assert s.unknown_models == set()


def test_unpriced_model_is_unknown_not_zero():
    t = PriceTable.load()
    assert t.cost_of(u("mystery-model", "evaluate", inp=1000)) is None
    s = t.summarize(
        [u("claude-sonnet-5-5", "evaluate", inp=1000), u("mystery-model", "write", inp=1000)]
    )
    assert s.per_task["write"] is None
    assert s.per_task["evaluate"] is not None
    assert s.total is None
    assert s.unknown_models == {"mystery-model"}
    assert format_cost(s.total) == UNKNOWN_COST == "unknown (no price configured)"


def test_format_cost():
    assert format_cost(0.01234) == "$0.0123"
    assert format_cost(0.0) == "$0.0000"


def test_empty_run_costs_zero():
    assert PriceTable.load().summarize([]).total == 0.0
