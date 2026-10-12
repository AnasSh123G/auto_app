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


def test_modal_targets_hide_background_links_and_other_frames(browser_ctx):
    ad = """<html><body><h1>Job</h1><a href="/wrong">Jetzt bewerben</a>
<iframe src="/frame"></iframe>
<div role="dialog" aria-modal="true" style="position:fixed;inset:0;background:white;z-index:100">
<p>Willst du Cookies?</p><button>Cookies akzeptieren</button><button onclick="this.parentElement.remove()">Alle ablehnen</button></div>
<button onclick="location.href='/form'">Bewerben ohne Konto</button></body></html>"""
    script = Script("Alle ablehnen", "Bewerben ohne Konto")
    _, outcome, seen, _ = _go(browser_ctx, {"/ad": ad, "/frame": '<a href="/wrong">Apply elsewhere</a>',
                                          "/form": FORM}, script)
    assert outcome.status == "found"
    assert [e["text"] for e in script.shown[0]["elements"]] == ["Alle ablehnen"]
    assert script.shown[0]["page"]["text"].startswith("Willst du Cookies?")
    assert "Job" not in script.shown[0]["page"]["text"]
    assert seen["/wrong"] == 0


def test_failed_clicks_allow_a_retry_and_report_failure_instead_of_a_loop(browser_ctx, monkeypatch):
    from playwright.sync_api import Error, Locator

    click = Locator.click
    tries = []

    def fail(self, *args, **kwargs):
        if self.get_attribute("data-autoapp-nav"):
            tries.append(1)
            raise Error("element intercepted by another overlay")
        return click(self, *args, **kwargs)

    monkeypatch.setattr(Locator, "click", fail)
    script = Script("Jetzt bewerben", "Jetzt bewerben")
    _, outcome, _, _ = _go(browser_ctx, {"/ad": AD}, script)
    assert len(tries) == 2
    assert outcome.status == "partial" and "click failed twice" in outcome.note
    assert "failed" in script.shown[1]["earlier_steps"][0]


def test_a_transient_failed_click_can_then_reach_the_form(browser_ctx, monkeypatch):
    from playwright.sync_api import Error, Locator

    original = Locator.click
    tries = []

    def once(self, *args, **kwargs):
        if self.get_attribute("data-autoapp-nav"):
            tries.append(1)
            if len(tries) == 1:
                raise Error("temporary overlay interception")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Locator, "click", once)
    _, outcome, _, _ = _go(browser_ctx, {"/ad": AD, "/form": FORM}, Script("Jetzt bewerben", "Jetzt bewerben"))
    assert outcome.status == "found" and len(tries) == 2


def test_a_profile_creating_continuation_is_handed_off_without_any_click(browser_ctx):
    ad = """<html><body><h1>Bewerbung</h1><form action="/register" method="post">
<label for="name">Vorname</label><input id="name" name="name">
<label for="email">E-Mail</label><input id="email" name="email">
<button>Zum Lebenslauf</button></form>
<p>Mit Klick auf 'Zum Lebenslauf' erstellst du dein Profil bei Workwise GmbH.</p></body></html>"""
    script = Script()
    _, outcome, seen, _ = _go(browser_ctx, {"/ad": ad}, script)
    assert outcome.status == "login_required" and "creates an account/profile" in outcome.note
    assert script.shown == [] and seen["POST /register"] == 0


