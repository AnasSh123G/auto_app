"""Evaluate step: ``new`` jobs with a description → ``skipped`` / ``review`` / ``approved``.

Failures never stop the run: an unparseable answer marks the job
``evaluation_failed``; the call limit leaves the rest ``new`` for the next run; only an
authentication error stops LLM work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from autoapp.db.status import Status
from autoapp.evaluation.judge import SAMPLE_EVERY, judge_evaluation
from autoapp.evaluation.prompt import JobView, build_system, build_user
from autoapp.evaluation.rules import finalize
from autoapp.evaluation.schema import EvaluationResult
from autoapp.llm.base import LLMAuthError, LLMCallLimitReached, LLMError
from autoapp.llm.retry import call_structured
from autoapp.steps.context import RunContext, profile_prompt_text, search_summary

DECISION_STATUS = {"apply": Status.APPROVED, "maybe": Status.REVIEW, "skip": Status.SKIPPED}
_DECISION_EVENT = {"apply": "job_approved", "maybe": "job_maybe", "skip": "job_skipped"}


@dataclass
class EvaluateSummary:
    evaluated: int = 0
    approved: int = 0
    review: int = 0
    skipped: int = 0
    failed: int = 0
    waiting: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: str | None = None
    judge_reviewed: int = 0
    judge_failed: int = 0
    judge_score_total: int = 0

    @property
    def judge_score(self) -> float | None:
        return self.judge_score_total / self.judge_reviewed if self.judge_reviewed else None


def _contact_fields(ctx: RunContext, job_id: str) -> str | None:
    found = []
    for s in ctx.repo.sources(job_id):
        try:
            raw = json.loads(s["raw"] or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and raw.get("contact_fields"):
            found.append(str(raw["contact_fields"]))
    return "\n".join(found) or None


def _title_truncated(ctx: RunContext, job_id: str) -> bool:
    for s in ctx.repo.sources(job_id):
        try:
            if json.loads(s["raw"] or "{}").get("title_truncated"):
                return True
        except (json.JSONDecodeError, AttributeError):
            continue
    return False


def evaluate_jobs(ctx: RunContext, job_ids: list[str] | None = None, *, force: bool = False) -> EvaluateSummary:
    summary = EvaluateSummary()
    repo = ctx.repo
    if job_ids:
        jobs = [j for j in (repo.get_job(i) for i in job_ids) if j is not None]
        if not force:
            jobs = [j for j in jobs if j["status"] == Status.NEW.value]
    else:
        jobs = repo.alert_jobs_first(repo.jobs_by_status(Status.NEW))
    jobs = [j for j in ctx.jobs_for_sources(jobs) if (j["description"] or "").strip()]
    limit = ctx.config.limits.max_evaluate_per_run
    if len(jobs) > limit:
        summary.waiting += len(jobs) - limit
        jobs = jobs[:limit]
    if not jobs:
        return summary

    client = ctx.llm("evaluate")
    system = build_system(profile_prompt_text(ctx.profile), ctx.documents.cv_text(),
                          ctx.config.llm.instructions("evaluate"))
    thresholds = ctx.config.evaluation

    for i, job in enumerate(jobs):
        contact = _contact_fields(ctx, job["id"])
        sources = repo.sources(job["id"])
        view = JobView(
            title=job["title"], company=job["company"], location=job["location"],
            source=sources[0]["source"] if sources else "unknown",
            description=job["description"], contact_fields=contact,
        )
        user = build_user(view, search_summary(ctx.search_profile(job["search_profile"])))
        try:
            result = call_structured(client, ctx.meter, task="evaluate", system=system, user=user,
                                     schema=EvaluationResult, max_retries=ctx.config.llm.max_retries)
        except LLMCallLimitReached as err:
            summary.waiting += len(jobs) - i
            summary.stopped = str(err)
            break
        except LLMAuthError as err:
            summary.waiting += len(jobs) - i
            summary.stopped = str(err)
            summary.errors.append(str(err))
            break
        except LLMError as err:
            summary.failed += 1
            summary.errors.append(f"{job['id']}: {err}")
            ctx.emit("error", job, detail=f"evaluation failed: {str(err)[:160]}")
            if job["status"] != Status.EVALUATION_FAILED.value:
                repo.transition(job["id"], Status.EVALUATION_FAILED, note=str(err)[:500])
            continue

        final = finalize(result, profile=ctx.profile, apply_threshold=thresholds.apply_threshold,
                         maybe_threshold=thresholds.maybe_threshold,
                         source_texts=[job["description"], contact])
        evaluation_id = repo.add_evaluation(job["id"], fit_score=final.fit_score, decision=final.decision,
                            provider=client.provider, model=client.model, payload=final.model_dump(mode="json"))
        updates = {}
        r = final.result
        if r.full_title and _title_truncated(ctx, job["id"]):
            updates["title"] = r.full_title
        if r.work_mode != "unknown" and not job["work_mode"]:
            updates["work_mode"] = r.work_mode
        if r.ad_salary and job["ad_salary_min"] is None and job["ad_salary_max"] is None:
            mult = 12 if r.ad_salary.period == "month" else 1
            updates["ad_salary_min"] = r.ad_salary.min_eur * mult if r.ad_salary.min_eur else None
            updates["ad_salary_max"] = r.ad_salary.max_eur * mult if r.ad_salary.max_eur else None
        if updates:
            repo.update_job(job["id"], **updates)
        target = DECISION_STATUS[final.decision]
        repo.transition(job["id"], target, note=f"fit {final.fit_score}: {final.decision}")
        summary.evaluated += 1
        ctx.emit(_DECISION_EVENT[final.decision], job, detail=f"score {final.fit_score}", score=final.fit_score)
        if final.decision == "apply":
            summary.approved += 1
        elif final.decision == "maybe":
            summary.review += 1
        else:
            summary.skipped += 1
        audit_due = (ctx.evaluation_count + 1) % SAMPLE_EVERY == 0
        audit = judge_evaluation(ctx, job, evaluation_id, result, final, contact)
        if audit:
            summary.judge_reviewed += 1
            summary.judge_score_total += audit.score
        elif audit_due:
            summary.judge_failed += 1
    if summary.judge_reviewed or summary.judge_failed:
        score = f"{summary.judge_score:.1f}/100" if summary.judge_score is not None else "unavailable"
        ctx.emit("judge_summary", detail=f"evaluator performance {score} (mean of {summary.judge_reviewed} "
                 f"sampled reviews; {summary.judge_failed} unavailable; sampling every 20 successful evaluations)")
    return summary
