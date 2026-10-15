"""The main menu (main-menu spec, design decision 1).

A thin loop over the commands' own functions: every entry calls what its command calls, so
the menu and the commands cannot drift apart. The menu holds no lock and no database
connection while it waits for input; each entry opens its own session like its command.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer

from autoapp.config.workspace import Workspace
from autoapp.setup.prompts import Prompter, SetupAborted

BACK = "__back__"


@dataclass
class MenuEntry:
    key: str
    title: str
    description: str


ENTRIES = (
    MenuEntry("run", "Run now", "search, evaluate and prepare; then send, save drafts or test (you choose)"),
    MenuEntry("stats", "Statistics", "models, ads read, approvals, emails, forms, sources and cost"),
    MenuEntry("apps", "Applications", "browse prepared and sent applications, change status, send or draft one"),
    MenuEntry("review", "Review queue", "jobs that need your decision or a fixed letter"),
    MenuEntry("forms", "Finish forms", "fill prepared form applications in your browser, one at a time"),
    MenuEntry("employers", "Employer sites", "careers sites found for your alert jobs: see, scan, confirm a website"),
    MenuEntry("redo", "Redo unsent", "process every job that is not sent again, as if it were new"),
    MenuEntry("add", "Add a job by URL", "add one job ad by its link"),
    MenuEntry("tests", "Tests", "test run, test email, check logins, dry run"),
    MenuEntry("settings", "Settings", "edit one part of the setup, redo it, reset a part, default run mode"),
    MenuEntry("quit", "Quit", ""),
)

APP_STATUSES = ("prepared", "drafted", "send_unconfirmed", "sent", "submitted", "interview", "offer", "rejected",
                "withdrawn", "expired")


# --- small helpers -------------------------------------------------------------------------------


def _connect(ws: Workspace) -> sqlite3.Connection:
    from autoapp.db.schema import open_db

    return open_db(ws.db_path)


def _rows(ws: Workspace, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    conn = _connect(ws)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# Order of the forms list: what the app can fill first, what only you can do last.
FORM_ORDER = {"found": 0, None: 1, "needs_browser": 2, "login_required": 3, "captcha": 3, "partial": 4,
              "none": 4, "no_employer_link": 5, "restricted": 5, "unreachable": 6}


def form_jobs(ws: Workspace) -> list[sqlite3.Row]:
    rows = _rows(ws, "SELECT j.*, a.form_status, a.form_note, a.folder FROM jobs j JOIN applications a ON a.job_id = j.id "
                     "WHERE j.status = 'prepared' AND COALESCE(a.channel, 'unknown') != 'email' "
                     "ORDER BY j.updated_at DESC")
    return sorted(rows, key=lambda r: FORM_ORDER.get(r["form_status"], 4))


def review_jobs(ws: Workspace) -> list[sqlite3.Row]:
    closed = ("skipped", "filtered", "sent", "submitted", "interview", "offer", "rejected", "withdrawn", "expired")
    marks = ",".join("?" * len(closed))
    return _rows(ws, f"SELECT * FROM jobs WHERE status = 'review' OR (needs_review = 1 AND status NOT IN ({marks})) "
                     "ORDER BY updated_at DESC", closed)


def badges(ws: Workspace) -> dict[str, str]:
    try:
        forms, review = len(form_jobs(ws)), len(review_jobs(ws))
    except sqlite3.Error:
        return {}
    return {"forms": f"{forms} prepared" if forms else "no prepared forms",
            "review": f"{review} waiting" if review else "nothing waiting"}


def _job_label(row: sqlite3.Row, extra: str = "") -> str:
    return f"{row['id']}  {row['status']:<9} {row['company']}: {row['title']}{extra}"


def _call(fn: Callable[..., object], *args, **kwargs) -> int:
    """Run a command function; return its exit code instead of leaving the menu."""
    try:
        fn(*args, **kwargs)
    except typer.Exit as e:
        return int(e.exit_code or 0)
    return 0


# --- the loop --------------------------------------------------------------------------------------


def main_menu(tctx: typer.Context, prompter: Prompter | None = None) -> None:
    from autoapp.setup.wizard import make_context

    ws: Workspace = tctx.obj.workspace
    p = prompter or make_context(ws).prompter
    actions: dict[str, Callable[[typer.Context, Prompter], str | None]] = {
        "run": entry_run, "redo": entry_redo, "stats": entry_stats, "apps": entry_applications, "review": entry_review,
        "forms": entry_forms, "employers": entry_employers, "add": entry_add, "tests": entry_tests, "settings": entry_settings,
    }
    while True:
        b = badges(ws)
        choices = [(e.key, f"{e.title}" + (f" ({b[e.key]})" if e.key in b else "")
                    + (f"  -  {e.description}" if e.description else "")) for e in ENTRIES]
        try:
            choice = p.select("What would you like to do?", choices, default="run")
        except (SetupAborted, KeyboardInterrupt):
            return  # Ctrl-C at the main menu leaves the program
        if choice == "quit":
            return
        try:
            result = actions[choice](tctx, p)
        except (SetupAborted, KeyboardInterrupt):
            result = "Cancelled."
        except Exception as err:  # noqa: BLE001 - one failing entry must not end the menu
            result = f"error: {err}"
        p.say(result or "Done.")
        p.say("")


# --- entries ----------------------------------------------------------------------------------------


def entry_run(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.commands import run_with_mode

    code = _call(run_with_mode, tctx, profile=None, mode=None, dry_run=False, interactive=True, prompter=p)
    return "Run finished." if code == 0 else f"Run finished with problems (exit code {code})."


def entry_redo(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.commands import run_with_mode

    code = _call(run_with_mode, tctx, profile=None, mode=None, dry_run=False, interactive=True, prompter=p, redo=True)
    return "Redo finished." if code == 0 else f"Redo finished with problems (exit code {code})."


def entry_stats(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.dashboard import show_stats

    show_stats(tctx.obj.workspace, "today", interactive=True)
    return "Back from statistics."


def finish_forms(tctx: typer.Context, p: Prompter, *, after_run: bool = False) -> str:
    """The list of prepared form applications. Every job returns here, so one can be opened again."""
    from autoapp.formfinder.explain import explain

    ws = tctx.obj.workspace
    jobs = form_jobs(ws)
    if not jobs:
        return "No prepared forms."
    ready = sum(1 for j in jobs if j["form_status"] in ("found", None))
    p.say(f"{len(jobs)} form application(s) are prepared. The app can fill {ready} of them as soon as they open; "
          "the others need a step from you first (shown in brackets).")
    if after_run:
        p.say("Pick one to work on it now, or choose 'Not now' and use Finish forms in the menu later.")
    opened: set[str] = set()
    while jobs:
        choices = [(j["id"], _job_label(j, f"  [{explain(j['form_status'], j['form_note']).short}]"
                                           + _filled_note(j) + ("  (opened)" if j["id"] in opened else "")))
                   for j in jobs]
        back = "Not now" if after_run and not opened else "Done"
        pick = p.select("Which application do you want to work on?", [*choices, (BACK, back)],
                        default=BACK if after_run else jobs[0]["id"])
        if pick == BACK:
            break
        job = next(j for j in jobs if j["id"] == pick)
        opened.add(pick)
        try:
            form_actions(tctx, p, job)
        except (SetupAborted, KeyboardInterrupt):
            p.say("Cancelled.")
        except Exception as err:  # noqa: BLE001 - one failing job must not end the list
            p.say(f"error: {err}")
        jobs = form_jobs(ws)
    return f"Worked on {len(opened)} application(s)." if opened else "No application opened."


def _filled_note(job: sqlite3.Row) -> str:
    """" (filled 10-09 14:32)" when assist has filled this job's form before (see filled_form.md)."""
    import re

    record = Path(job["folder"]) / "filled_form.md" if job["folder"] else None
    if record is None or not record.exists():
        return ""
    stamps = re.findall(r"^## (\d{4}-(\d\d-\d\d \d\d:\d\d))", record.read_text(encoding="utf-8"), flags=re.M)
    return f"  (filled {stamps[-1][1]})" if stamps else "  (filled before)"


