import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.db.repo import Repo
from autoapp.db.schema import MIGRATIONS, migrate, open_db
from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.formfinder.detect import detect
from autoapp.formfinder.finder import Outcome, find
from autoapp.formfinder.links import links
from autoapp.formfinder.store import entry_url, refresh, resolve, selected
from autoapp.llm.base import LLMAuthError, LLMOutputError, UsageMeter
from autoapp.llm.fake import FakeLLM
from autoapp.llm.settings import LLMSettings
from autoapp.steps.context import RunContext
from autoapp.steps.prepare import prepare_jobs
from tests.helpers import make_workspace
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

FIXTURES = Path(__file__).parent / "fixtures/forms"
FORM = (FIXTURES / "cbc_application.html").read_text()


@pytest.fixture
def ctx(tmp_path):
    context = RunContext.open(make_workspace(tmp_path))
    yield context
    context.close()


def transport(ctx, handler):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return handler(request)

    ctx._http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(respond)), 0, 0)
    return seen


def test_http_full_path_and_first_page(ctx):
    pages = {"/ad": "cbc_ad.html", "/land": "cbc_overlay.html", "/details/346291.html": "cbc_details.html",
             "/bewerbung/346291.html": "cbc_application.html"}
    seen = transport(ctx, lambda r: httpx.Response(200, text=(FIXTURES / pages[r.url.path]).read_text()))
    result = find(ctx, "https://cbc.example/ad", company="CBC Cologne", reference="346291")
    assert result.status == "found" and result.url == "https://cbc.example/bewerbung/346291.html"
    assert len(seen) == 4
    assert find(ctx, result.url).status == "found"
    assert len(seen) == 4


@pytest.mark.parametrize("kind", ["http", "meta", "link", "frame"])
def test_budget_covers_every_transition(ctx, kind):
    def response(r):
        next_url = f"/{int(r.url.path[1:]) + 1}"
        if kind == "http":
            return httpx.Response(302, headers={"Location": next_url})
        if kind == "meta":
            body = f'<meta http-equiv="refresh" content="0;url={next_url}">'
        elif kind == "frame":
            body = f'<iframe title="Application" src="{next_url}"></iframe>'
        else:
            body = f'<a href="{next_url}">Jetzt bewerben</a>'
        return httpx.Response(200, text=body)

    seen = transport(ctx, response)
    result = find(ctx, "https://cbc.example/0")
    assert result.status == "partial" and len(seen) == 5


@pytest.mark.parametrize("status", [403, 429, 999])
def test_block_and_restricted_redirect(ctx, status):
    seen = transport(ctx, lambda r: httpx.Response(status))
    assert find(ctx, "https://blocked.example/start").status == "needs_browser"
    assert find(ctx, "https://blocked.example/another").status == "needs_browser"
    assert len(seen) == 1
    transport(ctx, lambda r: httpx.Response(302, headers={"Location": "https://linkedin.com/jobs/1"}))
    result = find(ctx, "https://tracking.example/start")
    assert result.status == "restricted"
    assert len(ctx.http._cache) == 1


def test_loops_and_missing_entry(ctx):
    seen = transport(ctx, lambda r: httpx.Response(302, headers={"Location": "/start"}))
    assert find(ctx, "https://cbc.example/start").status == "partial"
    assert len(seen) == 1
    assert find(ctx, None).status == "none"
    assert find(ctx, "javascript:alert(1)").url is None


@pytest.mark.parametrize("fixture,status", [("login_wall.html", "login_required"),
                                           ("captcha_wall.html", "captcha"),
                                           ("cbc_application.html", "found")])
def test_detection_fixtures(fixture, status):
    assert detect((FIXTURES / fixture).read_text()).status == status


def test_false_forms_identity_and_submission_captcha():
    assert detect((FIXTURES / "false_forms.html").read_text()).status is None
    assert detect('<form><p>Newsletter</p><input name="name"><input type="email"></form>').status is None
    assert detect('<form><p>Contact us</p><input name="name"><input type="email"></form>').status is None
    assert detect(FORM, reference="other").status == "partial"
    assert detect(FORM, company="different employer").status == "partial"
    assert detect(FORM.replace("</form>", '<div class="g-recaptcha"></div></form>', 1)).status == "found"
    assert detect('<header>Sign in</header>' + FORM).status == "found"
    assert detect('<div data-application="yes">Application<input name="name"><input type="email"></div>').status == "found"
    assert detect('<p>Referenznummer: wrong</p>' + FORM, reference="346291").status == "partial"


