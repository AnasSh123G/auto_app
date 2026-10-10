import json
import os
from types import SimpleNamespace as NS

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from autoapp.llm.base import (
    Block,
    LLMAuthError,
    LLMClient,
    LLMInvalidRequestError,
    LLMOutputError,
    LLMTransientError,
)
from autoapp.llm.gemini_client import GeminiClient
from tests.llm._helpers import Evaluation

SYSTEM = [Block("variable part"), Block("stable rules", stable=True)]


class StubModels:
    def __init__(self, result):
        self.result = result
        self.requests: list[dict] = []

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make(result, effort="low"):
    models = StubModels(result)
    client = GeminiClient(
        model="gemini-3.8-flash", api_key="k", effort=effort, client=NS(models=models)
    )
    return client, models


def response(text, prompt=1000, cand=50, thoughts=30, cached=600):
    return NS(
        text=text,
        usage_metadata=NS(
            prompt_token_count=prompt,
            candidates_token_count=cand,
            thoughts_token_count=thoughts,
            cached_content_token_count=cached,
        ),
    )


def test_structured_request_shape_and_usage():
    client, models = make(response(json.dumps({"fit_score": 7, "rationale": "r"})), effort="medium")
    assert isinstance(client, LLMClient) and client.provider == "google"
    obj, usage = client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert obj == Evaluation(fit_score=7, rationale="r")
    (req,) = models.requests
    assert req["model"] == "gemini-3.8-flash" and req["contents"] == "ad"
    cfg = req["config"]
    assert isinstance(cfg, types.GenerateContentConfig)
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is Evaluation
    assert cfg.system_instruction == "stable rules\n\nvariable part"  # stable first
    assert cfg.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (1000, 80, 600)
    assert (usage.provider, usage.model, usage.task) == ("google", "gemini-3.8-flash", "evaluate")


def test_text_call():
    client, models = make(response("Hello"), effort=None)
    out, usage = client.text(task="write", system=SYSTEM, user="u", max_tokens=123)
    assert out == "Hello" and usage.task == "write"
    cfg = models.requests[0]["config"]
    assert cfg.max_output_tokens == 123 and cfg.response_mime_type is None
    assert cfg.thinking_config is None


def test_invalid_json_and_schema_mismatch_raise_output_error_with_usage():
    client, _ = make(response('{"rationale": "no score"}'))
    with pytest.raises(LLMOutputError, match="fit_score") as info:
        client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)
    assert info.value.usage is not None and info.value.usage.input_tokens == 1000
    client, _ = make(response("not json"))
    with pytest.raises(LLMOutputError):
        client.structured(task="evaluate", system=SYSTEM, user="ad", schema=Evaluation)


def api_error(code, status, **extra):
    return genai_errors.APIError(
        code, {"error": {"code": code, "message": "boom", "status": status, **extra}}
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (api_error(401, "UNAUTHENTICATED"), LLMAuthError),
        (api_error(403, "PERMISSION_DENIED"), LLMAuthError),
        (api_error(400, "INVALID_ARGUMENT", details=[{"reason": "API_KEY_INVALID"}]), LLMAuthError),
        (api_error(400, "INVALID_ARGUMENT"), LLMInvalidRequestError),
        (api_error(404, "NOT_FOUND"), LLMInvalidRequestError),
        (api_error(429, "RESOURCE_EXHAUSTED"), LLMTransientError),
        (api_error(503, "UNAVAILABLE"), LLMTransientError),
        (TimeoutError("slow"), LLMTransientError),
    ],
)
def test_error_mapping(exc, expected):
    client, _ = make(exc)
    with pytest.raises(expected):
        client.text(task="write", system=SYSTEM, user="u")


def test_retry_delay_is_carried():
    exc = api_error(429, "RESOURCE_EXHAUSTED", details=[{"retryDelay": "7s"}])
    client, _ = make(exc)
    with pytest.raises(LLMTransientError) as info:
        client.text(task="write", system=SYSTEM, user="u")
    assert info.value.retry_after == 7.0


def test_unknown_exceptions_propagate_unchanged():
    client, _ = make(ValueError("bug"))
    with pytest.raises(ValueError, match="bug"):
        client.text(task="write", system=SYSTEM, user="u")


@pytest.mark.live(env="GOOGLE_API_KEY")
@pytest.mark.skipif(not os.environ.get("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY not set")
def test_live_tiny_structured_call():
    client = GeminiClient(
        model=os.environ.get("AUTOAPP_LIVE_GEMINI_MODEL", "gemini-3.8-flash"),
        api_key=os.environ["GOOGLE_API_KEY"],
        effort="low",
    )
    obj, usage = client.structured(
        task="evaluate",
        system=[Block("Rate the fit of the job for a Python developer from 0 to 100.", stable=True)],
        user="Job: Senior Python developer, FastAPI.",
        schema=Evaluation,
    )
    assert 0 <= obj.fit_score <= 100 and usage.input_tokens > 0
