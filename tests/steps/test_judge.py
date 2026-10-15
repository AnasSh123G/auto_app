"""Sampled evaluator audits: scheduling, grounding, persistence and safe failure behavior."""

from __future__ import annotations

import json

import pytest

from autoapp.events import RunStats, StatsKeeper
from autoapp.llm.base import LLMAuthError, LLMInvalidRequestError
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext
from autoapp.steps.evaluate import evaluate_jobs
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import verdict


def add(ctx: RunContext, title: str) -> str:
    job_id = ctx.repo.insert_job(
        title=title, company="Muster GmbH", location="Berlin",
        description="Backend Developer role. Python and Docker. Apply at jobs@muster-gmbh.de.",
        search_profile="backend",
    )
    ctx.repo.add_source(job_id, "arbeitsagentur", f"id-{title}")
    return job_id


def judge_result(score: int, agrees: bool) -> dict:
    return {"score": score, "agrees": agrees, "rationale": f"audit {score}", "issues": []}


def configured_ctx(tmp_path, *, max_calls: int = 100) -> RunContext:
    ctx = RunContext.open(make_workspace(tmp_path))
    ctx.config.limits.max_llm_calls_per_run = max_calls
    ctx.config.limits.max_evaluate_per_run = 60
    return ctx


def test_audits_only_successes_twenty_and_forty_with_grounded_prompt_and_usage(tmp_path) -> None:
    ctx = configured_ctx(tmp_path)
    for i in range(41):
        add(ctx, f"Job {i:02d}")
    raw_answer = verdict(85, apply_email="invented@example.invalid")
    evaluator = FakeLLM({"evaluate": [raw_answer] * 41})
    auditor = FakeLLM({"judge": [judge_result(80, True), judge_result(40, False)]})
    ctx.clients.update(evaluate=evaluator, judge=auditor)
    events = []
    stats = RunStats()
    ctx.events.subscribe(events.append)
    ctx.events.subscribe(StatsKeeper(stats))

    summary = evaluate_jobs(ctx)

    assert summary.evaluated == 41 and ctx.evaluation_count == 41
    reviews = ctx.conn.execute(
        "SELECT jr.evaluation_id, jr.score, jr.agrees, jr.payload, e.job_id, j.title "
        "FROM judge_reviews jr JOIN evaluations e ON e.id = jr.evaluation_id "
        "JOIN jobs j ON j.id = e.job_id WHERE jr.run_id = ? ORDER BY jr.id",
        (ctx.run_id,),
    ).fetchall()
    evaluation_ids = [row["id"] for row in ctx.conn.execute("SELECT id FROM evaluations ORDER BY id")]
    assert [(row["evaluation_id"], row["score"], row["agrees"]) for row in reviews] == [
        (evaluation_ids[19], 80, 1), (evaluation_ids[39], 40, 0),
    ]
    assert json.loads(reviews[0]["payload"])["rationale"] == "audit 80"

    assert summary.judge_reviewed == 2 and summary.judge_score == 60
    assert stats.judge_reviewed == 2 and stats.judge_score == 60 and stats.judge_disagreed == 1
    assert any(e.kind == "judge_summary" and "60.0/100" in e.detail for e in events)
    [(_, system, user)] = auditor.calls_for("judge")[:1]
    prompt_system = "\n".join(block.text for block in system)
    prompt_data = json.loads(user)
    assert "Max Mustermann" in prompt_system and "Python-Services" in prompt_system
    assert prompt_data["job"]["title"] == reviews[0]["title"]
    assert "Backend Developer role" in prompt_data["job"]["description"]
    assert prompt_data["raw_evaluation"]["apply_email"] == "invented@example.invalid"
    final_answer = prompt_data["final_evaluation_after_code_rules"]["result"]
    assert final_answer["apply_email"] is None and final_answer["channel"] == "unknown"
    assert prompt_data["raw_evaluation"]["rationale"] == "r"
    assert [usage.task for usage in ctx.meter.usages].count("judge") == 2
    assert ctx.conn.execute("SELECT COUNT(*) FROM llm_usage WHERE run_id = ? AND task = 'judge'",
                            (ctx.run_id,)).fetchone()[0] == 2
    assert [ctx.repo.get_job(row["job_id"])["status"] for row in reviews] == ["approved", "approved"]
    ctx.close()


def test_nineteen_successes_have_no_synthetic_judge_score(tmp_path) -> None:
    ctx = configured_ctx(tmp_path)
    [add(ctx, f"Under {i:02d}") for i in range(19)]
    evaluator = FakeLLM({"evaluate": [verdict(85)] * 19})
    ctx.clients["evaluate"] = evaluator

    summary = evaluate_jobs(ctx)

    assert summary.evaluated == 19 and ctx.evaluation_count == 19
    assert summary.judge_reviewed == 0 and summary.judge_failed == 0 and summary.judge_score is None
    assert ctx.conn.execute("SELECT COUNT(*) FROM judge_reviews").fetchone()[0] == 0
    ctx.close()


def test_failing_evaluations_do_not_advance_sample_count_across_calls(tmp_path) -> None:
    ctx = configured_ctx(tmp_path)
    for i in range(19):
        add(ctx, f"First {i:02d}")
    evaluator = FakeLLM({"evaluate": [verdict(85)] * 19
                         + [LLMInvalidRequestError("bad evaluator request"), verdict(85)]})
    auditor = FakeLLM({"judge": [judge_result(91, True)]})
    ctx.clients.update(evaluate=evaluator, judge=auditor)

    first = evaluate_jobs(ctx)
    assert first.evaluated == 19 and ctx.evaluation_count == 19
    assert ctx.conn.execute("SELECT COUNT(*) FROM judge_reviews").fetchone()[0] == 0

    failed_id = add(ctx, "Failed evaluation")
    twentieth_id = add(ctx, "Twentieth success")
    second = evaluate_jobs(ctx, [failed_id, twentieth_id])
    assert second.failed == 1 and second.evaluated == 1 and ctx.evaluation_count == 20
    assert ctx.repo.get_job(failed_id)["status"] == "evaluation_failed"
    assert ctx.repo.get_job(twentieth_id)["status"] == "approved"
    assert ctx.conn.execute("SELECT evaluation_id FROM judge_reviews").fetchone()[0] == \
        ctx.repo.current_evaluation(twentieth_id)["id"]
    assert second.judge_reviewed == 1 and second.judge_score == 91
    ctx.close()


@pytest.mark.parametrize("failure", ["auth", "budget"])
def test_a_judge_failure_or_exhausted_budget_keeps_evaluator_decision(tmp_path, failure) -> None:
    ctx = configured_ctx(tmp_path, max_calls=20 if failure == "budget" else 100)
    job_ids = [add(ctx, f"Candidate {i:02d}") for i in range(20)]
    evaluator = FakeLLM({"evaluate": [verdict(85)] * 20})
    auditor = FakeLLM({"judge": [LLMAuthError("judge credential rejected")]})
    ctx.clients.update(evaluate=evaluator, judge=auditor)

    summary = evaluate_jobs(ctx)

    assert summary.evaluated == 20 and summary.judge_reviewed == 0 and summary.judge_failed == 1
    assert [ctx.repo.get_job(job_id)["status"] for job_id in job_ids] == ["approved"] * 20
    assert ctx.conn.execute("SELECT COUNT(*) FROM judge_reviews").fetchone()[0] == 0
    if failure == "auth":
        assert len(auditor.calls_for("judge")) == 1
    else:
        assert ctx.meter.calls == 20 and auditor.calls_for("judge") == []
    ctx.close()
