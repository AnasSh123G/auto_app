import email
from email import policy
from pathlib import Path

import httpx
import pytest

from autoapp.db.status import Status
from autoapp.fetch.http_fetcher import HttpFetcher
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer, MailAuthError
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.sources.stepstone_links import ResolvedLink
from autoapp.steps.context import RunContext
from autoapp.steps.fetch import fetch_details
from autoapp.steps.prepare import prepare_jobs
from autoapp.steps.render import parse_cover_letter_md, render_application
from autoapp.steps.search import run_search
from autoapp.steps.send import send_applications
from tests.helpers import make_workspace
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory

ALERTS = Path(__file__).parent.parent / "fixtures" / "alerts"


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


# --- search ---------------------------------------------------------------------------


class FakeSource:
    name = "fakeapi"

    def __init__(self, jobs, fail=False):
        self.jobs, self.fail, self.details = jobs, fail, 0

    def skip_reason(self):
        return None

    def search(self, profile):
        if self.fail:
            raise httpx.ConnectError("503 Service Unavailable")
        yield from (j.model_copy(update={"search_profile": profile.name}) for j in self.jobs)

    def fetch_detail(self, raw):
        self.details += 1
        return raw.model_copy(update={"description": "Volltext der Anzeige"})


def test_search_ingests_filters_and_isolates_failures(ctx):
    ctx.config.search_profiles[0].exclude_keywords = ["Werkstudent"]
    good = FakeSource([
        RawJob(source="fakeapi", source_job_id="1", title="Backend Developer", company="A GmbH", location="Berlin"),
        RawJob(source="fakeapi", source_job_id="2", title="Werkstudent Backend", company="B GmbH", location="Berlin"),
    ])
    s = run_search(ctx, api_sources=[good, type("Down", (FakeSource,), {"name": "down"})([], fail=True)],
                   mailbox=FakeMailbox([]))
    assert s.sources["fakeapi"].new == 2 and s.sources["fakeapi"].filtered == 1
    assert "503" in s.sources["down"].note
    assert good.details == 1  # details only for the job that passed the filter
    statuses = sorted(j["status"] for j in ctx.repo.jobs_by_status("new", "filtered"))
    assert statuses == ["filtered", "new"]
    reason = ctx.conn.execute("SELECT note FROM status_events WHERE to_status='filtered'").fetchone()[0]
    assert reason == "excluded keyword: Werkstudent"


class FakeResolver:
    def __init__(self):
        self.n = 0

    def resolve(self, link):
        self.n += 1
        if self.n == 2:
            return ResolvedLink(None, None, "unexpected response 404")
        jid = str(13850000 + self.n)
        return ResolvedLink(jid, f"https://www.stepstone.de/stellenangebote--{jid}-inline.html")

    def close(self):
        pass


def _eml(name):
    return email.message_from_bytes((ALERTS / name).read_bytes(), policy=policy.default)


def test_alerts_are_ingested_once(ctx):
    mbox = FakeMailbox([("<l1>", _eml("linkedin_alert.eml")), ("<s1>", _eml("stepstone_alert.eml"))])
    s = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert s.sources["alerts"].found == 18  # 6 LinkedIn postings + 12 StepStone cards
    jobs = ctx.repo.jobs_by_status(*[st.value for st in Status])
    assert len(jobs) == 17  # 4flow merged
    unavailable = ctx.repo.jobs_by_status("description_unavailable")
    assert len(unavailable) == 1
    again = run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert again.sources["alerts"].found == 0


def test_dry_run_does_not_mark_emails_processed(ctx):
    ctx.dry_run = True
    mbox = FakeMailbox([("<l1>", _eml("linkedin_alert.eml"))])
    run_search(ctx, api_sources=[], mailbox=mbox, resolver=FakeResolver())
    assert ctx.conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == 0


# --- fetch ------------------------------------------------------------------------------

PAGE = """<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Backend Developer (m/w/d)", "description": "<p>Wir suchen Python-Entwickler.</p>",
 "hiringOrganization": {"name": "A GmbH"}}</script></head><body></body></html>"""


def test_fetch_details_uses_json_ld_and_marks_failures(ctx):
    ok = ctx.repo.insert_job(title="Backend Developer", company="A GmbH", job_url="https://jobs.a-gmbh.de/1")
    gone = ctx.repo.insert_job(title="X", company="B", job_url="https://jobs.b.de/404")
    nourl = ctx.repo.insert_job(title="Y", company="C")

    def handler(req):
        return httpx.Response(200, text=PAGE) if req.url.path == "/1" else httpx.Response(404)

    http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(handler)), 0, 0, sleep=lambda s: None)
    s = fetch_details(ctx, http=http)
    assert s.fetched == 1 and s.unavailable == 2
    assert "Python-Entwickler" in ctx.repo.get_job(ok)["description"]
    assert {ctx.repo.get_job(j)["status"] for j in (gone, nourl)} == {"description_unavailable"}