def test_workwise_reject_all_request_passes_cdp_but_account_and_other_posts_do_not(browser_ctx):
    import json
    from collections import Counter

    from tests.assist.test_consent import REJECT

    context = browser_ctx.browser.new_context(service_workers="block")
    page = context.new_page()
    guard = NavigationGuard(context, [])
    seen = Counter()
    ad = '''<html><body><a href="/application">Jetzt bewerben</a>
<div role="dialog" aria-modal="true" style="position:fixed;inset:0;background:white;z-index:10">
<p>Willst du Cookies?</p><button onclick='fetch("https://api.workwise.io/cookieConsents", {
method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(
{essential:false,statistics:false,marketing:false,external_media:false})}).then(r=>{
if(r.ok)document.querySelector("[role=dialog]").remove()})'>Alle ablehnen</button></div>
<script>
fetch("https://api.workwise.io/register",{method:"POST",body:"email=max@example.com"}).catch(()=>{});
fetch("https://api.workwise.io/cookieConsents",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({email:"max@example.com"})}).catch(()=>{});
</script></body></html>'''
    account = '''<h1>Bewerbung</h1><form action="https://api.workwise.io/register" method="post">
<input name="first_name"><input name="email"><button>Zum Lebenslauf</button></form>
<p>Mit Klick auf 'Zum Lebenslauf' erstellst du dein Profil bei Workwise GmbH.</p>'''

    def respond(route):
        request = route.request
        from urllib.parse import urlsplit

        path = urlsplit(request.url).path
        seen[f"{request.method} {path}"] += 1
        headers = {"Access-Control-Allow-Origin": "https://www.workwise.io", "Access-Control-Allow-Headers": "content-type",
                   "Access-Control-Allow-Methods": "POST, OPTIONS"}
        if request.method == "POST":
            assert path == "/cookieConsents" and request.post_data_json == REJECT
            route.fulfill(status=200, body=json.dumps(REJECT), content_type="application/json", headers=headers)
        elif request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
        else:
            route.fulfill(status=200, body=ad if path.startswith("/job/") else account, content_type="text/html")

    # The mock route fulfils traffic after Chromium interception; test_consent separately exercises the page route.
    context.route("**/*", respond)
    script = Script("Alle ablehnen", "Jetzt bewerben")
    try:
        _, outcome = navigate_with_model(page, guard, "https://www.workwise.io/job/123018", choose=script,
                                        title="Developer", company="Workwise employer")
        assert outcome.status == "login_required", outcome
        assert seen["POST /cookieConsents"] == 1
        assert seen["POST /register"] == 0
        assert [e["text"] for e in script.shown[0]["elements"]] == ["Alle ablehnen"]
    finally:
        guard.close()
        context.close()


@pytest.mark.parametrize("label", ["Accept", "Akzeptieren", "Zustimmen"])
def test_generic_cookie_acceptance_is_not_offered_or_clicked(browser_ctx, label):
    ad = f'''<h1>Job</h1><div role="dialog" aria-modal="true"><p>Cookies</p>
<button onclick="localStorage.setItem('optional','accepted');fetch('/tracking')">{label}</button>
<button onclick="location.href='/form'">Alle ablehnen</button></div>'''
    script = Script(1, "Alle ablehnen")  # An invented choice for the hidden/forbidden affirmative element.
    _, outcome, seen, _ = _go(browser_ctx, {"/ad": ad, "/form": FORM}, script)
    assert outcome.status == "found"
    assert [e['text'] for e in script.shown[0]['elements']] == ["Alle ablehnen"]
    assert seen["/tracking"] == 0


@pytest.mark.parametrize("prose", [
    '<p style="display:none">By clicking Continue you create an account.</p>',
    '<style>.invisible{display:none}</style><p class="invisible">By clicking Continue you create an account.</p>',
    '<p>By clicking Continue you apply without creating an account.</p>',
])
def test_hidden_or_negated_creation_text_does_not_hide_an_account_free_form(browser_ctx, prose):
    _, outcome, _, _ = _go(browser_ctx, {"/ad": FORM.replace("</body>", prose + "</body>")}, Script())
    assert outcome.status == "found", outcome


def test_a_different_vacancy_is_not_accepted_without_custom_identity_attributes(browser_ctx):
    wrong = '''<h1>Sales Assistant</h1><p>Other GmbH</p><form><p>Apply here</p>
<input name="first_name"><input name="email"><input type="file" name="resume"></form>'''
    _, outcome, _, _ = _go(browser_ctx, {"/ad": AD, "/form": wrong}, Script("Jetzt bewerben"))
    assert outcome.status == "partial" and "conflicting vacancy" in outcome.note


@pytest.mark.parametrize("status,wall", [
    ("login_required", '<h1>Sign in</h1><form><input type="password"></form>'),
    ("captcha", '<h1>Verify you are human</h1><div class="hcaptcha">Complete the challenge</div>'),
])
def test_manual_walls_on_the_final_step_keep_the_status_and_reached_url(browser_ctx, status, wall):
    _, outcome, _, _ = _go(browser_ctx, {"/ad": AD, "/form": wall}, Script("Jetzt bewerben"), max_steps=1)
    assert outcome.status == status, outcome
    assert outcome.url.endswith('/form') and outcome.reached.endswith('/form')
