"""Prepare step: ``approved`` jobs → complete application folders → ``prepared``.

Folder contents follow the application-package spec. The letter is written by
``LetterWriter``; if the rendered PDF exceeds one page it is rewritten once shorter, and
flagged ``needs_review`` if it still does not fit.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from autoapp.db.repo import now_iso
from autoapp.db.status import Status
from autoapp.evaluation.schema import FinalEvaluation
from autoapp.llm.base import LLMAuthError, LLMCallLimitReached, LLMError
from autoapp.package import render as R
from autoapp.package.documents_select import select_documents
from autoapp.package.eml import build_application_email, write_eml
from autoapp.package.pdf import PdfRenderer, bundle
from autoapp.package.salary_expectation import compute_expectation, needs_salary_note
from autoapp.package.slug import folder_name
from autoapp.salary import resolve_salary_target
from autoapp.steps.context import RunContext, profile_prompt_text
from autoapp.writing.letter_format import date_line
from autoapp.writing.prompt import JobContext, WritingContext
from autoapp.writing.schema import CoverLetter
from autoapp.writing.writer import LetterFrame, LetterWriter, StyleSettings, WritingOutcome

BUNDLE_NAME = "Bewerbungsmappe.pdf"
_ASKS_SALARY = re.compile(
    r"gehaltsvorstellung|gehaltswunsch|gehaltsvorstellungen|salary expectation|desired salary|expected salary",
    re.I,
)


@dataclass
class PrepareSummary:
    prepared: int = 0
    prepared_ids: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    flagged: int = 0
    waiting: int = 0
    failed: list[str] = field(default_factory=list)
    stopped: str | None = None
    form_outcomes: dict[str, int] = field(default_factory=dict)


def ad_asks_salary(description: str | None) -> bool:
    return bool(_ASKS_SALARY.search(description or ""))


def salary_sentence(amount: int, language: str) -> str:
    formatted = f"{amount:,}".replace(",", ".")
    if language == "de":
        return f"Meine Gehaltsvorstellung liegt bei {formatted} EUR brutto im Jahr."
    return f"My salary expectation is EUR {formatted} gross per year."


def _copy(src: Path, folder: Path) -> Path:
    dest = folder / src.name
    shutil.copy2(src, dest)
    return dest


def _is_complete(ctx: RunContext, job_id: str, folder: Path) -> bool:
    """A folder left by an interrupted run (no README, no ``prepared_at``) is rebuilt, not skipped."""
    app = ctx.repo.get_application(job_id)
    return (folder / "README.md").exists() and app is not None and bool(app["prepared_at"])


def _folder_for(ctx: RunContext, job) -> Path:
    """The job's folder: the one already recorded (its name may carry a form-status marker), else a new plain name."""
    app = ctx.repo.get_application(job["id"])
    if app and app["folder"] and Path(app["folder"]).is_dir():
        return Path(app["folder"])
    return ctx.ws.applications_dir / folder_name(job["company"], job["title"], job["id"])


def _sample_jobs(ctx: RunContext) -> list:
    """The quick test: the best evaluated jobs whatever their score, one email and one form application first.

    A job below the apply threshold (review, skipped) is approved here, in the test workspace only, so that letter
    writing and form finding are exercised.
    """
    repo = ctx.repo
    sample = ctx.sample
    assert sample is not None
    pool = []
    for job in repo.jobs_by_status(Status.APPROVED, Status.REVIEW, Status.SKIPPED):
        ev = repo.current_evaluation(job["id"])
        if ev is None:
            continue
        try:
            channel = json.loads(ev["payload"] or "{}").get("channel")
        except (json.JSONDecodeError, TypeError):
            channel = None
        pool.append((job, ev["fit_score"] or 0, channel))
    pool.sort(key=lambda item: -item[1])
    picked: list = []
    for wanted in (("email",), ("employer_form", "platform")):  # one of each kind first
        match = next((p for p in pool if p[2] in wanted and p not in picked), None)
        if match:
            picked.append(match)
    picked += [p for p in pool if p not in picked]
    jobs = []
    for job, score, _ in picked[:sample.preparations]:
        if job["status"] != Status.APPROVED.value:
            repo.transition(job["id"], Status.APPROVED, note=f"quick test: prepared below the threshold (fit score {score})")
        jobs.append(repo.get_job(job["id"]))
    return jobs