@pytest.mark.parametrize("answer", [{"candidate": 0}, {"candidate": 99}, LLMOutputError("bad"), LLMAuthError("auth")])
def test_model_private_bounded_and_invalid(ctx, answer):
    seen = transport(ctx, lambda r: httpx.Response(200, text=(
        '<a href="/route?session=SECRET&email=max@example.com">Go</a><a href="/other">Proceed</a>'
        if r.url.path == "/start" else FORM
    )))
    llm = FakeLLM(script={"form_find": [answer]})
    ctx.clients["form_find"] = llm
    outcome = find(ctx, "https://cbc.example/start")
    assert len(llm.calls) == 1 and ctx.meter.calls == 1
    prompt = llm.calls[0][2]
    assert "SECRET" not in prompt and "max@example.com" not in prompt
    assert len(seen) == (2 if answer == {"candidate": 0} else 1)
    assert outcome.status == ("found" if answer == {"candidate": 0} else "partial")
    if isinstance(answer, LLMAuthError):
        find(ctx, "https://cbc.example/start")
        assert len(llm.calls) == 1


def test_model_inherits_evaluate_override():
    settings = LLMSettings(tasks={"evaluate": {"provider": "google", "model": "gemini-fast", "effort": "high"}})
    assert settings.resolve("form_find") == ("google", "gemini-fast", "low")


def test_model_budget_exhaustion_does_not_request(ctx):
    transport(ctx, lambda r: httpx.Response(200, text='<a href="/one">Go</a><a href="/two">Continue</a>'))
    ctx._meter = UsageMeter(0)
    assert find(ctx, "https://cbc.example/start").status == "partial"
    assert ctx.clients == {}


def form_job(ctx):
    jid = approved_job(ctx)
    row = ctx.repo.current_evaluation(jid)
    payload = json.loads(row["payload"])
    payload["result"]["channel"] = "employer_form"
    payload["result"]["apply_email"] = None
    payload["result"]["application_url"] = "https://cbc.example/start"
    payload["result"]["reference"] = "346291"
    ctx.repo.update_job(jid, company="CBC Cologne")
    ctx.conn.execute("UPDATE evaluations SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    ctx.conn.commit()
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    return jid


@pytest.mark.parametrize("mode", ["automatic", "review", "test"])
def test_prepare_and_retry_preserve_content_and_identity(ctx, mode):
    ctx.mode = mode
    jid = form_job(ctx)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    assert prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1]))).prepared == 1
    application = ctx.repo.get_application(jid)
    assert application["form_entry_url"] == "https://cbc.example/start"
    assert application["form_status"] == "found"
    assert ctx.repo.get_job(jid)["job_url"].startswith("https://www.stepstone.de/")
    folder = Path(application["folder"])
    for name in ("README.md", "job.md", "apply.html"):
        assert "https://cbc.example/start" in (folder / name).read_text()
        with (folder / name).open("a") as file:
            file.write("\nUser edit stays here.\n")
    before = (folder / "cover_letter.md").read_bytes()
    resolve(ctx, ctx.repo.get_job(jid))
    assert len(seen) == 1
    resolve(ctx, ctx.repo.get_job(jid), again=True, refresh_package=True)
    assert len(seen) == 1  # per-run cache is retained even for explicit retry
    assert (folder / "cover_letter.md").read_bytes() == before
    assert all("User edit stays here." in (folder / name).read_text() for name in ("README.md", "job.md", "apply.html"))


