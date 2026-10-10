"""The ``assist <id>`` session (task 11.7).

Opens the application page in a visible browser with the persistent profile (so manual
logins are kept), fills the current page only when the user asks, and never submits.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autoapp.assist.answers import (
    WORKSPACE_HEADER,
    Answered,
    ask_open_fields,
    assignment_for,
    field_label,
    is_long,
    question_key,
    read_sections,
    write_section,
)
from autoapp.assist.fields import FieldInfo, collect_fields
from autoapp.assist.filler import FillReport, fill_page
from autoapp.assist.llm_fill import eligible, fill_unmatched
from autoapp.assist.mapper import FillValues, map_fields
from autoapp.assist.snapshot import save_snapshot, snapshot_html
from autoapp.db.status import Status
from autoapp.evaluation.schema import FinalEvaluation
from autoapp.llm.base import LLMError
from autoapp.steps.context import RunContext, profile_prompt_text
from autoapp.writing.prompt import JobContext, WritingContext

MENU = "[f] fill the form now showing  [o] open the job page again  [h] show my prepared answers  [s] I submitted it  [q] back"


class NotPrepared(Exception):
    """The job has no application folder yet."""


def _folder_files(folder: Path, profile, documents=None) -> dict[str, Any]:
    """The prepared files for one application.

    CV, letter, certificates and the bundle come from the application folder. Work references (Arbeitszeugnisse)
    and ``other`` documents come from the folder when they were copied there, otherwise straight from
    ``documents/`` (files there are only read, never changed).
    """
    names = {p.name for p in folder.iterdir()}
    cv = next((folder / e.file for e in profile.documents if e.role == "cv" and e.file in names), None)
    certs = [folder / e.file for e in profile.documents if e.role == "certificate" and e.file in names]

    def of_role(role: str) -> list[Path]:
        found = []
        for e in profile.documents:
            if e.role != role:
                continue
            if e.file in names:
                found.append(folder / e.file)
            elif documents is not None and (documents.directory / e.file).exists():
                found.append(documents.directory / e.file)
        return found

    return {
        "cv": cv,
        "cover_letter_pdf": folder / "cover_letter.pdf" if "cover_letter.pdf" in names else None,
        "certificates": certs,
        "work_references": of_role("work_reference"),
        "other_documents": of_role("other"),
        "work_samples": of_role("writing_sample"),
        "bundle_pdf": folder / "Bewerbungsmappe.pdf" if "Bewerbungsmappe.pdf" in names else None,
    }


def _blue(path: object) -> str:
    """A location in blue in a terminal (plain text when piped or when NO_COLOR is set)."""
    from autoapp.dashboard.screen import current

    text = str(path)
    if (sys.stdout.isatty() or current() is not None) and not os.environ.get("NO_COLOR"):
        return f"\033[94m{text}\033[0m"
    return text


def _has_saved(f: FieldInfo, saved: dict[str, tuple[str, str]]) -> bool:
    """True when an earlier answer to this very question fits this field."""
    hit = saved.get(question_key(field_label(f)))
    return bool(hit and assignment_for(f, hit[1]))


def _motivation(folder: Path) -> str | None:
    """The motivation text written into form_answers.md during prepare."""
    path = folder / "form_answers.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if "## Motivation" not in text:
        return None
    block = text.split("## Motivation", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in block.splitlines()[1:] if ln.strip()]
    lines = [ln for ln in lines if not (ln.strip().startswith("_") and ln.strip().endswith("_"))]  # hint line
    value = "\n".join(lines).strip()
    return None if value in ("", "not set") else value
    return None


class AssistSession:
    def __init__(self, ctx: RunContext, job_id: str, *, ask: Callable[[str], str] | None = None,
                 say: Callable[[str], None] = print, fill: Callable[..., FillReport] = fill_page,
                 use_llm: bool | None = None, ask_questions: bool = True) -> None:
        self.ctx = ctx
        self.job_id = job_id
        # The terminal question. The default keeps the browser working while it waits (see ``_ask_pumping``);
        # tests pass their own function.
        self.ask = ask or self._ask_pumping
        self.say = say
        self.fill = fill
        job = ctx.repo.get_job(job_id)
        app = ctx.repo.get_application(job_id)
        if job is None:
            raise NotPrepared(f"unknown job {job_id}")
        if app is None or not app["folder"] or not Path(app["folder"]).exists():
            raise NotPrepared(f"job {job_id} has no application folder; run `autoapp prepare --job {job_id}` first")
        self.job, self.app = job, app
        self.folder = Path(app["folder"])
        self.url = job["application_url"] or job["job_url"]
        self.use_llm = ctx.config.assist.llm_answers if use_llm is None else use_llm
        self.scope = None
        self.skipped_answers = []
        self.model_assignments = []
        self.guard = None
        self.page = None
        self.context = None
        self.browser_closed = False
        self.ask_questions = ask_questions
        self._load_values()

    def _reload(self) -> None:
        """Read the job and application again after the folder may have been renamed (its files moved with it)."""
        self.app = self.ctx.repo.get_application(self.job_id)
        self.job = self.ctx.repo.get_job(self.job_id)
        self.folder = Path(self.app["folder"])
        self._load_values()

    def _load_values(self) -> None:
        """What may be put into a form. Read again when the folder was renamed: the files moved with it."""
        files = _folder_files(self.folder, self.ctx.profile, self.ctx.documents)
        self.values = FillValues.from_profile(
            self.ctx.profile, salary_expectation=self.app["salary_expectation"], motivation=_motivation(self.folder),
            company=self.job["company"], language=self.app["language"], **files
        )

    # -- filling ---------------------------------------------------------------------

    def _plan(self, page) -> tuple[list, list[str], list]:
        """What the model may add, texts to check, and the fields only the candidate can answer."""
        self.skipped_answers = []
        self.model_assignments = []  # never carried over: field ids repeat from page to page
        try:
            fields = collect_fields(page, restricted_domains=self.ctx.config.assist.restricted_domains,
                                    scope=self.scope)
        except Exception:  # noqa: BLE001 - a page that cannot be read has nothing to plan
            return [], [], []
        mapping = map_fields(fields, self.values)
        unmatched = eligible(mapping.unmatched)
        model_reads = self.use_llm and self.ctx.config.assist.fill_mode == "model"
        if model_reads:
            # The model sees every open field; only the fixed answers (and uploads, consent) stay with the rules.
            pending = [f for f in eligible(fields) if f.field_id not in mapping.fixed_ids]
        else:
            saved = read_sections(self.ctx.ws.form_answers_path)
            pending = [f for f in unmatched if not _has_saved(f, saved)]  # a saved answer needs no model
        if not unmatched and not pending:
            return [], [], []
        extra, review = [], []
        if self.use_llm and pending:
            rule_values = {a.field_id: a.value for a in mapping.assignments if a.field_id not in mapping.fixed_ids}
            try:
                extra, review = self._extra_assignments(
                    pending, material=self._material() if model_reads else None,
                    rule_values=rule_values if model_reads else None)
                if model_reads:
                    self.model_assignments = extra
            except LLMError as err:
                # No written answers this time, but your name, contact data, salary and uploads are still filled.
                self.say(f"Written answers were skipped: {err}. The facts from your profile are still filled.")
        done = {a.field_id for a in extra}
        return extra, review, [f for f in unmatched if f.field_id not in done]

    def _ask_open(self, open_fields: list) -> list[Answered]:
        """Ask the candidate the personal questions nothing else could answer, and keep the answers."""
        if not open_fields or not self.ask_questions:
            return []
        saved = read_sections(self.ctx.ws.form_answers_path)
        if any(not _has_saved(f, saved) for f in open_fields):
            self.say("These questions need your own answer. Press Enter to leave one for the browser.")
        answered = ask_open_fields(open_fields, saved, self.ask, self.say)
        for a in answered:
            write_section(self.folder / "form_answers.md", a.label, a.value)
            if not a.reused and not is_long(a.field):
                write_section(self.ctx.ws.form_answers_path, a.label, a.value, header=WORKSPACE_HEADER)
        if any(not a.reused for a in answered):
            self.say(f"Your answers have been saved in {_blue(self.folder / 'form_answers.md')} and (short ones) "
                     f"{_blue(self.ctx.ws.form_answers_path)}, so the next form that asks the same is filled automatically.")
        return answered

    def _material(self) -> str:
        """What the model may fill from: your facts, profile, CV and every saved answer."""
        facts = {k: v for k, v in asdict(self.values).items() if isinstance(v, str) and v}
        facts.update(self.ctx.profile.standard_answers)
        parts = ["## Facts\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()),
                 "## Profile\n" + profile_prompt_text(self.ctx.profile),
                 "## CV\n" + (self.ctx.documents.cv_text(self.app["language"]) or "")]
        for path in (self.ctx.ws.form_answers_path, self.folder / "form_answers.md"):
            if path.exists():
                parts.append(f"## Earlier answers ({path.name})\n" + path.read_text(encoding="utf-8"))
        return "\n\n".join(parts)

    def _extra_assignments(self, unmatched, material: str | None = None,
                           rule_values: dict[str, Any] | None = None) -> tuple[list, list[str]]:
        """The model's assignments and the lines to check.

        ``rule_values`` (the model reads the form) are what the rules would fill: a model value is only shown for
        checking when there was no rule value or it differs from it.
        """
        ev = self.ctx.repo.current_evaluation(self.job_id)
        final = FinalEvaluation.model_validate(json.loads(ev["payload"]))
        docs = self.ctx.documents
        wctx = WritingContext(profile_prompt_text(self.ctx.profile), docs.cv_text(self.app["language"]),
                              self.ctx.writing_instructions,
                              task_instructions=self.ctx.config.llm.instructions("form_fill"))
        jctx = JobContext(title=self.job["title"], company=self.job["company"],
                          description=self.job["description"] or "", language=self.app["language"] or "de",
                          requirements=list(final.result.requirements), decision=final.decision,
                          max_words=self.ctx.config.writing.max_words,
                          max_assumed_skills=self.ctx.config.writing.max_assumed_skills)
        facts = {key: value for key, value in asdict(self.values).items() if isinstance(value, str) and value}
        facts.update(self.ctx.profile.standard_answers)
        res = fill_unmatched(unmatched, client=self.ctx.llm("form_fill"), meter=self.ctx.meter, wctx=wctx,
                             job=jctx, facts=facts, max_retries=self.ctx.config.llm.max_retries,
                             instructions=self.ctx.config.llm.instructions("form_fill"), material=material,
                             rule_filled=frozenset(rule_values or ()))
        self.skipped_answers = res.skipped
        labels = {f.field_id: f.label for f in unmatched}
        options = {f.field_id: {v: t or v for v, t in f.options} for f in unmatched if f.options}

        def shown(field_id: str, value: Any) -> str:  # the option's text, not its internal value
            return str(options.get(field_id, {}).get(value, value))

        rules = rule_values or {}
        review = []
        for a in res.assignments:
            if a.field_id not in rules:
                review.append(f"{labels[a.field_id]}: {shown(a.field_id, a.value)}")
            elif rules[a.field_id] != a.value:
                review.append(f"{labels[a.field_id]}: {shown(a.field_id, a.value)} "
                              f"(your profile has {shown(a.field_id, rules[a.field_id])})")
        return res.assignments, review + res.needs_review

    def fill_current(self, page) -> FillReport:
        extra, review, answered = [], [], []
        if not self.folder.exists():  # renamed meanwhile (another step changed the form status)
            self._reload()
        from autoapp.assist.filler import is_restricted

        if page.url.startswith("file:"):
            message = "Local helper: copy answers manually; no live application to fill."
            self.say(message)
            return FillReport(message=message, needs_user=[message])
        if not is_restricted(page.url, self.ctx.config.assist.restricted_domains):
            if self.app["channel"] != "email":
                from autoapp.assist.navigate import application_scope

                self.scope, detection = application_scope(page, self.ctx.config.assist.restricted_domains,
                                                         company=self.job["company"], reference=self.app["reference"])
                if self.scope is None:
                    from autoapp.formfinder.explain import explain

                    found = explain(detection.status, detection.note) if detection else None
                    message = (found.what if found else "No application form is showing in the browser yet.") + \
                        " Open the form in the browser window, then press [f] again."
                    self.say(message)
                    return FillReport(message=message, needs_user=[message])
            extra, review, open_fields = self._plan(page)
            answered = self._ask_open(open_fields)
            extra = [*extra, *(a.assignment for a in answered)]
        kwargs = {"scope": self.scope} if self.scope else {}
        if self.model_assignments:
            kwargs["override_assignments"] = self.model_assignments
        report = self.fill(page, self.values, restricted_domains=self.ctx.config.assist.restricted_domains,
                           extra_assignments=extra, **kwargs)
        report.needs_user = list(dict.fromkeys([*report.needs_user, *self.skipped_answers]))
        if report.skipped_restricted:
            self.say(report.message)
            self.say(f"Use {self.folder / 'apply.html'} to copy your answers.")
            return report
        self.say(f"Filled: {', '.join(report.filled) or 'nothing'}")
        if report.filled:
            self.ctx.emit("form_filled", self.job, detail=f"{len(report.filled)} fields filled, not submitted")
            self._remember_fill(page, report, review, answered)
        if report.needs_user:
            self.say("Needs your input: " + ", ".join(report.needs_user))
        for r in review:
            self.say(f"Check this generated answer: {r}")
        for error in report.errors:
            self.say(f"Could not fill: {error}")
        self.say("Review the form and submit it yourself. Nothing was submitted.")
        return report

    def _save_copy(self, page) -> str | None:
        """Save the page as it is now, fields filled in, as ``filled_form.html`` in the folder. Returns its name."""
        try:
            index = self.scope.frame_index if self.scope else 0
            frame = page.frames[index] if index < len(page.frames) else page
            v = self.values
            labelled = [("Bewerbungsmappe (all in one PDF)", v.bundle_pdf), ("Cover letter", v.cover_letter_pdf),
                        ("CV", v.cv), *(("Certificate", c) for c in v.certificates),
                        *(("Arbeitszeugnis", c) for c in v.work_references),
                        *(("Other", c) for c in v.other_documents)]
            files = [(label, p.resolve().as_uri()) for label, p in labelled if p and p.exists()]
            html = snapshot_html(frame, url=page.url, title=f"{self.job['title']} at {self.job['company']}",
                                 files=files)
            path = save_snapshot(self.folder, html, page.url)
        except Exception as err:  # noqa: BLE001 - the copy is a bonus; the filled form in the browser is what counts
            self.say(f"The offline copy of the form could not be saved ({_first_line(err)}).")
            return None
        self.say(f"The filled form has been saved. Open it offline at: {_blue(path)}")
        return path.name

    def _remember_fill(self, page, report: FillReport, review: list[str], answered: list[Answered] = ()) -> None:
        """Keep a record of the fill in the folder, and remember the form's address for next time.

        A form you reached by clicking through yourself is saved as the found form, so the job opens straight on
        it the next time (and the folder loses its _NOFORM-style ending). The page itself is saved as an offline
        copy next to the record.
        """
        from datetime import datetime

        url = page.url
        if self.app["channel"] != "email" and url.startswith(("http://", "https://")) and (
                self.app["form_status"] != "found" or self.app["form_reached_url"] != url):
            from autoapp.formfinder.finder import Outcome
            from autoapp.formfinder.store import entry_url, save

            save(self.ctx, self.job, entry_url(self.ctx, self.job, self.app),
                 Outcome("found", url, url, "application form reached in the browser and filled"), refresh_package=True)
            self._reload()
        copy = self._save_copy(page)
        lines = [f"## {datetime.now().astimezone():%Y-%m-%d %H:%M} on {url}", "",
                 "Filled: " + ", ".join(report.filled)]
        if report.needs_user:
            lines.append("Needs your input: " + ", ".join(report.needs_user))
        lines += [f"Generated answer to check: {r}" for r in review]
        lines += [f"Your answer to '{a.label}': {a.value}" + (" (saved answer)" if a.reused else "")
                  for a in answered]
        if copy:
            lines.append(f"Offline copy of the filled form: {copy}")
        lines += ["Nothing was submitted by the app.", "", ""]
        record = self.folder / "filled_form.md"
        head = "" if record.exists() else (f"# Form fills for {self.job['title']} at {self.job['company']}\n\n"
                                           "Every time [f] filled this application's form. The form itself lives on "
                                           "the employer's site; this is the record of what the app put in.\n\n")
        with record.open("a", encoding="utf-8") as f:
            f.write(head + "\n".join(lines))
        self.say(f"The record of this fill has been saved in {_blue(record)}")

    # -- loop --------------------------------------------------------------------------

    def run(self, context) -> bool:
        """Drive the session on a Playwright browser context. Returns True if marked submitted."""
        try:
            return self._run(context)
        finally:
            if self.guard:
                try:
                    self.guard.close()
                except Exception:  # noqa: BLE001 - the browser may already be closed
                    pass

    def _run(self, context) -> bool:
        from playwright.sync_api import Error as BrowserError

        self.context = context
        self.browser_closed = False
        context.on("close", lambda _context: setattr(self, "browser_closed", True))
        page = context.pages[0] if context.pages else context.new_page()
        self.page = page
        try:
            self._open(context, page)
        except BrowserError as error:
            self.say(f"The browser stopped while opening the page ({_first_line(error)}).")
        self.say(f"Job: {self.job['title']} at {self.job['company']}")
        submitted = False
        while True:
            try:
                choice = (self.ask(MENU + "\n> ") or "").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "q"
            if choice == "s":
                submitted = True
                break
            if choice == "q":
                try:
                    answer = (self.ask("Did you submit the application? [y/N] ") or "").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                submitted = answer in ("y", "yes", "j", "ja")
                break
            if choice not in ("f", "h", "o"):
                continue
            if self.browser_closed:
                self.say("The browser window was closed. Press [q] to go back, then open this job again.")
                continue
            try:
                if choice == "f":
                    self.fill_current(self._active_page())
                elif choice == "o":
                    tab = self._active_page()
                    if not self._show_job_page(tab, [self.job["application_url"], self.url, self.job["job_url"]]):
                        self.say("The job page did not load either. Check your internet connection and the address: "
                                 f"{self.job['application_url'] or self.url}")
                else:
                    helper_page = context.new_page()
                    if self.guard:
                        self.guard.attach(helper_page)
                    helper_page.goto((self.folder / "apply.html").resolve().as_uri())
                    helper_page.bring_to_front()
            except BrowserError as error:
                if self.browser_closed:
                    self.say("The browser window was closed. Press [q] to go back, then open this job again.")
                else:
                    self.say(f"The browser did not respond ({_first_line(error)}). Try again, or press [q] to go back.")
        if submitted:
            self.mark_submitted()
        return submitted

    def _open(self, context, page) -> None:
        """Open the application in the browser and say what was found and what to do."""
        from autoapp.assist.filler import is_restricted
        from autoapp.formfinder.explain import explain

        if not self.url:
            page.goto((self.folder / "apply.html").resolve().as_uri())
            self.say("This job has no application link, so only your prepared answers (apply.html) were opened.")
            return
        if is_restricted(self.url, self.ctx.config.assist.restricted_domains):
            self._read_platform_link(context)
        if is_restricted(self.url, self.ctx.config.assist.restricted_domains):
            page.goto((self.folder / "apply.html").resolve().as_uri())
            for line in explain("restricted", f"restricted target: {urlsplit(self.url).hostname}").lines():
                self.say(line)
            return
        from autoapp.assist.navigate import NavigationGuard

        self.guard = NavigationGuard(context, self.ctx.config.assist.restricted_domains)
        if self.app["channel"] == "email":
            self.guard.navigation = False
            page.goto(self.url)
            page.bring_to_front()
            return
        from autoapp.formfinder.store import entry_url, save

        self.say("Looking for the application form in the browser window...")
        page, outcome = self._find_form(page, self.url)
        entry = entry_url(self.ctx, self.job, self.app)
        if (self.app["form_status"] == "found" and outcome.status in ("partial", "unreachable")
                and entry and entry != self.url):
            self.guard.reset()
            page, outcome = self._find_form(page, entry)
        save(self.ctx, self.job, entry, outcome, refresh_package=True)
        self._reload()  # the folder's name follows the form status, and the prepared files move with it
        self.guard.navigation = False  # from here on, the user may click anywhere
        self.page = page
        for line in explain(outcome.status, outcome.note).lines():
            self.say(line)
        if outcome.status != "found" and _looks_blank(page) and not (
                not page.url.startswith(("http://", "https://"))  # an error page, not a page the site chose to show
                and self._show_job_page(page, [self.url, outcome.reached, outcome.url, entry])):
            # Not even the job page opens: a plain page that says what happened is better than an error page.
            handoff = self.folder / "navigation_handoff.html"
            handoff.write_text(_handoff_html(explain(outcome.status, outcome.note), outcome.url or self.url,
                                             (self.folder / "apply.html").resolve().as_uri()), encoding="utf-8")
            page.goto(handoff.resolve().as_uri())
        page.bring_to_front()

    def _read_platform_link(self, context) -> None:
        """With ``sources.apply_links_with_login`` on for the platform: read the employer's apply address from the
        job page in this (logged-in) browser, and continue there. Otherwise nothing happens."""
        from autoapp.fetch.apply_links import PLATFORM_LABELS, ApplyLinkReader, platform_of

        site = platform_of(self.url)
        if not self.ctx.config.sources.apply_links_with_login.enabled_for(site):
            return
        self.say(f"Reading the company's apply link from the {PLATFORM_LABELS[site]} job page (with your login)...")
        link = ApplyLinkReader(context, min_delay_s=0, max_delay_s=0).read(self.url)
        if link.status != "found" or not link.url or is_restricted_url(link.url, self.ctx):
            self.say(link.note or "No company apply link was found.")
            return
        self.say(f"The company's apply page: {link.url}")
        self.ctx.repo.update_job(self.job_id, application_url=link.url)
        self.ctx.repo.upsert_application(self.job_id, form_entry_url=link.url, form_status=None, form_note=None)
        self._reload()
        self.url = link.url

    def _find_form(self, page, url: str):
        """Navigate to the form: the model chooses the clicks (``assist.navigate_mode``), else the link rules."""
        from autoapp.assist.navigate import navigate

        assist = self.ctx.config.assist
        if self.use_llm and assist.navigate_mode == "model":
            from autoapp.assist.agent_navigate import model_chooser, navigate_with_model

            p = self.ctx.profile.personal
            private = [p.email, p.phone, p.street, p.first_name, p.last_name]
            found_page, outcome = navigate_with_model(
                page, self.guard, url, choose=model_chooser(self.ctx), title=self.job["title"] or "",
                company=self.job["company"] or "", reference=self.app["reference"],
                max_steps=assist.navigate_max_steps, private=[v for v in private if v], say=self.say)
            if outcome is not None:
                return found_page, outcome
            self.guard.reset()
            self.guard.max_documents = 5
            page = found_page
        return navigate(page, self.guard, url, company=self.job["company"], reference=self.app["reference"])

    def _ask_pumping(self, prompt: str) -> str:
        """Ask in the terminal while the browser keeps loading pages.

        Playwright runs our request checks (restricted sites) only while Python calls into it, so a plain
        ``input()`` froze every page load until the next key: the browser looked offline. Here the browser is
        served in short slices until a line arrives.
        """
        return read_line_while(prompt, self._pump)

    def _pump(self) -> None:
        if self.browser_closed or self.context is None:
            time.sleep(0.1)
            return
        try:
            pages = [p for p in self.context.pages if not p.is_closed()]
            if pages:
                pages[0].wait_for_timeout(100)
            else:
                time.sleep(0.1)
        except Exception:  # noqa: BLE001 - the browser may close at any moment
            time.sleep(0.1)

    def _show_job_page(self, page, candidates) -> bool:
        """Put a real, working page in the tab (the job page, or the last page reached) and let the user take over.

        Automatic navigation can end on a page the safety checks refused to load; the browser then shows an error
        that looks like a lost connection. The checks are off from here on, so the same address loads normally.
        """
        from playwright.sync_api import Error as BrowserError

        from autoapp.assist.filler import is_restricted

        for url in dict.fromkeys(c for c in candidates if isinstance(c, str)):
            if not url.lower().startswith(("http://", "https://")) or is_restricted(
                    url, self.ctx.config.assist.restricted_domains):
                continue
            for attempt in range(2):
                try:
                    # The refused page's error screen is still being shown when automatic navigation ends; loading
                    # at once is cut short by it, so give it a moment and try once more if it still interrupts.
                    page.wait_for_timeout(600)
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    break
                except BrowserError:
                    if attempt:
                        break
            if not _looks_blank(page):
                return True
        return False

    def _active_page(self):
        """The tab to fill: the newest open web page (an apply button may have opened a new tab)."""
        pages = [p for p in self.context.pages if not p.is_closed() and p.url.startswith(("http://", "https://"))]
        if pages:
            return pages[-1]
        if self.page is not None and not self.page.is_closed():
            return self.page
        self.say("The application tab was closed; opening it again. Click through to the form if needed, then press [f].")
        self.page = self.context.new_page()
        if self.guard:
            self.guard.attach(self.page)
        self.page.goto(self.job["application_url"] or self.url or (self.folder / "apply.html").resolve().as_uri())
        return self.page

    def mark_submitted(self) -> None:
        from autoapp.db.repo import now_iso

        if self.job["status"] != Status.SUBMITTED.value:
            self.ctx.repo.transition(self.job_id, Status.SUBMITTED, note="submitted via assisted form")
        self.ctx.repo.upsert_application(self.job_id, applied_at=now_iso())
        self.say(f"{self.job_id} marked as submitted.")


def is_restricted_url(url: str, ctx) -> bool:  # noqa: ANN001 - RunContext
    from autoapp.assist.filler import is_restricted

    return is_restricted(url, ctx.config.assist.restricted_domains)


def read_line_while(prompt: str, pump: Callable[[], None], stream=None) -> str:  # noqa: ANN001
    """``input(prompt)`` that calls ``pump`` while no line has arrived yet (Linux/macOS terminals)."""
    from autoapp.dashboard.screen import current

    if stream is None and (screen := current()) is not None:  # the question opens under the pinned statistics
        return screen.ask_line(prompt, pump)
    stream = stream or sys.stdin
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return input(prompt)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    while True:
        ready, _, _ = select.select([fd], [], [], 0)
        if ready:
            line = stream.readline()
            if not line:
                raise EOFError
            return line.rstrip("\r\n")
        pump()


def _looks_blank(page) -> bool:
    """True for a browser error page or a page that shows nothing (blocked, empty or invisible)."""
    if not page.url.startswith(("http://", "https://")):
        return True
    try:
        return page.evaluate("""() => {
            const body = document.body;
            if (!body) return true;
            const style = getComputedStyle(body);
            return style.opacity === '0' || style.visibility === 'hidden' || !body.innerText.trim();
        }""")
    except Exception:  # noqa: BLE001 - a page that cannot be read is no help to the user either
        return True


def _first_line(error: Exception) -> str:
    return str(error).splitlines()[0] if str(error) else type(error).__name__


def _handoff_html(explanation, url: str | None, helper: str) -> str:
    link = f'<p><a href="{escape(url, quote=True)}">{escape(url)}</a></p>' if url else ""
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Continue yourself</title></head>"
            '<body style="background:white;color:#222;font:18px sans-serif;padding:32px;max-width:48em">'
            "<h1>Continue yourself</h1>"
            f"<p>{escape(explanation.what)}</p><p><b>What to do:</b> {escape(explanation.todo)}</p>"
            f"{link}<p>Nothing was filled or submitted.</p>"
            f'<p><a href="{escape(helper, quote=True)}">Show my prepared answers (apply.html)</a></p>'
            "</body></html>")


def open_browser_context(profile_dir: Path):
    """A visible Chromium with the persistent profile. Returns (playwright, context)."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(str(profile_dir), headless=False, service_workers="block")
    return pw, context
