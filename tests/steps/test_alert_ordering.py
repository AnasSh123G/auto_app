import pytest

from autoapp.db.status import Status
from autoapp.llm.base import UsageMeter
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext
from autoapp.steps.evaluate import evaluate_jobs
from autoapp.steps.prepare import prepare_jobs
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import add, verdict
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory


@pytest.fixture
def ctx(tmp_path):
    context = RunContext.open(make_workspace(tmp_path))
    yield context
    context.close()


def ordered_jobs(ctx, approved=False):
    make = approved_job if approved else add
    ids = [make(ctx, title=f"Job {i}") for i in range(5)]
    # Use explicit timestamps so order does not depend on random IDs within one second.
    for i, jid in enumerate(ids):
        ctx.conn.execute("DELETE FROM job_sources WHERE job_id = ?", (jid,))
        ctx.conn.execute("UPDATE jobs SET discovered_at = ? WHERE id = ?", (f"2026-10-01T00:00:0{i}+00:00", jid))
        ctx.repo.add_source(jid, "adzuna", f"api-{jid}")
    ctx.repo.add_source(ids[1], "stepstone-alert-recommended", f"alert-{ids[1]}")
    ctx.repo.add_source(ids[3], "indeed-match", f"alert-{ids[3]}")
    ctx.conn.commit()
    return ids


@pytest.mark.parametrize("explicit", [False, True])
def test_evaluate_alerts_before_limit_and_preserve_explicit_order(ctx, explicit):
    ids = ordered_jobs(ctx)
    ctx.config.limits.max_evaluate_per_run = 3
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)] * 3})
    summary = evaluate_jobs(ctx, ids if explicit else None)
    expected = ids[:3] if explicit else [ids[1], ids[3], ids[0]]
    calls = ctx.clients["evaluate"].calls
    assert [call[2].split("Title: ", 1)[1].split("\n", 1)[0] for call in calls] == [
        ctx.repo.get_job(jid)["title"] for jid in expected
    ]
    assert summary.evaluated == 3 and summary.waiting == 2
    assert {j["id"] for j in ctx.repo.jobs_by_status(Status.APPROVED)} == set(expected)


@pytest.mark.parametrize("explicit", [False, True])
def test_prepare_alerts_before_limit_and_preserve_explicit_order(ctx, explicit):
    ids = ordered_jobs(ctx, approved=True)
    ctx.config.limits.max_prepare_per_run = 3
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 3})
    summary = prepare_jobs(ctx, ids if explicit else None, renderer_factory=factory(FakeRenderer([1])))
    expected = ids[:3] if explicit else [ids[1], ids[3], ids[0]]
    assert summary.prepared_ids == expected
    assert summary.waiting == 2


def test_all_alert_sources_stably_classified(ctx):
    ids = [add(ctx, title=f"Job {i}") for i in range(6)]
    for jid, source in zip(ids[1:], [
        "linkedin-alert", "indeed-alert", "indeed-match", "stepstone-alert", "stepstone-alert-recommended",
    ], strict=True):
        ctx.repo.add_source(jid, source, f"alert-{jid}")
    supplied = [ctx.repo.get_job(jid) for jid in ids]
    assert [j["id"] for j in ctx.repo.alert_jobs_first(supplied)] == ids[1:] + ids[:1]


def test_interrupted_evaluation_handles_alerts_first(ctx):
    ids = ordered_jobs(ctx)

    def interrupt(*args):
        raise KeyboardInterrupt

    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85), verdict(85), interrupt]})
    with pytest.raises(KeyboardInterrupt):
        evaluate_jobs(ctx)
    assert {j["id"] for j in ctx.repo.jobs_by_status(Status.APPROVED)} == {ids[1], ids[3]}
    assert {j["id"] for j in ctx.repo.jobs_by_status(Status.NEW)} == {ids[0], ids[2], ids[4]}


def test_evaluation_budget_leaves_approved_jobs_waiting_for_prepare(ctx):
    ids = ordered_jobs(ctx)
    ctx._meter = UsageMeter(1)
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)]})
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    evaluation = evaluate_jobs(ctx)
    assert evaluation.evaluated == 1 and evaluation.waiting == 4 and evaluation.stopped
    preparation = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert preparation.prepared == 0 and preparation.waiting == 1 and preparation.stopped
    assert ctx.repo.get_job(ids[1])["status"] == "approved"
    assert ctx.meter.calls == 1 and ctx.clients["write"].calls == []