def test_dry_run_changes_nothing(ctx):
    jid = form_job(ctx)
    transport(ctx, lambda r: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    files = {p: p.read_bytes() for p in ctx.ws.root.rglob("*") if p.is_file()}
    result = CliRunner().invoke(app, ["-w", str(ctx.ws.root), "forms", "--again", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would resolve" in result.output
    assert all(path.read_bytes() == value for path, value in files.items())
    assert {p for p in ctx.ws.root.rglob("*") if p.is_file()} == set(files)


def test_dry_run_missing_workspace_is_not_created(tmp_path):
    workspace = tmp_path / "absent"
    result = CliRunner().invoke(app, ["-w", str(workspace), "forms", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not workspace.exists()


def test_v4_dry_run_is_read_only_and_migration_keeps_identity(tmp_path):
    workspace = make_workspace(tmp_path)
    conn = sqlite3.connect(workspace.db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn, MIGRATIONS[:4])
    repo = Repo(conn)
    jid = repo.insert_job(title="Developer", company="CBC", status="prepared",
                          job_url="https://ad.example/123", canonical_url="https://ad.example/123",
                          application_url="https://cbc.example/start", notes="user note")
    conn.execute("INSERT INTO applications (job_id,channel,folder) VALUES (?, 'employer_form', ?)",
                 (jid, str(workspace.applications_dir / jid)))
    conn.commit()
    conn.close()
    files = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    result = CliRunner().invoke(app, ["-w", str(workspace.root), "forms", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "https://cbc.example/start" in result.output
    assert {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()} == files
    migrated = open_db(workspace.db_path)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        application = Repo(migrated).get_application(jid)
        assert all(application[key] is None for key in
                   ("form_entry_url", "form_reached_url", "form_status", "form_checked_at", "form_note"))
        job = Repo(migrated).get_job(jid)
        assert job["canonical_url"] == job["job_url"] == "https://ad.example/123"
        assert job["notes"] == "user note"
    finally:
        migrated.close()


@pytest.mark.parametrize("status", [None, "found", "partial", "needs_browser", "login_required",
                                   "captcha", "restricted", "unreachable", "none"])
def test_backfill_selection_and_retry_for_all_statuses(ctx, status):
    jid = form_job(ctx)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    ctx.repo.upsert_application(jid, form_status=status)
    assert [r["id"] for r in selected(ctx.conn)] == ([jid] if status is None else [])
    assert [r["id"] for r in selected(ctx.conn, jid, again=True)] == [jid]
    ctx.repo.update_job(jid, application_url="https://stale.example/form")
    assert resolve(ctx, ctx.repo.get_job(jid), again=True, refresh_package=True).status == "found"
    assert seen == ["https://cbc.example/start"]
    ctx.conn.execute("UPDATE jobs SET status='submitted' WHERE id=?", (jid,))
    assert selected(ctx.conn, jid, again=True) == []


def test_four_model_calls_maximum_even_with_more_ambiguous_pages(ctx):
    def respond(request):
        step = int(request.url.path[1:])
        return httpx.Response(200, text=f'<a href="/{step+1}">Go</a><a href="/alternative{step}">Proceed</a>')

    seen = transport(ctx, respond)
    model = FakeLLM(script={"form_find": [{"candidate": 0}] * 5})
    ctx.clients["form_find"] = model
    assert find(ctx, "https://cbc.example/0").status == "partial"
    assert len(seen) == 5
    assert len(model.calls) == ctx.meter.calls == 4


def test_link_ranking_excludes_legal_share_submission_and_non_http_targets():
    body = """<a href="/apply">Apply now</a><a href="/no-email">Continue without email</a>
    <a href="mailto:jobs@example.com">Apply</a><a href="javascript:send()">Apply</a>
    <a href="/privacy">Privacy</a><a href="/share">Share job</a>
    <a href="/submit">Bewerbung absenden</a><a href="/create-account">Create account</a>"""
    candidates = links(body, "https://cbc.example/start", [])
    assert [c.url for c in candidates] == ["https://cbc.example/no-email", "https://cbc.example/apply"]


@pytest.mark.parametrize("failure", [404, 500, "network"])
def test_expected_fetch_failures_are_unreachable_not_found(ctx, failure):
    def respond(request):
        if failure == "network":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(failure)

    seen = transport(ctx, respond)
    outcome = find(ctx, "https://cbc.example/start")
    assert outcome.status == "unreachable"
    assert outcome.note
    assert outcome.url == "https://cbc.example/start"
    assert len(seen) == 1


def test_refresh_validation_does_not_rewrite_ambiguous_package(ctx):
    jid = form_job(ctx)
    transport(ctx, lambda request: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    folder = Path(ctx.repo.get_application(jid)["folder"])
    readme = folder / "README.md"
    readme.write_text(readme.read_text() * 2)
    before = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="missing or ambiguous"):
        refresh(folder, Outcome("partial", "https://cbc.example/new", None, "changed"))
    assert {p: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


def test_refresh_legacy_package_with_only_job_ad_links(tmp_path):
    (tmp_path / "README.md").write_text("- **Job ad:** https://ad.example/1\nUser notes\n")
    (tmp_path / "apply.html").write_text('<a href="https://ad.example/1" rel="noopener">Job ad</a>\nUser answers')
    (tmp_path / "job.md").write_text("- **Application page:** None\nDescription")
    refresh(tmp_path, Outcome("found", "https://employer.example/form", None, "application form found"))
    refresh(tmp_path, Outcome("partial", "https://employer.example/new", None, "continue manually"))
    for name in ("README.md", "apply.html", "job.md"):
        text = (tmp_path / name).read_text()
        assert text.count("<!-- autoapp application -->") == 1
        assert "https://employer.example/new" in text
        assert "https://employer.example/form" not in text
    assert "https://ad.example/1" in (tmp_path / "README.md").read_text()
    assert "User notes" in (tmp_path / "README.md").read_text()
    assert "User answers" in (tmp_path / "apply.html").read_text()


def test_form_refresh_preserves_secondary_arbeitnow_attribution(ctx):
    jid = form_job(ctx)
    url = "https://www.arbeitnow.com/jobs/companies/cbc/role"
    ctx.repo.add_source(jid, "arbeitnow", "cbc-role", url)
    transport(ctx, lambda request: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    folder = Path(ctx.repo.get_application(jid)["folder"])
    letter = (folder / "cover_letter.md").read_bytes()
    refresh(folder, Outcome("partial", "https://cbc.example/new", None, "continue manually"))
    for name in ("README.md", "job.md", "apply.html"):
        text = (folder / name).read_text()
        assert "Arbeitnow" in text and url in text
        assert "https://cbc.example/new" in text
    assert (folder / "cover_letter.md").read_bytes() == letter


def test_prepare_block_is_nonfatal_and_email_never_finds(ctx):
    email = approved_job(ctx)
    form = form_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER, LETTER]})
    seen = transport(ctx, lambda request: httpx.Response(403))
    summary = prepare_jobs(ctx, [email, form], renderer_factory=factory(FakeRenderer([1, 1])))
    assert summary.prepared == 2
    assert seen == ["https://cbc.example/start"]
    assert ctx.repo.get_application(email)["form_status"] is None
    assert ctx.repo.get_application(form)["form_status"] == "needs_browser"


def test_restricted_entry_gives_way_to_the_matched_employer_posting(ctx):
    jid = approved_job(ctx)
    row = ctx.repo.current_evaluation(jid)
    payload = json.loads(row["payload"])
    payload["result"]["application_url"] = None
    ctx.conn.execute("UPDATE evaluations SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    ctx.conn.commit()
    job = ctx.repo.get_job(jid)
    assert entry_url(ctx, job).startswith("https://www.stepstone.de/")  # nothing matched yet
    posting = "https://karriere.acme.example/job-3102.html"
    ctx.conn.execute("INSERT INTO employer_seeds (job_id, outcome, posting_url, checked_at) VALUES (?, 'unresolved', ?, ?)",
                     (jid, posting, "2026-10-09T12:00:00+00:00"))
    assert entry_url(ctx, job).startswith("https://www.stepstone.de/")  # only a match counts
    ctx.conn.execute("UPDATE employer_seeds SET outcome = 'matched' WHERE job_id = ?", (jid,))
    assert entry_url(ctx, job) == posting
    assert ctx.repo.get_job(jid)["job_url"].startswith("https://www.stepstone.de/")  # the job itself is unchanged
    ctx.repo.update_job(jid, application_url="https://jobs.acme.example/apply/7")
    assert entry_url(ctx, ctx.repo.get_job(jid)) == "https://jobs.acme.example/apply/7"  # a usable own entry wins


def test_a_non_address_from_the_model_falls_back_to_the_ad(ctx):
    jid = approved_job(ctx)
    row = ctx.repo.current_evaluation(jid)
    payload = json.loads(row["payload"])
    payload["result"]["application_url"] = "unKnown"
    ctx.conn.execute("UPDATE evaluations SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    ctx.conn.commit()
    assert entry_url(ctx, ctx.repo.get_job(jid)).startswith("https://www.stepstone.de/")


def test_folder_name_shows_form_status_and_follows_it(ctx):
    jid = form_job(ctx)
    transport(ctx, lambda r: httpx.Response(200, text="<html><body>Willkommen</body></html>"))  # no application on the page
    assert prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1]))).prepared == 1
    application = ctx.repo.get_application(jid)
    folder = Path(application["folder"])
    assert application["form_status"] == "none" and folder.name.endswith(f"_{jid}_NOFORM") and folder.is_dir()
    assert len(folder.name) < 100
    plain = folder.parent / folder.name.removesuffix("_NOFORM")
    assert not plain.exists()
    # The package files name their files by path: those paths follow the rename.
    old_text = (folder / "apply.html").read_text()
    assert f"{plain}/" not in old_text and str(folder) in old_text
    assert all(f"{plain}/" not in (folder / n).read_text() for n in ("README.md", "job.md"))
    assert "_NOFORM_NOFORM" not in old_text
    # Preparing again (--force) reuses the renamed folder instead of creating a second one.
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    assert prepare_jobs(ctx, [jid], force=True, renderer_factory=factory(FakeRenderer([1]))).prepared == 1
    assert [p.name for p in ctx.ws.applications_dir.iterdir() if jid in p.name] == [folder.name]
    assert Path(ctx.repo.get_application(jid)["folder"]) == folder
    # The user's own edit survives the rename that follows a better form result.
    (folder / "README.md").write_text((folder / "README.md").read_text() + "\nUser edit stays here.\n")
    # A later run that finds the form removes the marker.
    transport(ctx, lambda r: httpx.Response(200, text=FORM))
    result = resolve(ctx, ctx.repo.get_job(jid), again=True, refresh_package=True)
    assert result.status == "found"
    now = Path(ctx.repo.get_application(jid)["folder"])
    assert now == plain and now.is_dir() and not folder.exists()
    assert "User edit stays here." in (now / "README.md").read_text()
    assert f"{folder}/" not in "".join((now / n).read_text() for n in ("README.md", "job.md", "apply.html"))
    assert f"{plain}/Bewerbungsmappe.pdf" in (now / "apply.html").read_text()


def test_markers_only_for_form_applications_and_never_rename_user_folders(ctx, tmp_path):
    from autoapp.package.folders import marker_for, sync_folder_name
    from autoapp.package.slug import folder_name

    assert marker_for("email", "none") is None and marker_for("employer_form", "found") is None
    assert marker_for("employer_form", None) is None
    assert {marker_for("employer_form", s) for s in ("none", "partial")} == {"NOFORM"}
    assert marker_for("employer_form", "login_required") == "LOGIN" and marker_for("employer_form", "captcha") == "CAPTCHA"
    assert len(folder_name("A" * 90, "B" * 90, "74464c", "UNREACHABLE")) < 100
    jid = form_job(ctx)
    mine = ctx.ws.applications_dir / "my own name"
    mine.mkdir(parents=True)
    ctx.repo.upsert_application(jid, folder=str(mine), channel="employer_form", form_status="none")
    assert sync_folder_name(ctx, jid) == mine and mine.is_dir()


class _Reader:
    """A stand-in for the logged-in page reader."""

    def __init__(self, link):
        self.link, self.read_urls, self.on_wait = link, [], None

    def read(self, url):
        self.read_urls.append(url)
        return self.link


def _linkedin_job(ctx):
    jid = form_job(ctx)
    row = ctx.repo.current_evaluation(jid)
    payload = json.loads(row["payload"])
    payload["result"]["application_url"] = "https://www.linkedin.com/jobs/view/42/"
    ctx.conn.execute("UPDATE evaluations SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    ctx.conn.commit()
    return jid


def test_with_the_login_switched_on_the_company_link_replaces_the_linkedin_one(ctx):
    from autoapp.fetch.apply_links import ApplyLink, RunLookup

    events = []
    ctx.events.subscribe(events.append)
    ctx.config.sources.apply_links_with_login.linkedin = True
    fake = _Reader(ApplyLink("found", "https://cbc.example/start", "apply link read from the LinkedIn page"))
    ctx._apply_links = RunLookup(ctx, reader=fake)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    jid = _linkedin_job(ctx)
    outcome = resolve(ctx, ctx.repo.get_job(jid))
    assert outcome.status == "found" and fake.read_urls == ["https://www.linkedin.com/jobs/view/42/"]
    assert seen == ["https://cbc.example/start"]  # LinkedIn itself is never fetched
    assert ctx.repo.get_job(jid)["application_url"] == "https://cbc.example/start"
    lines = [e.detail for e in events if e.kind == "apply_link"]
    assert lines == ["reading the LinkedIn job page with your login", "found: https://cbc.example/start"]


def test_without_the_switch_nothing_is_read_and_easy_apply_says_so(ctx):
    from autoapp.fetch.apply_links import ApplyLink, RunLookup

    fake = _Reader(ApplyLink("on_platform", note="this job only takes applications on LinkedIn itself (\"Einfach bewerben\")"))
    ctx._apply_links = RunLookup(ctx, reader=fake)
    transport(ctx, lambda r: httpx.Response(200, text=FORM))
    jid = _linkedin_job(ctx)
    assert resolve(ctx, ctx.repo.get_job(jid)).status == "restricted" and fake.read_urls == []  # off by default

    ctx.config.sources.apply_links_with_login.linkedin = True
    outcome = resolve(ctx, ctx.repo.get_job(jid), again=True)
    assert outcome.status == "restricted" and "only takes applications on LinkedIn itself" in outcome.note
    from autoapp.formfinder.explain import explain

    shown = explain(outcome.status, outcome.note)
    assert shown.short == "apply on the site" and "Easy Apply" in shown.todo


def test_the_per_run_limit_is_kept(ctx):
    from autoapp.fetch.apply_links import ApplyLink, RunLookup

    ctx.config.sources.apply_links_with_login.linkedin = True
    ctx.config.sources.apply_links_with_login.max_per_run = 1
    fake = _Reader(ApplyLink("none", note="no apply button found on the LinkedIn page"))
    lookup = RunLookup(ctx, reader=fake)
    job = {"id": "x", "company": "A", "title": "B"}
    assert lookup.read(job, "https://www.linkedin.com/jobs/view/1/").status == "none"
    assert lookup.read(job, "https://www.linkedin.com/jobs/view/2/") is None and len(fake.read_urls) == 1


def test_a_portal_login_box_on_an_ad_page_is_not_a_login_wall():
    from autoapp.formfinder.detect import detect

    box = "<div class='modal'><form><input type='email' name='u'><input type='password' name='p'></form></div>"
    ad = ("<h1>Software Engineer (m/w/d)</h1>" + "<p>Du planst und entwickelst neue Anwendungen im Team.</p>" * 20
          + "<a href='https://portal.example/bewerben/3531'>Jetzt bewerben</a>")
    assert detect(f"<html><body>{box}{ad}</body></html>").status != "login_required"
    assert detect(f"<html><body>{box}<p>Bitte anmelden.</p></body></html>").status == "login_required"


def test_an_apply_page_built_by_javascript_is_handed_to_the_browser(ctx):
    # chefsculinar.de -> meine-karriere-im-handel.de/stellenangebot/bewerben/3531: the form is drawn by a script.
    from autoapp.fetch.http_fetcher import FetchResult
    from autoapp.formfinder.finder import find

    ad = ("<html><body><h1>Software Engineer (m/w/d)</h1>" + "<p>Aufgaben und Profil.</p>" * 5
          + "<a href='https://portal.example/stellenangebot/bewerben/3531'>Jetzt bewerben</a></body></html>")
    portal = ("<html><head><script src='/build/jobportal.js'></script></head><body><div id='app'></div>"
              "<a href='/bewerberinfos'>Bewerberinfos</a></body></html>")

    class Http:
        def fetch_once(self, url, **_):
            html = portal if "bewerben" in url else ad
            return FetchResult(url=url, status=200, html=html, final_url=url)

        def close(self):
            pass

    ctx._http = Http()
    out = find(ctx, "https://www.employer.example/software-engineer-m-w-d-40624.htm", title="Software Engineer")
    assert out.status == "needs_browser" and out.url == "https://portal.example/stellenangebot/bewerben/3531"


def test_an_arbeitsagentur_entry_gives_way_to_the_matched_employer_posting(ctx):
    jid = approved_job(ctx)
    ba = "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1-S"
    ctx.repo.update_job(jid, job_url=ba, application_url=None)
    row = ctx.repo.current_evaluation(jid)
    payload = json.loads(row["payload"])
    payload["result"]["application_url"] = None
    ctx.conn.execute("UPDATE evaluations SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    ctx.conn.commit()
    assert entry_url(ctx, ctx.repo.get_job(jid)) == ba  # nothing matched yet: the page is all there is
    posting = "https://karriere.acme.example/job-3102.html"
    ctx.conn.execute("INSERT INTO employer_seeds (job_id, outcome, posting_url, checked_at) VALUES (?, 'matched', ?, ?)",
                     (jid, posting, "2026-10-09T12:00:00+00:00"))
    assert entry_url(ctx, ctx.repo.get_job(jid)) == posting


# --- Arbeitsagentur jobs without an employer link (no_employer_link) ---------------------------------------------

BA_PAGE = "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-77-S"
EMPLOYER_AD = "https://karriere.cbc.example/job-77.html"


def aa_job(ctx, *, partner="jobvector.de", external="https://www.jobvector.de/job/x/"):
    """An approved form job from the Arbeitsagentur whose externeURL (if any) leads to a partner board."""
    jid = form_job(ctx)
    row = ctx.repo.current_evaluation(jid)
    payload = json.loads(row["payload"])
    payload["result"]["application_url"] = None
    ctx.conn.execute("UPDATE evaluations SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    ctx.conn.execute("DELETE FROM job_sources WHERE job_id = ?", (jid,))
    ctx.conn.commit()
    ctx.repo.update_job(jid, job_url=BA_PAGE, application_url=external)
    detail = {"allianzpartnerName": partner, **({"externeURL": external} if external else {})}
    ctx.repo.add_source(jid, "arbeitsagentur", "10001-77-S", raw={"detail": detail})
    return jid


def record_misses(ctx, jid, *, search=True, careers=True):
    if search:
        ctx.conn.execute("INSERT INTO posting_searches (job_id, found, reason, checked_at) VALUES (?, 0, ?, ?)",
                         (jid, "the search found only job boards", "2026-10-11T12:00:00+00:00"))
    if careers:
        ctx.conn.execute("INSERT INTO employer_seeds (job_id, outcome, reason, checked_at) VALUES (?, 'not_found', ?, ?)",
                         (jid, "no matching opening on the careers site", "2026-10-11T12:00:00+00:00"))
    ctx.conn.commit()


def prepared_texts(ctx, jid):
    folder = Path(ctx.repo.get_application(jid)["folder"])
    return folder, {n: (folder / n).read_text() for n in ("README.md", "job.md", "apply.html")}


def test_both_lookups_missing_prepares_the_letter_and_marks_the_folder_noemployer(ctx):
    jid = aa_job(ctx)
    record_misses(ctx, jid)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    assert prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1]))).prepared == 1
    assert seen == []  # the Arbeitsagentur (or partner board) page is never crawled
    app = ctx.repo.get_application(jid)
    assert app["form_status"] == "no_employer_link" and app["form_note"].startswith("employer ad not found yet: ")
    assert ctx.repo.get_job(jid)["application_url"] is None and ctx.repo.get_job(jid)["status"] == "prepared"
    folder, texts = prepared_texts(ctx, jid)
    assert folder.name.endswith(f"_{jid}_NOEMPLOYER") and (folder / "cover_letter.pdf").exists()
    for name, text in texts.items():
        assert "employer ad not found yet" in text, name
    assert f"- **Job ad:** {BA_PAGE}" in texts["README.md"] and "Application link:** none yet" in texts["README.md"]
    assert f'<a href="{BA_PAGE}" rel="noopener">Job ad</a>' in texts["apply.html"]
    assert "Application page</a>" not in texts["apply.html"]
    assert "Application page:** none yet" in texts["job.md"]
    for text in texts.values():
        assert f"Application page:** {BA_PAGE}" not in text and f"Application link:** {BA_PAGE}" not in text
    from autoapp.tracking.csv_export import export_csv

    export_csv(ctx.conn, ctx.ws.csv_path, ctx.config.tracking.delimiter, include_skipped=True)
    assert "employer ad not found" in ctx.ws.csv_path.read_text()


def test_the_employers_ad_found_later_refreshes_status_marker_and_links(ctx):
    jid = aa_job(ctx)
    record_misses(ctx, jid)
    transport(ctx, lambda r: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    old, _ = prepared_texts(ctx, jid)
    assert old.name.endswith("_NOEMPLOYER")
    ctx.conn.execute("INSERT INTO employer_seeds (job_id, outcome, posting_url, checked_at) VALUES (?, 'matched', ?, ?) "
                     "ON CONFLICT(job_id) DO UPDATE SET outcome = 'matched', posting_url = excluded.posting_url",
                     (jid, EMPLOYER_AD, "2026-10-12T12:00:00+00:00"))
    ctx.repo.update_job(jid, application_url=EMPLOYER_AD)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    assert resolve(ctx, ctx.repo.get_job(jid), again=True, refresh_package=True).status == "found"
    assert seen and seen[0] == EMPLOYER_AD
    folder, texts = prepared_texts(ctx, jid)
    assert not folder.name.endswith("_NOEMPLOYER") and not old.exists()
    assert ctx.repo.get_application(jid)["form_status"] == "found"
    assert EMPLOYER_AD in texts["README.md"] and EMPLOYER_AD in texts["apply.html"] and "none yet" not in texts["job.md"]


def test_a_job_prepared_before_the_lookups_ran_follows_them(ctx):
    from autoapp.employers.service import _recheck_no_employer_forms

    jid = aa_job(ctx)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    assert seen == []
    assert ctx.repo.get_application(jid)["form_status"] == "restricted"
    folder, _ = prepared_texts(ctx, jid)
    assert folder.name.endswith("_RESTRICTED")
    record_misses(ctx, jid)
    _recheck_no_employer_forms(ctx, [jid], lambda *a, **k: None)
    folder, texts = prepared_texts(ctx, jid)
    assert ctx.repo.get_application(jid)["form_status"] == "no_employer_link" and folder.name.endswith("_NOEMPLOYER")
    assert "employer ad not found yet" in texts["README.md"] and seen == []


def test_a_search_that_has_not_run_is_not_a_miss(ctx, monkeypatch):
    jid = aa_job(ctx)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    for misses in ({}, {"careers": False}, {"search": False}):  # neither lookup, or only one of them, ran
        ctx.conn.execute("DELETE FROM posting_searches")
        ctx.conn.execute("DELETE FROM employer_seeds")
        if misses == {"careers": False}:
            record_misses(ctx, jid, careers=False)
        elif misses == {"search": False}:
            record_misses(ctx, jid, search=False)
        outcome = resolve(ctx, ctx.repo.get_job(jid), again=True)
        assert outcome.status == "restricted" and outcome.url is None, misses
        assert "employer ad not searched yet" in outcome.note
        assert ctx.repo.get_job(jid)["application_url"] is None
    assert "PARALLEL_API_KEY" in resolve(ctx, ctx.repo.get_job(jid), again=True).note
    assert seen == []


def test_an_ad_posted_only_on_the_arbeitsagentur_keeps_that_link_with_a_label(ctx):
    jid = aa_job(ctx, partner="arbeitsagentur.de", external=None)
    record_misses(ctx, jid)
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    app = ctx.repo.get_application(jid)
    assert app["form_status"] == "restricted" and app["form_note"].startswith("only on the Arbeitsagentur")
    assert ctx.repo.get_job(jid)["application_url"] == BA_PAGE and seen == []
    _, texts = prepared_texts(ctx, jid)
    assert f"Application link:** {BA_PAGE} (only on the Arbeitsagentur)" in texts["README.md"]
    assert "only on the Arbeitsagentur" in texts["apply.html"] and "NOEMPLOYER" not in ctx.repo.get_application(jid)["folder"]


def test_a_direct_posting_still_waits_for_both_lookups(ctx):
    jid = aa_job(ctx, partner="arbeitsagentur.de", external=None)
    transport(ctx, lambda r: httpx.Response(200, text=FORM))
    outcome = resolve(ctx, ctx.repo.get_job(jid), again=True)
    assert outcome.status == "restricted" and outcome.url is None and "not searched yet" in outcome.note


def test_an_employers_own_externe_url_is_still_followed(ctx):
    jid = aa_job(ctx, partner="cbc.example", external="https://cbc.example/start")
    seen = transport(ctx, lambda r: httpx.Response(200, text=FORM))
    assert resolve(ctx, ctx.repo.get_job(jid)).status == "found" and seen == ["https://cbc.example/start"]


def test_the_new_status_in_labels_markers_and_explanations():
    from autoapp.formfinder.explain import explain
    from autoapp.formfinder.finder import LABELS
    from autoapp.package.folders import marker_for

    assert LABELS["no_employer_link"] == "employer ad not found"
    assert marker_for("employer_form", "no_employer_link") == "NOEMPLOYER" and marker_for("email", "no_employer_link") is None
    found = explain("no_employer_link", "employer ad not found yet: the search found only job boards")
    assert found.short == "employer ad not found" and "job boards" in found.what
    assert explain("restricted", "only on the Arbeitsagentur: x").short == "apply on the Arbeitsagentur"
    assert explain("restricted", "restricted target: arbeitsagentur.de; employer ad not searched yet: no key").short \
        == "employer ad not searched"
