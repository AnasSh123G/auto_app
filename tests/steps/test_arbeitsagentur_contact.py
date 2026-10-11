"""An Arbeitsagentur job's contact data from the detail call reaches the evaluation (employer-first spec 2d)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from autoapp.llm.fake import FakeLLM
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.arbeitsagentur import ArbeitsagenturSource
from autoapp.steps.context import RunContext
from autoapp.steps.evaluate import evaluate_jobs
from autoapp.steps.search import run_search
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import verdict

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "arbeitsagentur"
SEARCH = json.loads((FIX / "search_data_scientist_berlin.json").read_text(encoding="utf-8"))
DETAIL = json.loads((FIX / "detail_email.json").read_text(encoding="utf-8"))


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def test_the_detail_contact_email_is_stored_and_seen_by_the_evaluation(ctx):
    item = next(i for i in SEARCH["ergebnisliste"] if i["referenznummer"].startswith("14225"))
    search = {**SEARCH, "ergebnisliste": [{k: v for k, v in item.items() if k != "externeURL"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/jobdetails/" in request.url.path:
            assert base64.b64decode(request.url.path.rsplit("/", 1)[1]).decode().startswith("14225")
            return httpx.Response(200, json=DETAIL)
        return httpx.Response(200, json=search)

    source = ArbeitsagenturSource(httpx.Client(transport=httpx.MockTransport(handler)), delay_s=0)
    run_search(ctx, api_sources=[source], mailbox=FakeMailbox([]))
    (job,) = ctx.repo.jobs_by_status("new")
    (row,) = ctx.repo.sources(job["id"])
    stored = json.loads(row["raw"])
    assert "bewerbung@forto.example" in stored["contact_fields"] and "detail" in stored
    seen: list[str] = []

    def answer(task, system, user, schema):
        seen.append(user)
        return schema.model_validate(verdict(85, apply_email="bewerbung@forto.example"))

    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [answer]})
    evaluate_jobs(ctx)
    assert "Structured contact fields from the source" in seen[0] and "bewerbung@forto.example" in seen[0]
    payload = json.loads(ctx.repo.current_evaluation(job["id"])["payload"])
    assert payload["result"]["channel"] == "email"