def prepare_jobs(
    ctx: RunContext,
    job_ids: list[str] | None = None,
    *,
    force: bool = False,
    renderer_factory: Callable[[], AbstractContextManager[PdfRenderer]] = PdfRenderer,
    today: date | None = None,
) -> PrepareSummary:
    summary = PrepareSummary()
    repo = ctx.repo
    if job_ids:
        jobs = [j for j in (repo.get_job(i) for i in job_ids) if j is not None]
        allowed = {Status.APPROVED.value} | ({Status.PREPARED.value} if force else set())
        jobs = [j for j in jobs if j["status"] in allowed]
    elif ctx.sample is not None and ctx.sample.ignore_threshold:
        jobs = _sample_jobs(ctx)
    else:
        jobs = repo.alert_jobs_first(repo.jobs_by_status(Status.APPROVED))
    limit = ctx.config.limits.max_prepare_per_run
    if len(jobs) > limit:
        summary.waiting = len(jobs) - limit
        jobs = jobs[:limit]
    if not jobs:
        return summary

    writer = LetterWriter(ctx.llm("write"), ctx.meter, max_retries=ctx.config.llm.max_retries)
    with renderer_factory() as renderer:
        for i, job in enumerate(jobs):
            folder = _folder_for(ctx, job)
            complete = folder.exists() and _is_complete(ctx, job["id"], folder)
            if complete and not force:
                summary.skipped_existing.append(job["id"])
                continue
            # An unfinished folder (interrupted run, or a job sent back by a redo) is rebuilt in place; its old
            # letter is kept with a timestamp, like with force.
            try:
                flagged = _prepare_one(ctx, job, folder, writer, renderer, today or date.today(),
                                       force or folder.exists())
            except (LLMCallLimitReached, LLMAuthError) as err:
                summary.stopped = str(err)
                summary.waiting += len(jobs) - i
                break
            except LLMError as err:
                summary.failed.append(f"{job['id']}: {err}")
                ctx.emit("error", job, detail=f"preparation failed: {str(err)[:160]}")
                continue
            summary.prepared += 1
            summary.prepared_ids.append(job["id"])
            summary.flagged += int(flagged)
            app = repo.get_application(job["id"])
            if app and app["form_status"]:
                summary.form_outcomes[app["form_status"]] = summary.form_outcomes.get(app["form_status"], 0) + 1
            saved = Path(app["folder"]) if app and app["folder"] else folder
            ctx.emit("folder_saved", job, detail=saved.name, channel=app["channel"] if app else None)
            if flagged:
                fresh = repo.get_job(job["id"])
                reasons = "; ".join(json.loads(fresh["needs_review_reasons"] or "[]")) if fresh else ""
                ctx.emit("needs_review", job, detail=reasons[:200] or "needs review")
    return summary


def _write_letter(writer: LetterWriter, wctx: WritingContext, jctx: JobContext, frame: LetterFrame,
                  style: StyleSettings, html_for: Callable[[CoverLetter], str], renderer: PdfRenderer,
                  pdf_path: Path) -> tuple[WritingOutcome, list[str]]:
    outcome = writer.write(wctx, jctx, frame, style)
    pages = renderer.html_to_pdf(html_for(outcome.letter), pdf_path)
    extra: list[str] = []
    if pages > 1:
        outcome = writer.shorten(wctx, jctx, frame, style)
        pages = renderer.html_to_pdf(html_for(outcome.letter), pdf_path)
        if pages > 1:
            extra.append(f"the cover letter is {pages} pages long; shorten it and run `autoapp render`")
    return outcome, extra


