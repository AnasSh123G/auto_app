import html
import json
import re
import sqlite3
from pathlib import Path

from autoapp.assist.filler import is_restricted
from autoapp.config.models import DEFAULT_RESTRICTED_DOMAINS
from autoapp.db.repo import now_iso
from autoapp.formfinder.employer_link import (
    NO_EMPLOYER_LINK,
    ONLY_ON_ARBEITSAGENTUR,
    employer_link,
    is_arbeitsagentur_job,
    is_arbeitsagentur_page,
    outcome_for,
)
from autoapp.formfinder.finder import LABELS, Outcome, find

START = "<!-- autoapp application -->"
END = "<!-- /autoapp application -->"


def entry_url(ctx, job, app=None):
    """Where form finding starts. A restricted platform link (LinkedIn, Indeed, StepStone) gives way to the
    vacancy's own posting when employer discovery matched it; the job's stored data is not changed.
    An Arbeitsagentur job never starts at its Arbeitsagentur (or partner board) page: its entry is the employer's
    address, or None when there is none yet (see ``employer_link``)."""
    if is_arbeitsagentur_job(ctx, job):
        return employer_link(ctx, job, app).entry
    url = _stored_entry(ctx, job, app)
    if not url or is_restricted(url, _restricted_domains(ctx)):
        return _employer_posting(ctx, job) or url
    return url


def _stored_entry(ctx, job, app=None):
    if app and _web(app["form_entry_url"]):
        return app["form_entry_url"]
    if _web(job["application_url"]):
        return job["application_url"]
    evaluation = ctx.repo.current_evaluation(job["id"])
    if evaluation:
        payload = json.loads(evaluation["payload"])
        url = payload.get("result", {}).get("application_url")
        if _web(url):  # the model sometimes answers "unknown"
            return url
    return job["job_url"]


def _web(url) -> bool:
    return isinstance(url, str) and url.strip().lower().startswith(("http://", "https://"))


def _restricted_domains(ctx):
    config = getattr(ctx, "config", None)
    return config.assist.restricted_domains if config is not None else DEFAULT_RESTRICTED_DOMAINS


def _employer_posting(ctx, job):
    try:
        row = ctx.repo.conn.execute("SELECT posting_url FROM employer_seeds WHERE job_id = ? AND outcome = 'matched'",
                                    (job["id"],)).fetchone()
    except sqlite3.OperationalError:  # a workspace from before employer discovery (read-only inspection)
        return None
    return row["posting_url"] if row and row["posting_url"] else None


def _status_text(outcome: Outcome) -> str:
    """``Form status`` text: the note already says what is missing for a job without an employer link."""
    return outcome.note if outcome.status == NO_EMPLOYER_LINK else f"{LABELS[outcome.status]}: {outcome.note}"


def _only_on_arbeitsagentur(outcome: Outcome) -> bool:
    return outcome.status == "restricted" and outcome.note.startswith(ONLY_ON_ARBEITSAGENTUR)


def _safe_outcome(outcome: Outcome) -> Outcome:
    """Remove a legacy Arbeitsagentur link before rendering any application section."""
    legacy_only_note = _only_on_arbeitsagentur(outcome)
    board_link = is_arbeitsagentur_page(outcome.url) or is_arbeitsagentur_page(outcome.reached)
    if outcome.status == NO_EMPLOYER_LINK and board_link:
        return Outcome(outcome.status, None, None, outcome.note)
    if board_link or (outcome.status == "restricted" and legacy_only_note):
        return Outcome(
            "restricted", None, None,
            "restricted target: arbeitsagentur.de; legacy Arbeitsagentur link removed; "
            "employer application page not verified",
        )
    if outcome.status == NO_EMPLOYER_LINK and is_arbeitsagentur_page(outcome.url):
        return Outcome(outcome.status, None, outcome.reached, outcome.note)
    return outcome


def _block(name: str, label: str, outcome: Outcome) -> str:
    """The marked application section of README.md, job.md or apply.html for an outcome."""
    outcome = _safe_outcome(outcome)
    status = _status_text(outcome)
    url = outcome.url
    if outcome.status == NO_EMPLOYER_LINK:
        url = None
    if name == "apply.html":
        if not url:
            return f"<p>{html.escape(status)}</p>"
        return (f'<p><a href="{html.escape(url, quote=True)}" rel="noopener">Application page</a>'
                f"</p>\n<p>{html.escape(status)}</p>")
    shown = url if url else ("none yet" if outcome.status == NO_EMPLOYER_LINK else "not available")
    return f"- **{label}:** {shown}\n- **Form status:** {status}"


