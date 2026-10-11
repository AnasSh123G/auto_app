"""Pipeline commands: search, add, describe, evaluate, prepare, render, send, assist, run, export.

Every state-changing command holds the workspace lock, logs to ``logs/run-*.log`` (secrets
redacted), imports CSV edits first and refreshes ``applications.csv`` at the end.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

import typer

from autoapp.cli import app, get_context
from autoapp.config.models import ConfigError
from autoapp.lock import WorkspaceBusy, workspace_lock
from autoapp.logging_setup import setup_run_logging
from autoapp.steps.context import RunContext
from autoapp.tracking.csv_export import export_csv, sync_csv

log = logging.getLogger("autoapp")

SECRET_ENV = ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
              "AUTOAPP_ALERTS_PASSWORD", "AUTOAPP_SMTP_PASSWORD", "PARALLEL_API_KEY")


def _secrets(ctx: RunContext | None = None) -> list[str]:
    names = set(SECRET_ENV)
    if ctx is not None:
        for acc in (ctx.config.mail.alerts, ctx.config.mail.sending):
            if acc is not None and acc.password_env:
                names.add(acc.password_env)
    return [os.environ[n] for n in names if os.environ.get(n)]


@contextmanager
def session(tctx: typer.Context, *, dry_run: bool = False, interactive: bool = True,
            lock: bool = True, start_run: bool = True, needs_llm: bool = False) -> Iterator[RunContext]:
    """Lock, load, sync the CSV, yield the run context, export the CSV, release."""
    ws = get_context(tctx).workspace
    try:
        guard = workspace_lock(ws) if lock else _nullcontext()
        with guard:
            try:
                ctx = RunContext.open(ws, dry_run=dry_run, interactive=interactive, start_run=start_run)
            except ConfigError as err:
                for line in err.errors:
                    typer.echo(f"error: {line}", err=True)
                raise typer.Exit(code=2) from err
            if needs_llm:
                from autoapp.llm.factory import check_keys

                missing = check_keys(ctx.config.llm)
                if missing:
                    ctx.close()
                    for var in missing:
                        typer.echo(f"error: {var} is not set; add it to {ws.env_path} or run "
                                   "`autoapp setup --section llm`", err=True)
                    raise typer.Exit(code=2)
            setup_run_logging(ws, _secrets(ctx))
            if start_run:
                from autoapp.llm.openrouter_prices import auto_refresh

                if note := auto_refresh(ws.price_cache_path, ctx.config.llm):
                    typer.echo(f"notice: {note}", err=True)
            for notice in ctx.config.notices:
                typer.echo(f"notice: {notice}", err=True)
            if start_run:
                from autoapp.dashboard.wiring import attach_run_events

                attach_run_events(ctx, _secrets(ctx))
                from autoapp.dashboard.screen import current

                if (up := current()) is not None:  # e.g. form filling while the run's screen is up
                    up.watch(ctx.events, apply_stats=True, stats=ctx.stats)
            tracking = ctx.config.tracking
            for warning in sync_csv(ctx.conn, ws.csv_path, tracking.delimiter):
                typer.echo(f"warning: {warning}", err=True)
            try:
                yield ctx
            finally:
                export_csv(ctx.conn, ws.csv_path, tracking.delimiter, include_skipped=tracking.include_skipped)
                if start_run:
                    from autoapp.dashboard.wiring import finish_run_events

                    finish_run_events(ctx)
                ctx.close()
    except WorkspaceBusy as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(code=3) from err


@contextmanager
def _nullcontext() -> Iterator[None]:
    yield


def _interactive(non_interactive: bool) -> bool:
    from autoapp.setup.prompts import is_interactive

    return is_interactive() and not non_interactive


def _echo_error(text: str, ctx: RunContext | None = None) -> None:
    from autoapp.logging_setup import RedactionFilter

    typer.echo("error: " + RedactionFilter(_secrets(ctx)).redact(str(text)), err=True)


def _echo_lines(lines: list[str], ctx: RunContext | None = None) -> None:
    """Print summary lines with secrets masked (error texts can echo a password back)."""
    from autoapp.logging_setup import RedactionFilter

    redact = RedactionFilter(_secrets(ctx)).redact
    for line in lines:
        line = redact(line)
        log.info(line)
        typer.echo(line)


# --- discovery ---------------------------------------------------------------------------


@app.command()
def search(
    tctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-p", help="Run only this search profile."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not mark alert emails as processed."),
) -> None:
    """Search all enabled sources with your search profiles, then fetch missing details."""
    from autoapp.employers.service import (
        add_careers_matches,
        alerts_search_first,
        find_alert_ads,
        find_own_ads,
        own_ads_wanted,
        run_discovery,
        seed_arbeitsagentur_misses,
    )
    from autoapp.steps.fetch import fetch_details
    from autoapp.steps.pipeline import RunSummary
    from autoapp.steps.search import run_search

    with session(tctx, dry_run=dry_run) as ctx:
        s = RunSummary(search=run_search(ctx, profile), alert_mode=ctx.config.sources.alerts.mode)
        search_first = alerts_search_first(ctx.config)
        if search_first:
            s.alerts = find_alert_ads(ctx)
            if s.search is not None:
                s.alerts.llm_extracted = s.search.alert_llm_extracted
        if search_first or ctx.config.sources.employer_careers.enabled:
            s.employers = run_discovery(ctx, profile=profile)
            if s.alerts is not None:
                add_careers_matches(ctx, s.alerts)
        s.fetch = fetch_details(ctx)
        if own_ads_wanted(ctx.config):
            s.own_ads = find_own_ads(ctx)
            s.late_employers = seed_arbeitsagentur_misses(ctx, s.own_ads)
        _echo_lines(s.lines(), ctx)
        for error in (s.employers.errors if s.employers else []):
            _echo_error(error, ctx)


@app.command()
def add(
    tctx: typer.Context,
    url: str = typer.Argument(..., help="Link to the job ad."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Search profile to file it under."),
) -> None:
    """Add a job by URL; its page is fetched like any other job."""
    from autoapp.discovery.ingest import ingest
    from autoapp.discovery.normalize import canonical_url
    from autoapp.sources.models import RawJob
    from autoapp.steps.fetch import fetch_details

    with session(tctx) as ctx:
        canon = canonical_url(url)
        raw = RawJob(source="manual", source_job_id=canon, title="(title pending)",
                     company=urlsplit(canon).netloc or "(company pending)", url=url,
                     raw={"title_truncated": True, "company_unknown": True},
                     search_profile=profile or (ctx.config.search_profiles[0].name if ctx.config.search_profiles else None))
        out = ingest(ctx.repo, raw)
        if not out.created:
            typer.echo(f"Already known as job {out.job_id}.")
            return
        f = fetch_details(ctx)
        job = ctx.repo.get_job(out.job_id)
        typer.echo(f"Added job {out.job_id}: {job['title']} at {job['company']} (status {job['status']}).")
        if f.unavailable:
            typer.echo(f"The page could not be read. Paste the ad with `autoapp describe {out.job_id}`.")


@app.command()
def describe(
    tctx: typer.Context,
    job_id: str = typer.Argument(...),
    file: str | None = typer.Option(None, "--file", "-f", help="Read the ad text from this file."),
) -> None:
    """Paste the description of a job whose page could not be fetched."""
    from autoapp.db.status import Status

    if file:
        text = open(file, encoding="utf-8").read()
    elif sys.stdin.isatty():
        typer.echo("Paste the job ad, then press Ctrl-D on an empty line:")
        text = sys.stdin.read()
    else:
        text = sys.stdin.read()
    if not text.strip():
        typer.echo("No text given; nothing changed.", err=True)
        raise typer.Exit(code=1)
    with session(tctx) as ctx:
        job = ctx.repo.get_job(job_id)
        if job is None:
            typer.echo(f"unknown job {job_id}", err=True)
            raise typer.Exit(code=1)
        ctx.repo.update_job(job_id, description=text.strip())
        if job["status"] in (Status.DESCRIPTION_UNAVAILABLE.value, Status.EVALUATION_FAILED.value):
            ctx.repo.transition(job_id, Status.NEW, note="description pasted by hand")
        typer.echo(f"Stored the description; job {job_id} will be evaluated next.")


# --- evaluation and preparation ------------------------------------------------------------


@app.command()
def evaluate(
    tctx: typer.Context,
    job: list[str] = typer.Option(None, "--job", "-j", help="Evaluate only these job IDs."),
    force: bool = typer.Option(False, "--force", help="Evaluate again even if already evaluated."),
) -> None:
    """Evaluate new jobs against your CV and profile."""
    from autoapp.steps.evaluate import evaluate_jobs
    from autoapp.steps.pipeline import RunSummary

    with session(tctx, needs_llm=True) as ctx:
        s = RunSummary(evaluate=evaluate_jobs(ctx, job or None, force=force))
        s.cost = ctx.prices.summarize(ctx.meter.usages)
        _echo_lines(s.lines(), ctx)
        for e in s.evaluate.errors:
            _echo_error(e, ctx)


@app.command()
def prepare(
    tctx: typer.Context,
    job: list[str] = typer.Option(None, "--job", "-j", help="Prepare only these job IDs."),
    force: bool = typer.Option(False, "--force", help="Rebuild existing folders (the old letter is backed up)."),
) -> None:
    """Create application folders for approved jobs."""
    from autoapp.steps.pipeline import RunSummary
    from autoapp.steps.prepare import prepare_jobs

    with session(tctx, needs_llm=True) as ctx:
        p = prepare_jobs(ctx, job or None, force=force)
        s = RunSummary(prepare=p)
        s.cost = ctx.prices.summarize(ctx.meter.usages)
        _echo_lines(s.lines(), ctx)
        for j in p.skipped_existing:
            typer.echo(f"{j}: folder exists; use --force to rebuild it")
        for f in p.failed:
            _echo_error(f, ctx)


@app.command()
def render(tctx: typer.Context, job_id: str = typer.Argument(...)) -> None:
    """Re-render a prepared application after editing its cover_letter.md (no AI involved)."""
    from autoapp.steps.render import render_application

    with session(tctx) as ctx:
        try:
            warnings = render_application(ctx, job_id)
        except ValueError as err:
            typer.echo(f"error: {err}", err=True)
            raise typer.Exit(code=1) from err
        for w in warnings:
            typer.echo(f"warning: {w}")
        typer.echo(f"Re-rendered {ctx.repo.get_application(job_id)['folder']}")


# --- submission ----------------------------------------------------------------------------


@app.command()
def send(
    tctx: typer.Context,
    job_id: str | None = typer.Argument(None, help="Job ID (optional with --test)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Send without asking for confirmation."),
    test: bool = typer.Option(False, "--test", help="Send the email to your own address instead of the employer."),
) -> None:
    """Send one prepared email application (works even with email.auto_send off).

    With --test, the application email (subject starting with TEST_RUN:, a first line saying
    where it would have gone, the letter and the attachment) comes to you instead of the
    employer; nothing is recorded and the employer is never contacted.
    """
    from autoapp.steps.send import send_applications, send_test_email

    if test:
        with session(tctx) as ctx:
            r = send_test_email(ctx, job_id)
            if r.error:
                _echo_error(r.error, ctx)
                raise typer.Exit(code=1)
            if r.kind == "connection":
                typer.echo(f"No prepared email application found; sent a short test mail to {r.sent_to}.")
            else:
                typer.echo(f"Sent one email to {r.sent_to}, subject starting with TEST_RUN:. "
                           f"Its first line says it is what would be sent to {r.original_to} in a real run.\n"
                           f'Address found in the ad: "{r.evidence or "NOT FOUND in the stored ad text"}"\n'
                           "Nothing was sent to the employer or recorded; the job can still be sent for real.")
            if r.warning:
                typer.echo(f"warning: {r.warning}")
        return
    if job_id is None:
        typer.echo("error: give a job ID, or use --test to send a test mail to yourself", err=True)
        raise typer.Exit(code=2)

    with session(tctx) as ctx:
        def confirm(jid, msg) -> bool:
            files = ", ".join(p.get_filename() or "?" for p in msg.iter_attachments())
            typer.echo(f"To:      {msg['To']}\nSubject: {msg['Subject']}\nFiles:   {files}")
            job = ctx.repo.get_job(jid)
            if job is not None and job["status"] == "drafted":
                typer.echo("warning: a draft of this application is saved in your mailbox; if you also send "
                           "the draft, the employer gets it twice")
                return typer.confirm("Send it from here anyway?", default=False)
            return yes or typer.confirm("Send this application now?", default=False)

        s = send_applications(ctx, [job_id], manual=True, confirm=confirm)
        if s.sent:
            typer.echo(f"Sent. {job_id} is now marked as sent.")
        for reason in s.not_sent.get(job_id, []):
            typer.echo(f"not sent: {reason}")
        for e in s.errors:
            _echo_error(e, ctx)
        if s.errors:
            raise typer.Exit(code=1)


@app.command()
def draft(
    tctx: typer.Context,
    job_id: str = typer.Argument(...),
    again: bool = typer.Option(False, "--again", help="Save a new draft even if one was saved before."),
) -> None:
    """Save a prepared email application as a draft in your mailbox (nothing is sent)."""
    from autoapp.mail.drafts import imap_utf7_decode
    from autoapp.steps.drafts import draft_applications

    with session(tctx) as ctx:
        s = draft_applications(ctx, [job_id], again=again)
        if s.saved:
            typer.echo(f"Saved the draft in your mailbox's {imap_utf7_decode(s.folder or '')} folder. "
                       f"Read it there and send it yourself; {job_id} is now drafted.")
        for note in s.notes.get(job_id, []):
            typer.echo(f"note: {note}")
        if job_id in s.existing:
            typer.echo("A draft was already saved for this job. Use --again to save a new one.")
        for reason in s.not_drafted.get(job_id, []):
            typer.echo(f"not drafted: {reason}")
        for e in s.errors:
            _echo_error(e, ctx)
        if s.errors or job_id in s.not_drafted:
            raise typer.Exit(code=1)


@app.command()
def drafts(
    tctx: typer.Context,
    check: bool = typer.Option(False, "--check", help="Mark drafts you sent by hand as sent."),
) -> None:
    """List saved drafts, or with --check, look in your Sent folder for drafts you have sent."""
    from autoapp.steps.drafts import check_drafts

    with session(tctx) as ctx:
        if check:
            c = check_drafts(ctx)
            for jid in c.sent:
                typer.echo(f"{jid}: sent by you from the draft; marked sent")
            for jid in c.missing:
                typer.echo(f"{jid}: draft missing (deleted?); run `autoapp draft {jid} --again` to save a new one")
            typer.echo(f"{len(c.sent)} sent, {len(c.missing)} missing, {len(c.still_drafted)} still waiting in Drafts")
            for e in c.errors:
                _echo_error(e, ctx)
            if c.errors:
                raise typer.Exit(code=1)
            return
        rows = ctx.conn.execute(
            "SELECT d.job_id, d.recipient, d.subject, d.created_at, j.status FROM drafts d JOIN jobs j ON j.id = d.job_id "
            "WHERE d.state = 'saved' ORDER BY d.created_at DESC").fetchall()
        for r in rows:
            typer.echo(f"{r['job_id']}  {r['status']:<9} {r['created_at'][:16]}  {r['recipient']}  {r['subject']}")
        if not rows:
            typer.echo("No drafts saved yet. Review-first runs and `autoapp draft <id>` save them.")


@app.command()
def forms(
    tctx: typer.Context,
    job: str | None = typer.Option(None, "--job", "-j"),
    again: bool = typer.Option(False, "--again", help="Retry every status, including found, from the original entry."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List starting addresses without any changes or requests."),
    audit: bool = typer.Option(False, "--audit", help="Read-only report of the saved form links: status, job boards, "
                                                      "sources and causes. Changes nothing, makes no requests."),
    as_json: bool = typer.Option(False, "--json", help="With --audit: print the report as JSON."),
) -> None:
    """Find application forms for prepared non-email jobs and refresh their links."""
    import sqlite3

    from autoapp.formfinder.store import entry_url, resolve, selected

    if as_json and not audit:
        typer.echo("error: --json needs --audit", err=True)
        raise typer.Exit(2)
    if audit:
        if job or again or dry_run:
            typer.echo("error: --audit cannot be combined with --job, --again or --dry-run", err=True)
            raise typer.Exit(2)
        _forms_audit(get_context(tctx).workspace, as_json)
        return

    if dry_run:
        ws = get_context(tctx).workspace
        if not ws.db_path.exists():
            typer.echo("No prepared forms.")
            return
        conn = sqlite3.connect(ws.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
            if "form_status" not in columns:
                rows = conn.execute(
                    "SELECT j.* FROM jobs j JOIN applications a ON a.job_id=j.id "
                    "WHERE j.status='prepared' AND a.channel != 'email' AND a.folder IS NOT NULL "
                    "ORDER BY j.discovered_at, j.id"
                ).fetchall()
                rows = [r for r in rows if job is None or r["id"] == job]
            else:
                rows = selected(conn, job, again)
            from types import SimpleNamespace

            from autoapp.db.repo import Repo

            readonly = SimpleNamespace(repo=Repo(conn))
            for row in rows:
                application = readonly.repo.get_application(row["id"])
                if "form_entry_url" not in columns:
                    application = None
                typer.echo(f"{row['id']}: would resolve {entry_url(readonly, row, application)} and refresh package links")
        except sqlite3.Error as error:
            typer.echo(f"error: cannot inspect the database read-only: {error}", err=True)
            raise typer.Exit(1) from error
        finally:
            conn.close()
        return
    with session(tctx) as ctx:
        rows = selected(ctx.conn, job, again)
        if job and not rows:
            typer.echo("error: job is not an eligible unresolved prepared form (use --again to retry)", err=True)
            raise typer.Exit(2)
        for row in rows:
            try:
                result = resolve(ctx, row, again=again, refresh_package=True)
            except (OSError, ValueError, sqlite3.Error) as error:
                ctx.emit("error", row, detail=f"form resolution failed: {error}")
                _echo_error(str(error), ctx)
                raise typer.Exit(1) from error
            from autoapp.formfinder.finder import LABELS

            typer.echo(f"{row['id']}: {LABELS[result.status]}: {result.url} ({result.note})")


def _audit_data(ws) -> dict:  # noqa: ANN001
    """The form-link audit of a workspace, read-only; an empty one when it has no database yet."""
    from autoapp.config.models import DEFAULT_RESTRICTED_DOMAINS, ConfigError, load_config
    from autoapp.formfinder import audit as form_audit

    try:
        restricted = load_config(ws).assist.restricted_domains
    except (ConfigError, OSError):
        restricted = DEFAULT_RESTRICTED_DOMAINS
    conn = form_audit.open_readonly(ws.db_path)
    if conn is None:
        return form_audit.empty()
    try:
        return form_audit.audit(conn, restricted)
    finally:
        conn.close()


def _forms_audit(ws, as_json: bool) -> None:  # noqa: ANN001
    import json
    import sqlite3

    from autoapp.formfinder import audit as form_audit

    try:
        data = _audit_data(ws)
    except sqlite3.Error as error:
        typer.echo(f"error: cannot inspect the database read-only: {error}", err=True)
        raise typer.Exit(1) from error
    if as_json:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        for line in form_audit.lines(data):
            typer.echo(line)


@app.command()
def employers(
    tctx: typer.Context,
    discover: bool = typer.Option(False, "--discover", help="Look up and scan employer careers sites for alert jobs."),
    job: list[str] = typer.Option(None, "--job", "-j", help="With --discover: only the employers of these alert jobs."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="With --discover: only this search profile."),
    again: bool = typer.Option(False, "--again", help="Scan again even if a recent scan exists. Saved sites are not searched again; "
                                                     "failed lookups are retried now (that searches)."),
    refresh_site: bool = typer.Option(False, "--refresh-site", help="Look the employer's website up again (uses search)."),
    website: str | None = typer.Option(None, "--website", help="Confirm this website for the employer of --job."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Describe what would happen: no requests, no changes."),
) -> None:
    """Show saved employer sites, or with --discover find careers sites for your alert jobs.

    Without options this only reads what is saved. --dry-run never opens a writable workspace.
    """
    from autoapp.config.models import DEFAULT_RESTRICTED_DOMAINS
    from autoapp.employers.guard import check_target
    from autoapp.employers.plan import describe_saved, plan_discovery

    acting = discover or bool(website)
    jobs = list(job or [])
    problem = None
    if website and len(jobs) != 1:
        problem = "--website needs exactly one --job (the alert job whose employer it belongs to)"
    elif website and refresh_site:
        problem = "--website and --refresh-site cannot be combined: confirm a site or look it up again, not both"
    elif website and profile:
        problem = "--website works on one --job; --profile does not apply"
    elif refresh_site and not discover:
        problem = "--refresh-site searches for the website again, so it needs --discover"
    elif not acting and (again or jobs or profile or dry_run):
        problem = "--again, --job, --profile and --dry-run need --discover"
    if problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(code=2)
    ws = get_context(tctx).workspace
    if website:
        # A dry run must not touch the network, so it skips the DNS check and validates the address only.
        resolver = (lambda host: ["93.184.216.34"]) if dry_run else None
        why = check_target(website, DEFAULT_RESTRICTED_DOMAINS, **({"resolver": resolver} if resolver else {}))
        if why:
            typer.echo(f"error: cannot use {website}: {why}", err=True)
            raise typer.Exit(code=2)
    if not acting:
        for line in describe_saved(ws):
            typer.echo(line)
        return
    if dry_run:
        for line in plan_discovery(ws, job_ids=jobs or None, profile=profile, again=again, refresh_site=refresh_site,
                                   website=website):
            typer.echo(line)
        return
    from autoapp.employers.service import run_discovery

    with session(tctx) as ctx:
        known = {r["id"] for r in ctx.conn.execute("SELECT id FROM jobs")}
        missing = [j for j in jobs if j not in known]
        if missing:
            typer.echo(f"error: unknown job: {', '.join(missing)}", err=True)
            raise typer.Exit(code=2)
        if profile and profile not in {p.name for p in ctx.config.search_profiles}:
            typer.echo(f"error: no search profile named {profile!r}", err=True)
            raise typer.Exit(code=2)
        summary = run_discovery(ctx, job_ids=jobs or None, profile=profile, again=again, refresh_site=refresh_site,
                                website=website, force=True)
        lines = summary.lines()
        if not summary.employers:
            lines = ["Nothing to discover: no eligible alert jobs (unfiltered, not sent or closed) for this selection."]
        _echo_lines(lines, ctx)
        if summary.errors:
            raise typer.Exit(code=1)


@app.command()
def assist(tctx: typer.Context, job_id: str = typer.Argument(...)) -> None:
    """Open the application form in a browser and fill it when you ask. You submit it."""
    from autoapp.assist.session import AssistSession, NotPrepared, open_browser_context
    from autoapp.dashboard.screen import echo_color, task_screen

    def say(text: str) -> None:
        typer.echo(text, color=echo_color())

    with session(tctx) as ctx, task_screen(ctx.stats, activity=f"filling the form for {job_id}", bus=ctx.events):
        try:
            sess = AssistSession(ctx, job_id, say=say)
            if sess.app["channel"] != "email" and sess.app["form_status"] is None:
                from autoapp.formfinder.store import resolve

                resolve(ctx, sess.job, refresh_package=True)
                sess = AssistSession(ctx, job_id, say=say)
        except NotPrepared as err:
            typer.echo(f"error: {err}", err=True)
            raise typer.Exit(code=1) from err
        pw, context = open_browser_context(ctx.ws.browser_profile_dir)
        try:
            sess.run(context)
        finally:
            for close in (context.close, pw.stop):
                try:
                    close()
                except Exception:  # noqa: BLE001 - the user may have closed the browser already
                    pass


@app.command("reset-data")
def reset_data(
    tctx: typer.Context,
    answers: bool = typer.Option(False, "--answers", help="Also delete your saved form answers (form_answers.md)."),
    logins: bool = typer.Option(False, "--logins", help="Also delete the browser logins (browser-profile/)."),
    yes: bool = typer.Option(False, "--yes", help="Do not ask (for scripts). Without a terminal it is required."),
) -> None:
    """Delete all job data (history, folders, CSV, logs, test runs) and keep the setup. Asks first."""
    from autoapp import reset_data as wipe
    from autoapp.setup.prompts import is_interactive

    ws = get_context(tctx).workspace
    if not yes and not is_interactive():
        typer.echo("error: nothing deleted: confirm with --yes when there is no terminal to ask in", err=True)
        raise typer.Exit(code=2)
    try:
        wipe.run(ws, say=typer.echo, confirm=lambda q: typer.confirm(q, default=False), answers=answers,
                 logins=logins, assume_yes=yes)
    except WorkspaceBusy as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(code=3) from err


@app.command()
def login(tctx: typer.Context, site: str = typer.Argument(..., help="linkedin, stepstone, indeed or workwise")) -> None:
    """Log in to a job site once in the app's browser; the login is kept for later.

    LinkedIn, StepStone and Indeed: needed when sources.apply_links_with_login is on for them. Workwise: applications
    on Workwise need your Workwise account. The app never types your password; you log in yourself.
    """
    from autoapp.fetch.apply_links import LOGIN_PAGES, PLATFORM_LABELS

    site = site.strip().lower()
    if site not in LOGIN_PAGES:
        typer.echo(f"error: unknown site {site!r}; choose one of {', '.join(LOGIN_PAGES)}", err=True)
        raise typer.Exit(code=2)
    ws = get_context(tctx).workspace
    ws.ensure_layout()
    open_login_page(ws.browser_profile_dir, LOGIN_PAGES[site])
    typer.echo(f"Log in to {PLATFORM_LABELS[site]} in the browser window that opened. Then come back here.")
    try:
        input("Press Enter when you are logged in (or to close the browser)... ")
    except (EOFError, KeyboardInterrupt):
        pass
    close_login_page()
    typer.echo(f"Done. The {PLATFORM_LABELS[site]} login is kept in {ws.browser_profile_dir}.")


_login_browser: list = []


def open_login_page(profile_dir, url: str) -> None:  # noqa: ANN001
    from autoapp.assist.session import open_browser_context

    pw, context = open_browser_context(profile_dir)
    _login_browser[:] = [pw, context]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(url)
    page.bring_to_front()


def close_login_page() -> None:
    if not _login_browser:
        return
    pw, context = _login_browser
    for close in (context.close, pw.stop):
        try:
            close()
        except Exception:  # noqa: BLE001 - the window may be closed already
            pass
    _login_browser.clear()


@app.command()
def run(
    tctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-p", help="Run only this search profile."),
    mode: str | None = typer.Option(None, "--mode", help="automatic, review or test (skips the question)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do everything except sending, drafting and marking emails."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Never prompt (for scheduled runs)."),
    no_dashboard: bool = typer.Option(False, "--no-dashboard", help="Print plain lines instead of the live view."),
    redo: bool = typer.Option(False, "--redo", help="Process every job that is not sent again, as if it were new "
                              "(evaluate, rebuild its folder, look for its form). Sent jobs are never touched."),
    fresh: bool = typer.Option(False, "--fresh", help="Test run only: start from an empty job history in "
                               "workspace/test-runs/, so every job found counts as new. Your real history is not touched."),
) -> None:
    """Run the whole pipeline: search, fetch, evaluate, prepare, then send, draft or test-send, and export."""
    from autoapp.config.models import RUN_MODES

    if mode is not None and mode not in RUN_MODES:
        typer.echo(f"error: unknown mode {mode!r}; the valid values are automatic, review and test", err=True)
        raise typer.Exit(code=2)
    if fresh and mode not in (None, "test"):
        typer.echo("error: --fresh is only for test runs (--mode test): it must never send to employers", err=True)
        raise typer.Exit(code=2)
    interactive = _interactive(non_interactive)
    if redo and fresh:
        typer.echo("error: --redo and --fresh cannot be combined: --fresh already starts from an empty history", err=True)
        raise typer.Exit(code=2)
    run_with_mode(tctx, profile=profile, mode="test" if fresh else mode, dry_run=dry_run, interactive=interactive,
                  no_dashboard=no_dashboard, fresh=fresh, redo=redo)


@app.command(name="test")
def quick_test(
    tctx: typer.Context,
    quick: bool = typer.Option(False, "--quick", help="A small sample of every step, then a pass/fail checklist."),
    full: bool = typer.Option(False, "--full", help="A whole test run in a fresh test workspace "
                              "(same as `run --mode test --fresh`)."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Use only this search profile."),
    no_dashboard: bool = typer.Option(False, "--no-dashboard", help="Print plain lines instead of the live view."),
) -> None:
    """Test the app in a throwaway workspace: pick --quick or --full (without either, nothing starts; exit code 2).

    --quick reads the newest one or two alert emails (not marked as processed), asks each job source for a couple
    of jobs, looks up a couple of employers online, evaluates a few jobs, prepares one email and one form
    application and sends at most one TEST_RUN email to yourself, then prints a pass/fail checklist. Exit code 1
    when any part FAILED.

    --full is a whole test run with every job counted as new, every email coming to you marked TEST_RUN.

    Your real jobs, statuses and CSV are not touched; the searches and AI calls are real and count against your limits.
    """
    if quick and full:
        typer.echo("error: --quick and --full cannot be combined: pick one", err=True)
        raise typer.Exit(code=2)
    if not quick and not full:
        typer.echo("autoapp test needs a choice:\n"
                   "  autoapp test --quick   a small sample of every step, then a checklist\n"
                   "  autoapp test --full    a whole test run in a fresh test workspace (like run --mode test --fresh)",
                   err=True)
        raise typer.Exit(code=2)
    run_with_mode(tctx, profile=profile, mode="test", dry_run=False, interactive=_interactive(False),
                  no_dashboard=no_dashboard, ask_mode=False, fresh=True, sample=quick)


def run_with_mode(tctx: typer.Context, *, profile: str | None, mode: str | None, dry_run: bool, interactive: bool,
                  no_dashboard: bool = False, prompter=None, ask_mode: bool = True, fresh: bool = False,
                  redo: bool = False, sample: bool = False) -> None:
    """``run`` and the menu's "Run now": resolve the mode, run, then offer to finish forms.

    ``fresh`` (test mode only) runs in a new test workspace with an empty job history (``steps.sandbox``).
    ``sample`` (the quick test, ``autoapp test --quick``) takes a small sample of every step and ends with a checklist.
    """
    if fresh:
        from autoapp.steps import sandbox

        if mode != "test":
            raise ValueError("a fresh run is a test run")
        app_ctx = get_context(tctx)
        real = app_ctx.workspace
        box = sandbox.create(real)
        app_ctx.workspace = box.ws
        try:
            run_with_mode(tctx, profile=profile, mode="test", dry_run=dry_run, interactive=interactive,
                          no_dashboard=no_dashboard, prompter=prompter, ask_mode=False, sample=sample)
        finally:
            app_ctx.workspace = real
            sandbox.merge_back(box)
            if sample:
                _echo_form_audit(real)
            typer.echo(sandbox.describe(box))
        return
    from autoapp.dashboard.live import PlainPrinter, can_use_live
    from autoapp.dashboard.screen import TaskScreen, current
    from autoapp.dashboard.view import make_console
    from autoapp.menu.run_prompt import MODE_LABELS, choose_run
    from autoapp.steps.pipeline import run_pipeline

    # The task screen (statistics pinned on top, the log below, questions at the bottom) stays up from the start of
    # the run until the last prepared form is dealt with.
    screen: TaskScreen | None = None
    s = None
    checklist = None
    forms = 0
    try:
        with session(tctx, dry_run=dry_run, interactive=interactive, needs_llm=True) as ctx:
            if mode is None and interactive and ask_mode:
                from autoapp.mail.send import sends_today
                from autoapp.setup.wizard import make_context

                prompter = prompter or make_context(ctx.ws).prompter
                from datetime import UTC, datetime

                choice = choose_run(prompter, ctx.config, sends_today(ctx.repo, datetime.now(UTC)),
                                    ask_profile=profile is None)
                mode, profile = choice.mode, profile or choice.profile
            if redo and not _start_redo(ctx, dry_run=dry_run, interactive=interactive, prompter=prompter):
                return
            ctx.mode = mode or ctx.config.run.default_mode
            if sample:
                from autoapp.steps.context import SampleLimits

                ctx.sample = SampleLimits()
                ctx.sample.apply_to(ctx.config)
            if ctx.stats is not None:
                ctx.stats.mode = MODE_LABELS[ctx.mode]
            log.info("run mode: %s%s", MODE_LABELS[ctx.mode], " (dry run)" if dry_run else "")
            if ctx.stats is not None:
                live = interactive and not no_dashboard and current() is None and can_use_live(make_console())
                if live:
                    screen = TaskScreen(ctx.stats, activity="run").start()
                    if screen.alive:
                        screen.watch(ctx.events, apply_stats=False)
                    else:
                        screen = None
                if screen is None and current() is None:
                    ctx.events.subscribe(PlainPrinter())
            s = run_pipeline(ctx, profile)
            _echo_lines(s.lines(), ctx)
            if sample:
                from autoapp.steps.quicktest import build_report, form_links

                links = form_links(ctx, s.prepare.prepared_ids if s.prepare else [])
                checklist = build_report(ctx.config, s, links=links)
                _echo_lines(checklist.lines(), ctx)
            forms = len(s.forms_to_finish) if interactive and not dry_run and not sample else 0  # no forms to finish in a quick test
        if forms:
            from autoapp.menu.main import finish_forms
            from autoapp.setup.prompts import SetupAborted
            from autoapp.setup.wizard import make_context

            if screen is not None and screen.alive:
                screen.activity = "finishing forms"
                prompter = screen.prompter
            else:
                prompter = prompter or make_context(get_context(tctx).workspace).prompter
            try:
                prompter.say(finish_forms(tctx, prompter, after_run=True))
            except (KeyboardInterrupt, SetupAborted):
                pass
    except BaseException:
        if screen is not None:
            screen.stop()
        raise
    if screen is not None:
        # After the form list there is nothing more to read on the screen; without it, the log can be read first.
        screen.stop(wait_for_key=not forms)
    if s is not None and (not s.ok or (checklist is not None and checklist.failed)):
        raise typer.Exit(code=1)


def _echo_form_audit(real) -> None:  # noqa: ANN001
    """The quick test's baseline: the real workspace's form links, read-only (a failure only skips the lines)."""
    import sqlite3

    from autoapp.formfinder import audit as form_audit

    try:
        for line in form_audit.lines(_audit_data(real), "real workspace"):
            typer.echo(line)
    except sqlite3.Error as error:
        typer.echo(f"existing form links: not available ({error})")


def _start_redo(ctx, *, dry_run: bool, interactive: bool, prompter) -> bool:  # noqa: ANN001
    """Show what a redo would touch, ask (interactive), back the database up and reset the jobs."""
    from autoapp.steps import redo

    p = redo.plan(ctx.conn)
    for line in redo.describe(p):
        typer.echo(line)
    if dry_run:
        typer.echo("Dry run: nothing was reset and nothing was run.")
        return False
    if not p.total:
        typer.echo("Nothing to redo.")
        return True
    if interactive:
        from autoapp.setup.wizard import make_context

        prompter = prompter or make_context(ctx.ws).prompter
        if not prompter.confirm(f"Process these {p.total} jobs again? Evaluating them uses the AI model again, and "
                                "your old letters are kept with a timestamp.", default=False):
            typer.echo("Nothing changed.")
            return False
    typer.echo(f"Database backed up to {redo.backup_database(ctx)}")
    redo.redo_unsent(ctx)
    typer.echo(f"{p.total} jobs are back to new. Limits per run apply "
               f"({ctx.config.limits.max_evaluate_per_run} evaluations, {ctx.config.limits.max_prepare_per_run} "
               "applications); a normal run continues with the rest.")
    return True


@app.command()
def export(tctx: typer.Context) -> None:
    """Write applications.csv (it is also refreshed after every command)."""
    with session(tctx, start_run=False) as ctx:
        typer.echo(f"Wrote {ctx.ws.csv_path}")


@app.command()
def stats(
    tctx: typer.Context,
    period: str = typer.Option("today", "--period", "-p", help="run, today, 7d, 30d or all."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Print once, never wait for keys."),
) -> None:
    """Show statistics: models, ads, approvals, emails, forms, sources and cost. Live while a run is active."""
    from autoapp.dashboard import show_stats
    from autoapp.dashboard.queries import PERIODS

    if period not in PERIODS:
        typer.echo(f"error: unknown period {period!r}; valid periods: {', '.join(PERIODS)}", err=True)
        raise typer.Exit(code=2)
    show_stats(get_context(tctx).workspace, period, interactive=_interactive(non_interactive))