def _use_address(tctx: typer.Context, p: Prompter, job: sqlite3.Row) -> bool:
    """Ask for the real application form's address and make it the job's application link."""
    from autoapp.assist.filler import is_restricted
    from autoapp.config.models import load_config
    from autoapp.steps.context import RunContext

    ws = tctx.obj.workspace
    p.say("Open the job in your normal browser, click through to the employer's own application form (for example "
          "'Apply on company website'), and copy the address from the address bar.")
    url = p.text("Address of the application form (https://..., empty to go back)").strip()
    if not url:
        return False
    if not url.lower().startswith(("http://", "https://")):
        p.say("That is not a web address (it must start with https://).")
        return False
    if is_restricted(url, load_config(ws).assist.restricted_domains):
        p.say("That address is still on LinkedIn, Indeed or StepStone (or another site the app never automates). "
              "Use the employer's own page that opens after 'Apply on company website'.")
        return False
    ctx = RunContext.open(ws, start_run=False)
    try:
        ctx.repo.update_job(job["id"], application_url=url)
        ctx.repo.upsert_application(job["id"], form_entry_url=url, form_status="partial",
                                    form_note="address given by you; not opened yet")
    finally:
        ctx.close()
    return True


def form_actions(tctx: typer.Context, p: Prompter, job: sqlite3.Row) -> None:
    """Open a job's form; when the app cannot fill it right away, say why and ask what to do first."""
    import webbrowser

    from autoapp.cli import mark
    from autoapp.commands import assist
    from autoapp.formfinder.explain import explain

    if job["form_status"] in ("found", None):
        _call(assist, tctx, job_id=job["id"])
        return
    found = explain(job["form_status"], job["form_note"])
    p.say(_job_label(job))
    for line in found.lines():
        p.say(line)
    if job["form_status"] == "no_employer_link":
        actions = []  # nothing to open in the app's browser: there is no employer page yet
    elif job["form_status"] == "restricted":
        from autoapp.cli import load_config_or_default
        from autoapp.fetch.apply_links import PLATFORM_LABELS, platform_of

        site = platform_of(job["application_url"] or job["job_url"])
        with_login = load_config_or_default(tctx.obj.workspace).sources.apply_links_with_login.enabled_for(site)
        actions = [("browser", f"Read the company's apply link on {PLATFORM_LABELS[site]} with your login, then "
                               "fill the form there")] if with_login else []
    else:
        actions = [("browser", "Open it in the app's browser (you click through, then the app fills the form)")]
    actions += [("address", "I found the real application form: paste its address, the app opens and fills it"),
                ("link", "Open the job link in my normal browser"),
                ("submitted", "I applied already: mark it as submitted"),
                ("skip", "I won't apply: mark it as skipped"),
                (BACK, "Back to the list")]
    default = actions[0][0]
    while True:  # opening the link or a wrong address keeps you here, so you can paste the form's address next
        choice = p.select("What do you want to do?", actions, default=default)
        if choice == "browser":
            _call(assist, tctx, job_id=job["id"])
        elif choice == "address":
            if not _use_address(tctx, p, job):
                continue
            _call(assist, tctx, job_id=job["id"])
        elif choice == "link":
            url = job["application_url"] or job["job_url"]
            _, app = _app_row(tctx.obj.workspace, job["id"])
            if url:
                if webbrowser.open(url) is False:
                    p.say(f"Could not launch your default browser. Open this address manually: {url}")
                else:
                    p.say(f"Opened {url} in your browser. Find the application form there, then paste its address here.")
            else:
                p.say("No job or application address is saved for this position.")
            if app and app["folder"]:
                p.say(f"Your prepared answers: {Path(app['folder']) / 'apply.html'}")
            default = "address"
            continue
        elif choice == "submitted":
            _call(mark, tctx, job_id=job["id"], status="submitted", note="applied manually")
        elif choice == "skip":
            _call(mark, tctx, job_id=job["id"], status="skipped", note="not applying")
        return


