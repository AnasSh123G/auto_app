from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest

from autoapp.assist.fields import collect_fields
from autoapp.assist.filler import fill_page
from autoapp.assist.navigate import NavigationGuard, application_scope, navigate
from autoapp.assist.session import AssistSession
from autoapp.steps.context import RunContext
from autoapp.steps.prepare import prepare_jobs
from tests.helpers import make_workspace
from tests.steps.test_prepare_step import FakeRenderer, factory
from tests.test_formfinder import form_job, transport

pytestmark = pytest.mark.browser
FIXTURES = Path(__file__).parent.parent / "fixtures/forms"


@contextmanager
def serving(pages):
    seen = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["POST " + self.path] += 1
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            seen[self.path] += 1
            value = pages.get(self.path, "")
            if isinstance(value, tuple):
                self.send_response(302)
                self.send_header("Location", value[0].format(port=self.server.server_port))
                self.end_headers()
            elif isinstance(value, int):
                self.send_response(value)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(value.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://localhost:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_full_cbc_navigation_and_scoped_fill(browser_ctx, values):
    pages = {"/ad": "cbc_ad.html", "/land": "cbc_overlay.html", "/details/346291.html": "cbc_details.html",
             "/bewerbung/346291.html": "cbc_application.html"}
    with serving({url: (FIXTURES / file).read_text() for url, file in pages.items()}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            current, outcome = navigate(page, guard, base + "/ad", company="CBC Cologne", reference="346291")
            assert outcome.status == "found", outcome
            assert current.url == base + "/bewerbung/346291.html"
            assert current is not page
            assert guard.documents == 4
            assert len(seen) == 4
            assert all(not field.value for field in collect_fields(current) if field.type not in ("checkbox", "radio"))
            scope, _ = application_scope(current, [], company="CBC Cologne", reference="346291")
            guard.navigation = False
            report = fill_page(current, values, restricted_domains=[], scope=scope)
            assert "Vorname" in report.filled
            assert current.locator('[name="subscriber_name"]').input_value() == ""
            assert not current.locator("#consent").is_checked()
            assert current.evaluate("window.submits") == 0
        finally:
            guard.close()
            for item in browser_ctx.pages:
                item.close()


@pytest.mark.parametrize("kind", ["redirect", "frame", "popup", "asset"])
def test_no_restricted_destination_request(browser_ctx, kind):
    blocked = "http://127.0.0.1:{port}/forbidden"
    if kind == "redirect":
        pages = {"/start": (blocked,)}
    else:
        tag = {"frame": '<iframe src="{url}"></iframe>', "popup": '<a href="{url}" target="_blank">Jetzt bewerben</a>',
               "asset": '<img src="{url}">'}[kind]
        pages = {"/start": tag}
    with serving(pages) as (base, seen):
        if kind != "redirect":
            pages["/start"] = pages["/start"].format(url=blocked.format(port=base.rsplit(":", 1)[1]))
            if kind in ("frame", "asset"):
                pages["/start"] += (FIXTURES / "cbc_application.html").read_text()
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, ["127.0.0.1"])
        try:
            _, outcome = navigate(page, guard, base + "/start")
            assert seen["/forbidden"] == 0
            assert outcome.status in ("found", "restricted")
        finally:
            guard.close()
            page.close()


@pytest.mark.parametrize("fixture,status", [("login_wall.html", "login_required"), ("captcha_wall.html", "captcha")])
def test_browser_wall_handoff(browser_ctx, fixture, status):
    with serving({"/start": (FIXTURES / fixture).read_text()}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, outcome = navigate(page, guard, base + "/start")
            assert outcome.status == status
            assert seen["/start"] == 1
        finally:
            guard.close()
            page.close()


def test_delayed_no_form_controls(browser_ctx):
    body = """<html><body>Application<script>
    setTimeout(()=>document.body.insertAdjacentHTML('beforeend',
    '<div data-application="yes">Application<input name="first_name"><input type="email"></div>'),100);
    </script></body></html>"""
    with serving({"/start": body}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, outcome = navigate(page, guard, base + "/start")
            assert outcome.status == "found"
        finally:
            guard.close()
            page.close()


@pytest.mark.parametrize("control", [
    '<button data-href="/forbidden">Jetzt bewerben</button>',
    '<button type="submit" data-href="/forbidden">Jetzt bewerben</button>',
    '<input type="image" src="/pixel" alt="Jetzt bewerben">',
    '<button type="button" form="app" data-href="/forbidden">Jetzt bewerben</button>',
    '<button type="button" onclick="document.forms[0].submit()">Jetzt bewerben</button>',
])
def test_submit_and_ambiguous_controls_are_not_clicked(browser_ctx, monkeypatch, control):
    ticks = iter([0, 11])
    monkeypatch.setattr("autoapp.assist.navigate.time", SimpleNamespace(monotonic=lambda: next(ticks)))
    body = f'<form id="app" action="/forbidden" onsubmit="window.submits++">{control}</form><script>window.submits=0</script>'
    with serving({"/start": body}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, result = navigate(page, guard, base + "/start")
            assert result.status == "partial"
            assert seen["/forbidden"] == 0
            assert page.evaluate("window.submits") == 0
        finally:
            guard.close()
            page.close()


def test_allowed_frame_scope(browser_ctx, values):
    body = """<iframe title="Application" src="/form"></iframe><input name="first_name" value="Outside">"""
    form = (FIXTURES / "cbc_application.html").read_text()
    with serving({"/start": body, "/form": form}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            current, result = navigate(page, guard, base + "/start")
            assert result.status == "found"
            scope, _ = application_scope(current, [])
            assert scope.frame_index == 1
            guard.navigation = False
            fill_page(current, values, restricted_domains=[], scope=scope)
            assert current.locator('input[name="first_name"]').input_value() == "Outside"
            assert current.frames[1].locator('input[name="first_name"]').input_value() == "Max"
            assert current.frames[1].evaluate("window.submits") == 0
            assert seen["/form"] == 1
        finally:
            guard.close()
            for item in browser_ctx.pages:
                item.close()


def test_safe_cookie_rejection(browser_ctx):
    form = (FIXTURES / "cbc_application.html").read_text().replace("<form ", "<form hidden ", 1)
    button = """<button type="button" onclick="this.remove();document.querySelector('form').hidden=false">Alle ablehnen</button>"""
    with serving({"/start": button + form}) as (base, _):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, result = navigate(page, guard, base + "/start")
            assert result.status == "found"
            assert page.get_by_role("button", name="Alle ablehnen").count() == 0
            assert page.evaluate("window.submits") == 0
            assert not page.locator("#consent").is_checked()
        finally:
            guard.close()
            page.close()


def test_real_browser_redirect_budget(browser_ctx):
    with serving({f"/{index}": (f"/{index + 1}",) for index in range(8)}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, result = navigate(page, guard, base + "/0")
            assert result.status == "partial"
            assert sum(seen.values()) == 5
            assert seen["/5"] == 0
        finally:
            guard.close()
            page.close()


def test_background_post_is_blocked_without_ending_navigation(browser_ctx):
    body = """<script>fetch('/telemetry', {method:'POST'}).catch(()=>{});</script>"""
    body += (FIXTURES / "cbc_application.html").read_text()
    with serving({"/start": body}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, result = navigate(page, guard, base + "/start")
            page.wait_for_timeout(100)
            assert result.status == "found"
            assert guard.reason is None
            assert seen["POST /telemetry"] == 0
            assert page.evaluate("window.submits") == 0
        finally:
            guard.close()
            page.close()


def test_script_post_navigation_is_refused(browser_ctx):
    body = """<form action="/submit" method="post"></form>
    <script>document.forms[0].submit()</script>"""
    with serving({"/start": body}) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            _, result = navigate(page, guard, base + "/start")
            assert result.status == "partial"
            assert result.note == "submission refused during navigation"
            assert seen["POST /submit"] == 0
        finally:
            guard.close()
            page.close()


def test_script_popup_redirect_cannot_reach_restricted_destination(browser_ctx):
    body = """<script>window.open('/forward','_blank')</script>""" + (FIXTURES / "cbc_application.html").read_text()
    pages = {"/start": body, "/forward": ("http://127.0.0.1:{port}/forbidden",)}
    with serving(pages) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, ["127.0.0.1"])
        try:
            navigate(page, guard, base + "/start")
            page.wait_for_timeout(100)
            assert seen["/forbidden"] == 0
        finally:
            guard.close()
            for item in browser_ctx.pages:
                item.close()


def test_controlled_context_disables_service_worker_traffic(browser_ctx):
    context = browser_ctx.browser.new_context(service_workers="block")
    pages = {"/start": '<script>navigator.serviceWorker.register("/worker.js")</script>' +
             (FIXTURES / "cbc_application.html").read_text(),
             "/worker.js": 'fetch("http://127.0.0.1:{port}/forbidden")'}
    with serving(pages) as (base, seen):
        pages["/worker.js"] = pages["/worker.js"].format(port=base.rsplit(":", 1)[1])
        page = context.new_page()
        guard = NavigationGuard(context, ["127.0.0.1"])
        try:
            _, result = navigate(page, guard, base + "/start")
            assert result.status == "found"
            page.wait_for_timeout(100)
            assert seen["/worker.js"] == seen["/forbidden"] == 0
        finally:
            guard.close()
            context.close()


@pytest.mark.parametrize("stale_status", [410, 403, 200])
def test_assist_stale_recovery_or_block_handoff_updates_all_links(browser_ctx, tmp_path, stale_status):
    ctx = RunContext.open(make_workspace(tmp_path))
    pages = {"/ad": "cbc_ad.html", "/land": "cbc_overlay.html", "/details/346291.html": "cbc_details.html",
             "/bewerbung/346291.html": "cbc_application.html"}
    contents = {url: (FIXTURES / file).read_text() for url, file in pages.items()}
    contents["/stale"] = '<body style="opacity:0">Loading...</body>' if stale_status == 200 else stale_status
    try:
        with serving(contents) as (base, seen):
            jid = form_job(ctx)
            ctx.repo.update_job(jid, application_url=base + "/ad")
            transport(ctx, lambda request: httpx.Response(200, text=contents["/bewerbung/346291.html"]))
            prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
            if stale_status == 200:
                ctx.repo.upsert_application(jid, form_status="partial")
            ctx.repo.update_job(jid, application_url=base + "/stale")
            ctx.repo.update_job(jid, notes="Keep my note")
            folder = Path(ctx.repo.get_application(jid)["folder"])
            letter = (folder / "cover_letter.md").read_bytes()
            answers = iter(["h", "q", "n"])
            session = AssistSession(ctx, jid, ask=lambda prompt: next(answers), say=lambda text: None, use_llm=False)
            assert session.run(browser_ctx) is False
            folder = Path(ctx.repo.get_application(jid)["folder"])  # renamed when the form status changed
            assert browser_ctx.pages[-1].url == (folder / "apply.html").resolve().as_uri()
            assert browser_ctx.pages[-1].locator("body").is_visible()
            application = ctx.repo.get_application(jid)
            assert seen["/stale"] == 1
            assert application["form_entry_url"] == base + "/ad"
            if stale_status == 410:
                assert seen["/ad"] == 1
                assert application["form_status"] == "found"
                expected = base + "/bewerbung/346291.html"
            else:
                assert seen["/ad"] == 0
                assert application["form_status"] == ("partial" if stale_status == 200 else "needs_browser")
                expected = base + "/stale"
                # the reached page shows nothing, so a plain explanation replaces it
                assert browser_ctx.pages[0].url.endswith("/navigation_handoff.html")
                assert browser_ctx.pages[0].get_by_role("heading", name="Continue yourself").is_visible()
                if stale_status == 403:
                    assert "refused the app" in browser_ctx.pages[0].content()
                assert "Show my prepared answers" in browser_ctx.pages[0].content()
            assert ctx.repo.get_job(jid)["application_url"] == expected
            for name in ("README.md", "job.md", "apply.html"):
                assert expected in (folder / name).read_text()
            assert expected in ctx.ws.csv_path.read_text()
            assert "Keep my note" in ctx.ws.csv_path.read_text()
            assert (folder / "cover_letter.md").read_bytes() == letter
    finally:
        ctx.close()
        for page in browser_ctx.pages:
            page.close()


def _prepared(tmp_path, url, page_text):
    ctx = RunContext.open(make_workspace(tmp_path))
    jid = form_job(ctx)
    ctx.repo.update_job(jid, application_url=url)
    transport(ctx, lambda request: httpx.Response(200, text=page_text))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    return ctx, jid


def _script(*steps):
    """Answers for ``ask``; a callable step runs first (the user doing something) and returns the answer."""
    queue = list(steps)
    return lambda prompt: (lambda step: step() if callable(step) else step)(queue.pop(0))


def test_without_a_clear_apply_button_the_real_page_stays_and_a_new_tab_is_filled(browser_ctx, tmp_path):
    application = (FIXTURES / "cbc_application.html").read_text()
    ad = ('<html><body><h1>Softwareentwickler (m/w/d)</h1><p>Wir freuen uns auf Ihre Bewerbung.</p>'
          '<button type="button" onclick="window.open(\'/bewerbung/346291.html\')">Weiter</button></body></html>')
    ctx = None
    try:
        with serving({"/ad": ad, "/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/ad", ad)
            said, filled = [], []

            def user_clicks_apply():
                assert browser_ctx.pages[0].url == base + "/ad"  # the real page, not a local stand-in
                with browser_ctx.expect_page() as popup:
                    browser_ctx.pages[0].click("text=Weiter")
                popup.value.wait_for_load_state()
                return "f"

            def fake_fill(page, values, **kwargs):
                filled.append(page.url)
                from autoapp.assist.filler import FillReport
                return FillReport(filled=["Vorname"])

            session = AssistSession(ctx, jid, ask=_script(user_clicks_apply, "q", "n"), say=said.append,
                                    fill=fake_fill, use_llm=False)
            assert session.run(browser_ctx) is False
            text = "\n".join(said)
            assert "click the apply button yourself" in text and "unambiguous" not in text
            assert filled == [base + "/bewerbung/346291.html"]  # the popup was allowed and filled
            # the form the user reached is remembered, and what was filled is recorded in the folder
            application = ctx.repo.get_application(jid)
            assert application["form_status"] == "found"
            assert application["form_reached_url"] == base + "/bewerbung/346291.html"
            assert not application["folder"].endswith("_NOFORM")
            record = (Path(application["folder"]) / "filled_form.md").read_text()
            assert "Filled: Vorname" in record and base + "/bewerbung/346291.html" in record
    finally:
        if ctx:
            ctx.close()
        for page in browser_ctx.pages:
            page.close()


def test_a_closed_tab_is_opened_again(browser_ctx, tmp_path):
    application = (FIXTURES / "cbc_application.html").read_text()
    ctx = None
    try:
        with serving({"/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/bewerbung/346291.html", application)
            said, filled = [], []

            def user_closes_the_tab():
                browser_ctx.pages[0].close()
                return "f"

            def fake_fill(page, values, **kwargs):
                filled.append(page.url)
                from autoapp.assist.filler import FillReport
                return FillReport(filled=["Vorname"])

            session = AssistSession(ctx, jid, ask=_script(user_closes_the_tab, "q", "n"), say=said.append,
                                    fill=fake_fill, use_llm=False)
            assert session.run(browser_ctx) is False
            assert filled == [base + "/bewerbung/346291.html"]
            assert any("tab was closed" in line for line in said)
    finally:
        if ctx:
            ctx.close()
        for page in browser_ctx.pages:
            page.close()


def test_a_closed_browser_window_ends_the_session_without_a_crash(browser_ctx, tmp_path):
    application = (FIXTURES / "cbc_application.html").read_text()
    own = browser_ctx.browser.new_context()
    ctx = None
    try:
        with serving({"/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/bewerbung/346291.html", application)
            said = []

            def user_closes_the_window():
                own.close()
                return "f"

            session = AssistSession(ctx, jid, ask=_script(user_closes_the_window, "q", "n"), say=said.append,
                                    use_llm=False)
            assert session.run(own) is False
            assert any("browser window was closed" in line for line in said)
    finally:
        if ctx:
            ctx.close()


@pytest.mark.parametrize("pumped", [False, True])
def test_the_browser_keeps_loading_while_the_terminal_waits_for_a_key(browser_ctx, pumped):
    """The cause of "the app's browser has no internet": request checks only run while Python serves the browser."""
    import os
    import threading
    import time

    from autoapp.assist.session import read_line_while

    pages = {"/start": "<p>start</p><script>setTimeout(()=>{location.href='/next'},300)</script>", "/next": "<p>next</p>"}
    with serving(pages) as (base, seen):
        guard = NavigationGuard(browser_ctx, ["linkedin.com"])
        page = browser_ctx.new_page()
        try:
            guard.attach(page)
            page.goto(base + "/start")
            guard.navigation = False
            read, write = os.pipe()
            threading.Timer(1.5, lambda: os.write(write, b"q\n")).start()
            pump = (lambda: page.wait_for_timeout(100)) if pumped else (lambda: time.sleep(0.1))
            assert read_line_while("", pump, stream=os.fdopen(read)) == "q"
            assert seen["/next"] == (1 if pumped else 0)
        finally:
            guard.close()
            page.close()


def test_after_a_blocked_navigation_the_tab_is_a_working_page_you_can_move_on_from(browser_ctx, tmp_path):
    """The reported dead end: the check blocked the 6th page, leaving an error page that looked offline."""
    chain = {f"/{index}": (f"/{index + 1}",) for index in range(6)}
    chain["/6"] = "<html><body><p>Job ad</p></body></html>"
    chain["/other"] = "<html><body><p>You can still browse</p></body></html>"
    with serving(chain) as (base, seen):
        ctx, jid = _prepared(tmp_path, base + "/0", "<html><body>ad</body></html>")
        said = []
        try:
            probe = {}

            def user_browses():
                tab = [p for p in browser_ctx.pages if not p.is_closed()][-1]
                probe["start"] = tab.url
                tab.goto(base + "/other", wait_until="domcontentloaded")
                probe["moved"] = tab.locator("body").inner_text()
                return "q"

            session = AssistSession(ctx, jid, ask=_script(user_browses, "n"), say=said.append, use_llm=False)
            session.run(browser_ctx)
            assert probe["start"].startswith("http"), probe["start"]  # not an error page or a local stand-in
            assert probe["moved"] == "You can still browse"
            assert any("followed four pages" in line for line in said)
        finally:
            ctx.close()
            for page in browser_ctx.pages:
                page.close()


def test_a_missing_ai_key_does_not_stop_the_fill(browser_ctx, tmp_path, monkeypatch):
    application = (FIXTURES / "cbc_application.html").read_text()
    application = application.replace(  # a question nothing but the model can answer
        '<input name="phone"', '<label for="farbe">Lieblingsfarbe</label><input name="farbe" id="farbe"><input name="phone"')
    ctx = None
    try:
        with serving({"/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/bewerbung/346291.html", application)
            said, filled = [], []
            monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            ctx.clients.clear()  # the real client is built on first use, and the key is missing

            def fake_fill(page, values, **kwargs):
                from autoapp.assist.filler import FillReport
                filled.append(kwargs.get("extra_assignments"))
                return FillReport(filled=["Vorname"])

            session = AssistSession(ctx, jid, ask=_script("f", "q", "n"), say=said.append, fill=fake_fill, use_llm=True,
                                    ask_questions=False)
            assert session.run(browser_ctx) is False
            assert filled == [[]]  # still filled, without written answers
            assert any("Written answers were skipped" in line and "is not set" in line for line in said)
    finally:
        if ctx:
            ctx.close()
        for page in browser_ctx.pages:
            page.close()


def _with_questions(application: str) -> str:
    return application.replace(
        '<input name="phone"',
        '<label for="farbe">Lieblingsfarbe</label><input name="farbe" id="farbe">'
        '<label for="quelle">Wie haben Sie von uns erfahren?</label><input name="quelle" id="quelle">'
        '<input name="phone"')


def test_personal_questions_are_asked_saved_reused_and_the_form_copy_is_kept(browser_ctx, tmp_path):
    application = _with_questions((FIXTURES / "cbc_application.html").read_text())
    ctx = None
    try:
        with serving({"/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/bewerbung/346291.html", application)
            said, asked = [], []

            def answer(prompt):
                asked.append(prompt)
                return "Blau"

            def route(steps):  # the menu and the questions share ``ask``; questions start with "Question:"
                menu = iter(steps)
                return lambda prompt: answer(prompt) if prompt.startswith("Question:") else next(menu)

            session = AssistSession(ctx, jid, ask=route(["f", "q", "n"]), say=said.append, use_llm=False)
            session.run(browser_ctx)

            assert len(asked) == 1 and "Lieblingsfarbe" in asked[0]  # LinkedIn question was not asked
            page = next(p for p in browser_ctx.pages if not p.is_closed() and "346291" in p.url)
            assert page.input_value("#farbe") == "Blau"
            assert page.input_value("#quelle") == "LinkedIn"

            saved = (ctx.ws.form_answers_path).read_text(encoding="utf-8")
            assert "## Lieblingsfarbe" in saved and "Blau" in saved
            folder = Path(ctx.repo.get_application(jid)["folder"])
            assert "Blau" in (folder / "form_answers.md").read_text(encoding="utf-8")
            record = (folder / "filled_form.md").read_text(encoding="utf-8")
            assert "Your answer to 'Lieblingsfarbe': Blau" in record and "filled_form.html" in record
            copy = (folder / "filled_form.html").read_text(encoding="utf-8")
            assert 'value="Blau"' in copy and "<script" not in copy.lower()

            # a second application with the same question: answered from the saved file, nothing asked
            page.fill("#farbe", "")
            asked.clear()
            session2 = AssistSession(ctx, jid, ask=route(["f", "q", "n"]), say=said.append, use_llm=False)
            session2.run(browser_ctx)
            assert asked == []
    finally:
        if ctx:
            ctx.close()
        for p in browser_ctx.pages:
            p.close()


def test_the_model_reads_the_form_and_the_rules_values_are_not_reported_open(browser_ctx, tmp_path):
    import re

    from autoapp.assist.answers import WORKSPACE_HEADER, write_section
    from autoapp.llm.fake import FakeLLM

    application = _with_questions((FIXTURES / "cbc_application.html").read_text())
    ctx = None
    try:
        with serving({"/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/bewerbung/346291.html", application)
            write_section(ctx.ws.form_answers_path, "Lieblingsfarbe", "Grün", header=WORKSPACE_HEADER)
            listed = []

            def model(task, system, user, schema):
                listed.append(user)
                farbe = re.search(r"id=(\S+) type=\S+ label='Lieblingsfarbe'", user).group(1)
                return {"choices": [{"field_id": farbe, "action": "fill", "value": "Grün", "source": "Grün"}]}

            ctx.clients["form_fill"] = FakeLLM(script={"form_fill": [model]})
            said = []
            session = AssistSession(ctx, jid, ask=_script("f", "q", "n"), say=said.append, use_llm=True,
                                    ask_questions=False)
            session.run(browser_ctx)

            assert "label='Vorname'" in listed[0]  # the model sees the fields the rules know too
            assert "Wie haben Sie von uns erfahren" not in listed[0]  # but never a fixed answer
            page = next(p for p in browser_ctx.pages if not p.is_closed() and "346291" in p.url)
            assert page.input_value("#farbe") == "Grün"
            assert page.input_value("#quelle") == "LinkedIn"
            assert page.input_value("#first_name")  # skipped by the model: the rules' value is used
            open_line = next((line for line in said if line.startswith("Needs your input")), "")
            assert "Vorname" not in open_line and "Lieblingsfarbe" not in open_line
            assert not any("Check this generated answer: Vorname" in line for line in said)
    finally:
        if ctx:
            ctx.close()
        for p in browser_ctx.pages:
            p.close()


def test_the_model_clicks_through_where_the_rules_give_up(browser_ctx, tmp_path):
    from autoapp.llm.fake import FakeLLM

    application = (FIXTURES / "cbc_application.html").read_text()
    ad = ('<html><body><h1>Softwareentwickler (m/w/d)</h1><p>Wir freuen uns auf Ihre Bewerbung.</p>'
          '<button type="button" onclick="window.open(\'/bewerbung/346291.html\')">Weiter</button></body></html>')
    ctx = None
    try:
        with serving({"/ad": ad, "/bewerbung/346291.html": application}) as (base, seen):
            ctx, jid = _prepared(tmp_path, base + "/ad", ad)

            def model(task, system, user, schema):
                import json

                element = next(e["n"] for e in json.loads(user)["elements"] if e["text"] == "Weiter")
                return {"action": "click", "element": element}

            ctx.clients["form_find"] = FakeLLM(script={"form_find": [model]})
            ctx.config.assist.fill_mode = "rules"
            said, filled = [], []

            def fake_fill(page, values, **kwargs):
                filled.append(page.url)
                from autoapp.assist.filler import FillReport
                return FillReport(filled=["Vorname"])

            session = AssistSession(ctx, jid, ask=_script("f", "q", "n"), say=said.append, fill=fake_fill,
                                    use_llm=True, ask_questions=False)
            session.run(browser_ctx)
            assert filled == [base + "/bewerbung/346291.html"]  # reached without the user clicking
            assert any('clicking "Weiter"' in line for line in said)
            assert ctx.repo.get_application(jid)["form_status"] == "found"
    finally:
        if ctx:
            ctx.close()
        for page in browser_ctx.pages:
            page.close()
