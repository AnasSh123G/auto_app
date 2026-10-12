"""HTTP first, then a bounded browser pass; no application writes or account creation."""

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Error as BrowserError

from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.formfinder.browser import fallback
from autoapp.formfinder.finder import Outcome
from autoapp.formfinder.store import resolve
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext
from autoapp.steps.prepare import prepare_jobs
from tests.assist.test_navigate import FIXTURES, serving
from tests.helpers import make_workspace
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

FORM = (FIXTURES / "cbc_application.html").read_text().replace("CBC Cologne", "Muster GmbH").replace("346291", "2026-17")
JS_AD = '<html><body><h1>Job</h1><button onclick="location.href=\'/form\'">Jetzt bewerben</button></body></html>'


@pytest.fixture(name="browser_ctx")
def browser_fixture():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(service_workers="block")
        yield context
        browser.close()


@pytest.fixture
def ctx(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path))
    ctx.config.assist.browser_fallback = True
    yield ctx
    ctx.close()


def http(ctx, body):
    ctx._http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))), 0, 0)


def form_job(ctx, url):
    jid = approved_job(ctx)
    ctx.repo.update_job(jid, application_url=url)
    final = json.loads(ctx.repo.current_evaluation(jid)["payload"])
    final["result"].update(channel="employer_form", apply_email=None, application_url=url)
    ctx.repo.add_evaluation(jid, fit_score=88, decision="apply", payload=final)
    return jid


@pytest.mark.browser
def test_resolve_reaches_a_js_form_records_both_strategies_and_saves_the_url(ctx, browser_ctx):
    with serving({"/ad": JS_AD, "/form": FORM}) as (base, seen):
        jid = form_job(ctx, base + "/ad")
        http(ctx, JS_AD)
        ctx.clients["form_find"] = FakeLLM(script={"form_find": [{"action": "click", "element": 1}]})
        outcome = resolve(ctx, ctx.repo.get_job(jid), browser=browser_ctx.browser)
        assert outcome.status == "found" and outcome.url == base + "/form"
        app = ctx.repo.get_application(jid)
        assert app["form_reached_url"] == base + "/form" and app["form_status"] == "found"
        assert ctx.repo.get_job(jid)["application_url"] == base + "/form"
        attempts = ctx.conn.execute('SELECT strategy, status, hops FROM form_attempts ORDER BY id').fetchall()
        assert [(r['strategy'], r['status']) for r in attempts] == [("http", "none"), ("browser", "found")]
        assert all("?" not in hop["url"] for hop in json.loads(attempts[-1]["hops"]))
        assert seen["POST /send"] == 0


@pytest.mark.browser
def test_preparation_uses_the_pdf_browser_and_packages_the_browser_form_url(ctx):
    # A real PDF renderer is essential: a nested sync_playwright() would break this integration.
    with serving({"/ad": JS_AD, "/form": FORM}) as (base, seen):
        jid = form_job(ctx, base + "/ad")
        http(ctx, JS_AD)
        ctx.clients["form_find"] = FakeLLM(script={"form_find": [{"action": "click", "element": 1}]})
        ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
        result = prepare_jobs(ctx)
        assert result.prepared_ids == [jid] and result.form_outcomes == {"found": 1}
        app = ctx.repo.get_application(jid)
        folder = Path(app["folder"])
        assert base + "/form" in (folder / "README.md").read_text()
        assert base + "/form" in (folder / "apply.html").read_text()
        assert (folder / "cover_letter.pdf").stat().st_size > 0
        assert ctx.repo.get_job(jid)["application_url"] == base + "/form"
        assert seen["POST /send"] == 0


@pytest.mark.parametrize("status", ["found", "restricted", "no_employer_link", "login_required", "captcha", "unreachable"])
def test_finished_or_manual_http_results_never_launch_a_browser(ctx, monkeypatch, status):
    import autoapp.formfinder.browser as module

    def forbidden(*args, **kw):
        pytest.fail("browser should not be opened")

    monkeypatch.setattr(module, "open_context", forbidden)
    result = Outcome(status, "https://employer.example/ad", None, "original result")
    assert fallback(ctx, result.url, result, title="Job", company="Employer") == result


