import pytest

from autoapp.db.status import Status
from autoapp.llm.base import LLMAuthError, LLMOutputError
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext, profile_prompt_text
from autoapp.steps.evaluate import evaluate_jobs
from tests.helpers import make_workspace


def verdict(score, decision_hint="", **kw):
    base = dict(
        fit_score=score, rationale="r", requirements=[
            {"text": "Python", "proficiency": "intermediate", "core": True, "status": "matched", "evidence": "Python"},
            {"text": "Grundkenntnisse in Docker", "proficiency": "basic", "core": False, "status": "bridgeable"},
        ],
        dealbreakers=[], seniority="mid", german_level_required="B2", work_mode="hybrid",
        channel="email", apply_email="jobs@muster-gmbh.de", contact_person="Frau Weber", reference="2026-17",
        requested_documents=["cv", "cover_letter", "certificates"], language="de",
    )
    base.update(kw)
    return base


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def add(ctx, title="Backend Developer", desc="Bewerbung an jobs@muster-gmbh.de. Python, Docker."):
    jid = ctx.repo.insert_job(title=title, company="Muster GmbH", location="Berlin", description=desc,
                              search_profile="backend")
    ctx.repo.add_source(jid, "arbeitsagentur", f"id-{title}")
    return jid


def test_paths_and_statuses(ctx):
    a, m, s, f = add(ctx, "A"), add(ctx, "B"), add(ctx, "C"), add(ctx, "D")
    no_desc = ctx.repo.insert_job(title="E", company="X", description=None)
    by_title = {"A": verdict(85), "B": verdict(68), "C": verdict(40), "D": {"bad": 1}}

    def answer(task, system, user, schema):
        title = user.split("Title: ", 1)[1].split("\n", 1)[0]
        item = by_title[title]
        if "bad" in item:
            raise LLMOutputError("missing fit_score")
        return schema.model_validate(item)

    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [answer] * 5})
    s_ = evaluate_jobs(ctx)
    assert (s_.approved, s_.review, s_.skipped, s_.failed) == (1, 1, 1, 1)
    st = {j: ctx.repo.get_job(j)["status"] for j in (a, m, s, f, no_desc)}
    assert st == {a: "approved", m: "review", s: "skipped", f: "evaluation_failed", no_desc: "new"}
    ev = ctx.repo.current_evaluation(a)
    assert ev["fit_score"] == 85 and ev["decision"] == "apply"


def test_prompt_contains_profile_without_private_contact_data(ctx):
    text = profile_prompt_text(ctx.profile)
    assert "Docker: basic" in text and "1234567" not in text and "Musterstraße" not in text


def test_force_reevaluates_and_keeps_history(ctx):
    a = add(ctx)
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85), verdict(50)]})
    evaluate_jobs(ctx)
    evaluate_jobs(ctx, [a], force=True)
    rows = ctx.conn.execute("SELECT fit_score, is_current FROM evaluations WHERE job_id = ? ORDER BY id", (a,)).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(85, 0), (50, 1)]


def test_limit_leaves_rest_new(ctx):
    ctx.config.limits.max_evaluate_per_run = 2
    ids = [add(ctx, f"T{i}") for i in range(3)]
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85), verdict(85)]})
    s = evaluate_jobs(ctx)
    assert s.evaluated == 2 and s.waiting == 1
    assert sorted(ctx.repo.get_job(i)["status"] for i in ids) == ["approved", "approved", Status.NEW.value]


def test_auth_error_stops(ctx):
    add(ctx, "A"), add(ctx, "B")
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [LLMAuthError("401")]})
    s = evaluate_jobs(ctx)
    assert s.stopped and s.waiting == 2 and s.evaluated == 0


def test_hallucinated_email_dropped_in_stored_payload(ctx):
    import json

    a = add(ctx, desc="Bewerbung bitte über unser Portal.")
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)]})
    evaluate_jobs(ctx)
    payload = json.loads(ctx.repo.current_evaluation(a)["payload"])
    assert payload["result"]["apply_email"] is None and payload["result"]["channel"] == "unknown"


def test_cost_limit_stops_evaluating_and_leaves_the_rest_new(ctx):
    from autoapp.llm.settings import Price

    ctx.prices.prices["fake-model"] = Price(input=1_000_000, output=0)  # $1 per input token
    ctx.config.limits.max_cost_per_run_usd = 3.0
    ids = [add(ctx, f"T{i}") for i in range(5)]
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)] * 5})
    s = evaluate_jobs(ctx)
    per_call = ctx.meter.cost / ctx.meter.calls
    assert per_call > 0 and ctx.meter.cost_exhausted
    assert s.evaluated == ctx.meter.calls < 5 and s.waiting == 5 - s.evaluated
    assert "cost limit" in s.stopped
    assert sum(ctx.repo.get_job(i)["status"] == Status.NEW.value for i in ids) == s.waiting
