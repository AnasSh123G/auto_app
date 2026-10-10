"""Live view controller (14.5), plain fallback (14.6), watching from another process (14.7)."""

from __future__ import annotations

import io
import re
import threading
import time

from rich.console import Console

from autoapp.dashboard import show_stats, watch_run
from autoapp.dashboard.keys import ScriptedKeys
from autoapp.dashboard.live import PlainPrinter, can_use_live
from autoapp.dashboard.screen import TaskScreen
from autoapp.dashboard.wiring import attach_run_events
from autoapp.events import RunEvent
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.steps.pipeline import run_pipeline
from tests.steps.test_pipeline import _ctx, _source
from tests.steps.test_prepare_step import FakeRenderer, factory

ANSI = re.compile(r"\x1b\[")


def _console(width=120):
    return Console(file=io.StringIO(), width=width, height=40, force_terminal=True, color_system=None)


def _run(ctx, **kw):
    return run_pipeline(ctx, api_sources=[_source()], mailbox=FakeMailbox([]),
                        renderer_factory=factory(FakeRenderer([1])), mailer=FakeMailer(), **kw)


def test_the_screen_shows_the_runs_events_and_counts(tmp_path):
    ctx = _ctx(tmp_path)
    stats = attach_run_events(ctx)
    screen = TaskScreen(stats, capture=False)  # not started: only its log and counters are looked at
    screen.watch(ctx.events, apply_stats=False)
    s = _run(ctx)
    assert s.ok and stats.finished
    kinds = [e.kind for e in screen.entries if isinstance(e, RunEvent)]
    assert kinds[-2:] == ["folder_saved", "email_sent"]
    assert any("SENT" in line for line in screen._log_view())
    ctx.close()


def test_plain_output_is_one_line_per_event_without_control_characters(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    attach_run_events(ctx)
    lines = []
    ctx.events.subscribe(PlainPrinter(lines.append))
    _run(ctx)
    assert any("SAVED" in ln for ln in lines) and any("SENT" in ln and "jobs@muster-gmbh.de" in ln for ln in lines)
    assert all("\n" not in ln and not ANSI.search(ln) for ln in lines)
    ctx.close()


def test_no_live_view_without_a_terminal_or_when_too_narrow():
    assert not can_use_live(_console(), io.StringIO())

    class Tty(io.StringIO):
        def isatty(self):
            return True

    assert can_use_live(_console(120), Tty())
    assert not can_use_live(_console(50), Tty())


def test_plain_printer_ignores_hidden_events():
    out = []
    p = PlainPrinter(out.append)
    p(RunEvent("llm_call"))
    p(RunEvent("step_started", detail="search"))
    p(RunEvent("email_held", company="A", detail="daily cap of 5 reached"))
    assert len(out) == 1 and "EMAIL HELD" in out[0]


# --- 14.7: watching from another process -----------------------------------------------------


def test_viewer_follows_a_running_run_and_closing_it_does_not_stop_it(tmp_path):
    from autoapp.steps.context import RunContext
    from tests.steps.test_other_steps import FakeSource
    from tests.steps.test_pipeline import EVAL
    from tests.steps.test_prepare_step import LETTER

    ws = _ctx(tmp_path).ws  # creates the workspace (the context is not used further)
    gate, started = threading.Event(), threading.Event()

    class SlowSource(FakeSource):
        def search(self, profile):
            started.set()
            gate.wait(10)  # the run stays in "search" until the viewer has looked
            yield from super().search(profile)

    result = {}

    def worker():
        from autoapp.llm.fake import FakeLLM

        ctx = RunContext.open(ws)  # its own connection, like a second process
        ctx.mode = "automatic"
        ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [EVAL]})
        ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
        attach_run_events(ctx)
        try:
            result["s"] = run_pipeline(ctx, api_sources=[SlowSource(_source().jobs)], mailbox=FakeMailbox([]),
                                       renderer_factory=factory(FakeRenderer([1])), mailer=FakeMailer())
        finally:
            ctx.close()

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(10)
    time.sleep(0.2)
    console = _console()
    assert watch_run(ws, console=console, keys=ScriptedKeys(), max_refreshes=1)
    assert "step: search" in console.file.getvalue()
    gate.set()  # the viewer is closed; the run goes on
    t.join(20)
    assert result["s"].ok and result["s"].send.sent
    console2 = _console()
    watch_run(ws, console=console2, keys=ScriptedKeys("x"))
    later = console2.file.getvalue()
    assert "SENT" in later and "finished" in later


def test_stats_page_switches_to_the_live_run(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    attach_run_events(ctx)
    ctx.emit("step_started", detail="evaluate")  # progress is written, run not finished
    seen = {}
    monkeypatch.setattr("autoapp.dashboard.pages.watch_run", lambda ws, **kw: seen.setdefault("watched", True))
    show_stats(ctx.ws, "today", interactive=True, console=_console(), keys=ScriptedKeys("q"))
    assert seen == {"watched": True}
    ctx.close()


def test_stale_run_is_marked(tmp_path):
    from datetime import UTC, datetime, timedelta

    ctx = _ctx(tmp_path)
    attach_run_events(ctx)
    ctx.emit("step_started", detail="prepare")
    console = _console()
    later = datetime.now(UTC) + timedelta(minutes=20)
    watch_run(ctx.ws, console=console, keys=ScriptedKeys(), max_refreshes=1, now=lambda: later)
    assert "STALE" in console.file.getvalue()
    ctx.close()


def test_stats_page_period_key_and_quit(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo.close_run(ctx.run_id)
    console = _console()
    show_stats(ctx.ws, "today", interactive=True, console=console, keys=ScriptedKeys("pq"))
    out = console.file.getvalue()
    assert "Statistics: today" in out and "Statistics: last 7 days" in out
    ctx.close()