@pytest.mark.parametrize("url", ["https://www.adzuna.de/details/1", "https://www.arbeitnow.com/jobs/1",
                                "https://de.indeed.com/viewjob?jk=1", "https://www.linkedin.com/jobs/1", None])
def test_board_and_missing_entries_are_not_browser_targets(ctx, monkeypatch, url):
    import autoapp.formfinder.browser as module

    monkeypatch.setattr(module, "open_context", lambda *a: pytest.fail("not an eligible entry"))
    outcome = Outcome("needs_browser", url, None, "HTTP blocked")
    assert fallback(ctx, url, outcome, title="Job", company="Employer") == outcome


@pytest.mark.parametrize("setting", ["disabled", "dry_run", "exhausted", "rules"])
def test_opt_out_and_limits_prevent_browser_work(ctx, setting, monkeypatch):
    import autoapp.formfinder.browser as module

    if setting == "disabled":
        ctx.config.assist.browser_fallback = False
    elif setting == "dry_run":
        ctx.dry_run = True
    elif setting == "rules":
        ctx.config.assist.navigate_mode = "rules"
    else:
        ctx.config.limits.max_llm_calls_per_run = 0
    monkeypatch.setattr(module, "open_context", lambda *a: pytest.fail("browser should not be opened"))
    outcome = Outcome("partial", "https://employer.example/ad", None, "ambiguous")
    assert fallback(ctx, outcome.url, outcome, title="Job", company="Employer") == outcome


def test_a_browser_launch_failure_preserves_the_http_result_and_prevents_repeated_launches(ctx, monkeypatch):
    import autoapp.formfinder.browser as module

    calls = []

    @contextmanager
    def broken(*args):
        calls.append(1)
        raise BrowserError("Chromium is not installed")
        yield

    monkeypatch.setattr(module, "open_context", broken)
    ctx.clients["form_find"] = FakeLLM()
    outcome = Outcome("needs_browser", "https://employer.example/apply", None, "HTTP 403")
    for _ in range(2):
        assert fallback(ctx, outcome.url, outcome, title="Job", company="Employer") == outcome
    assert calls == [1]


def test_missing_form_find_model_preserves_http_result_without_opening_browser(ctx, monkeypatch):
    import autoapp.formfinder.browser as module
    from autoapp.llm.factory import LLMConfigError

    def missing_model(_task):
        raise LLMConfigError("No form-finding model configured")

    monkeypatch.setattr(module, "open_context", lambda *a: pytest.fail("browser should not be opened"))
    monkeypatch.setattr(ctx, "llm", missing_model)
    outcome = Outcome("needs_browser", "https://employer.example/apply", None, "HTTP 403")
    assert fallback(ctx, outcome.url, outcome, title="Job", company="Employer") == outcome
    assert getattr(ctx, "_browser_fallback_unavailable", False)


@pytest.mark.browser
def test_browser_fallback_targets_an_application_control_inside_an_iframe(ctx, browser_ctx):
    ad = '''<html><body><h1>Job</h1><a href="/wrong">Jetzt bewerben</a>
<iframe title="Application" src="/frame"></iframe></body></html>'''
    frame = '<button type="button" onclick="window.top.location.href=\'/form\'">Jetzt bewerben</button>'
    with serving({"/ad": ad, "/frame": frame, "/form": FORM, "/wrong": "not the job"}) as (base, seen):
        http(ctx, ad)
        ctx.clients["form_find"] = FakeLLM(script={"form_find": [{"action": "click", "element": 2}]})
        jid = form_job(ctx, base + "/ad")
        outcome = resolve(ctx, ctx.repo.get_job(jid), browser=browser_ctx.browser)
        assert outcome.status == "found" and outcome.url == base + "/form"
        assert seen["GET /wrong"] == 0
        assert seen["POST /send"] == 0


