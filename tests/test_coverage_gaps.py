"""Tests that close gaps found in the spec-to-test review (one test per spec scenario)."""

from __future__ import annotations

import configparser
import email
import json
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.config.models import Location, SearchProfile
from autoapp.db.status import Status
from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.fetch.http_fetcher import FetchResult, HttpFetcher
from autoapp.llm.base import UsageMeter
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer
from autoapp.sources.adzuna import AdzunaSource
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.alerts.stepstone import StepStoneParser
from autoapp.sources.models import RawJob
from autoapp.sources.stepstone_links import ResolvedLink
from autoapp.steps.context import RunContext
from autoapp.steps.evaluate import evaluate_jobs
from autoapp.steps.fetch import fetch_details
from autoapp.steps.pipeline import RunSummary
from autoapp.steps.prepare import prepare_jobs
from autoapp.steps.render import render_application
from autoapp.steps.search import run_search
from autoapp.steps.send import send_applications
from autoapp.tracking.commands import mark
from autoapp.writing.prompt import JobContext, WritingContext, build_user
from autoapp.writing.writer import LetterFrame, LetterWriter
from tests.alerts.conftest import load_eml
from tests.helpers import CONFIG, make_workspace
from tests.steps.test_evaluate_step import verdict
from tests.steps.test_other_steps import FakeSource, _prepared
from tests.steps.test_prepare_step import (
    AD,
    LETTER,
    FakeRenderer,
    approved_job,
    factory,
)
from tests.test_cli_commands import SETUP, _unavailable

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


@pytest.fixture
def cli_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return make_workspace(tmp_path, config=CONFIG + SETUP)


def cli(ws, *args):
    return runner.invoke(app, ["-w", str(ws.root), *args], catch_exceptions=False)


# --- discovery -----------------------------------------------------------------------------


def test_two_search_profiles_each_job_records_its_profile(ctx):
    ctx.config.search_profiles = [
        SearchProfile(name="backend-berlin", keywords=["Backend Developer"], locations=[Location(name="Berlin")]),
        SearchProfile(name="remote-de", keywords=["Data Engineer"]),
    ]
    queried: list[str] = []

    class PerProfile(FakeSource):
        def search(self, profile):
            queried.append(profile.name)
            yield RawJob(source="fakeapi", source_job_id=f"id-{profile.name}", title=f"Job from {profile.name}",
                         company=f"Firma {profile.name}", location="Berlin", search_profile=profile.name)

    run_search(ctx, api_sources=[PerProfile([])], mailbox=FakeMailbox([]))
    assert queried == ["backend-berlin", "remote-de"]  # queried exactly once per profile
    rows = {j["title"]: j["search_profile"] for j in ctx.repo.jobs_by_status(*[s.value for s in Status])}
    assert rows == {"Job from backend-berlin": "backend-berlin", "Job from remote-de": "remote-de"}


def test_adzuna_unconfigured_is_skipped_and_other_sources_still_run(ctx):
    fake = FakeSource([RawJob(source="fakeapi", source_job_id="1", title="Backend Developer", company="A GmbH",
                              location="Berlin")])
    s = run_search(ctx, api_sources=[AdzunaSource(env={}), fake], mailbox=FakeMailbox([]))
    assert s.sources["adzuna"].note == "adzuna: skipped (not configured)"
    assert s.sources["adzuna"].found == 0
    assert s.sources["fakeapi"].new == 1
    assert len(ctx.repo.jobs_by_status("new")) == 1


def test_run_summary_shows_unrecognised_email_count(ctx):
    msg = EmailMessage()
    msg["From"] = "Unknown Jobs <alert@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = "New jobs for you"
    msg["Message-ID"] = "<indeed-1@indeed.com>"
    msg.set_content("Some jobs")
    s = run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<indeed-1@indeed.com>", msg)]))
    assert s.unrecognised_emails == 1
    lines = RunSummary(search=s).lines()
    assert any("unknown senders: 1" in line for line in lines), lines


# --- detail fetching -------------------------------------------------------------------------


class FakeBrowser:
    def __init__(self, result: FetchResult, blocked_sites: dict[str, str] | None = None):
        self.result = result
        self.blocked_sites = blocked_sites or {}
        self.fetched: list[str] = []
        self.closed = False

    def fetch(self, url):
        self.fetched.append(url)
        return self.result

    def close(self):
        self.closed = True


def _no_network_http() -> HttpFetcher:
    return HttpFetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))), 0, 0,
                       sleep=lambda s: None)


