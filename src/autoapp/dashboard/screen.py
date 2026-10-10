"""The task screen: statistics pinned at the top, a scrollable log below, questions at the bottom.

While a run (or form filling) is going on, the terminal belongs to one full-screen view, so nothing can push the
statistics out of sight:

* the **statistics** panel is always drawn at the top and refreshes on its own;
* the **log** below holds the run's events and everything printed meanwhile (summaries, assist messages,
  warnings). It scrolls with the mouse wheel or PgUp/PgDn; with no question open also with k/j, u/d and G;
* **questions** (menus, yes/no, typed answers) open at the bottom, under the log.

Everything written to the terminal while the screen is up (``print``, ``typer.echo``, logging, even a browser
process) is caught at the file-descriptor level and shown as log lines instead. When the task is over, the screen
closes, and the printed lines and the final statistics are written to the normal terminal, statistics last.

The view runs in a background thread; the task keeps the main thread (Playwright needs that). A question from the
task waits for its answer here; Ctrl-C with no question open interrupts the task as before.
"""

from __future__ import annotations

import _thread
import io
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console, RenderableType
from rich.text import Text

from autoapp.dashboard.view import feed_line, render_run_top
from autoapp.events import FEED_KINDS, EventBus, RunEvent, RunStats

log = logging.getLogger("autoapp")

LOG_KEEP = 5000
_LIFECYCLE = frozenset({"step_started", "step_finished", "run_finished"})
_LIST_KINDS = frozenset({"select", "checkbox", "confirm"})
_TEXT_KINDS = frozenset({"text", "line"})

_current: TaskScreen | None = None


def current() -> TaskScreen | None:
    """The screen that is up right now, if any."""
    return _current if _current is not None and _current.alive else None


class ScreenGone(RuntimeError):
    """The screen stopped (or failed) while a question was waiting: ask in the plain terminal instead."""


@dataclass
class _Request:
    kind: str  # select | checkbox | confirm | text | line
    message: str
    choices: list[tuple[Any, str]] = field(default_factory=list)
    default: Any = None
    text_default: str = ""
    validate: Callable[[str], str | None] | None = None
    password: bool = False
    multiline: bool = False
    cursor: int = 0
    checked: set[int] = field(default_factory=set)
    error: str = ""
    answer: Any = None
    cancelled: bool = False
    done: threading.Event = field(default_factory=threading.Event)


# --------------------------------------------------------------------------- pure helpers (tested directly)


def ansi_lines(renderable: RenderableType, width: int, color_system: str | None) -> list[str]:
    """``renderable`` as terminal lines (with colour codes) at ``width``."""
    buf = io.StringIO()
    Console(file=buf, width=max(10, width), force_terminal=color_system is not None, color_system=color_system,
            highlight=False, legacy_windows=False, soft_wrap=False).print(renderable)
    return buf.getvalue().rstrip("\n").split("\n")


def visible_slice(total: int, height: int, scroll: int) -> tuple[int, int, int]:
    """``(start, end, scroll)`` of the lines to show: the last ``height`` lines, ``scroll`` lines back in time."""
    height = max(1, height)
    scroll = max(0, min(scroll, total - height))
    end = total - scroll
    return max(0, end - height), end, scroll


def split_heights(rows: int, stats: int, prompt: int, *, chrome: int = 2, min_log: int = 3) -> tuple[int, int]:
    """``(stats, log)`` heights. The statistics shrink last; the log never gets fewer than ``min_log`` lines."""
    log_h = rows - stats - prompt - chrome
    if log_h >= min_log:
        return stats, log_h
    return max(0, rows - prompt - chrome - min_log), min_log


def _color_system() -> str | None:
    if os.environ.get("NO_COLOR"):
        return None
    return "truecolor" if os.environ.get("COLORTERM") in ("truecolor", "24bit") else "256"


# --------------------------------------------------------------------------- the screen