def test_detail_fetch_uses_and_closes_run_owned_transport(ctx):
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: seen.append(str(req.url)) or httpx.Response(200, text=PAGE)
    ))
    ctx._http = HttpFetcher(client, 0, 0)
    ctx.repo.insert_job(title="Backend Developer", company="A GmbH", job_url="https://jobs.a-gmbh.de/1")
    assert fetch_details(ctx).fetched == 1
    assert ctx.http.fetch_once("https://jobs.a-gmbh.de/1").ok
    assert seen == ["https://jobs.a-gmbh.de/1"]
    ctx.close()
    assert client.is_closed


# --- send & render ------------------------------------------------------------------------


def _prepared(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    return jid


def test_dry_run_reports_would_send_without_mailer(ctx):
    ctx.mode = "automatic"
    jid = _prepared(ctx)
    ctx.dry_run = True
    mailer = FakeMailer()
    s = send_applications(ctx, mailer=mailer)
    assert s.would_send == [jid] and mailer.sent == []
    assert ctx.repo.get_job(jid)["status"] == "prepared"


def test_send_step_sends_eligible_jobs(ctx):
    jid = _prepared(ctx)
    mailer = FakeMailer()
    s = send_applications(ctx, mailer=mailer)
    assert s.sent == [jid] and len(mailer.sent) == 1
    assert mailer.sent[0]["To"] == "jobs@muster-gmbh.de"
    assert ctx.repo.get_job(jid)["status"] == "sent"


def test_auth_failure_stops_sending(ctx):
    ctx.mode = "automatic"
    jid = _prepared(ctx)
    s = send_applications(ctx, mailer=FakeMailer(MailAuthError("bad")))
    assert s.auth_failed and ctx.repo.get_job(jid)["status"] == "prepared"


def test_manual_send_asks_for_confirmation(ctx):
    jid = _prepared(ctx)
    asked = []
    s = send_applications(ctx, [jid], manual=True, mailer=FakeMailer(),
                          confirm=lambda j, m: asked.append(m["Subject"]) or False)
    assert asked and s.not_sent[jid] == ["not confirmed"]


def test_render_uses_edited_letter_without_llm(ctx):
    jid = _prepared(ctx)
    folder = Path(ctx.repo.get_application(jid)["folder"])
    md = folder / "cover_letter.md"
    md.write_text(md.read_text().replace("Ich freue mich auf ein Gespräch.", "Ich freue mich sehr auf Ihre Antwort."))
    ctx.clients.clear()
    seen = []

    class Capture(FakeRenderer):
        def html_to_pdf(self, html, out):
            seen.append(html)
            return super().html_to_pdf(html, out)

    render_application(ctx, jid, renderer_factory=factory(Capture([1])))
    assert "Ich freue mich sehr auf Ihre Antwort." in seen[0]
    assert ctx.clients == {}  # no LLM client was even created


def test_parse_cover_letter_roundtrip():
    from autoapp.writing.schema import CoverLetter

    letter = CoverLetter(language="de", subject="Bewerbung als X", salutation="Sehr geehrte Damen und Herren,",
                         paragraphs=["Absatz eins.", "Absatz zwei."], closing="Mit freundlichen Grüßen",
                         signature="Max Mustermann", motivation_short="", email_body="")
    back = parse_cover_letter_md(letter.to_markdown(), "de")
    assert (back.subject, back.salutation, back.paragraphs, back.closing, back.signature) == (
        letter.subject, letter.salutation, letter.paragraphs, letter.closing, letter.signature)


def test_search_single_profile(ctx):
    from autoapp.config.models import SearchProfile

    ctx.config.search_profiles.append(SearchProfile(name="remote-de", keywords=["Data Engineer"]))
    seen = []

    class Recorder(FakeSource):
        def search(self, profile):
            seen.append(profile.name)
            return iter(())

    run_search(ctx, "remote-de", api_sources=[Recorder([])], mailbox=FakeMailbox([]))
    assert seen == ["remote-de"]
    assert run_search(ctx, "nope", api_sources=[], mailbox=FakeMailbox([])).warnings


def test_fetch_details_says_what_happens_to_every_page(ctx):
    from autoapp.events import RunStats, StatsKeeper
    from autoapp.fetch.http_fetcher import FetchResult

    events, stats = [], RunStats()
    ctx.events.subscribe(StatsKeeper(stats))
    ctx.events.subscribe(events.append)
    for title, url in (("Read", "https://jobs.a-gmbh.de/1"), ("Gone", "https://jobs.a-gmbh.de/404"),
                       ("Wall", "https://www.stepstone.de/a"), ("After", "https://www.stepstone.de/b"),
                       ("Nowhere", None)):
        ctx.repo.insert_job(title=title, company="A GmbH", job_url=url)

    def handler(req):
        return httpx.Response(200, text=PAGE) if req.url.path == "/1" else httpx.Response(404)

    http = HttpFetcher(httpx.Client(transport=httpx.MockTransport(handler)), 12, 12, sleep=lambda s: None)

    class Browser:
        on_wait, blocked_sites = None, {}

        def fetch(self, url):
            if url.endswith("/a"):
                self.blocked_sites = {"stepstone": "bot challenge"}
                return FetchResult(url=url, status=403, blocked=True, reason="bot challenge (HTTP 403)")
            return FetchResult(url=url, status=0, blocked=True, reason="stepstone disabled for this run: bot challenge")

    fetch_details(ctx, http=http, browser=Browser())
    lines = {e.title: (e.kind, e.detail) for e in events if e.kind.startswith("detail_") and e.kind != "detail_waiting"}
    import re

    progress = {}
    for title, (_, detail) in lines.items():
        i, left = re.match(r"\[(\d)/5\] .*, (\d|the last one)( left)?$", detail).group(1, 2)
        progress[title] = int(i)
        assert (left == "the last one") == (i == "5") and (left == "the last one" or int(left) == 5 - int(i))
    assert sorted(progress.values()) == [1, 2, 3, 4, 5]
    assert lines["Read"][0] == "detail_read" and "read the jobs.a-gmbh.de page" in lines["Read"][1]
    assert lines["Gone"][0] == "detail_failed" and "could not read the jobs.a-gmbh.de page: HTTP 404" in lines["Gone"][1]
    assert lines["Wall"][0] == "detail_blocked" and "stepstone blocked the app" in lines["Wall"][1]
    assert lines["After"][0] == "detail_skipped" and "disabled for this run" in lines["After"][1]
    assert lines["Nowhere"][0] == "detail_skipped" and "the job has no address" in lines["Nowhere"][1]
    waits = [e for e in events if e.kind == "detail_waiting"]
    second = "Gone" if progress["Gone"] > progress["Read"] else "Read"  # the second page of the same host waits
    assert len(waits) == 1 and waits[0].title == second and "waiting 12 s" in waits[0].detail
    started = next(e for e in events if e.kind == "details_started")
    assert started.detail == "5 job pages to read (other sites 2, stepstone 2)"
    assert (stats.details_total, stats.details_processed, stats.details_fetched, stats.details_unavailable) == (5, 5, 1, 4)
    assert stats.details_wait_until is None


def test_the_header_shows_the_page_and_the_countdown():
    from datetime import UTC, datetime, timedelta

    from autoapp.dashboard.view import render_run_top, to_text
    from autoapp.events import RunStats

    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    rs = RunStats(started_at=now.isoformat(), step="fetch", details_total=34, details_processed=3,
                  details_wait_until=(now + timedelta(seconds=25)).isoformat())
    assert "step: fetch (page 4 of 34, waiting 25 s)" in to_text(render_run_top(rs, 120, now=now), 120)


def test_an_ad_that_says_it_is_closed_is_marked_expired_and_not_evaluated(ctx):
    from autoapp.fetch.extract import closed_notice
    from autoapp.fetch.http_fetcher import FetchResult

    events = []
    ctx.events.subscribe(events.append)
    closed = ctx.repo.insert_job(title="Closed", company="A GmbH", job_url="https://www.linkedin.com/jobs/view/1/")
    open_ = ctx.repo.insert_job(title="Open", company="A GmbH", job_url="https://www.linkedin.com/jobs/view/2/")
    banner = ('<html><body><h1>Python Developer</h1><figure class="closed-job"><figcaption>'
              'Es werden keine Bewerbungen mehr angenommen</figcaption></figure>' + PAGE + '</body></html>')

    class Browser:
        on_wait, blocked_sites = None, {}

        def fetch(self, url):
            return FetchResult(url=url, status=200, html=banner if url.endswith("/1/") else PAGE)

    summary = fetch_details(ctx, browser=Browser())
    assert ctx.repo.get_job(closed)["status"] == "expired" and ctx.repo.get_job(open_)["status"] == "new"
    assert summary.closed == 1 and summary.fetched == 1
    line = next(e for e in events if e.kind == "detail_closed")
    assert line.title == "Closed" and "keine Bewerbungen mehr angenommen" in line.detail
    assert closed_notice("<p>No longer accepting applications</p>") == "No longer accepting applications"
    assert closed_notice("<p>Diese Stelle ist leider nicht mehr verfügbar.</p>")
    assert closed_notice(PAGE) is None  # a normal ad is not taken for a closed one
