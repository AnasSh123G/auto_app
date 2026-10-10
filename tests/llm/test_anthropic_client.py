import os
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from autoapp.llm.anthropic_client import AnthropicClient
from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMInvalidRequestError,
    LLMOutputError,
    LLMTransientError,
)


class Verdict(BaseModel):
    fit_score: int
    decision: str


def _usage(**kw):
    base = dict(input_tokens=100, output_tokens=50, cache_read_input_tokens=3000, cache_creation_input_tokens=0)
    base.update(kw)
    return SimpleNamespace(**base)


class StubMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(("parse", kwargs))
        if self.error:
            raise self.error
        return self.response

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.error:
            raise self.error
        return self.response


def _client(response=None, error=None, model="claude-sonnet-5-5", effort="low"):
    stub = SimpleNamespace(messages=StubMessages(response, error))
    return AnthropicClient(model=model, api_key="x", effort=effort, client=stub), stub.messages


SYSTEM = [Block("rules + CV", stable=True), Block("profile", stable=True), Block("today's note")]


def test_structured_request_shape_and_usage():
    resp = SimpleNamespace(
        stop_reason="end_turn",
        usage=_usage(),
        content=[SimpleNamespace(type="text", text="{}", parsed_output=Verdict(fit_score=80, decision="apply"))],
    )
    client, msgs = _client(resp)
    out, usage = client.structured(task="evaluate", system=SYSTEM, user="<job_ad>…</job_ad>", schema=Verdict)

    assert out == Verdict(fit_score=80, decision="apply")
    _, kwargs = msgs.calls[0]
    assert kwargs["output_format"] is Verdict
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["output_config"] == {"effort": "low"}
    system = kwargs["system"]
    # Cache breakpoint sits on the last stable block only.
    assert [b.get("cache_control") for b in system] == [None, {"type": "ephemeral"}, None]
    assert kwargs["messages"] == [{"role": "user", "content": "<job_ad>…</job_ad>"}]
    # Normalised: total input includes cache reads/writes.
    assert usage.input_tokens == 3100
    assert usage.cached_input_tokens == 3000
    assert usage.output_tokens == 50
    assert usage.provider == "anthropic" and usage.task == "evaluate"


def test_no_effort_for_haiku():
    resp = SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[SimpleNamespace(type="text", text="hi")])
    client, msgs = _client(resp, model="claude-haiku-4-5", effort="low")
    client.text(task="write", system=SYSTEM, user="x")
    assert "output_config" not in msgs.calls[0][1]


def test_refusal_and_truncation_are_output_errors():
    for stop in ("refusal", "max_tokens"):
        resp = SimpleNamespace(stop_reason=stop, usage=_usage(), content=[])
        client, _ = _client(resp)
        with pytest.raises(LLMOutputError) as exc:
            client.structured(task="evaluate", system=SYSTEM, user="x", schema=Verdict)
        assert exc.value.usage is not None


def test_missing_structured_output():
    resp = SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[SimpleNamespace(type="text", text="x")])
    client, _ = _client(resp)
    with pytest.raises(LLMOutputError):
        client.structured(task="evaluate", system=SYSTEM, user="x", schema=Verdict)


def _status_error(cls, code, headers=None):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(code, request=req, headers=headers or {})
    return cls("boom", response=resp, body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (lambda: _status_error(anthropic.AuthenticationError, 401), LLMAuthError),
        (lambda: _status_error(anthropic.BadRequestError, 400), LLMInvalidRequestError),
        (lambda: _status_error(anthropic.RateLimitError, 429, {"retry-after": "7"}), LLMTransientError),
        (lambda: _status_error(anthropic.InternalServerError, 500), LLMTransientError),
    ],
)
def test_error_mapping(error, expected):
    client, _ = _client(error=error())
    with pytest.raises(expected):
        client.structured(task="evaluate", system=SYSTEM, user="x", schema=Verdict)


def test_rate_limit_carries_retry_after():
    client, _ = _client(error=_status_error(anthropic.RateLimitError, 429, {"retry-after": "7"}))
    with pytest.raises(LLMTransientError) as exc:
        client.text(task="write", system=SYSTEM, user="x")
    assert exc.value.retry_after == 7.0


@pytest.mark.live(env="ANTHROPIC_API_KEY")
def test_live_structured_call():
    client = AnthropicClient(
        model=os.environ.get("AUTOAPP_LIVE_ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
        effort="low",
    )
    out, usage = client.structured(
        task="evaluate",
        system=[Block("You rate job fit. Answer with JSON.", stable=True)],
        user="Candidate: Python backend developer. Job: Python backend developer in Berlin. "
        "Give fit_score 0-100 and decision apply/maybe/skip.",
        schema=Verdict,
    )
    assert 0 <= out.fit_score <= 100
    assert usage.output_tokens > 0
