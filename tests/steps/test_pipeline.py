import csv

from autoapp.llm.fake import FakeLLM
from autoapp.llm.settings import Price
from autoapp.mail.connection import FakeMailer, MailAuthError
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.sources.models import RawJob
from autoapp.steps.context import RunContext
from autoapp.steps.pipeline import run_pipeline
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import verdict
from tests.steps.test_other_steps import FakeSource
from tests.steps.test_prepare_step import AD, LETTER, FakeRenderer, factory

EVAL = verdict(90, apply_email="jobs@muster-gmbh.de", requirements=[
    {"text": "Python", "proficiency": "intermediate", "core": True, "status": "matched", "evidence": "Python"},
    {"text": "Grundkenntnisse in Kubernetes", "proficiency": "basic", "core": False, "status": "bridgeable"},
])


def _ctx(tmp_path, auto_send=True, dry_run=False):
    ctx = RunContext.open(make_workspace(tmp_path), dry_run=dry_run)
    ctx.mode = "automatic" if auto_send else "review"
    ctx.prices.prices["fake-model"] = Price(input=1.0, output=1.0)  # priced, so no "unknown price" warning
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [EVAL]})
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    return ctx


def _source():
    job = RawJob(source="fakeapi", source_job_id="1", title="Backend Developer (m/w/d)", company="Muster GmbH",
                 location="Berlin", description=AD,
                 ad_salary_min=60000, ad_salary_max=70000)
    return FakeSource([job])


def test_end_to_end_sends_and_documents(tmp_path):
    ctx = _ctx(tmp_path)
    mailer = FakeMailer()
    s = run_pipeline(ctx, api_sources=[_source()], mailbox=FakeMailbox([]),
                     renderer_factory=factory(FakeRenderer([1])), mailer=mailer)
    assert s.ok, s.errors
    assert s.evaluate.approved == 1 and s.prepare.prepared == 1 and len(s.send.sent) == 1
    assert len(mailer.sent) == 1
    rows = list(csv.DictReader(ctx.ws.csv_path.open(encoding="utf-8-sig"), delimiter=";"))
    assert len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["company"] == "Muster GmbH"
    assert rows[0]["salary_expectation"] == "65000"
    text = "\n".join(s.lines())
    assert "1 sent" in text and "estimated LLM cost" in text
    ctx.close()


def test_bad_smtp_password_is_an_error_but_other_steps_complete(tmp_path):
    ctx = _ctx(tmp_path)
    s = run_pipeline(ctx, api_sources=[_source()], mailbox=FakeMailbox([]),
                     renderer_factory=factory(FakeRenderer([1])), mailer=FakeMailer(MailAuthError("bad password")))
    assert not s.ok and any("bad password" in e for e in s.errors)
    assert s.prepare.prepared == 1
    assert ctx.ws.csv_path.exists()
    ctx.close()


def test_dry_run_sends_nothing(tmp_path):
    ctx = _ctx(tmp_path, auto_send=True, dry_run=True)
    mailer = FakeMailer()
    s = run_pipeline(ctx, api_sources=[_source()], mailbox=FakeMailbox([]),
                     renderer_factory=factory(FakeRenderer([1])), mailer=mailer)
    assert s.ok and len(s.send.would_send) == 1 and mailer.sent == []
    assert "would send 1" in "\n".join(s.lines())
    ctx.close()


# --- alert modes ---------------------------------------------------------------------------


def _alert_ctx(tmp_path, mode):
    from autoapp.discovery.ingest import card_to_raw, ingest
    from autoapp.sources.alerts.models import AlertCard
    from tests.employers.test_service import EMP_CONFIG

    ctx = RunContext.open(make_workspace(tmp_path, config=EMP_CONFIG, alert_mode=mode))
    ctx.prices.prices["fake-model"] = Price(input=1.0, output=1.0)
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(20)] * 3})
    card = AlertCard(source="linkedin-alert", title="Software Engineer (m/w/d)", company="CHEFS CULINAR",
                     locations=["Weeze"], source_job_id="4477", url="https://www.linkedin.com/jobs/view/4477/")
    return ctx, ingest(ctx.repo, card_to_raw(card)).job_id


def test_search_first_run_reads_no_job_board_and_reports_the_mode(tmp_path):
    from tests.employers.test_alert_ads import OWN, PAGES, _search
    from tests.employers.webfake import FakeWeb
    from tests.steps.test_search_first_fetch import Browser, http_seen

    ctx, job_id = _alert_ctx(tmp_path, "search_first")
    browser, seen = Browser(), []
    api, calls = _search([OWN])
    s = run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([]), employer_fetch=FakeWeb(PAGES), employer_api=api,
                     http=http_seen(seen), browser=browser, renderer_factory=factory(FakeRenderer([1])))
    assert s.ok, s.errors
    assert browser.urls == [] and not any("linkedin" in u for u in seen)
    job = ctx.repo.get_job(job_id)
    assert job["application_url"] == OWN and job["description"]
    assert s.alerts is not None and s.alerts.direct == [job_id]
    text = "\n".join(s.lines())
    assert "alert mode: search first" in text and "1 found by searching company and title" in text
    ctx.close()


def test_platform_run_keeps_the_old_order_and_reads_the_job_board(tmp_path):
    from tests.employers.test_alert_ads import _search
    from tests.steps.test_search_first_fetch import Browser, http_seen

    ctx, job_id = _alert_ctx(tmp_path, "platform")
    ctx.config.sources.employer_careers.enabled = False
    browser = Browser()
    api, calls = _search([])
    s = run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([]), employer_api=api, http=http_seen([]), browser=browser,
                     renderer_factory=factory(FakeRenderer([1])))
    assert browser.urls == ["https://www.linkedin.com/jobs/view/4477/"] and calls == []
    assert s.alerts is None and s.employers is None
    assert "alert mode: platform" in "\n".join(s.lines())
    ctx.close()
