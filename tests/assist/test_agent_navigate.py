"""The model chooses the clicks to the application form; the safety rules stay in code."""

from __future__ import annotations

import pytest

from autoapp.assist.agent_navigate import NavAction, navigate_with_model
from autoapp.assist.navigate import NavigationGuard
from tests.assist.test_navigate import FIXTURES, serving

pytestmark = pytest.mark.browser

FORM = (FIXTURES / "cbc_application.html").read_text()
AD = """<html><body><h1>Python Developer</h1><p>Kontakt: max@example.org</p>
<form action="/send" method="post"><button>Jetzt bewerben im Formular</button></form>
<button type="button" onclick="fetch('/send', {method: 'POST'})">Absenden</button>
<a href="mailto:jobs@example.org">Bewerbung per E-Mail</a>
<button type="button" onclick="location.href='/form'">Jetzt bewerben</button>
</body></html>"""


class Script:
    """A model that picks elements by their text; records what it was shown."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.shown = []

    def __call__(self, payload):
        self.shown.append(payload)
        step = self.steps.pop(0)
        if isinstance(step, NavAction) or step is None:
            return step
        if isinstance(step, int):  # a raw element number
            return NavAction(action="click", element=step)
        n = next(e["n"] for e in payload["elements"] if e["text"] == step)
        return NavAction(action="click", element=n)


def _go(browser_ctx, pages, script, **kw):
    with serving(pages) as (base, seen):
        page = browser_ctx.new_page()
        guard = NavigationGuard(browser_ctx, [])
        try:
            current, outcome = navigate_with_model(page, guard, base + "/ad", choose=script, title="Python Developer",
                                                   company="CBC Cologne", **kw)
            return current, outcome, seen, len(browser_ctx.pages)
        finally:
            guard.close()
            for p in browser_ctx.pages:
                p.close()


def test_a_javascript_button_is_clicked_and_forbidden_elements_are_never_offered(browser_ctx):
    script = Script(1, "Jetzt bewerben")  # element 1 is the form's own button: refused, then the real one
    current, outcome, seen, _ = _go(browser_ctx, {"/ad": AD, "/form": FORM}, script)
    assert outcome.status == "found", outcome
    offered = [e["text"] for e in script.shown[0]["elements"]]
    assert offered == ["Jetzt bewerben"]  # no form button, no "Absenden", no mailto link
    assert "not in the list" in script.shown[1]["earlier_steps"][0]
    assert seen["POST /send"] == 0
    assert "max@example.org" not in str(script.shown[0])


def test_a_new_window_opens_in_the_same_guarded_tab(browser_ctx):
    ad = '<html><body><h1>Job</h1><button type="button" onclick="window.open(\'/form\')">Bewerben</button></body></html>'
    script = Script("Bewerben")
    before = len(browser_ctx.pages)
    _, outcome, _, pages = _go(browser_ctx, {"/ad": ad, "/form": FORM}, script)
    assert outcome.status == "found", outcome
    assert pages == before + 1  # only the tab the test opened


def test_the_model_stops_at_a_login(browser_ctx):
    ad = '<html><body><h1>Job</h1><a href="/login">Anmelden um dich zu bewerben</a></body></html>'
    script = Script(NavAction(action="stop", stop_kind="login", reason="account needed"))
    _, outcome, _, _ = _go(browser_ctx, {"/ad": ad}, script)
    assert outcome.status == "login_required"


def test_without_the_model_the_rules_take_over(browser_ctx):
    _, outcome, _, _ = _go(browser_ctx, {"/ad": AD}, Script(None))
    assert outcome is None


def test_the_same_click_twice_is_a_loop(browser_ctx):
    ad = '<html><body><h1>Job</h1><button type="button" onclick="void 0">Mehr erfahren</button></body></html>'
    _, outcome, _, _ = _go(browser_ctx, {"/ad": ad}, Script("Mehr erfahren", "Mehr erfahren"))
    assert outcome.status == "partial" and outcome.note == "navigation loop"


def test_the_step_limit_is_kept(browser_ctx):
    pages = {f"/p{i}": f'<html><body><a href="/p{i + 1}">Weiter zur Bewerbung {i}</a></body></html>' for i in range(10)}
    pages["/ad"] = pages["/p0"]
    script = Script(*[f"Weiter zur Bewerbung {i}" for i in range(10)])
    _, outcome, _, _ = _go(browser_ctx, pages, script, max_steps=3)
    assert outcome.status == "partial" and "within 3 steps" in outcome.note