def refresh(folder: Path, outcome: Outcome, *, suppress_job_ad: bool = False):
    updates = {}
    for name, label in (("README.md", "Application link"), ("job.md", "Application page"), ("apply.html", "")):
        path = folder / name
        text = path.read_text(encoding="utf-8")
        block = _block(name, label, outcome)
        if name == "apply.html":
            legacy = r'<a href="[^"]*" rel="noopener">Application page</a>'
        else:
            legacy = rf"^- \*\*{label}:\*\* .*?$"
        marked = f"{START}\n{block}\n{END}"
        if START in text and END in text:
            updated, count = re.subn(re.escape(START) + r".*?" + re.escape(END),
                                     lambda m, replacement=marked: replacement, text, flags=re.S)
        else:
            updated, count = re.subn(legacy, lambda m, replacement=marked: replacement, text, flags=re.M)
            if count == 0 and name == "README.md":
                updated, count = re.subn(
                    r"^(- \*\*Job ad:\*\* .*?)$",
                    lambda m, replacement=marked: m.group(0) + "\n" + replacement, text, flags=re.M,
                )
            elif count == 0 and name == "apply.html":
                updated, count = re.subn(
                    r'<a href="[^"]*" rel="noopener">Job ad</a>',
                    lambda m, replacement=marked: m.group(0) + "\n" + replacement, text,
                )
        if count != 1:
            raise ValueError(f"Cannot safely refresh {path}: application link section is missing or ambiguous")
        updates[path] = updated
    for path, text in updates.items():
        if suppress_job_ad and path.name in {"README.md", "job.md"}:
            text = re.sub(r"^- \*\*Job ad:\*\* .*?$", "- **Job ad:** not available", text, flags=re.M)
        elif suppress_job_ad and path.name == "apply.html":
            text = re.sub(r'<a href="[^"]*" rel="noopener">Job ad</a>\s*', "", text)
        path.write_text(text, encoding="utf-8")


def save(ctx, job, entry, outcome: Outcome, *, refresh_package=False):
    outcome = _safe_outcome(outcome)
    outcome = Outcome(outcome.status, outcome.url, outcome.reached, " ".join(outcome.note.split())[:200])
    app = ctx.repo.get_application(job["id"])
    if refresh_package:
        refresh(Path(app["folder"]), outcome, suppress_job_ad=is_arbeitsagentur_page(job["job_url"]))
    form_entry = entry or (app["form_entry_url"] if app else None)
    if is_arbeitsagentur_page(form_entry):
        form_entry = None
    ctx.repo.upsert_application(
        job["id"], form_entry_url=form_entry,
        form_reached_url=outcome.reached, form_status=outcome.status, form_checked_at=now_iso(),
        form_note=outcome.note,
    )
    ctx.repo.update_job(job["id"], application_url=outcome.url)
    if refresh_package:
        from autoapp.package.folders import sync_folder_name
        from autoapp.tracking.csv_export import export_csv

        sync_folder_name(ctx, job["id"])

        tracking = ctx.config.tracking
        export_csv(ctx.conn, ctx.ws.csv_path, tracking.delimiter, include_skipped=tracking.include_skipped)
    ctx.emit("form_resolved", job, detail=_status_text(outcome), form_status=outcome.status)


def resolve(ctx, job, *, again=False, refresh_package=False, browser=None):
    app = ctx.repo.get_application(job["id"])
    entry = entry_url(ctx, job, app)
    if app and app["form_status"] == "found" and not again:
        return Outcome("found", job["application_url"], app["form_reached_url"], app["form_note"])
    if is_arbeitsagentur_job(ctx, job) and (link := employer_link(ctx, job, app)).kind != "employer":
        # The Arbeitsagentur page is a job ad, not an application page: it is never crawled or kept as the link.
        outcome = outcome_for(ctx, job, link)
        save(ctx, job, None, outcome, refresh_package=refresh_package)
        return outcome
    evaluation = ctx.repo.current_evaluation(job["id"])
    reference = json.loads(evaluation["payload"]).get("result", {}).get("reference") if evaluation else None
    note = ""
    if entry and is_restricted(entry, _restricted_domains(ctx)):
        # 1. the employer's own posting was not found (entry_url would have used it); 2. with the login, if allowed.
        entry, note = platform_apply_link(ctx, job, entry)
    outcome = find(ctx, entry, title=job["title"], company=job["company"], reference=reference, job_id=job["id"])
    from autoapp.formfinder.browser import fallback

    outcome = fallback(ctx, entry, outcome, title=job["title"], company=job["company"], reference=reference,
                       job_id=job["id"], browser=browser)
    if note and outcome.status == "restricted":
        outcome = Outcome(outcome.status, outcome.url, outcome.reached, f"{outcome.note}; {note}")
    save(ctx, job, entry, outcome, refresh_package=refresh_package)
    return outcome


def platform_apply_link(ctx, job, entry):
    """``(entry, note)``: the employer's apply address read from a platform page with the login
    (``sources.apply_links_with_login``), or the platform address and why it was not replaced."""
    lookup = getattr(ctx, "apply_links", None)
    if lookup is None or lookup.wanted(entry) is None:
        return entry, ""
    link = lookup.read(job, entry)
    if link is None:
        return entry, ""
    if link.status == "found" and link.url and not is_restricted(link.url, _restricted_domains(ctx)):
        ctx.repo.update_job(job["id"], application_url=link.url)
        if ctx.repo.get_application(job["id"]) is not None:  # later form checks start there too
            ctx.repo.upsert_application(job["id"], form_entry_url=link.url)
        return link.url, ""
    return entry, link.note


def selected(conn, job_id=None, again=False):
    rows = conn.execute(
        "SELECT j.*, a.form_status, a.form_entry_url FROM jobs j JOIN applications a ON a.job_id=j.id "
        "WHERE j.status='prepared' AND a.channel != 'email' AND a.folder IS NOT NULL ORDER BY j.discovered_at, j.id"
    ).fetchall()
    return [row for row in rows if (job_id is None or row["id"] == job_id)
            and (again or row["form_status"] is None)]