def _prepare_one(ctx: RunContext, job, folder: Path, writer: LetterWriter, renderer: PdfRenderer,
                 today: date, force: bool) -> bool:
    repo = ctx.repo
    ev_row = repo.current_evaluation(job["id"])
    final = FinalEvaluation.model_validate(json.loads(ev_row["payload"]))
    r = final.result
    if r.channel != "email":
        from autoapp.formfinder.store import resolve

        resolve(ctx, job)
        job = repo.get_job(job["id"])
    language = ctx.config.writing.language if ctx.config.writing.language != "auto" else r.language
    profile = ctx.profile
    personal = profile.personal

    # Salary: only an employer-stated range takes part (never platform estimates).
    target = resolve_salary_target(profile, r.seniority, job["location"])
    expectation = compute_expectation(target, job["ad_salary_min"], job["ad_salary_max"])
    asks = ad_asks_salary(job["description"])

    docs = ctx.documents
    selection = select_documents(profile, language, list(r.requested_documents),
                                 ctx.config.writing.always_attach_certificates)
    sp = ctx.search_profile(job["search_profile"])

    samples = [docs.texts[e.file] for e in docs.by_role("writing_sample") if e.file in docs.texts]
    cv_text = docs.texts.get(selection.cv.file) if selection.cv else docs.cv_text(language)
    wctx = WritingContext(profile_prompt_text(profile), cv_text or docs.cv_text(),
                          ctx.writing_instructions, samples, ctx.config.llm.instructions("write"))
    jctx = JobContext(
        title=job["title"], company=job["company"], description=job["description"] or "",
        language=language, requirements=list(r.requirements), decision=final.decision,
        max_words=ctx.config.writing.max_words, max_assumed_skills=ctx.config.writing.max_assumed_skills,
        profile_instructions=sp.writing_instructions if sp else None,
        salary_line=salary_sentence(expectation, language) if (asks and expectation) else None,
        earliest_start=profile.availability.earliest_start,
    )
    frame = LetterFrame(r.contact_person, r.reference, f"{personal.first_name} {personal.last_name}")
    style = StyleSettings(ctx.config.writing.banned_phrases, ctx.config.writing.allowed_phrases)

    if folder.exists() and force:
        old = folder / "cover_letter.md"
        if old.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            shutil.copy2(old, folder / f"cover_letter.{stamp}.md")
    folder.mkdir(parents=True, exist_ok=True)

    recipient = [job["company"]] + ([f"z. Hd. {r.contact_person}"] if r.contact_person and language == "de"
                                    else [f"Attn. {r.contact_person}"] if r.contact_person else [])
    dline = date_line(personal.city, today, language)

    def html_for(letter: CoverLetter) -> str:
        return R.render_cover_letter_html(letter, personal, recipient, dline)

    letter_pdf = folder / "cover_letter.pdf"
    outcome, page_problems = _write_letter(writer, wctx, jctx, frame, style, html_for, renderer, letter_pdf)
    letter = outcome.letter
    (folder / "cover_letter.md").write_text(letter.to_markdown(), encoding="utf-8")

    # Documents
    files: list[str] = ["cover_letter.md", "cover_letter.pdf"]
    cv_copy = _copy(docs.path_of(selection.cv), folder) if selection.cv else None
    cert_copies = [_copy(docs.path_of(e), folder) for e in selection.certificates]
    extra_copies = [_copy(docs.path_of(e), folder) for e in selection.extras]
    if selection.photo:
        extra_copies.append(_copy(docs.path_of(selection.photo), folder))
    files += [p.name for p in ([cv_copy] if cv_copy else []) + cert_copies + extra_copies]

    b = bundle(letter_pdf, cv_copy, cert_copies, folder / BUNDLE_NAME, ctx.config.email.max_bundle_mb)
    files.append(BUNDLE_NAME)

    # Form answers and helper page
    answers = R.FormAnswers(
        sender=personal, country=personal.country, salary_expectation=expectation,
        earliest_start=profile.availability.earliest_start, notice_period=profile.availability.notice_period,
        work_authorization=profile.work_authorization, language_levels=dict(profile.language_levels),
        linkedin_url=personal.linkedin_url, xing_url=personal.xing_url, motivation=letter.motivation_short,
    )
    (folder / "form_answers.md").write_text(R.render_form_answers_md(answers), encoding="utf-8")
    upload = [R.FileItem("Bewerbungsmappe (all in one PDF)", (folder / BUNDLE_NAME).resolve()),
              R.FileItem("Cover letter", letter_pdf.resolve())]
    if cv_copy:
        upload.append(R.FileItem("CV", cv_copy.resolve()))
    upload += [R.FileItem("Certificate", p.resolve()) for p in cert_copies]
    upload += [R.FileItem("Other", p.resolve()) for p in extra_copies]
    from autoapp.sources.arbeitnow import source_link

    sources = repo.sources(job["id"])
    arbeitnow_link = source_link(sources)
    (folder / "apply.html").write_text(
        R.render_apply_html(R.ApplyData(job["title"], job["company"], answers.items(), upload,
                                        job["job_url"], job["application_url"] or r.application_url,
                                        arbeitnow_url=arbeitnow_link)),
        encoding="utf-8",
    )
    files += ["form_answers.md", "apply.html"]

    # Email draft
    channel = r.channel
    if channel == "email" and r.apply_email:
        msg = build_application_email(
            sender_name=f"{personal.first_name} {personal.last_name}", sender_email=personal.email,
            to=r.apply_email, subject=letter.subject, body=letter.email_body, attachment=folder / BUNDLE_NAME,
        )
        write_eml(msg, folder / "email.eml")
        files.append("email.eml")

    # Job and evaluation snapshots
    locations = [job["location"]] if job["location"] else []
    locations += [loc["city"] for loc in repo.locations(job["id"])]
    (folder / "job.md").write_text(R.render_job_md(R.JobInfo(
        title=job["title"], company=job["company"], description=job["description"] or "",
        locations=locations, job_url=job["job_url"], application_url=job["application_url"] or r.application_url,
        sources=sorted({s["source"] for s in sources}), ad_salary_min=job["ad_salary_min"],
        ad_salary_max=job["ad_salary_max"], salary_estimate_min=job["salary_estimate_min"],
        salary_estimate_max=job["salary_estimate_max"],
        arbeitnow_url=arbeitnow_link,
    )), encoding="utf-8")
    (folder / "evaluation.md").write_text(
        R.render_evaluation_md(final, R.EvaluationExtra(job["title"], job["company"])), encoding="utf-8")
    files += ["job.md", "evaluation.md"]

    review = list(outcome.needs_review) + page_problems
    (folder / "README.md").write_text(R.render_readme_md(R.ReadmeData(
        title=job["title"], company=job["company"], channel=channel, job_id=job["id"], files=files,
        apply_email=r.apply_email, application_url=job["application_url"] or r.application_url,
        job_url=job["job_url"], reference=r.reference, missing_documents=selection.missing,
        needs_review_reasons=review, assumed_skills=letter.assumed_skills,
        salary_note=needs_salary_note(expectation, asks), bundle_size_mb=round(b.size_bytes / 1024 / 1024, 2),
        bundle_max_mb=ctx.config.email.max_bundle_mb, bundle_too_large=b.too_large, warnings=list(b.skipped),
        arbeitnow_url=arbeitnow_link,
    )), encoding="utf-8")

    repo.upsert_application(
        job["id"], folder=str(folder), channel=channel, contact_person=r.contact_person,
        contact_email=r.apply_email, reference=r.reference,
        salary_expectation=str(expectation) if expectation else None,
        assumed_skills=letter.assumed_skills, language=language, prepared_at=now_iso(),
    )
    if channel != "email":
        from autoapp.formfinder.finder import Outcome
        from autoapp.formfinder.store import refresh

        application = repo.get_application(job["id"])
        refresh(folder, Outcome(application["form_status"], job["application_url"],
                                application["form_reached_url"], application["form_note"]))
        from autoapp.package.folders import sync_folder_name

        sync_folder_name(ctx, job["id"])
    repo.set_needs_review(job["id"], review)
    if job["status"] != Status.PREPARED.value:
        now_app = repo.get_application(job["id"])
        repo.transition(job["id"], Status.PREPARED, note=f"folder {Path(now_app['folder']).name}")
    return bool(review)