def test_fetch_details_replaces_truncated_title(ctx):
    full_title = "Duales Studium (B.Sc.) Data Science und Künstliche Intelligenz"
    cards = StepStoneParser().parse(load_eml("stepstone_alert.eml"))
    card = next(c for c in cards if c.title.startswith("Duales Studium"))
    assert card.title_truncated
    url = "https://www.stepstone.de/stellenangebote--Duales-Studium--13850001-inline.html"
    out = ingest(ctx.repo, card_to_raw(card, ResolvedLink("13850001", url)))
    assert out.created
    page = ('<html><head><script type="application/ld+json">'
            + json.dumps({"@type": "JobPosting", "title": full_title, "description": "<p>Studium bei Seeberger.</p>",
                          "hiringOrganization": {"name": "Seeberger GmbH"}})
            + "</script></head><body></body></html>")
    browser = FakeBrowser(FetchResult(url=url, status=200, html=page, final_url=url))
    s = fetch_details(ctx, http=_no_network_http(), browser=browser)
    assert browser.fetched == [url]
    assert s.fetched == 1
    job = ctx.repo.get_job(out.job_id)
    assert job["title"] == full_title
    assert "Seeberger" in job["description"]


def test_fetch_details_challenge_marks_unavailable_and_reports_blocked_site(ctx):
    url = "https://www.stepstone.de/stellenangebote--X--1-inline.html"
    jid = ctx.repo.insert_job(title="Dev", company="A GmbH", job_url=url)
    browser = FakeBrowser(FetchResult(url=url, status=403, blocked=True, reason="challenge"),
                          blocked_sites={"stepstone": "challenge"})
    s = fetch_details(ctx, http=_no_network_http(), browser=browser)
    assert s.unavailable == 1 and s.fetched == 0
    assert ctx.repo.get_job(jid)["status"] == "description_unavailable"
    assert "stepstone" in s.blocked_sites
    assert any("challenge" in (e["note"] or "") for e in ctx.repo.history(jid))
    assert any("blocked: stepstone" in line for line in RunSummary(fetch=s).lines())


def test_add_command_stores_source_manual(cli_ws, monkeypatch):
    monkeypatch.setattr("autoapp.steps.fetch.fetch_details", lambda ctx, **kw: _unavailable(ctx))
    r = cli(cli_ws, "add", "https://jobs.example-firma.de/stellen/42")
    job_id = r.output.split("Added job ", 1)[1].split(":", 1)[0]
    c = RunContext.open(cli_ws, start_run=False)
    try:
        assert [s["source"] for s in c.repo.sources(job_id)] == ["manual"]
    finally:
        c.close()


# --- evaluation -----------------------------------------------------------------------------


def _new_job(ctx, title="Backend Developer"):
    jid = ctx.repo.insert_job(title=title, company="Muster GmbH", location="Berlin", description=AD,
                              search_profile="backend")
    ctx.repo.add_source(jid, "arbeitsagentur", f"id-{jid}")
    return jid


def test_stored_evaluation_has_contact_reference_and_requested_documents(ctx):
    jid = _new_job(ctx)
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)]})
    evaluate_jobs(ctx)
    payload = json.loads(ctx.repo.current_evaluation(jid)["payload"])
    assert payload["result"]["contact_person"] == "Frau Weber"
    assert payload["result"]["reference"] == "2026-17"
    assert payload["result"]["requested_documents"] == ["cv", "cover_letter", "certificates"]


def test_mark_apply_on_maybe_approves_job_and_sets_decision_apply(ctx):
    jid = _new_job(ctx)
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(68)]})
    evaluate_jobs(ctx)
    assert ctx.repo.get_job(jid)["status"] == "review"
    assert ctx.repo.current_evaluation(jid)["decision"] == "maybe"
    mark(ctx.conn, jid, "apply")
    assert ctx.repo.get_job(jid)["status"] == "approved"
    ev = ctx.repo.current_evaluation(jid)
    assert ev["decision"] == "apply", "spec: the job's decision becomes apply"
    assert json.loads(ev["payload"])["decision"] == "apply"


def test_reopened_database_does_not_reevaluate_evaluated_jobs(tmp_path):
    ws = make_workspace(tmp_path)
    c = RunContext.open(ws)
    jid = _new_job(c)
    c.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(85)]})
    assert evaluate_jobs(c).evaluated == 1
    c.close()

    c2 = RunContext.open(ws)
    try:
        llm = FakeLLM(script={})  # any call raises AssertionError
        c2.clients["evaluate"] = llm
        s = evaluate_jobs(c2)
        s2 = evaluate_jobs(c2, [jid])
        assert (s.evaluated, s2.evaluated) == (0, 0)
        assert llm.calls == []
        assert c2.repo.get_job(jid)["status"] == "approved"
    finally:
        c2.close()