@pytest.mark.browser
def test_refresh_package_uses_the_browser_fallback_result(ctx, browser_ctx):
    from autoapp.steps.prepare import prepare_jobs

    with serving({"/ad": JS_AD, "/form": FORM}) as (base, seen):
        jid = form_job(ctx, base + "/ad")
        ctx.config.assist.browser_fallback = False
        http(ctx, "<html><body><h1>Job</h1>Apply online soon.</body></html>")
        ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
        prepared = prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
        assert prepared.prepared_ids == [jid]
        app = ctx.repo.get_application(jid)
        old_folder = Path(app["folder"])
        assert old_folder.name.endswith(f"_{jid}_NOFORM")

        ctx.config.assist.browser_fallback = True
        http(ctx, JS_AD)
        ctx.clients["form_find"] = FakeLLM(script={"form_find": [{"action": "click", "element": 1}]})
        outcome = resolve(ctx, ctx.repo.get_job(jid), again=True, refresh_package=True, browser=browser_ctx.browser)

        assert outcome.status == "found" and outcome.url == base + "/form"
        refreshed = Path(ctx.repo.get_application(jid)["folder"])
        assert refreshed != old_folder and not old_folder.exists()
        assert base + "/form" in (refreshed / "README.md").read_text()
        assert base + "/form" in (refreshed / "apply.html").read_text()
        assert seen["POST /send"] == 0


@pytest.mark.browser
def test_browser_fallback_rejects_an_unrelated_application_form_without_custom_identity_attrs(ctx, browser_ctx):
    unrelated = """<html><body><h1>Sales Assistant</h1><form>
<p>Apply for Sales Assistant at Other GmbH</p>
<input name="first_name"><input name="email" type="email">
</form></body></html>"""
    with serving({"/ad": JS_AD, "/form": unrelated}) as (base, seen):
        jid = form_job(ctx, base + "/ad")
        ctx.repo.update_job(jid, title="Backend Developer", company="Muster GmbH")
        http(ctx, JS_AD)
        ctx.clients["form_find"] = FakeLLM(script={"form_find": [{"action": "click", "element": 1}]})

        outcome = resolve(ctx, ctx.repo.get_job(jid), browser=browser_ctx.browser)

        assert outcome.status == "partial" and outcome.url == base + "/ad"
        assert "conflicting" in outcome.note
        assert ctx.repo.get_application(jid)["form_status"] == "partial"
        assert ctx.repo.get_job(jid)["application_url"] == base + "/ad"
        assert seen["POST /send"] == 0


@pytest.mark.browser
def test_class_hidden_account_copy_needs_rendered_confirmation_then_finds_the_form(ctx, browser_ctx):
    page = """<html><head><style>.terms { display: none }</style></head><body>
<h1>Backend Developer</h1><form data-company="Muster GmbH">
<p>Application</p><input name="first_name"><input name="email" type="email">
<p class="terms">By clicking Continue you create an account.</p></form></body></html>"""
    with serving({"/ad": page}) as (base, seen):
        jid = form_job(ctx, base + "/ad")
        ctx.repo.update_job(jid, title="Backend Developer", company="Muster GmbH")
        http(ctx, page)
        ctx.clients["form_find"] = FakeLLM()

        from autoapp.formfinder.finder import find

        assert find(ctx, base + "/ad", title="Backend Developer", company="Muster GmbH").status == "needs_browser"
        outcome = resolve(ctx, ctx.repo.get_job(jid), again=True, browser=browser_ctx.browser)

        assert outcome.status == "found" and outcome.url == base + "/ad"
        assert ctx.repo.get_application(jid)["form_status"] == "found"
        assert seen["POST /send"] == 0


@pytest.mark.browser
def test_a_profile_step_is_saved_as_manual_without_creating_any_account(ctx, browser_ctx):
    account = """<html><body><h1>Bewerbung</h1><form action='/register' method='post'>
<input name='first_name'><input name='email'><button>Zum Lebenslauf</button></form>
<p>Mit Klick auf 'Zum Lebenslauf' erstellst du dein Profil bei Workwise GmbH.</p></body></html>"""
    with serving({"/ad": JS_AD, "/form": account}) as (base, seen):
        jid = form_job(ctx, base + "/ad")
        http(ctx, JS_AD)
        ctx.clients["form_find"] = FakeLLM(script={"form_find": [{"action": "click", "element": 1}]})
        result = resolve(ctx, ctx.repo.get_job(jid), browser=browser_ctx.browser)
        assert result.status == "login_required" and "creates an account/profile" in result.note
        assert ctx.repo.get_application(jid)["form_status"] == "login_required"
        assert seen["POST /register"] == 0
