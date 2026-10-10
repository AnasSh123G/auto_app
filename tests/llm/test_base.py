import pytest

from autoapp.llm.base import (
    Block,
    LLMCallLimitReached,
    MemoryUsageRecorder,
    Usage,
    UsageMeter,
)


def _usage() -> Usage:
    return Usage(provider="fake", model="m", task="evaluate", input_tokens=10, output_tokens=5)


def test_limit_stops_the_41st_call():
    recorder = MemoryUsageRecorder()
    meter = UsageMeter(max_calls=40, recorder=recorder)
    for _ in range(40):
        meter.check()
        meter.add(_usage())
    assert meter.exhausted
    assert meter.remaining == 0
    with pytest.raises(LLMCallLimitReached):
        meter.check()
    assert len(recorder.rows) == 40


def test_no_limit():
    meter = UsageMeter(max_calls=None)
    for _ in range(100):
        meter.check()
        meter.add(_usage())
    assert meter.remaining is None
    assert not meter.exhausted


def test_negative_limit_rejected():
    with pytest.raises(ValueError):
        UsageMeter(max_calls=-1)


def test_block_defaults_to_variable():
    assert Block("x").stable is False
    assert Block("x", stable=True).stable is True


def test_cost_limit_stops_the_meter_and_says_so():
    prices = {"m": 1.0}  # one dollar per call
    meter = UsageMeter(max_calls=None, max_cost=2.5, cost_of=lambda u: prices.get(u.model))
    for _ in range(3):
        meter.check()
        meter.add(_usage())
    assert meter.cost == 3.0 and meter.exhausted and meter.cost_exhausted
    with pytest.raises(LLMCallLimitReached, match=r"cost limit of \$2\.50.*\$3\.00 spent"):
        meter.check()


def test_calls_without_a_price_are_listed_and_never_block():
    meter = UsageMeter(max_calls=None, max_cost=0.01, cost_of=lambda u: None)
    for _ in range(5):
        meter.check()
        meter.add(_usage())
    assert not meter.exhausted and meter.unpriced == {_usage().model}


def test_zero_cost_limit_allows_no_calls_and_negative_is_rejected():
    assert UsageMeter(max_calls=None, max_cost=0.0, cost_of=lambda u: 0.0).exhausted
    with pytest.raises(ValueError):
        UsageMeter(max_calls=None, max_cost=-1)
