import json
import os
from types import SimpleNamespace as NS

import httpx2
import openai
import pytest

from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMClient,
    LLMInvalidRequestError,
    LLMOutputError,
    LLMTransientError,
)
from autoapp.llm.openrouter_client import OpenRouterClient
from tests.llm._helpers import Evaluation

SYSTEM = [Block("variable"), Block("stable rules", stable=True)]
GOOD = json.dumps({"fit_score": 5, "rationale": "r"})


class StubCompletions:
    def __init__(self, results):
        self.results = list(results)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(*results, model="openai/gpt-6-luna", effort="low"):
    comp = StubCompletions(results)
    client = OpenRouterClient(
        model=model, api_key="k", effort=effort, client=NS(chat=NS(completions=comp))
    )
    return client, comp


def completion(content=GOOD, prompt=1000, out=80, cached=400):
    return NS(
        choices=[NS(message=NS(content=content))],
        usage=NS(
            prompt_tokens=prompt,
            completion_tokens=out,
            prompt_tokens_details=NS(cached_tokens=cached),
        ),
    )


def status_error(cls, code, message="boom", headers=None, body=None):
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return cls(message, response=httpx2.Response(code, headers=headers, request=request), body=body)


def test_json_schema_mode_request_shape_and_usage():
    client, comp = make(completion())
    assert isinstance(client, LLMClient) and client.provider == "openrouter"
    obj, usage = client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert obj.fit_score == 5
    (req,) = comp.requests
    assert req["model"] == "openai/gpt-6-luna"
    assert req["messages"] == [
        {"role": "system", "content": "stable rules\n\nvariable"},
        {"role": "user", "content": "ad"},
    ]
    assert req["extra_body"] == {"reasoning": {"effort": "low"}}
    rf = req["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "Evaluation"
    schema = rf["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["fit_score", "rationale", "requirements"]
    nested = schema["$defs"]["Requirement"]
    assert nested["additionalProperties"] is False and nested["required"] == ["text", "core"]
    assert "default" not in json.dumps(schema)
    assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (1000, 80, 400)


def test_falls_back_to_json_object_when_json_schema_is_rejected():
    rejected = status_error(
        openai.BadRequestError, 400, "Provider does not support response_format json_schema"
    )
    client, comp = make(rejected, completion())
    obj, _ = client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert obj.fit_score == 5
    assert len(comp.requests) == 2
    second = comp.requests[1]
    assert second["response_format"] == {"type": "json_object"}
    system_text = second["messages"][0]["content"]
    assert "stable rules" in system_text and '"fit_score"' in system_text
    assert second["messages"][1] == {"role": "user", "content": "ad"}


def test_other_400_is_an_invalid_request_not_a_fallback():
    client, comp = make(status_error(openai.BadRequestError, 400, "context too long"))
    with pytest.raises(LLMInvalidRequestError):
        client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert len(comp.requests) == 1


def test_text_call_and_no_effort():
    client, comp = make(completion("Hi"), effort=None)
    out, usage = client.text(task="write", system=SYSTEM, user="u", max_tokens=99)
    assert out == "Hi" and usage.task == "write"
    req = comp.requests[0]
    assert req["max_tokens"] == 99 and "extra_body" not in req and "response_format" not in req


def test_anthropic_models_get_cache_control_on_the_stable_block():
    client, comp = make(completion(), model="anthropic/claude-sonnet-5-5")
    client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    content = comp.requests[0]["messages"][0]["content"]
    assert content[0] == {
        "type": "text",
        "text": "stable rules",
        "cache_control": {"type": "ephemeral"},
    }
    assert content[1] == {"type": "text", "text": "variable"}


def test_invalid_output_carries_usage():
    client, _ = make(completion('{"rationale": "x"}'))
    with pytest.raises(LLMOutputError, match="fit_score") as info:
        client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert info.value.usage.output_tokens == 80
    client, _ = make(completion(None))
    with pytest.raises(LLMOutputError):
        client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)


def test_fenced_json_is_accepted():
    client, _ = make(completion(f"```json\n{GOOD}\n```"))
    obj, _ = client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert obj.fit_score == 5


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (status_error(openai.AuthenticationError, 401), LLMAuthError),
        (status_error(openai.PermissionDeniedError, 403), LLMAuthError),
        (status_error(openai.NotFoundError, 404), LLMInvalidRequestError),
        (status_error(openai.UnprocessableEntityError, 422), LLMInvalidRequestError),
        (status_error(openai.RateLimitError, 429), LLMTransientError),
        (status_error(openai.InternalServerError, 502), LLMTransientError),
        (
            openai.APITimeoutError(httpx2.Request("POST", "https://openrouter.ai")),
            LLMTransientError,
        ),
        (
            openai.APIConnectionError(request=httpx2.Request("POST", "https://openrouter.ai")),
            LLMTransientError,
        ),
    ],
)
def test_error_mapping(exc, expected):
    for call in ("structured", "text"):
        client, _ = make(exc)
        with pytest.raises(expected):
            if call == "structured":
                client.structured(task="evaluate", system=SYSTEM, user="u", schema=Evaluation)
            else:
                client.text(task="write", system=SYSTEM, user="u")


def test_retry_after_header_is_carried():
    exc = status_error(openai.RateLimitError, 429, headers={"retry-after": "12"})
    client, _ = make(exc)
    with pytest.raises(LLMTransientError) as info:
        client.text(task="write", system=SYSTEM, user="u")
    assert info.value.retry_after == 12.0


@pytest.mark.live(env="OPENROUTER_API_KEY")
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_live_tiny_structured_call():
    client = OpenRouterClient(
        model=os.environ.get("AUTOAPP_LIVE_OPENROUTER_MODEL", "openai/gpt-6-luna"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        effort="low",
    )
    obj, usage = client.structured(
        task="evaluate",
        system=[Block("Rate the fit of the job for a Python developer from 0 to 100.", stable=True)],
        user="Job: Senior Python developer, FastAPI.",
        schema=Evaluation,
    )
    assert 0 <= obj.fit_score <= 100 and usage.input_tokens > 0
