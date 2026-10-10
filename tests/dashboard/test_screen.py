"""The task screen: pinned statistics, scrollable log, questions at the bottom."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from autoapp.dashboard.screen import TaskScreen, split_heights, visible_slice
from autoapp.events import EventBus, RunEvent, RunStats
from autoapp.setup.prompts import SetupAborted

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def screen():
    with create_pipe_input() as inp:
        s = TaskScreen(RunStats(run_id="r", started_at=T0.isoformat()), input=inp, output=DummyOutput(),
                       capture=False, refresh_interval=0.05).start()
        s.keys = inp
        try:
            yield s
        finally:
            s.stop()


def _answer_soon(screen, keys: str) -> None:
    def send():
        while screen._req is None:
            time.sleep(0.01)
        screen.keys.send_text(keys)

    threading.Thread(target=send, daemon=True).start()


def test_heights_keep_the_statistics_and_a_log():
    assert split_heights(40, 16, 0) == (16, 22)
    assert split_heights(40, 16, 10) == (16, 12)
    stats, log = split_heights(20, 16, 6)  # too small: the statistics give up lines, the log keeps 3
    assert log == 3 and stats + log + 6 + 2 <= 20


def test_visible_slice_scrolls_back_and_clamps():
    assert visible_slice(100, 10, 0) == (90, 100, 0)
    assert visible_slice(100, 10, 30) == (60, 70, 30)
    assert visible_slice(100, 10, 500) == (0, 10, 90)
    assert visible_slice(5, 10, 3) == (0, 5, 0)


def test_select_text_and_confirm_are_answered_on_the_screen(screen):
    p = screen.prompter
    _answer_soon(screen, "\x1b[B\r")  # down, Enter
    assert p.select("Which one?", [("a", "First"), ("b", "Second")], default="a") == "b"
    _answer_soon(screen, "x\r\x7fhello\r")  # the first answer is refused by the check, then corrected
    assert p.text("Name", validate=lambda v: None if len(v) > 2 else "too short") == "hello"
    _answer_soon(screen, "n")
    assert p.confirm("Sure?") is False
    shown = "\n".join(e for e in screen.entries if isinstance(e, str))
    assert "Which one?" in shown and "Second" in shown and "hello" in shown


def test_ctrl_c_cancels_a_question(screen):
    _answer_soon(screen, "\x03")
    with pytest.raises(SetupAborted):
        screen.prompter.select("Pick", [("a", "A")])


def test_a_typed_line_can_pump_the_browser_meanwhile(screen):
    pumped = []
    _answer_soon(screen, "f\r")
    assert screen.ask_line("> ", pump=lambda: (pumped.append(1), time.sleep(0.01))) == "f"
    assert pumped


def test_events_of_other_sessions_count_but_do_not_end_the_run(screen):
    bus = EventBus()
    screen.watch(bus, apply_stats=True)
    bus.emit(RunEvent("form_filled", at=T0, company="ACME", title="Dev", detail="3 fields"))
    bus.emit(RunEvent("run_finished", at=T0, data={"ok": True}))
    assert not screen.stats.finished
    assert any(isinstance(e, RunEvent) and e.kind == "form_filled" for e in screen.entries)


def test_log_view_scrolls_and_keeps_its_place_when_lines_arrive(screen):
    for i in range(200):
        screen.add_line(f"line-{i:03d}")
    view = screen._log_view()
    assert view[-1].endswith("line-199")
    screen.scroll_by(50)
    view = screen._log_view()
    assert "line-199" not in "".join(view) and view[-1].endswith("line-149")
    screen.add_line("line-200")
    assert screen._log_view()[-1].endswith("line-149")  # still where it was
    screen.scroll_to_latest()
    assert screen._log_view()[-1].endswith("line-200")


def test_the_prompter_falls_back_when_the_screen_is_gone(screen, monkeypatch):
    screen.stop()
    calls = []

    class Plain:
        def select(self, message, choices, default=None):
            calls.append(message)
            return "a"

    p = screen.prompter
    p._fallback = Plain()
    assert p.select("Pick", [("a", "A")]) == "a" and calls == ["Pick"]
