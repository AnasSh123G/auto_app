"""OpenRouter prices come from its public model list, are cached for a day, and never beat the user's own."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from autoapp.llm.cost import PriceTable
from autoapp.llm.openrouter_prices import load_cached, parse_models, refresh
from autoapp.llm.settings import LLMSettings, Price

LIST = {"data": [
    {"id": "google/gemini-3.8-flash", "pricing": {"prompt": "0.00000075", "completion": "0.00000375",
                                                  "input_cache_read": "0.000000075", "web_search": "0.014"}},
    {"id": "qwen/qwen3.8-flash", "pricing": {"prompt": "0.00000015", "completion": "0.00000047"}},
    {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
    {"id": "broken/model", "pricing": {"prompt": "free", "completion": "0"}},
    {"id": "no/pricing"},
]}
OPENROUTER = LLMSettings(provider="openrouter", model="google/gemini-3.8-flash")
ANTHROPIC = LLMSettings(provider="anthropic", model="claude-sonnet-5-5")


def client(calls: list, payload=LIST, status=200):
    def handler(request):
        calls.append(str(request.url))
        assert "authorization" not in request.headers  # the public list needs no key
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_prices_are_read_per_million_tokens_and_unusable_entries_are_skipped():
    prices = parse_models(LIST)
    assert prices["google/gemini-3.8-flash"] == Price(input=0.75, output=3.75, cached_input=0.075)
    assert prices["qwen/qwen3.8-flash"] == Price(input=0.15, output=0.47)
    assert set(prices) == {"google/gemini-3.8-flash", "qwen/qwen3.8-flash"}  # "-1" and non-numeric prices left out


def test_fetch_is_cached_for_a_day_and_only_when_openrouter_is_used(tmp_path):
    path, calls = tmp_path / "prices.json", []
    now = datetime(2026, 10, 9, 12, tzinfo=UTC)
    assert refresh(path, ANTHROPIC, now=now, client=client(calls)) is None and calls == []  # no OpenRouter model in use
    assert refresh(path, OPENROUTER, now=now, client=client(calls)) is None and len(calls) == 1
    cached, fetched = load_cached(path)
    assert cached["qwen/qwen3.8-flash"].output == 0.47 and fetched == now
    refresh(path, OPENROUTER, now=now + timedelta(hours=23), client=client(calls))
    assert len(calls) == 1  # still fresh
    refresh(path, OPENROUTER, now=now + timedelta(hours=25), client=client(calls))
    assert len(calls) == 2
    refresh(path, OPENROUTER, now=now + timedelta(hours=26), client=client(calls), force=True)
    assert len(calls) == 3


def test_a_failed_fetch_keeps_the_saved_prices_and_says_so(tmp_path):
    path, calls = tmp_path / "prices.json", []
    now = datetime(2026, 10, 9, 12, tzinfo=UTC)
    refresh(path, OPENROUTER, now=now, client=client(calls))
    before = path.read_text()
    note = refresh(path, OPENROUTER, now=now + timedelta(days=2), client=client(calls, status=503))
    assert note and "keeping the saved ones" in note and path.read_text() == before
    note = refresh(path, OPENROUTER, now=now + timedelta(days=2), client=client(calls, payload={"data": []}))
    assert note and path.read_text() == before
    (tmp_path / "bad.json").write_text("{not json")
    assert load_cached(tmp_path / "bad.json") == ({}, None)
    assert load_cached(tmp_path / "missing.json") == ({}, None)


def test_price_order_is_shipped_then_fetched_then_your_config():
    fetched = parse_models(LIST)
    table = PriceTable.load({"qwen/qwen3.8-flash": Price(input=9, output=9)}, fetched)
    assert table.price_of("google/gemini-3.8-flash").input == 0.75  # fetched beats the shipped table (0.75 too, but from OpenRouter)
    assert table.price_of("qwen/qwen3.8-flash").input == 9  # the configuration always wins
    assert table.price_of("claude-sonnet-5-5") is not None  # the shipped table is the fallback
    cached = json.dumps({"fetched_at": "2026-10-09T12:00:00+00:00", "models": {"a/b": {"input": 1, "output": 2}}})
    assert json.loads(cached)["models"]["a/b"]["output"] == 2
