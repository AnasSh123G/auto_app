"""Connects a command's run to the event subscribers (database, log, live view)."""

from __future__ import annotations

from datetime import UTC, datetime

from autoapp.events import DbEventWriter, LogEventWriter, RunStats, StatsKeeper
from autoapp.llm.base import TASKS
from autoapp.llm.settings import LLMSettings
from autoapp.steps.context import RunContext

TASK_LABELS = {"evaluate": "evaluate", "write": "write", "form_fill": "assist form-fill", "form_find": "find form",
               "alert_extract": "read alert emails", "judge": "judge"}


def usage_text(calls: int, tokens: int, cost: float | None) -> str:
    """``7 calls, 11,699 tokens, $0.0019`` (``no calls`` when the task was not used)."""
    from autoapp.llm.cost import format_cost

    if not calls:
        return "no calls"
    return f"{calls} call{'s' if calls != 1 else ''}, {tokens:,} tokens, " + ("cost unknown" if cost is None else format_cost(cost))


def models_in_use(settings: LLMSettings) -> dict[str, str]:
    """``{"evaluate": "openrouter qwen/qwen3.8-flash (low)", ...}`` from the configuration."""
    out = {}
    for task in TASKS:
        provider, model, effort = settings.resolve(task)
        out[TASK_LABELS.get(task, task)] = f"{provider} {model}" + (f" ({effort})" if effort else "")
    return out


def attach_run_events(ctx: RunContext, secrets: list[str] | None = None) -> RunStats:
    """Subscribe the counters, the database writer and the log writer; return the live counters.

    ``secrets`` (and key-like tokens) are masked in every event before anything sees it.
    """
    from autoapp.logging_setup import RedactionFilter
    from autoapp.mail.send import sends_today

    ctx.events.redact = RedactionFilter(secrets).redact

    now = datetime.now(UTC)
    stats = RunStats(
        run_id=ctx.run_id, dry_run=ctx.dry_run, started_at=now.isoformat(timespec="seconds"),
        models=models_in_use(ctx.config.llm), daily_cap=ctx.config.email.daily_cap,
        sent_today_before=sends_today(ctx.repo, now),
    )
    ctx.stats = stats
    ctx.events.subscribe(StatsKeeper(stats))  # first, so the writers see updated counters
    ctx.events.subscribe(DbEventWriter(ctx.conn, ctx.run_id, stats))
    ctx.events.subscribe(LogEventWriter())
    return stats


def finish_run_events(ctx: RunContext, ok: bool = True) -> None:
    """Emit ``run_finished`` once (``run`` does it itself) and close the run row."""
    stats = getattr(ctx, "stats", None)
    if stats is not None and not stats.finished:
        ctx.emit("run_finished", ok=ok)
    ctx.repo.close_run(ctx.run_id)
