import pytest

from autoapp.llm.base import Block, LLMClient, LLMTransientError
from autoapp.llm.fake import FakeLLM
from tests.llm._helpers import Evaluation

SYSTEM = [Block("rules", stable=True)]


def test_returns_scripted_objects_in_order_and_records_calls():
    first = Evaluation(fit_score=1, rationale="a")
    llm = FakeLLM(script={"evaluate": [first, {"fit_score": 2, "rationale": "b"}]})
    assert isinstance(llm, LLMClient)
    obj1, usage = llm.structured(task="evaluate", system=SYSTEM, user="u1", schema=Evaluation)
    obj2, _ = llm.structured(task="evaluate", system=SYSTEM, user="u2", schema=Evaluation)
    assert obj1 is first and obj2.fit_score == 2
    assert usage.task == "evaluate" and usage.input_tokens > 0 and usage.provider == "fake"
    assert [c[2] for c in llm.calls] == ["u1", "u2"]


def test_exceptions_and_callables():
    llm = FakeLLM(
        script={
            "evaluate": [
                LLMTransientError("429"),
                lambda task, system, user, schema: schema(fit_score=len(user), rationale=task),
            ],
            "write": ["Dear team"],
        }
    )
    with pytest.raises(LLMTransientError):
        llm.structured(task="evaluate", system=SYSTEM, user="x", schema=Evaluation)
    obj, _ = llm.structured(task="evaluate", system=SYSTEM, user="four", schema=Evaluation)
    assert obj.fit_score == 4
    assert llm.text(task="write", system=SYSTEM, user="x")[0] == "Dear team"


def test_exhausted_script_raises_assertion_error():
    llm = FakeLLM(script={"evaluate": []})
    with pytest.raises(AssertionError):
        llm.structured(task="evaluate", system=SYSTEM, user="x", schema=Evaluation)
    with pytest.raises(AssertionError):
        llm.text(task="write", system=SYSTEM, user="x")
