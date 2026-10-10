import pytest

from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMCallLimitReached,
    LLMInvalidRequestError,
    LLMOutputError,
    LLMTransientError,
    UsageMeter,
)
from autoapp.llm.fake import FakeLLM
from autoapp.llm.retry import backoff_delay, call_structured, call_text
from tests.llm._helpers import Evaluation

SYSTEM = [Block("rules", stable=True)]
GOOD = {"fit_score": 80, "rationale": "ok"}
BAD = {"rationale": "no score"}  # missing fit_score


def run(llm, meter=None, **kw):
    sleeps: list[float] = []
    meter = meter or UsageMeter(max_calls=None)
    kw.setdefault("sleep", sleeps.append)
    result = call_structured(
        llm, meter, task="evaluate", system=SYSTEM, user="job ad", schema=Evaluation, **kw
    )
    return result, meter, sleeps


def test_missing_field_is_repaired_with_the_error_in_the_prompt():
    llm = FakeLLM(script={"evaluate": [BAD, GOOD]})
    result, meter, _ = run(llm)
    assert result.fit_score == 80
    assert len(llm.calls) == 2
    first, second = llm.calls[0][2], llm.calls[1][2]
    assert first == "job ad"
    assert second.startswith("job ad\n\nYour previous answer was invalid: ")
    assert "fit_score" in second
    assert second.endswith("Reply again with JSON that matches the schema exactly.")
    assert meter.calls == 2  # the failed attempt still counts


def test_two_invalid_responses_fail():
    llm = FakeLLM(script={"evaluate": [BAD, BAD, GOOD]})
    with pytest.raises(LLMOutputError, match="fit_score"):
        run(llm)
    assert len(llm.calls) == 2


def test_rate_limit_then_success_backs_off():
    llm = FakeLLM(
        script={"evaluate": [LLMTransientError("429"), LLMTransientError("429", retry_after=30), GOOD]}
    )
    result, meter, sleeps = run(llm)
    assert result.fit_score == 80
    assert sleeps == [1, 30]  # retry_after wins when larger than 2
    assert meter.calls == 1


def test_transient_gives_up_after_max_retries():
    llm = FakeLLM(script={"evaluate": [LLMTransientError("503")] * 5})
    with pytest.raises(LLMTransientError):
        run(llm, max_retries=2)
    assert len(llm.calls) == 3


def test_backoff_is_exponential_and_capped():
    assert [backoff_delay(i) for i in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert backoff_delay(0, retry_after=500) == 500


@pytest.mark.parametrize("error", [LLMAuthError("401"), LLMInvalidRequestError("400")])
def test_auth_and_invalid_request_stop_immediately(error):
    llm = FakeLLM(script={"evaluate": [error, GOOD]})
    with pytest.raises(type(error)):
        run(llm)
    assert len(llm.calls) == 1


def test_meter_limit_is_checked_before_each_attempt():
    llm = FakeLLM(script={"evaluate": [GOOD, GOOD]})
    meter = UsageMeter(max_calls=1)
    run(llm, meter)
    with pytest.raises(LLMCallLimitReached):
        run(llm, meter)
    assert len(llm.calls) == 1


def test_call_text_retries_transient_errors():
    llm = FakeLLM(script={"write": [LLMTransientError("500"), "Dear Sir"]})
    sleeps: list[float] = []
    meter = UsageMeter(max_calls=None)
    out = call_text(llm, meter, task="write", system=SYSTEM, user="u", sleep=sleeps.append)
    assert out == "Dear Sir" and sleeps == [1] and meter.calls == 1
