"""Renderers for the statistics page and the live run view (pure functions of their data).

Everything here returns ``rich`` renderables and does no I/O, so tests can render to text at a
fixed width. Below ``COMPACT_WIDTH`` columns the panels are stacked as compact lists.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from autoapp.dashboard.queries import Stats
from autoapp.events import FEED_KINDS, LABEL_WIDTH, LABELS, RunEvent, RunStats
from autoapp.llm.cost import format_cost

COMPACT_WIDTH = 100
MIN_LIVE_WIDTH = 60

_FEED_STYLE = {
    "email_sent": "bold green",
    "email_would_send": "green",
    "email_held": "yellow",
    "email_failed": "bold red",
    "folder_saved": "bold cyan",
    "form_filled": "bold magenta",
    "draft_saved": "bold blue",
    "draft_would_save": "blue",
    "test_sent": "bold green",
    "test_would_send": "green",
    "error": "red",
    "warning": "yellow",
}


def _pct(share: float | None) -> str:
    return "n/a" if share is None else f"{share * 100:.0f} %"


def _form_counts(outcomes: dict[str, int]) -> str:
    from autoapp.formfinder.finder import LABELS

    return ", ".join(f"{LABELS[status]}: {n}" for status, n in outcomes.items()) or "none"


def _kv(title: str, rows: Iterable[tuple[str, object]], compact: bool) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    for key, value in rows:
        table.add_row(key, str(value))
    if compact:
        return Group(Text(title, style="bold"), table, Text(""))
    return Panel(table, title=title, title_align="left", border_style="dim")


def _model_table(st: Stats, compact: bool) -> RenderableType:
    """Calls, tokens and estimated cost of every model used in the period."""
    title = f"AI usage per model ({st.label})"
    if not st.per_model:
        return _kv(title, [("-", "no AI calls in this period")], compact)
    if compact:
        return _kv(title, [(f"{m.provider} {m.model}",
                            f"{m.calls} calls, {m.tokens:,} tokens, {format_cost(m.cost)}") for m in st.per_model], True)
    table = Table(box=None, padding=(0, 2), header_style="dim", expand=True)
    table.add_column("model", no_wrap=True)
    table.add_column("via", style="dim", no_wrap=True)
    for header in ("calls", "input tokens", "of which cached", "output tokens", "estimated cost"):
        table.add_column(header, justify="right", no_wrap=True)
    table.add_column("used for", style="dim", overflow="fold", min_width=18)
    for m in st.per_model:
        used_for = ", ".join(f"{task}\u00a0{n}" for task, n in sorted(m.tasks.items()))
        table.add_row(m.model, m.provider, f"{m.calls:,}", f"{m.input_tokens:,}", f"{m.cached_tokens:,}",
                      f"{m.output_tokens:,}", "unknown" if m.cost is None else format_cost(m.cost), used_for)
    return Panel(table, title=title, title_align="left", border_style="dim")


def _layout(panels: Sequence[RenderableType], compact: bool) -> RenderableType:
    if compact:
        return Group(*panels)
    return Columns(list(panels), equal=True, expand=True)


# --- statistics page ---------------------------------------------------------------------


def render_stats_page(st: Stats, width: int, *, interactive: bool = False) -> RenderableType:
    compact = width < COMPACT_WIDTH
    header = Text.assemble(("Statistics: ", "bold"), (st.label, "bold cyan"))
    if interactive:
        header.append("    p: change period   q: back", style="dim")
    parts: list[RenderableType] = [header, Text("")]
    if not st.has_runs:
        parts.append(Text("No runs yet. Start one with `autoapp run`; until then every count is zero.", style="yellow"))
        parts.append(Text(""))

    from autoapp.dashboard.wiring import TASK_LABELS, usage_text

    by_label = {TASK_LABELS.get(task, task): u for task, u in st.task_usage.items()}

    def used(label: str) -> str:
        u = by_label.get(label, {})
        return usage_text(u.get("calls", 0), u.get("input", 0) + u.get("output", 0), u.get("cost"))

    model_rows = [(label, f"{model}   {used(label)}") for label, model in st.models.items()]
    models = _kv(f"Models in use ({st.label})", model_rows or [("-", "not configured")], compact)
    ads = _kv("Ads", [
        ("read", st.ads_found), ("new", st.ads_new), ("duplicates", st.duplicates),
        ("filtered by rules", st.filtered), ("evaluated", st.evaluated),
        ("approved (high match)", f"{st.approved} ({_pct(st.approval_share)} of evaluated)"),
        ("maybe", st.maybe), ("skipped", st.skipped),
        ("average fit score", "n/a" if st.average_score is None else f"{st.average_score:.0f}"),
    ], compact)
    channels = ", ".join(f"{_channel(c)} {n}" for c, n in sorted(st.folders_by_channel.items())) or "none"
    held = ", ".join(f"{r} {n}" for r, n in sorted(st.emails_held.items())) or "none"
    apps = _kv("Applications", [
        ("folders saved", f"{st.folders_saved} ({channels})"), ("emails sent", st.emails_sent),
        ("emails held back", held), ("unconfirmed sends", st.send_unconfirmed),
        ("forms filled (assist)", st.forms_filled), ("marked submitted", st.submitted),
        ("form links", _form_counts(st.form_outcomes)),
        ("review queue", st.review_queue), ("flagged needs_review", st.needs_review),
    ], compact)
    src_rows: list[tuple[str, object]] = [(name, f"{s.found} found, {s.new} new")
                                          for name, s in sorted(st.sources.items())]
    src_rows += [(f"{name} feed", ", ".join(f"{key}: {value}" for key, value in sorted(s.details.items())))
                 for name, s in sorted(st.sources.items()) if s.details]
    src_rows += [("alert emails read", st.alert_emails_read), ("unknown senders", st.unrecognised_senders)]
    src_rows += [(f"Adzuna {label}", f"{used} of {cap} requests") for label, (used, cap) in st.adzuna.items()]
    src_rows += [(f"Arbeitnow {label}", f"{used} of {cap} requests") for label, (used, cap) in st.arbeitnow.items()]
    sources = _kv("Sources", src_rows, compact)
    cost = st.cost.total if st.cost else 0.0
    llm = _kv("AI usage (estimated)", [
        ("calls", st.llm_calls), ("tokens", f"{st.tokens:,}"),
        ("estimated cost", format_cost(cost)),
        ("per prepared application", format_cost(st.cost_per_application) if st.folders_saved else "n/a"),
    ], compact)
    statuses = _kv("Jobs by status (now)", list(st.status_counts.items()) or [("-", 0)], compact)
    brush = _kv("To brush up (most often required)", [
        *[(f"missing: {t}", n) for t, n in st.top_missing[:5]],
        *[(f"basic: {t}", n) for t, n in st.top_bridgeable[:5]],
    ] or [("-", "no evaluations in this period")], compact)
    parts.append(_layout([models, ads, apps], compact))
    parts.append(_layout([sources, llm, statuses], compact))
    parts.append(_kv("Judge: evaluator performance", [
        ("sampling", "every 20 successful evaluations (5%)"),
        ("reviews completed", st.judge_reviewed),
        ("performance score (mean)", "not sampled yet" if st.judge_score is None else f"{st.judge_score:.1f}/100"),
        ("disagreements", st.judge_disagreed), ("reviews unavailable", st.judge_failed),
    ], compact))
    parts.append(_model_table(st, compact))
    if st.employer is not None:
        e = st.employer
        scans = ", ".join(f"{n} {k}" for k, n in sorted(e.scans.items())) or "none"
        parts.append(_kv("Employer sites", [
            ("saved sites", f"{e.verified_sites} verified, {e.unverified} not verified"),
            ("scans", scans),
            ("alert ads found at the employer", f"{e.alerts_matched} ({e.alerts_recovered} descriptions recovered)"),
            ("alert ads unresolved", e.alerts_unresolved),
            ("new employer jobs", e.jobs_added),
            ("deferred alert jobs", e.deferred),
            ("search requests", f"{e.searches} (estimated ${e.estimated_usd:.3f} gross, not a bill)"),
            *[(f"search limit {label}", f"{used} of {cap}") for label, (used, cap) in e.search_limits.items()],
        ], compact))
    parts.append(brush)
    if st.last_run_problems:
        problems = Table.grid(padding=(0, 1))
        for kind, detail in st.last_run_problems:
            problems.add_row(Text(kind.upper(), style="red" if kind == "error" else "yellow"), Text(detail))
        parts.append(Panel(problems, title="Warnings and errors (latest run)", title_align="left",
                           border_style="dim") if not compact else Group(Text("Warnings and errors", style="bold"),
                                                                         problems))
    return Group(*parts)


def _channel(c: str) -> str:
    return {"email": "email", "employer_form": "employer form", "platform": "job-site form"}.get(c, c)


# --- live run view -----------------------------------------------------------------------


def _elapsed(started_at: str, now: datetime) -> str:
    try:
        secs = int((now - datetime.fromisoformat(started_at)).total_seconds())
    except ValueError:
        return "?"
    return f"{secs // 60}:{secs % 60:02d}"


def _task_model_lines(models: dict[str, str], usage: dict[str, dict]) -> list[Text]:
    """One line per task: its model, then the calls, tokens and cost it used (``models`` is keyed by label)."""
    from autoapp.dashboard.wiring import TASK_LABELS, usage_text

    by_label = {TASK_LABELS.get(task, task): u for task, u in usage.items()}
    lines = []
    for label, model in models.items():
        u = by_label.get(label) or {}
        lines.append(Text.assemble((f"{label}: ", "dim"), (model, ""), ("   ", ""),
                                   (usage_text(u.get("calls", 0), u.get("input", 0) + u.get("output", 0), u.get("cost")),
                                    "bold" if u.get("calls") else "dim")))
    return lines


def render_run_top(rs: RunStats, width: int, *, now: datetime | None = None, stale: bool = False) -> RenderableType:
    now = now or datetime.now(UTC)
    compact = width < COMPACT_WIDTH
    title = Text("Run ", style="bold")
    if rs.dry_run:
        title.append("DRY RUN ", style="bold black on yellow")
    if rs.mode:
        title.append(f"[{rs.mode}] ", style="bold")
    state = "finished" if rs.finished else (f"step: {rs.step}" if rs.step else "starting")
    if not rs.finished and rs.step == "fetch" and rs.details_total:
        state += f" (page {min(rs.details_processed + 1, rs.details_total)} of {rs.details_total}"
        if rs.details_wait_until:
            try:
                left = (datetime.fromisoformat(rs.details_wait_until) - now).total_seconds()
            except ValueError:
                left = 0
            if left > 0:
                state += f", waiting {left:.0f} s"
        state += ")"
    title.append(f"{state}   elapsed {_elapsed(rs.started_at, now)}", style="cyan")
    if stale:
        title.append("   STALE: no update for 15 minutes", style="bold red")
    last = {"review first": "draft", "test run": "test send"}.get(rs.mode or "", "send")
    steps = ["search", "fetch", "evaluate", "prepare", last]
    if rs.employers_seen or rs.step == "employer sites" or "employer sites" in rs.steps_done:
        steps.insert(1, "employer sites")
    progress = Text(" > ".join(steps), style="dim")
    for s in rs.steps_done:
        progress.highlight_words([s], style="green")
    if rs.step:
        progress.highlight_words([rs.step], style="bold yellow")
    if rs.mode == "review first":
        sent_label = "would draft" if rs.dry_run else "drafts saved"
        sent_value = rs.drafts_would_save if rs.dry_run else rs.drafts_saved
    elif rs.mode == "test run":
        sent_label = "test emails" if not rs.dry_run else "would test"
        sent_value = rs.test_emails
    else:
        sent_label = "would send" if rs.dry_run else "emails sent"
        sent_value = rs.emails_would_send if rs.dry_run else rs.emails_sent
    cost = "unknown" if rs.cost_unknown else format_cost(rs.cost_usd)
    counters = [
        ("ads read", rs.ads_found), ("new", rs.ads_new), ("filtered", rs.filtered),
        ("emails read", rs.alert_emails_read), ("jobs from email", rs.alert_email_jobs),
        ("evaluated", rs.evaluated), ("approved", f"{rs.approved} ({_pct(rs.approval_share)})"),
        ("judge reviews", rs.judge_reviewed),
        ("evaluator performance", "not sampled yet" if rs.judge_score is None else f"{rs.judge_score:.1f}/100"),
        ("judge disagreements", rs.judge_disagreed), ("judge unavailable", rs.judge_failed),
        ("folders saved", rs.folders_saved), ("emails prepared", rs.emails_prepared),
        (sent_label, sent_value), ("forms saved", rs.forms_saved),
        ("form links", _form_counts(rs.form_outcomes)),
        ("held back", rs.emails_held), ("errors", rs.errors),
        *([("employer sites", f"{rs.employers_seen} ({rs.employers_cached} saved)"),
           ("alerts at employer", f"{rs.employer_alerts_matched} ({rs.employer_alerts_recovered} recovered)"),
           ("employer jobs", rs.employer_jobs_added),
           ("searches", f"{rs.employer_searches} (~${rs.employer_search_usd:.3f})")] if rs.employers_seen else []),
        ("AI calls", rs.llm_calls), ("AI tokens", f"{rs.tokens:,}"),
        ("est. cost so far", cost),
        ("sent today", f"{rs.sent_today} of {rs.daily_cap}" if rs.daily_cap is not None else rs.sent_today),
    ]
    models = _task_model_lines(rs.models, rs.task_usage)
    if compact:
        lines = [Text(f"{k}: {v}") for k, v in counters]
        return Group(title, progress, *lines, Text("models:", style="dim"), *models)
    grid = Table.grid(padding=(0, 3), expand=True)
    columns = 4 if width >= 160 else 3 if width >= 120 else 2
    for _ in range(columns):
        grid.add_column()
    cells = [Text.assemble((f"{k} ", "dim"), (str(v), "bold")) for k, v in counters]
    for i in range(0, len(cells), columns):
        grid.add_row(*cells[i:i + columns], *[Text("")] * (columns - len(cells[i:i + columns])))
    return Panel(Group(title, progress, Text(""), grid, Text(""), Text("models", style="dim"), *models),
                 border_style="cyan")


def feed_line(e: RunEvent, width: int) -> Text:
    at = e.at.astimezone().strftime("%H:%M:%S")
    label = LABELS.get(e.kind, e.kind.upper())
    quiet = e.kind not in FEED_KINDS and e.kind not in ("error", "warning")
    style = "dim" if quiet else _FEED_STYLE.get(e.kind, "")
    if e.kind in ("detail_blocked", "detail_failed", "detail_closed"):
        style = "yellow"
    who = ", ".join(p for p in (e.company, e.title) if p)
    detail = e.detail or ""
    if e.kind in ("email_sent", "email_would_send", "draft_saved", "draft_would_save", "test_sent",
                  "test_would_send") and detail:
        detail = "→ " + detail
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(f"{at} ", style="dim")
    line.append(f"{label:<{LABEL_WIDTH}} ", style=style or None)
    budget = max(10, width - (LABEL_WIDTH + 11))  # time, tag and spacing
    # Keep room for the detail (the recipient or folder) by shortening the title first.
    # Page lines start with their progress ("[4/34] ..."), which must stay readable.
    reserve = min(len(detail) + 2, 70 if e.kind.startswith("detail") else 22) if detail else 0
    who_budget = max(10, budget - reserve)
    if who:
        shown = who if len(who) <= who_budget else who[: who_budget - 1] + "…"
        line.append(shown, style=None if not quiet else "dim")
        budget -= len(shown) + 2
    if detail and budget > 4:
        line.append(("  " if who else "") + (detail if len(detail) <= budget else detail[: budget - 1] + "…"),
                    style="dim")
    return line


def render_feed(events: Sequence[RunEvent], width: int, height: int, *, only_emails_and_forms: bool,
                scroll: int = 0) -> RenderableType:
    """The last ``height`` feed lines; ``scroll`` goes that many lines back in time."""
    all_shown = [e for e in events if e.level != "hidden"
                 and (not only_emails_and_forms or e.kind in FEED_KINDS)]
    height = max(1, height)
    scroll = max(0, min(scroll, len(all_shown) - height))
    end = len(all_shown) - scroll
    shown = all_shown[max(0, end - height):end]
    title = "Feed: emails and forms only (f: show everything)" if only_emails_and_forms else \
        "Feed (f: emails and forms only)"
    if scroll:
        title += f"  [{scroll} newer lines below: j/d down, G latest]"
    body: list[RenderableType] = [feed_line(e, width - 4) for e in shown] or [Text("Nothing yet.", style="dim")]
    return Panel(Group(*body), title=title, title_align="left", border_style="dim")


def _rendered_lines(renderable: RenderableType, width: int) -> int:
    """How many terminal lines ``renderable`` takes at ``width``."""
    console = Console(width=width, file=io.StringIO(), force_terminal=False, legacy_windows=False)
    return len(console.render_lines(renderable, pad=False))


def render_run_view(rs: RunStats, events: Sequence[RunEvent], width: int, height: int = 40, *,
                    only_emails_and_forms: bool = False, final: bool = False, stale: bool = False,
                    now: datetime | None = None, scroll: int = 0, waiting: bool = True) -> RenderableType:
    top = render_run_top(rs, width, now=now, stale=stale)
    if final:
        status = "finished without errors" if rs.ok is not False else "finished with errors"
        footer = Text(f"Run {status}." + (" Press any key to continue." if waiting else ""), style="bold")
    else:
        footer = Text("q: close this view (the run continues)   f: filter", style="dim")
    # The whole view must fit the terminal, or the live display cuts the bottom off. The feed gets what the
    # stats panel and the footer leave: its two border lines and one spare line are subtracted.
    used = _rendered_lines(top, width) + _rendered_lines(footer, width)
    feed_height = max(3, height - used - 3)
    parts = [top, render_feed(events, width, feed_height, only_emails_and_forms=only_emails_and_forms,
                              scroll=scroll), footer]
    return Group(*parts)


# --- text output for tests and plain mode -------------------------------------------------


def make_console(width: int | None = None, *, file=None, color: bool | None = None) -> Console:
    no_color = bool(os.environ.get("NO_COLOR")) if color is None else not color
    return Console(width=width, file=file, no_color=no_color, color_system=None if no_color else "auto",
                   highlight=False, soft_wrap=False)


def to_text(renderable: RenderableType, width: int) -> str:
    buf = io.StringIO()
    console = Console(width=width, file=buf, no_color=True, color_system=None, highlight=False,
                      force_terminal=False, legacy_windows=False)
    console.print(renderable)
    return buf.getvalue()