def entry_forms(tctx: typer.Context, p: Prompter) -> str:
    from datetime import UTC, datetime

    from autoapp.cli import load_config_or_default
    from autoapp.dashboard.screen import task_screen
    from autoapp.dashboard.wiring import models_in_use
    from autoapp.events import RunStats

    config = load_config_or_default(tctx.obj.workspace)
    stats = RunStats(started_at=datetime.now(UTC).isoformat(timespec="seconds"), mode="finishing forms",
                     step="finishing forms", models=models_in_use(config.llm), daily_cap=config.email.daily_cap)
    # While forms are being filled the statistics stay pinned on top (each form's session counts into them).
    with task_screen(stats, activity="finishing forms") as screen:
        return finish_forms(tctx, screen.prompter if screen is not None else p)


def entry_employers(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.commands import employers
    from autoapp.employers.plan import describe_saved

    def run(**options) -> int:
        base = dict(discover=False, job=[], profile=None, again=False, refresh_site=False, website=None, dry_run=False)
        return _call(employers, tctx, **{**base, **options})

    while True:
        try:
            choice = p.select("Employer sites", [
                ("show", "Show saved employer sites and their last scans"),
                ("preview", "Preview what a lookup would do (nothing is requested or changed)"),
                ("discover", "Find careers sites for my alert jobs now"),
                ("again", "Scan the saved sites again (no new search)"),
                ("website", "Confirm the right website for one job"),
                (BACK, "Back"),
            ], default="show")
        except (SetupAborted, KeyboardInterrupt):
            return "Back from employer sites."
        if choice == BACK:
            return "Back from employer sites."
        if choice == "show":
            for line in describe_saved(tctx.obj.workspace):
                p.say(line)
        elif choice == "preview":
            run(discover=True, dry_run=True)
        elif choice in ("discover", "again"):
            question = ("This reads public pages and may use search requests (about $1 per 1,000). Continue?"
                        if choice == "discover" else "Scan the saved employer sites again?")
            if p.confirm(question, default=False):
                run(discover=True, again=choice == "again")
        elif choice == "website":
            job = p.text("Job ID whose employer you want to correct").strip()
            url = p.text("The employer's own website (https://...)").strip()
            if job and url:
                run(job=[job], website=url)


def entry_add(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.commands import add, describe
    from autoapp.discovery.normalize import canonical_url

    url = p.text("Link to the job ad").strip()
    if not url:
        return "Nothing added."
    code = _call(add, tctx, url=url, profile=None)
    rows = _rows(tctx.obj.workspace, "SELECT j.* FROM jobs j JOIN job_sources s ON s.job_id = j.id "
                                     "WHERE s.source = 'manual' AND s.source_job_id = ?", (canonical_url(url),))
    if code or not rows:
        return "The job could not be added." if code else "Done."
    job = rows[0]
    if job["status"] == "description_unavailable" and p.confirm("The page could not be read. Paste the ad text now?",
                                                                 default=True):
        text = p.multiline("Paste the job ad")
        if text.strip():
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write(text)
            try:
                _call(describe, tctx, job_id=job["id"], file=f.name)
            finally:
                os.unlink(f.name)
    return f"Added job {job['id']}."


# --- applications -------------------------------------------------------------------------------------


def _app_row(ws: Workspace, job_id: str) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    jobs = _rows(ws, "SELECT * FROM jobs WHERE id = ?", (job_id,))
    apps = _rows(ws, "SELECT * FROM applications WHERE job_id = ?", (job_id,))
    return (jobs[0] if jobs else None), (apps[0] if apps else None)


def entry_applications(tctx: typer.Context, p: Prompter) -> str:
    ws = tctx.obj.workspace
    status = p.select("Which applications?", [("", "All"), *[(s, s) for s in APP_STATUSES]], default="")
    statuses = (status,) if status else APP_STATUSES
    marks = ",".join("?" * len(statuses))
    rows = _rows(ws, f"SELECT j.*, e.fit_score FROM jobs j LEFT JOIN evaluations e ON e.job_id = j.id AND e.is_current = 1 "
                     f"WHERE j.status IN ({marks}) ORDER BY j.updated_at DESC", statuses)
    if not rows:
        return "No applications with that status yet."
    pick = p.select("Which application?", [*[(r["id"], _job_label(r, f"  (score {r['fit_score']})"
                                                               if r["fit_score"] is not None else ""))
                                             for r in rows], (BACK, "Back")])
    if pick == BACK:
        return "Back."
    return application_actions(tctx, p, pick)


def application_actions(tctx: typer.Context, p: Prompter, job_id: str) -> str:
    from autoapp.cli import mark
    from autoapp.commands import assist, draft, render, send

    ws = tctx.obj.workspace
    while True:
        job, app = _app_row(ws, job_id)
        if job is None:
            return f"unknown job {job_id}"
        folder = Path(app["folder"]) if app and app["folder"] else None
        email = bool(app and app["channel"] == "email")
        actions = [("folder", "Show the folder path"), ("letter", "Show the cover letter text"),
                   ("status", "Change the status")]
        if not (email and folder is None):
            actions.append(("render", "Re-render after editing the letter"))
        if email:
            actions += [("draft", "Save as a draft in my mailbox"), ("send", "Send now (asks for confirmation)"),
                        ("test", "Send a test copy to myself")]
        else:
            actions += [("fill", "Fill the form in the browser")]
        actions.append((BACK, "Back"))
        p.say(_job_label(job))
        choice = p.select("What do you want to do with it?", actions, default=BACK)
        if choice == BACK:
            return "Back."
        if choice == "folder":
            if folder is not None:
                p.say(str(folder))
            elif email:
                p.say("No local folder: this application is a draft in your mailbox's Drafts folder.")
            else:
                p.say("No folder yet.")
        elif choice == "letter":
            letter = folder / "cover_letter.md" if folder else None
            if letter and letter.exists():
                p.say(letter.read_text(encoding="utf-8"))
            else:
                p.say(app["letter_md"] if app and app["letter_md"] else "No cover letter yet.")
        elif choice == "status":
            new = p.select("New status", [(s, s) for s in ("approved", *APP_STATUSES, "skipped")], default=job["status"])
            _call(mark, tctx, job_id=job_id, status=new, note=None)
        elif choice == "render":
            _call(render, tctx, job_id=job_id)
        elif choice == "draft":
            _call(draft, tctx, job_id=job_id, again=job["status"] == "drafted")
        elif choice == "send":
            _call(send, tctx, job_id=job_id, yes=False, test=False)
        elif choice == "test":
            _call(send, tctx, job_id=job_id, yes=False, test=True)
        elif choice == "fill":
            _call(assist, tctx, job_id=job_id)


# --- review queue -----------------------------------------------------------------------------------------


def _evaluation_summary(ws: Workspace, job: sqlite3.Row) -> str:
    import json

    rows = _rows(ws, "SELECT fit_score, decision, payload FROM evaluations WHERE job_id = ? AND is_current = 1",
                 (job["id"],))
    lines = [_job_label(job)]
    if rows:
        r = rows[0]
        payload = json.loads(r["payload"] or "{}")
        res = payload.get("result", payload)
        lines.append(f"score {r['fit_score']}, decision {r['decision']}")
        if res.get("rationale"):
            lines.append(str(res["rationale"]))
        missing = [q.get("text") for q in res.get("requirements") or [] if q.get("status") == "missing"]
        if missing:
            lines.append("missing: " + ", ".join(missing))
    reasons = json.loads(job["needs_review_reasons"] or "[]")
    if reasons:
        lines.append("needs review: " + "; ".join(reasons))
    return "\n".join(lines)


def entry_review(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.cli import mark
    from autoapp.commands import render

    ws = tctx.obj.workspace
    jobs = review_jobs(ws)
    if not jobs:
        return "Nothing waiting for review."
    pick = p.select("Which job?", [*[(j["id"], _job_label(j)) for j in jobs], (BACK, "Back")])
    if pick == BACK:
        return "Back."
    job = next(j for j in jobs if j["id"] == pick)
    p.say(_evaluation_summary(ws, job))
    actions = [("apply", "Apply (prepare it in the next run)"), ("skip", "Skip it")]
    _, app = _app_row(ws, pick)
    if app and (app["folder"] or app["letter_md"]):
        actions.append(("letter", "Show the cover letter path" if app["folder"] else "Show the cover letter text"))
    if app and app["folder"]:
        actions.append(("render", "Re-render after editing the letter"))
    actions.append((BACK, "Back"))
    choice = p.select("Decision", actions, default=BACK)
    if choice in ("apply", "skip"):
        _call(mark, tctx, job_id=pick, status=choice, note=None)
        return f"{pick}: {'approved' if choice == 'apply' else 'skipped'}."
    if choice == "letter":
        if not app["folder"]:
            return app["letter_md"] or "No cover letter yet."
        return str(Path(app["folder"]) / "cover_letter.md")
    if choice == "render":
        _call(render, tctx, job_id=pick)
        return "Re-rendered."
    return "Back."


# --- tests ---------------------------------------------------------------------------------------------------


def entry_tests(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.commands import run_with_mode, send

    choice = p.select("Which test?", [
        ("quick", "Quick test: a small sample of every step (a few emails, jobs, employers, one application), then a checklist"),
        ("testrun", "Test run: a full run where every email comes to you, marked TEST_RUN"),
        ("fresh", "Full test run (fresh): as above, but every job counts as new (your history is not touched)"),
        ("email", "Send a test email for one application to myself"),
        ("logins", "Check the logins (mailboxes, AI model, Adzuna)"),
        ("dry", "Dry run: a full run that sends nothing, saves no drafts and marks no emails"),
        (BACK, "Back"),
    ], default="quick")
    if choice == "quick":
        code = _call(run_with_mode, tctx, profile=None, mode="test", dry_run=False, interactive=True, prompter=p,
                     ask_mode=False, fresh=True, sample=True)
        return "Quick test finished." if code == 0 else f"Quick test finished with problems (exit code {code})."
    if choice == "testrun":
        code = _call(run_with_mode, tctx, profile=None, mode="test", dry_run=False, interactive=True, prompter=p,
                     ask_mode=False)
        return "Test run finished." if code == 0 else f"Test run finished with problems (exit code {code})."
    if choice == "fresh":
        code = _call(run_with_mode, tctx, profile=None, mode="test", dry_run=False, interactive=True, prompter=p,
                     ask_mode=False, fresh=True)
        return "Full test run finished." if code == 0 else f"Full test run finished with problems (exit code {code})."
    if choice == "email":
        jobs = _rows(tctx.obj.workspace, "SELECT j.* FROM jobs j JOIN applications a ON a.job_id = j.id "
                                         "WHERE j.status IN ('prepared', 'drafted') AND a.channel = 'email' "
                                         "ORDER BY j.updated_at DESC")
        pick = None
        if jobs:
            pick = p.select("Which application?", [(j["id"], _job_label(j)) for j in jobs], default=jobs[0]["id"])
        _call(send, tctx, job_id=pick, yes=False, test=True)
        return "Test email done."
    if choice == "logins":
        for line in check_logins(tctx.obj.workspace):
            p.say(line)
        return "Login checks done."
    if choice == "dry":
        code = _call(run_with_mode, tctx, profile=None, mode=None, dry_run=True, interactive=True, prompter=p,
                     ask_mode=False)
        return "Dry run finished." if code == 0 else f"Dry run finished with problems (exit code {code})."
    return "Back."


def check_logins(ws: Workspace, checks=None, draft_store_factory=None, adzuna_check=None) -> list[str]:
    """One pass/fail line per check; a failure never stops the others."""
    from autoapp.config.models import load_config
    from autoapp.setup.wizard import make_context

    checks = checks or make_context(ws).checks
    config = load_config(ws)
    lines: list[str] = []

    def line(name: str, ok: bool, message: str = "", hint: str = "") -> None:
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {message}" + (f" ({hint})" if hint else "")))

    alerts = config.mail.alerts
    if alerts is None:
        line("alert mailbox", False, "not configured", "Settings > sources")
    else:
        r = checks.imap(alerts, os.environ.get(alerts.password_env or "", ""), config.sources.alerts.folder)
        line(f"alert mailbox ({alerts.address}, folder {config.sources.alerts.folder})", r.ok, r.message,
             "Settings > sources")
    sending = config.mail.sending
    if sending is None:
        line("sending mailbox", False, "not configured", "Settings > email")
    else:
        password = os.environ.get(sending.password_env or "", "")
        r = checks.smtp(sending, password)
        line(f"sending mailbox SMTP ({sending.address})", r.ok, r.message, "Settings > email")
        try:
            from autoapp.mail.drafts import ImapDraftStore, imap_utf7_decode

            store = (draft_store_factory or (lambda: ImapDraftStore(sending, password)))()
            try:
                folder = store.drafts_folder()
            finally:
                store.close()
            line(f"sending mailbox IMAP, Drafts folder found: {imap_utf7_decode(folder)}", True)
        except Exception as err:  # noqa: BLE001
            line("sending mailbox IMAP and Drafts folder", False, str(err), "Settings > email")
    r = checks.llm(config.llm, "evaluate", os.environ)
    line(f"AI model ({config.llm.resolve('evaluate')[1]})", r.ok, r.message, "Settings > llm")
    if config.sources.adzuna.enabled:
        ok, message = (adzuna_check or _adzuna_check)()
        line("Adzuna key", ok, message, "Settings > sources")
    if config.sources.alerts.enabled and config.sources.alerts.mode == "search_first":
        key_set = bool(os.environ.get("PARALLEL_API_KEY"))
        line("search key for search-first alert jobs (PARALLEL_API_KEY)", key_set,
             "not set: alert jobs wait until a key is saved", "Settings > sources")
    return lines


def _adzuna_check() -> tuple[bool, str]:
    import httpx

    app_id, key = os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")
    if not (app_id and key):
        return False, "ADZUNA_APP_ID or ADZUNA_APP_KEY is not set"
    try:
        r = httpx.get("https://api.adzuna.com/v1/api/jobs/de/search/1", timeout=20,
                      params={"app_id": app_id, "app_key": key, "results_per_page": 1, "what": "python"})
    except httpx.HTTPError as err:
        return False, f"could not reach Adzuna: {err}"
    if r.status_code in (401, 403):
        return False, "Adzuna rejected the app ID or key"
    return (r.status_code == 200), ("" if r.status_code == 200 else f"HTTP {r.status_code}")


# --- settings ------------------------------------------------------------------------------------------------


def section_summaries(ws: Workspace) -> dict[str, str]:
    """One line with the current value of each setup section."""
    from autoapp.config.models import ConfigError, load_config, load_profile

    try:
        c = load_config(ws)
    except ConfigError:
        return {}
    try:
        prof = load_profile(ws)
        profile = f"{prof.personal.first_name} {prof.personal.last_name}, {prof.personal.city}"
        docs = f"{len(list(ws.documents_dir.glob('*')))} files"
    except (ConfigError, OSError):
        profile, docs = "not set", "?"
    keywords = sum(len(sp.keywords) for sp in c.search_profiles)
    sources = [n for n, on in (("Arbeitsagentur", c.sources.arbeitsagentur.enabled), ("Adzuna", c.sources.adzuna.enabled),
                              ((f"alert emails ({c.sources.alerts.mode.replace('_', ' ')})"), c.sources.alerts.enabled)) if on]
    return {
        "documents": docs,
        "profile": profile,
        "salary": "see profile.yaml",
        "search": f"{len(c.search_profiles)} profiles, {keywords} keywords",
        "sources": ", ".join(sources) or "none",
        "llm": ", ".join(f"{t}: {c.llm.resolve(t)[1]}" for t in ("evaluate", "write", "form_fill", "form_find", "judge")),
        "email": (f"sending from {c.mail.sending.address}" if c.mail.sending else "no sending mailbox")
                 + f", default mode {c.run.default_mode}",
        "writing": f"language {c.writing.language}, max {c.writing.max_words} words",
    }


def entry_settings(tctx: typer.Context, p: Prompter) -> str:
    from autoapp.setup.wizard import (
        SECTION_ORDER,
        backup_files,
        make_context,
        registry,
        reset_section,
        run_onboarding,
        run_section,
    )

    ws = tctx.obj.workspace
    summaries = section_summaries(ws)
    sections = registry()
    choice = p.select("Settings", [
        *[(f"edit:{n}", f"{sections[n].title}: {summaries.get(n, '')}") for n in SECTION_ORDER],
        ("mode", "Default run mode for scheduled runs"),
        ("redo", "Redo the whole setup (a backup is made first)"),
        ("reset", "Reset one section to its defaults"),
        ("wipe", "Delete all job data (history, folders, logs, test runs); keeps the setup"),
        (BACK, "Back"),
    ], default=BACK)
    sctx = make_context(ws)
    sctx.prompter = p
    if choice.startswith("edit:"):
        name = choice.split(":", 1)[1]
        return f"Saved the {name} section." if run_section(sctx, name) else f"The {name} section is not finished."
    if choice == "mode":
        return set_default_mode(ws, p)
    if choice == "redo":
        if not p.confirm("Redo every section? Your current answers are the defaults.", default=False):
            return "Nothing changed."
        backup = backup_files(ws, [ws.config_path, ws.profile_path, ws.writing_instructions_path, ws.env_path],
                              label="redo-all")
        p.say(f"Backup written to {backup}.")
        run_onboarding(sctx, only_missing=False)
        return "Setup redone."
    if choice == "reset":
        name = p.select("Which section?", [(n, sections[n].title) for n in SECTION_ORDER])
        return "Reset." if reset_section(sctx, name) else "Nothing changed."
    if choice == "wipe":
        from autoapp import reset_data as wipe
        from autoapp.lock import WorkspaceBusy

        answers = p.confirm("Also delete your saved form answers (form_answers.md)?", default=False)
        logins = p.confirm("Also delete the browser logins (LinkedIn, Workwise, ...)?", default=False)
        try:
            done = wipe.run(ws, say=p.say, confirm=lambda q: p.confirm(q, default=False), answers=answers,
                            logins=logins)
        except WorkspaceBusy as err:
            return str(err)
        return "Job data deleted." if done else "Nothing deleted."
    return "Back."


def set_default_mode(ws: Workspace, p: Prompter) -> str:
    from autoapp.config.io import load_yaml, to_plain
    from autoapp.config.models import load_config
    from autoapp.setup.sections import RUN_MODE_CHOICES
    from autoapp.setup.wizard import write_key

    current = load_config(ws).run.default_mode
    mode = p.select("Default run mode for scheduled runs", list(RUN_MODE_CHOICES), default=current)
    raw = to_plain(load_yaml(ws.config_path)) or {}
    run = dict(raw.get("run") or {})
    run["default_mode"] = mode
    write_key(ws.config_path, "run", run)
    email = dict(raw.get("email") or {})
    if "auto_send" in email:
        from autoapp.setup.wizard import write_or_drop

        email.pop("auto_send")
        write_or_drop(ws.config_path, "email", email)
    return f"Scheduled runs now use {mode}."