class TaskScreen:
    def __init__(self, stats: RunStats, *, activity: str = "", input=None, output=None,  # noqa: A002
                 capture: bool = True, refresh_interval: float = 0.5) -> None:
        self.stats = stats
        self.activity = activity  # "Now: ..." in the footer
        self.entries: deque[RunEvent | str] = deque(maxlen=LOG_KEEP)
        self.only_emails_and_forms = False
        self.scroll = 0
        self.final = False
        self.alive = False
        self.failed: BaseException | None = None
        self._input, self._output = input, output
        self._capture = capture
        self._refresh = refresh_interval
        self._colors = _color_system()
        self._lock = threading.RLock()
        self._req: _Request | None = None
        self._closed = threading.Event()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._saved_fds: dict[int, int] = {}
        self._readers: list[threading.Thread] = []
        self._term = None
        self._cache: dict[int, tuple[Any, int, list[str]]] = {}
        self._log_h = 10
        self._partial = {1: "", 2: ""}
        self.app = None

    # -- feeding -----------------------------------------------------------------------

    def watch(self, bus: EventBus, *, apply_stats: bool, stats: RunStats | None = None) -> None:
        """Show ``bus``'s events in the log. ``apply_stats`` also counts them (for sessions other than the run).

        A second session's start and end do not touch the pinned statistics' state. ``stats`` (that session's own
        counters) fills in what a screen started without a run does not know: the models in use and the send cap.
        """
        if stats is not None and stats is not self.stats:
            if not self.stats.models:
                self.stats.models = dict(stats.models)
            if not self.stats.daily_cap:
                self.stats.daily_cap = stats.daily_cap
                self.stats.sent_today_before = stats.sent_today_before

        def subscriber(e: RunEvent) -> None:
            if apply_stats and e.kind not in _LIFECYCLE:
                self.stats.apply(e)
            self.add_event(e)

        bus.subscribe(subscriber)

    def add_event(self, e: RunEvent) -> None:
        if e.level == "hidden":
            self._invalidate()
            return
        with self._lock:
            self.entries.append(e)
            if self.scroll:  # keep what is being read in place
                self.scroll += 1
        self._invalidate()

    def add_line(self, text: str) -> None:
        with self._lock:
            self.entries.append(text)
            if self.scroll:
                self.scroll += 1
        self._invalidate()

    def _invalidate(self) -> None:
        if self.app is not None and self.alive:
            try:
                self.app.invalidate()
            except Exception:  # noqa: BLE001 - drawing must never reach the task
                pass

    # -- start / stop -------------------------------------------------------------------

    def start(self) -> TaskScreen:
        global _current
        if self._capture:
            self._grab_terminal()
        self.app = self._build_app()
        self.alive = True
        self._thread = threading.Thread(target=self._app_main, name="autoapp-screen", daemon=True)
        self._thread.start()
        self._started.wait(5)
        if not self.alive:  # it could not even start: give the terminal back
            self._release_terminal()
            return self
        _current = self
        return self

    def _app_main(self) -> None:
        try:
            self.app.run(pre_run=self._started.set, handle_sigint=False)
        except BaseException as err:  # noqa: BLE001 - reported by the main thread
            self.failed = err
            log.warning("the run screen failed; continuing with plain output: %s", err, exc_info=True)
        finally:
            self.alive = False
            self._started.set()
            req = self._req
            if req is not None:
                req.done.set()

    def stop(self, *, wait_for_key: bool = False) -> None:
        """Close the screen and give the terminal back. ``wait_for_key`` first lets the log be read."""
        global _current
        if wait_for_key and self.alive:
            self.final = True
            self._invalidate()
            while self.alive and not self._closed.wait(0.2):
                pass
        if self.alive and self.app is not None:
            try:
                self.app.loop.call_soon_threadsafe(self._exit_app)
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.alive = False
        if _current is self:
            _current = None
        self._release_terminal()
        if self._capture:
            self._print_after()

    def _exit_app(self) -> None:
        if self.app is not None and self.app.is_running:
            self.app.exit()

    @contextmanager
    def suspended(self) -> Iterator[None]:
        """Give the terminal back for a while (an editor), then show the screen again."""
        self.stop_drawing()
        try:
            yield
        finally:
            self.resume_drawing()

    def stop_drawing(self) -> None:
        if self.alive and self.app is not None:
            self.app.loop.call_soon_threadsafe(self._exit_app)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.alive = False
        self._release_terminal()

    def resume_drawing(self) -> None:
        global _current
        self._started.clear()
        if self._capture:
            self._grab_terminal()
        self.app = self._build_app()
        self.alive = True
        self._thread = threading.Thread(target=self._app_main, name="autoapp-screen", daemon=True)
        self._thread.start()
        self._started.wait(5)
        _current = self if self.alive else None

    # -- the terminal ------------------------------------------------------------------

    def _grab_terminal(self) -> None:
        """Send fd 1 and 2 to the log; keep a copy of the real terminal for drawing."""
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
        self._term = os.fdopen(os.dup(1), "w", encoding="utf-8", errors="replace")
        for fd in (1, 2):
            self._saved_fds[fd] = os.dup(fd)
            r, w = os.pipe()
            os.dup2(w, fd)
            os.close(w)
            t = threading.Thread(target=self._read_pipe, args=(r, fd), name=f"autoapp-screen-fd{fd}", daemon=True)
            t.start()
            self._readers.append(t)

    def _read_pipe(self, r: int, fd: int) -> None:
        # A browser started meanwhile keeps a copy of the pipe; after the screen it goes to the real terminal.
        while True:
            try:
                data = os.read(r, 65536)
            except OSError:
                return
            if not data:
                os.close(r)
                return
            try:
                if self.alive:
                    self._take_output(fd, data.decode("utf-8", errors="replace"))
                else:  # the real terminal (fd itself once it is given back)
                    os.write(self._saved_fds.get(fd, fd), data)
            except Exception:  # noqa: BLE001 - this thread must keep draining the pipe
                pass

    def _take_output(self, fd: int, text: str) -> None:
        text = self._partial[fd] + text
        *lines, self._partial[fd] = text.split("\n")
        for line in lines:
            self.add_line(line.rstrip("\r").rsplit("\r", 1)[-1])  # a progress line keeps its last state

    def _release_terminal(self) -> None:
        if not self._saved_fds:
            return
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.05)  # let the readers take what was just flushed
        for fd, saved in self._saved_fds.items():
            os.dup2(saved, fd)
            os.close(saved)
            if self._partial.get(fd):
                self.entries.append(self._partial[fd])
                self._partial[fd] = ""
        self._saved_fds = {}
        if self._term is not None:
            try:
                self._term.close()
            except Exception:  # noqa: BLE001
                pass
            self._term = None

    def _print_after(self) -> None:
        """What the screen showed, for the normal terminal: the printed lines, then the final statistics."""
        console = Console(highlight=False, soft_wrap=False)
        for entry in list(self.entries):
            if isinstance(entry, str):
                console.print(Text.from_ansi(entry))
        console.print(render_run_top(self.stats, console.width))
        path = _log_path()
        if path:
            console.print(Text(f"Every line of this run is in {path}", style="dim"))

    # -- drawing -----------------------------------------------------------------------

    def _build_app(self):
        from prompt_toolkit.application import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.filters import Condition, has_focus
        from prompt_toolkit.formatted_text import ANSI
        from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
        from prompt_toolkit.key_binding.defaults import load_key_bindings
        from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.layout.processors import ConditionalProcessor, PasswordProcessor
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.output import create_output

        screen = self
        self._buffer = Buffer(multiline=Condition(lambda: bool(self._req and self._req.multiline)))

        class LogControl(FormattedTextControl):
            def mouse_handler(self, mouse_event):  # noqa: ANN001
                if mouse_event.event_type == MouseEventType.SCROLL_UP:
                    screen.scroll_by(3)
                    return None
                if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                    screen.scroll_by(-3)
                    return None
                return NotImplemented

        stats_win = Window(FormattedTextControl(lambda: ANSI("\n".join(self._stats_lines()))),
                           height=lambda: Dimension.exact(self._heights()[0]), wrap_lines=False)
        log_title = Window(FormattedTextControl(self._log_title), height=1, style="class:title")
        self._log_win = Window(LogControl(lambda: ANSI("\n".join(self._log_view())), focusable=True),
                               height=lambda: Dimension.exact(self._heights()[1]), wrap_lines=False)
        prompt_win = Window(FormattedTextControl(self._prompt_text),
                            height=lambda: Dimension.exact(self._prompt_lines()), wrap_lines=False)
        is_text = Condition(lambda: bool(self._req and self._req.kind in _TEXT_KINDS))
        self._input_win = Window(
            BufferControl(self._buffer, input_processors=[
                ConditionalProcessor(PasswordProcessor(), Condition(lambda: bool(self._req and self._req.password)))]),
            height=lambda: Dimension.exact(5 if self._req and self._req.multiline else 1))
        footer = Window(FormattedTextControl(self._footer), height=1)
        root = HSplit([stats_win, log_title, self._log_win, prompt_win, ConditionalContainer(self._input_win, is_text),
                       footer])

        kb = KeyBindings()
        listing = Condition(lambda: bool(self._req and self._req.kind in _LIST_KINDS))
        idle = Condition(lambda: self._req is None)
        free_keys = idle | listing  # single letters are free when nothing is being typed
        typing = has_focus(self._buffer) & is_text

        @kb.add("pageup")
        def _(event) -> None:  # noqa: ANN001
            self.scroll_by(max(1, self._log_h - 1))

        @kb.add("pagedown")
        def _(event) -> None:  # noqa: ANN001
            self.scroll_by(-max(1, self._log_h - 1))

        @kb.add("c-c")
        def _(event) -> None:  # noqa: ANN001
            if self._req is not None:
                self._finish(cancelled=True)
            elif self.final:
                self._closed.set()
            else:
                _thread.interrupt_main()  # Ctrl-C stops the task, as it always did

        kb.add("k", filter=idle)(lambda event: self.scroll_by(1))
        kb.add("j", filter=idle)(lambda event: self.scroll_by(-1))
        kb.add("u", filter=idle)(lambda event: self.scroll_by(max(1, self._log_h // 2)))
        kb.add("d", filter=idle)(lambda event: self.scroll_by(-max(1, self._log_h // 2)))
        kb.add("G", filter=free_keys)(lambda event: self.scroll_to_latest())
        kb.add("end", filter=~typing)(lambda event: self.scroll_to_latest())

        @kb.add("f", filter=idle)
        def _(event) -> None:  # noqa: ANN001
            self.only_emails_and_forms = not self.only_emails_and_forms
            self.scroll = 0

        @kb.add("q", filter=idle & Condition(lambda: self.final))
        @kb.add("enter", filter=idle & Condition(lambda: self.final))
        @kb.add("escape", filter=idle & Condition(lambda: self.final))
        def _(event) -> None:  # noqa: ANN001
            self._closed.set()

        @kb.add("up", filter=listing)
        def _(event) -> None:  # noqa: ANN001
            self._move(-1)

        @kb.add("down", filter=listing)
        def _(event) -> None:  # noqa: ANN001
            self._move(1)

        @kb.add(" ", filter=listing)
        def _(event) -> None:  # noqa: ANN001
            req = self._req
            if req is not None and req.kind == "checkbox":
                req.checked ^= {req.cursor}

        @kb.add("y", filter=listing)
        @kb.add("n", filter=listing)
        def _(event) -> None:  # noqa: ANN001
            req = self._req
            if req is not None and req.kind == "confirm":
                req.cursor = 0 if event.data == "y" else 1
                self._submit()

        @kb.add("enter", filter=listing)
        def _(event) -> None:  # noqa: ANN001
            self._submit()

        @kb.add("enter", filter=typing & ~Condition(lambda: bool(self._req and self._req.multiline)))
        @kb.add("escape", "enter", filter=typing)
        def _(event) -> None:  # noqa: ANN001
            self._submit()

        kwargs: dict[str, Any] = {}
        if self._input is not None:
            kwargs["input"] = self._input
        if self._output is not None:
            kwargs["output"] = self._output
        elif self._term is not None:
            kwargs["output"] = create_output(stdout=self._term)
        app = Application(layout=Layout(root, focused_element=self._log_win),
                          key_bindings=merge_key_bindings([load_key_bindings(), kb]), full_screen=True,
                          mouse_support=True, refresh_interval=self._refresh, erase_when_done=True, **kwargs)
        return app

    def _width(self) -> int:
        try:
            return self.app.output.get_size().columns
        except Exception:  # noqa: BLE001
            return 100

    def _rows(self) -> int:
        try:
            return self.app.output.get_size().rows
        except Exception:  # noqa: BLE001
            return 40

    def _stats_lines(self) -> list[str]:
        try:
            lines = ansi_lines(render_run_top(self.stats, self._width()), self._width(), self._colors)
        except Exception as err:  # noqa: BLE001 - a broken panel must not stop the task
            lines = [f"(statistics could not be drawn: {err})"]
        return lines[: self._heights_for(len(lines))[0]]

    def _heights_for(self, stats_len: int) -> tuple[int, int]:
        return split_heights(self._rows(), stats_len, self._prompt_lines() + self._input_lines())

    def _heights(self) -> tuple[int, int]:
        try:
            n = len(ansi_lines(render_run_top(self.stats, self._width()), self._width(), None))
        except Exception:  # noqa: BLE001
            n = 1
        stats_h, self._log_h = self._heights_for(n)
        return stats_h, self._log_h

    def _input_lines(self) -> int:
        req = self._req
        if req is None or req.kind not in _TEXT_KINDS:
            return 0
        return 5 if req.multiline else 1

    def _entry_lines(self, entry: RunEvent | str, width: int) -> list[str]:
        key = id(entry)
        hit = self._cache.get(key)
        if hit is not None and hit[0] is entry and hit[1] == width:
            return hit[2]
        renderable = feed_line(entry, width) if isinstance(entry, RunEvent) else Text.from_ansi(entry)
        lines = ansi_lines(renderable, width, self._colors)
        if len(self._cache) > LOG_KEEP * 2:
            self._cache.clear()
        self._cache[key] = (entry, width, lines)
        return lines

    def _log_view(self) -> list[str]:
        width = self._width()
        with self._lock:
            entries = [e for e in self.entries if not self.only_emails_and_forms or isinstance(e, str)
                       or e.kind in FEED_KINDS]
        height = self._log_h
        # Only the newest entries can be on screen: render from the end until enough lines are collected.
        need = height + self.scroll
        lines: list[str] = []
        for i in range(len(entries) - 1, -1, -1):
            lines[:0] = self._entry_lines(entries[i], width)
            if len(lines) >= need:
                break
        start, end, self.scroll = visible_slice(len(lines), height, self.scroll)
        return lines[start:end] or [""]

    def _log_title(self):
        title = "Log: emails and forms only (f: everything)" if self.only_emails_and_forms else "Log"
        hint = "  mouse wheel / PgUp PgDn: scroll"
        if self.scroll:
            hint += f"   [{self.scroll} newer lines below: End or G for the latest]"
        return [("bold", title), ("class:dim fg:ansibrightblack", hint)]

    def _footer(self):
        if self.final:
            return [("bold", "Finished. Scroll the log above, then press q or Enter to close.")]
        now = [("bold", f"Now: {self.activity}   ")] if self.activity else []
        if self._req is None:
            keys = "f: filter   k/j u/d: scroll   G: latest   Ctrl-C: stop"
        elif self._req.kind in _LIST_KINDS:
            keys = "↑/↓: choose   Enter: confirm" + ("   Space: tick" if self._req.kind == "checkbox" else "") + \
                "   PgUp/PgDn: scroll the log   Ctrl-C: cancel"
        else:
            keys = ("Esc then Enter: send" if self._req.multiline else "Enter: send") + \
                "   PgUp/PgDn: scroll the log   Ctrl-C: cancel"
        return [*now, ("fg:ansibrightblack", keys)]

    def _visible_choices(self, req: _Request) -> tuple[int, int]:
        n = min(len(req.choices), max(3, self._rows() // 3))
        start = max(0, min(req.cursor - n // 2, len(req.choices) - n))
        return start, start + n

    def _prompt_lines(self) -> int:
        req = self._req
        if req is None:
            return 0
        lines = 1 + (1 if req.error else 0)
        if req.kind in ("select", "checkbox"):
            start, end = self._visible_choices(req)
            lines += end - start
        return lines

    def _prompt_text(self):
        req = self._req
        if req is None:
            return []
        width = self._width()
        out: list[tuple[str, str]] = [("fg:ansicyan bold", "? "), ("bold", _clip(req.message, width - 12))]
        if req.kind == "confirm":
            out.append(("", "  (Y/n)" if req.default else "  (y/N)"))
        out.append(("", "\n"))
        if req.kind in ("select", "checkbox"):
            start, end = self._visible_choices(req)
            for i in range(start, end):
                _, label = req.choices[i]
                mark = ("[x] " if i in req.checked else "[ ] ") if req.kind == "checkbox" else ""
                more = "↑ " if i == start and start > 0 else ("↓ " if i == end - 1 and end < len(req.choices) else "  ")
                style = "fg:ansicyan bold" if i == req.cursor else ""
                out.append((style, f"{'❯' if i == req.cursor else ' '} {more}{mark}{_clip(label, width - 10)}\n"))
        if req.error:
            out.append(("fg:ansired", req.error + "\n"))
        if out and out[-1][1].endswith("\n"):
            out[-1] = (out[-1][0], out[-1][1][:-1])
        return out

    # -- scrolling ---------------------------------------------------------------------

    def scroll_by(self, lines: int) -> None:
        self.scroll = max(0, self.scroll + lines)  # clamped when drawn
        self._invalidate()

    def scroll_to_latest(self) -> None:
        self.scroll = 0
        self._invalidate()

    # -- questions ---------------------------------------------------------------------

    def ask(self, req: _Request, pump: Callable[[], None] | None = None) -> _Request:
        """Show ``req`` and wait for its answer (calling ``pump`` meanwhile). Raises ``ScreenGone`` if the screen
        is not there to answer it."""
        if not self.alive or self.app is None:
            raise ScreenGone()
        try:
            self.app.loop.call_soon_threadsafe(self._open, req)
        except Exception as err:  # noqa: BLE001
            raise ScreenGone() from err
        while not req.done.is_set():
            if not self.alive:
                raise ScreenGone()
            if pump is not None:
                pump()
            else:
                req.done.wait(0.1)
        if not self.alive and req.answer is None and not req.cancelled:
            raise ScreenGone()
        return req

    def _open(self, req: _Request) -> None:
        from prompt_toolkit.document import Document

        self._req = req
        self.scroll = 0
        if req.kind in _TEXT_KINDS:
            self._buffer.document = Document(req.text_default, len(req.text_default))
            self.app.layout.focus(self._input_win)
        else:
            self.app.layout.focus(self._log_win)
        self._invalidate()

    def _move(self, step: int) -> None:
        req = self._req
        if req is not None and req.choices:
            req.cursor = (req.cursor + step) % len(req.choices)

    def _submit(self) -> None:
        req = self._req
        if req is None:
            return
        if req.kind in _TEXT_KINDS:
            value = self._buffer.text
            if req.validate is not None:
                problem = req.validate(value)
                if problem:
                    req.error = problem
                    return
            req.answer = value
            shown = "*" * min(len(value), 8) if req.password else value
        elif req.kind == "checkbox":
            req.answer = [req.choices[i][0] for i in sorted(req.checked)]
            shown = ", ".join(req.choices[i][1] for i in sorted(req.checked)) or "(none)"
        else:
            req.answer = req.choices[req.cursor][0]
            shown = req.choices[req.cursor][1]
        self.add_line(f"\x1b[36m?\x1b[0m {req.message}  \x1b[36m{shown}\x1b[0m")
        self._finish()

    def _finish(self, *, cancelled: bool = False) -> None:
        req = self._req
        if req is None:
            return
        req.cancelled = cancelled
        if cancelled:
            self.add_line(f"? {req.message}  (cancelled)")
        self._req = None
        self._buffer.reset()
        self.app.layout.focus(self._log_win)
        req.done.set()

    def ask_line(self, prompt: str, pump: Callable[[], None] | None = None) -> str:
        """A typed answer (the assist session's questions). Ctrl-C raises KeyboardInterrupt, like ``input()``."""
        message = prompt.strip() or "Your answer"
        req = self.ask(_Request("line", message), pump)
        if req.cancelled:
            raise KeyboardInterrupt
        return str(req.answer or "")

    @property
    def prompter(self) -> ScreenPrompter:
        return ScreenPrompter(self)


def _clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= max(4, width) else text[: max(3, width) - 1] + "…"


def _log_path() -> str | None:
    for handler in logging.getLogger("autoapp").handlers:
        name = getattr(handler, "baseFilename", None)
        if name:
            return name
    return None


class ScreenPrompter:
    """The wizard's ``Prompter`` on the task screen. Falls back to the terminal prompts if the screen is gone."""

    def __init__(self, screen: TaskScreen) -> None:
        self.screen = screen
        self._fallback = None

    def _plain(self):
        if self._fallback is None:
            from autoapp.setup.prompts import QuestionaryPrompter

            self._fallback = QuestionaryPrompter()
        return self._fallback

    def _ask(self, req: _Request) -> Any:
        from autoapp.setup.prompts import SetupAborted

        req = self.screen.ask(req)
        if req.cancelled:
            raise SetupAborted()
        return req.answer

    def say(self, text: str = "") -> None:
        if self.screen.alive:
            for line in str(text).split("\n"):
                self.screen.add_line(line)
        else:
            self._plain().say(text)

    def table(self, title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
        from rich.table import Table

        t = Table(title=title, title_justify="left")
        for h in headers:
            t.add_column(h)
        for row in rows:
            t.add_row(*[str(c) for c in row])
        if not self.screen.alive:
            return self._plain().table(title, headers, rows)
        for line in ansi_lines(t, self.screen._width() - 2, self.screen._colors):
            self.screen.add_line(line)
        return None

    def text(self, message: str, default: str = "", validate=None) -> str:  # noqa: ANN001
        try:
            return str(self._ask(_Request("text", message, text_default=default, validate=validate)))
        except ScreenGone:
            return self._plain().text(message, default, validate)

    def password(self, message: str) -> str:
        try:
            return str(self._ask(_Request("text", message, password=True)))
        except ScreenGone:
            return self._plain().password(message)

    def multiline(self, message: str, default: str = "") -> str:
        try:
            return str(self._ask(_Request("text", message, text_default=default, multiline=True)))
        except ScreenGone:
            return self._plain().multiline(message, default)

    def confirm(self, message: str, default: bool = True) -> bool:
        try:
            return bool(self._ask(_Request("confirm", message, choices=[(True, "Yes"), (False, "No")],
                                           default=default, cursor=0 if default else 1)))
        except ScreenGone:
            return self._plain().confirm(message, default)

    def confirm_or_back(self, message: str, default: bool = True) -> Any:
        from autoapp.setup.prompts import BACK

        try:
            return self._ask(_Request("select", message, choices=[(True, "Yes"), (False, "No"),
                                                                  (BACK, "← Back to the previous question")],
                                      cursor=0 if default else 1))
        except ScreenGone:
            return self._plain().confirm_or_back(message, default)

    def select(self, message: str, choices: Sequence[tuple[Any, str]], default: Any = None) -> Any:
        items = list(choices)
        cursor = next((i for i, (v, _) in enumerate(items) if v == default), 0)
        try:
            return self._ask(_Request("select", message, choices=items, default=default, cursor=cursor))
        except ScreenGone:
            return self._plain().select(message, choices, default)

    def checkbox(self, message: str, choices: Sequence[tuple[Any, str]], defaults: Sequence[Any] = ()) -> list:
        items = list(choices)
        checked = {i for i, (v, _) in enumerate(items) if v in defaults}
        try:
            return list(self._ask(_Request("checkbox", message, choices=items, checked=checked)))
        except ScreenGone:
            return self._plain().checkbox(message, choices, defaults)

    def edit_file(self, path: Path) -> None:
        if not self.screen.alive:
            return self._plain().edit_file(path)
        with self.screen.suspended():
            self._plain().edit_file(path)
        return None


# --------------------------------------------------------------------------- entry points


def can_show() -> bool:
    from autoapp.dashboard.live import can_use_live
    from autoapp.dashboard.view import make_console

    try:
        return can_use_live(make_console()) and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@contextmanager
def task_screen(stats: RunStats | None = None, *, activity: str = "", enabled: bool = True,
                wait_at_end: bool = False, bus: EventBus | None = None) -> Iterator[TaskScreen | None]:
    """Show the task screen for the block, unless one is up already (then that one is used) or there is no
    terminal for it. Sessions opened inside the block count into its statistics; ``bus`` is the events of the
    session ``stats`` belongs to (shown, already counted)."""
    up = current()
    if up is not None:
        previous, up.activity = up.activity, activity or up.activity
        try:
            yield up
        finally:
            up.activity = previous
        return
    if not enabled or not can_show():
        yield None
        return
    screen = TaskScreen(stats or RunStats(started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                                          mode=activity or None, step=activity or None), activity=activity).start()
    if not screen.alive:
        yield None
        return
    if bus is not None:
        screen.watch(bus, apply_stats=stats is None)
    try:
        yield screen
    except BaseException:
        screen.stop()
        raise
    screen.stop(wait_for_key=wait_at_end)


def echo_color() -> bool | None:
    """``color=`` for ``typer.echo``: keep colours while the screen catches the output (it is no terminal then)."""
    return True if current() is not None else None