# --- writer -----------------------------------------------------------------------------------


def _job_ctx(language="de", max_words=300):
    return JobContext(title="Backend Developer", company="Muster GmbH", description="Python Entwickler gesucht.",
                      language=language, requirements=[], decision="apply", max_words=max_words,
                      max_assumed_skills=5)


def test_writer_prompt_language_follows_letter_language():
    assert "in German" in build_user(_job_ctx("de"))
    assert "in English" in build_user(_job_ctx("en"))
    assert "in English" not in build_user(_job_ctx("de"))


SENTENCE = "Ich entwickle Services mit Python und betreue die Pipelines meines Teams. "  # 12 words


def _draft(words: int) -> dict:
    n = words // 12
    paragraphs = [(SENTENCE * (n // 3)).strip() for _ in range(3)]
    return {"paragraphs": paragraphs, "motivation_short": "Ihre Plattform passt zu meiner Arbeit.",
            "email_body": "Guten Tag,\n\nanbei meine Bewerbung.\n\nViele Grüße\nMax", "claims": []}


def _word_count(letter) -> int:
    return len(letter.body.split())


def _write(llm: FakeLLM):
    w = LetterWriter(llm, UsageMeter(50), max_retries=0)
    wctx = WritingContext("name: Max Mustermann", "CV Python Services Pipelines Team", "")
    return w.write(wctx, _job_ctx(), LetterFrame(None, None, "Max Mustermann"))


def test_over_long_draft_is_rewritten_once_and_fits_limit():
    long_draft, short_draft = _draft(384), _draft(240)
    assert 300 < len(" ".join(long_draft["paragraphs"]).split())
    llm = FakeLLM(script={"write": [long_draft, short_draft]})
    out = _write(llm)
    assert len(llm.calls) == 2 and out.llm_calls == 2
    assert "words" in llm.calls[1][2] and "limit is 300" in llm.calls[1][2]
    assert _word_count(out.letter) <= 300
    assert out.needs_review == []


def test_still_over_long_after_rewrite_is_flagged_needs_review():
    llm = FakeLLM(script={"write": [_draft(384), _draft(384)]})
    out = _write(llm)
    assert len(llm.calls) == 2  # only one rewrite
    assert out.needs_review and any("words" in r and "300" in r for r in out.needs_review)


def test_search_profile_writing_instructions_reach_write_prompt(ctx):
    ctx.config.search_profiles[0].writing_instructions = "Use a more informal tone"
    approved_job(ctx)
    llm = FakeLLM(script={"write": [LETTER]})
    ctx.clients["write"] = llm
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert "Use a more informal tone" in llm.calls[0][2]


def test_instructions_file_appears_in_system_blocks_of_first_write_call(ctx):
    text = ctx.ws.writing_instructions_path.read_text(encoding="utf-8")
    assert text.strip()
    approved_job(ctx)
    llm = FakeLLM(script={"write": [LETTER]})
    ctx.clients["write"] = llm
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    system_text = "\n".join(b.text for b in llm.calls[0][1])
    assert text.strip() in system_text


# --- prepare ----------------------------------------------------------------------------------


def _folder(ctx, jid) -> Path:
    return Path(ctx.repo.get_application(jid)["folder"])


def test_prepared_email_subject_and_body(ctx):
    jid = _prepared(ctx)
    msg = email.message_from_bytes((_folder(ctx, jid) / "email.eml").read_bytes(), policy=policy.default)
    assert msg["Subject"] == "Bewerbung als Backend Developer, Kennziffer 2026-17"
    assert msg.get_body(("plain",)).get_content().strip() == LETTER["email_body"]


def test_form_answers_contain_notice_period_and_earliest_start(ctx):
    jid = _prepared(ctx)
    text = (_folder(ctx, jid) / "form_answers.md").read_text(encoding="utf-8")
    assert "4 Wochen zum Monatsende" in text
    assert "1. Dezember 2026" in text


@pytest.mark.parametrize("estimate", [(55000, 75000), (70000, 90000)])
def test_platform_estimate_never_changes_salary_expectation(ctx, estimate):
    jid = ctx.repo.insert_job(title="Backend Developer (m/w/d)", company="Muster GmbH", location="Berlin",
                              description=AD, search_profile="backend", salary_estimate_min=estimate[0],
                              salary_estimate_max=estimate[1],
                              job_url="https://www.stepstone.de/stellenangebote--1-inline.html")
    ctx.repo.add_source(jid, "stepstone-alert", f"src-{jid}")
    from tests.steps.test_prepare_step import evaluation_payload

    final = evaluation_payload(ctx)
    ctx.repo.add_evaluation(jid, fit_score=final.fit_score, decision=final.decision,
                            payload=final.model_dump(mode="json"))
    ctx.repo.transition(jid, Status.APPROVED)
    assert ctx.repo.get_job(jid)["ad_salary_min"] is None
    llm = FakeLLM(script={"write": [LETTER]})
    ctx.clients["write"] = llm
    ctx.mode = "automatic"  # this test reads the folder's form_answers.md
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert ctx.repo.get_application(jid)["salary_expectation"] == "65000"
    assert "65.000" in llm.calls[0][2]  # the salary line offered to the writer
    assert "65000" in (_folder(ctx, jid) / "form_answers.md").read_text(encoding="utf-8")


# --- send -------------------------------------------------------------------------------------


def test_manual_send_confirmed_marks_sent_and_second_send_is_refused(ctx):
    jid = _prepared(ctx)
    mailer = FakeMailer()
    s = send_applications(ctx, [jid], manual=True, confirm=lambda *_: True, mailer=mailer)
    assert s.sent == [jid] and len(mailer.sent) == 1
    assert ctx.repo.get_job(jid)["status"] == "sent"
    s2 = send_applications(ctx, [jid], manual=True, confirm=lambda *_: True, mailer=mailer)
    assert s2.sent == [] and len(mailer.sent) == 1
    assert any("already sent" in r for r in s2.not_sent[jid]), s2.not_sent


# --- CLI ----------------------------------------------------------------------------------------


def test_prepare_command_prepares_three_approved_jobs_without_search_or_evaluate(cli_ws, monkeypatch):
    # this test checks the folder package, so the CLI runs in automatic mode
    cli_ws.config_path.write_text(cli_ws.config_path.read_text(encoding="utf-8") + "\nrun:\n  default_mode: automatic\n",
                                  encoding="utf-8")
    c = RunContext.open(cli_ws, start_run=False)
    ids = [approved_job(c, company=f"Firma {i}") for i in range(3)]
    c.close()

    def boom(*a, **kw):
        raise AssertionError("prepare must not search or evaluate")

    monkeypatch.setattr("autoapp.steps.search.run_search", boom)
    monkeypatch.setattr("autoapp.steps.evaluate.evaluate_jobs", boom)
    llm = FakeLLM(script={"write": [LETTER] * 3})
    monkeypatch.setattr("autoapp.steps.context.RunContext.llm", lambda self, task: llm)
    # ``renderer_factory=PdfRenderer`` is bound when prepare_jobs is defined, so patch the default itself.
    from autoapp.steps import prepare as prepare_mod

    monkeypatch.setitem(prepare_mod.prepare_jobs.__kwdefaults__, "renderer_factory", factory(FakeRenderer([1])))
    r = cli(cli_ws, "prepare")
    assert r.exit_code == 0, r.output
    folders = [p for p in cli_ws.applications_dir.iterdir() if p.is_dir()]
    assert len(folders) == 3
    assert len(llm.calls) == 3
    c = RunContext.open(cli_ws, start_run=False)
    try:
        assert {c.repo.get_job(i)["status"] for i in ids} == {"prepared"}
    finally:
        c.close()


# --- deployment -----------------------------------------------------------------------------------


def _unit(name: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # keep key case
    cp.read(ROOT / "deploy" / "systemd" / name, encoding="utf-8")
    return cp


def test_systemd_timer_runs_daily_and_service_runs_non_interactive():
    timer, service = _unit("autoapp.timer"), _unit("autoapp.service")
    on_calendar = timer["Timer"]["OnCalendar"]
    assert on_calendar == "daily" or on_calendar.startswith("*-*-*"), on_calendar
    assert timer["Timer"]["Persistent"].lower() == "true"
    assert "run --non-interactive" in service["Service"]["ExecStart"]


# --- assist ----------------------------------------------------------------------------------------


def test_assist_mark_submitted_records_event_dated_today(ctx):
    from autoapp.assist.session import AssistSession

    jid = _prepared(ctx)
    sess = AssistSession(ctx, jid, say=lambda s: None, use_llm=False)
    sess.mark_submitted()
    assert ctx.repo.get_job(jid)["status"] == "submitted"
    ev = [e for e in ctx.repo.history(jid) if e["to_status"] == "submitted"]
    assert len(ev) == 1
    now = datetime.now(UTC)
    stamp = datetime.fromisoformat(ev[0]["at"])
    assert stamp.tzinfo is not None
    assert stamp.date() in {now.date(), now.astimezone(ZoneInfo("Europe/Berlin")).date()}
    assert abs((now - stamp).total_seconds()) < 120


# --- real browser -----------------------------------------------------------------------------------


@pytest.mark.browser
def test_render_after_edit_puts_edited_sentence_in_pdfs_and_keeps_email_attachment(ctx):
    from pypdf import PdfReader

    from autoapp.package.pdf import PdfRenderer

    jid = _prepared(ctx)
    folder = _folder(ctx, jid)
    md = folder / "cover_letter.md"
    new = "Ich freue mich sehr auf Ihre Antwort."
    md.write_text(md.read_text().replace("Ich freue mich auf ein Gespräch.", new))
    assert new in md.read_text()
    render_application(ctx, jid, renderer_factory=PdfRenderer)

    def page1(name):
        return " ".join(PdfReader(folder / name).pages[0].extract_text().split())

    assert new in page1("cover_letter.pdf")
    assert new in page1("Bewerbungsmappe.pdf")
    msg = email.message_from_bytes((folder / "email.eml").read_bytes(), policy=policy.default)
    atts = list(msg.iter_attachments())
    assert [a.get_filename() for a in atts] == ["Bewerbungsmappe.pdf"]
    assert atts[0].get_content() == (folder / "Bewerbungsmappe.pdf").read_bytes()


@pytest.mark.browser
def test_apply_html_copy_button_puts_salary_on_clipboard(ctx):
    from playwright.sync_api import sync_playwright

    jid = _prepared(ctx)
    page_uri = (_folder(ctx, jid) / "apply.html").resolve().as_uri()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.grant_permissions(["clipboard-read", "clipboard-write"])
            page = context.new_page()
            page.goto(page_uri)
            card = page.locator(".card", has=page.locator(".label", has_text="Salary expectation"))
            card.get_by_role("button", name="Copy").click()
            page.wait_for_function("document.querySelector('button.done') !== null")
            assert page.evaluate("navigator.clipboard.readText()") == "65000"
        finally:
            browser.close()


def test_adzuna_limit_is_reported_in_the_search_summary(ctx):
    from autoapp.sources.budget import RequestBudget

    def handler(request):
        return httpx.Response(429, json={})

    src = AdzunaSource(httpx.Client(transport=httpx.MockTransport(handler)),
                       env={"ADZUNA_APP_ID": "i", "ADZUNA_APP_KEY": "k"}, delay_s=0,
                       budget=RequestBudget(ctx.conn, "adzuna", per_run=5, per_day=5, per_month=5))
    s = run_search(ctx, api_sources=[src], mailbox=FakeMailbox([]))
    assert "adzuna: Adzuna says too many requests (HTTP 429)" in s.sources["adzuna"].note


def test_default_adzuna_source_uses_the_configured_caps(ctx):
    from autoapp.steps.search import _default_sources

    ctx.config.sources.adzuna.max_requests_per_run = 7
    adzuna = next(s for s in _default_sources(ctx) if s.name == "adzuna")
    assert adzuna._budget._per_run == 7


def test_a_pdf_is_never_read_as_a_page():
    import httpx

    from autoapp.fetch.http_fetcher import HttpFetcher

    pdf = httpx.MockTransport(lambda r: httpx.Response(200, content=b"%PDF-1.7 \x00\xff binary",
                                                       headers={"Content-Type": "application/pdf"}))
    page = HttpFetcher(httpx.Client(transport=pdf), sleep=lambda s: None).fetch("https://firma.de/stelle.pdf")
    assert not page.ok and page.html is None and "application/pdf" in page.reason


def test_a_huge_ad_is_cut_before_it_reaches_the_model():
    from autoapp.evaluation.prompt import MAX_AD_CHARS, JobView, build_user

    user = build_user(JobView(title="Dev", company="X", location=None, source="s", description="x" * 2_000_000), "")
    assert len(user) < MAX_AD_CHARS + 1000 and "was cut" in user
