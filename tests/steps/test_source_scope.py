"""Run source choices isolate discovery and saved job queues without changing config."""

from email.message import EmailMessage

import pytest

from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.steps import search
from autoapp.steps.context import RunContext
from tests.helpers import make_workspace
from tests.steps.test_other_steps import FakeSource


@pytest.fixture
def ctx(tmp_path):
    context = RunContext.open(make_workspace(tmp_path))
    context.config.sources.alerts.llm_fallback = False
    yield context
    context.close()


def alert_mailbox():
    msg = EmailMessage()
    msg["From"] = "jobs@mail.xing.com"
    msg["Subject"] = "Neue Jobs für dich"
    msg.set_content("----------------------------------------\nBackend Developer\n"
                    "=> https://www.xing.com/m/DUMMY_JOB\nEmail GmbH\nBerlin\nVollzeit\n")
    return FakeMailbox([("<sources@example.test>", msg)])


@pytest.mark.parametrize("source_mode,expected", [
    ("email_only", ["alerts"]), ("full", ["alerts", "fakeapi"]), ("web_only", ["fakeapi"]),
])
def test_selected_discovery_sources_only(ctx, source_mode, expected):
    ctx.source_mode = source_mode
    config_before = ctx.config.model_dump()
    source = FakeSource([RawJob(source="fakeapi", source_job_id="web-1", title="Data Engineer",
                                company="Web GmbH", location="Berlin")])
    summary = search.run_search(ctx, api_sources=[source], mailbox=alert_mailbox())
    assert list(summary.sources) == expected
    assert ctx.config.model_dump() == config_before
    assert ctx.conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == int(source_mode != "web_only")
    jobs = ctx.repo.jobs_by_status("new")
    assert {j["company"] for j in jobs} == {
        "Email GmbH" if name == "alerts" else "Web GmbH" for name in expected
    }


def test_email_only_never_constructs_default_web_sources(ctx, monkeypatch):
    ctx.source_mode = "email_only"
    monkeypatch.setattr(search, "_default_sources", lambda *a, **kw: pytest.fail("web sources constructed"))
    assert search.run_search(ctx, mailbox=alert_mailbox()).alert_cards == 1


def test_web_only_never_opens_alert_mailbox(ctx):
    class Mailbox:
        def list_messages(self, folder):
            pytest.fail("alert mailbox opened")

    ctx.source_mode = "web_only"
    assert search.run_search(ctx, api_sources=[], mailbox=Mailbox()).alert_emails == 0


def test_invalid_source_mode_fails_before_discovery(ctx, monkeypatch):
    ctx.source_mode = "typo"
    monkeypatch.setattr(search, "_search_alerts", lambda *a, **kw: pytest.fail("mail touched"))
    with pytest.raises(ValueError, match="source"):
        search.run_search(ctx, api_sources=[])


@pytest.mark.parametrize("source_mode,selected", [
    ("full", [0, 1, 2, 3, 4]), ("email_only", [1, 2, 3]), ("web_only", [0, 2, 4]),
])
def test_saved_jobs_selected_by_provenance_in_stable_order(ctx, source_mode, selected):
    ids = [ctx.repo.insert_job(title=f"Job {n}", company=f"Company {n}") for n in range(5)]
    ctx.repo.add_source(ids[1], "xing-alert", None)
    ctx.repo.add_source(ids[2], "linkedin-alert", "1")
    ctx.repo.add_source(ids[2], "adzuna", "2")
    ctx.repo.add_source(ids[3], "indeed-alert", "3")
    ctx.repo.add_source(ids[3], "employer-careers", "emp:3")
    ctx.repo.add_source(ids[4], "employer-careers", "emp:4")
    ctx.source_mode = source_mode
    jobs = [ctx.repo.get_job(jid) for jid in ids]
    assert [j["id"] for j in ctx.jobs_for_sources(jobs)] == [ids[n] for n in selected]
    assert [j["id"] for j in jobs] == ids


def test_context_defaults_to_full(ctx):
    assert ctx.source_mode == "full"


@pytest.mark.parametrize("source_mode,expected", [
    ("email_only", {"Email GmbH"}), ("full", {"Email GmbH", "Web GmbH"}), ("web_only", {"Web GmbH"}),
])
def test_pipeline_processes_only_saved_jobs_in_selected_sources(ctx, source_mode, expected):
    from autoapp.discovery.ingest import ingest
    from autoapp.llm.fake import FakeLLM
    from autoapp.steps.pipeline import run_pipeline
    from tests.steps.test_evaluate_step import verdict
    from tests.steps.test_prepare_step import AD

    ids = {}
    for source, company in [("xing-alert", "Email GmbH"), ("adzuna", "Web GmbH")]:
        ids[company] = ingest(ctx.repo, RawJob(source=source, source_job_id=company,
                                             title="Backend Developer", company=company,
                                             location="Berlin", description=AD)).job_id
    ctx.source_mode = source_mode
    ctx.mode = "review"
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(20)] * len(expected)})
    summary = run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([]))
    assert summary.ok, summary.errors
    assert summary.evaluate.evaluated == len(expected)
    for company, jid in ids.items():
        assert ctx.repo.get_job(jid)["status"] == ("skipped" if company in expected else "new")
    assert summary.source_mode == source_mode
    assert any("job sources:" in line for line in summary.lines())
