"""Email discovery precedes website queries and keeps cross-source enrichment."""

from email.message import EmailMessage

import pytest

from autoapp.db.status import Status
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.steps import search
from autoapp.steps.context import RunContext, SampleLimits
from tests.helpers import make_workspace
from tests.steps.test_other_steps import FakeSource


@pytest.fixture
def ctx(tmp_path):
    context = RunContext.open(make_workspace(tmp_path))
    context.config.sources.alerts.llm_fallback = False
    yield context
    context.close()


def mailbox():
    msg = EmailMessage()
    msg["From"] = "jobalerts-noreply@linkedin.com"
    msg["Subject"] = "Your job alert for Backend Developer"
    msg["Message-ID"] = "<email-first@example.com>"
    msg.set_content(
        "Backend Developer\nA GmbH\nBerlin\n"
        "View job: https://www.linkedin.com/jobs/view/123456/\n"
    )
    return FakeMailbox([("<email-first@example.com>", msg)])


def duplicate(**kwargs):
    return RawJob(source="fakeapi", source_job_id="api-1", title="Backend Developer",
                  company="A GmbH", location="Berlin", **kwargs)


@pytest.mark.parametrize("sample", [False, True])
def test_emails_are_stored_and_reported_before_api_search(ctx, sample):
    if sample:
        ctx.sample = SampleLimits()
    events = []
    ctx.events.subscribe(events.append)

    class Source(FakeSource):
        def search(self, profile):
            assert ctx.repo.find_by_source("linkedin-alert", "123456") is not None
            assert ctx.conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == 1
            yield from super().search(profile)

    summary = search.run_search(ctx, api_sources=[Source([duplicate()])], mailbox=mailbox())
    assert list(summary.sources) == ["alerts", "fakeapi"]
    assert summary.sources["alerts"].new == 1
    assert summary.sources["fakeapi"].new == 0
    assert summary.sources["fakeapi"].linked == 1
    assert [event.data["source"] for event in events if event.kind == "source_done"] == ["alerts", "fakeapi"]


def test_default_sources_are_created_after_email_ingestion(ctx, monkeypatch):
    def sources(context, **kwargs):
        assert context.repo.find_by_source("linkedin-alert", "123456") is not None
        return []

    monkeypatch.setattr(search, "_default_sources", sources)
    search.run_search(ctx, mailbox=mailbox())


def test_non_stepstone_mail_needs_no_link_resolver(ctx, monkeypatch):
    def unexpected_resolver():
        pytest.fail("a StepStone HTTP client is unnecessary for this mailbox")

    monkeypatch.setattr(search, "StepStoneLinkResolver", unexpected_resolver)
    search.run_search(ctx, api_sources=[], mailbox=mailbox())
    search.run_search(ctx, api_sources=[], mailbox=FakeMailbox([]))


@pytest.mark.parametrize("unavailable", ["failure", "disabled", "unconfigured"])
def test_websites_are_still_searched_without_a_working_mailbox(ctx, unavailable):
    class BrokenMailbox:
        def list_messages(self, folder):
            raise RuntimeError("mailbox unavailable")

    mb = BrokenMailbox()
    if unavailable == "disabled":
        ctx.config.sources.alerts.enabled = False
    elif unavailable == "unconfigured":
        ctx.config.mail.alerts = None
        mb = None
    summary = search.run_search(ctx, api_sources=[FakeSource([duplicate()])], mailbox=mb)
    assert summary.sources["fakeapi"].new == 1
    if unavailable == "failure":
        assert "mailbox unavailable" in summary.sources["alerts"].note
    elif unavailable == "unconfigured":
        assert "no alert mailbox configured" in summary.sources["alerts"].note
    else:
        assert "alerts" not in summary.sources


def test_api_detail_enriches_existing_email_job_once(ctx):
    source = FakeSource([duplicate(), duplicate()])
    summary = search.run_search(ctx, api_sources=[source], mailbox=mailbox())
    job = ctx.repo.find_by_source("linkedin-alert", "123456")
    assert job["description"] == "Volltext der Anzeige"
    assert job["job_url"] == "https://www.linkedin.com/jobs/view/123456/"
    assert source.details == 1
    assert summary.sources["fakeapi"].linked == 2
    assert len(ctx.repo.jobs_by_status(Status.NEW)) == 1
    assert {s["source"] for s in ctx.repo.sources(job["id"])} == {"linkedin-alert", "fakeapi"}


def test_filtered_email_duplicate_needs_no_detail_request(ctx):
    ctx.config.search_profiles[0].exclude_keywords = ["Backend"]
    source = FakeSource([duplicate()])
    summary = search.run_search(ctx, api_sources=[source], mailbox=mailbox())
    assert summary.sources["alerts"].filtered == 1
    assert summary.sources["fakeapi"].linked == 1
    assert source.details == 0
    assert len(ctx.repo.jobs_by_status(Status.FILTERED)) == 1


def test_duplicate_detail_failure_does_not_stop_other_jobs(ctx):
    class Source(FakeSource):
        def fetch_detail(self, raw):
            if raw.source_job_id == "api-1":
                raise RuntimeError("detail unavailable")
            return super().fetch_detail(raw)

    other = duplicate().model_copy(update={"source_job_id": "api-2", "title": "Python Developer"})
    summary = search.run_search(ctx, api_sources=[Source([duplicate(), other])], mailbox=mailbox())
    assert summary.sources["fakeapi"].linked == 1
    assert summary.sources["fakeapi"].new == 1
    assert "details failed" in summary.sources["fakeapi"].note
    assert ctx.repo.find_by_source("fakeapi", "api-2")["description"] == "Volltext der Anzeige"
