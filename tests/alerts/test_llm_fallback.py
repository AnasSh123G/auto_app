"""LLM fallback for alert emails and cards (employer-first spec 1b and 3), with a fake LLM and fake search."""

from __future__ import annotations

import json
from email.message import EmailMessage

from autoapp.config.models import Config
from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.employers.posting import (
    EMAIL_TEXT_REVIEW,
    OBJECTIVE_MAX_CHARS,
    build_excerpt_search,
    email_text_reasons,
    judge,
)
from autoapp.employers.service import AlertResolutionSummary, find_alert_ads
from autoapp.llm.fake import FakeLLM
from autoapp.mail.send import check_eligibility
from autoapp.sources.alerts import AlertCard, FakeMailbox, UnparsedEmail, ingest_alerts
from autoapp.sources.alerts.llm_extract import (
    AlertExtractor,
    ExtractedJob,
    card_needs_fallback,
    validate,
)
from autoapp.steps.context import RunContext
from autoapp.steps.search import run_search
from tests.employers.identity_world import search_client
from tests.employers.test_service import EMP_CONFIG
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace

EMAIL = "max.mustermann@example.com"
LINKEDIN_TEXT = (
    "Your job alert for Machine Learning Engineer\n"
    "Machine Learning Engineer\nAcme Robotics GmbH\nBerlin, Germany\n"
    "Apply now: https://www.linkedin.com/comm/jobs/view/991001/\n"
    "Data Scientist\nBeta Analytics AG\nHamburg\n"
    "Apply now: https://www.linkedin.com/comm/jobs/view/991002/\n"
    f"This email was intended for Max Mustermann. Contact {EMAIL}\n")


def broken_linkedin(mid: str = "<broken@x>") -> tuple[str, EmailMessage]:
    """A LinkedIn alert in a layout the parser does not know: it finds no card in it."""
    msg = EmailMessage()
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["Subject"] = "Your job alert for Machine Learning Engineer"
    msg["Message-ID"] = mid
    msg.set_content(LINKEDIN_TEXT)
    return mid, msg


def workwise_mail(mid: str = "<ww@x>") -> tuple[str, EmailMessage]:
    msg = EmailMessage()
    msg["From"] = "Workwise <jobs@workwise.io>"
    msg["Subject"] = "Neue Jobs fuer dich"
    msg["Message-ID"] = mid
    msg.set_content(
        "<html><body><table><tr><td><a href='https://www.workwise.io/job/1002-backend-developer'>"
        "Backend Developer (m/w/d)</a><div>Neu bei Muster Software: Verstaerkung fuer das Team in Berlin</div>"
        "</td></tr></table></body></html>", subtype="html")  # html only: the excerpt is read from it
    return mid, msg


def ctx_with(tmp_path, llm: FakeLLM | None, extra: str = "", config: str = EMP_CONFIG) -> RunContext:
    ctx = RunContext.open(make_workspace(tmp_path, config=config + extra, alert_mode="search_first"))
    if llm is not None:
        ctx.clients["alert_extract"] = llm
    return ctx


def processed(ctx: RunContext) -> list[str]:
    return [r["message_id"] for r in ctx.conn.execute("SELECT message_id FROM processed_emails")]


GOOD = {"jobs": [
    {"title": "Machine Learning Engineer", "company": "Acme Robotics GmbH", "city": "Berlin",
     "job_link": "https://www.linkedin.com/comm/jobs/view/991001/"},
    {"title": "Data Scientist", "company": "Beta Analytics AG", "city": "Hamburg", "job_link": ""}]}


def test_a_zero_card_linkedin_email_still_yields_its_jobs_and_is_processed_once(tmp_path) -> None:
    ctx = ctx_with(tmp_path, FakeLLM({"alert_extract": [GOOD]}))
    mailbox = FakeMailbox([broken_linkedin()])
    summary = run_search(ctx, api_sources=[], mailbox=mailbox)
    assert summary.alert_cards == 2 and summary.alert_llm_extracted == 2 and summary.alert_unprocessed == 0
    assert processed(ctx) == ["<broken@x>"]
    jobs = {r["title"]: r for r in ctx.conn.execute("SELECT * FROM jobs")}
    assert jobs["Machine Learning Engineer"]["company"] == "Acme Robotics GmbH"
    raw = json.loads(ctx.conn.execute("SELECT raw FROM job_sources WHERE source_job_id = '991001'").fetchone()["raw"])
    assert raw["llm_extracted"] and "Acme Robotics" in raw["email_excerpt"] and EMAIL not in raw["email_excerpt"]
    again = run_search(ctx, api_sources=[], mailbox=mailbox)  # processed: the model is not asked a second time
    assert again.alert_cards == 0 and len(ctx.clients["alert_extract"].calls) == 1
    ctx.close()


def test_the_model_sees_only_the_redacted_text(tmp_path) -> None:
    llm = FakeLLM({"alert_extract": [GOOD]})
    ctx = ctx_with(tmp_path, llm)
    run_search(ctx, api_sources=[], mailbox=FakeMailbox([broken_linkedin()]))
    [(_, _, user)] = llm.calls_for("alert_extract")
    assert EMAIL not in user and "Mustermann" not in user and "Acme Robotics" in user
    ctx.close()


def test_extraction_output_that_is_not_in_the_text_is_rejected() -> None:
    jobs = [ExtractedJob(title="Machine Learning Engineer", company="Invented Corp", city="Berlin",
                         job_link="https://www.linkedin.com/comm/jobs/view/12345/"),
            ExtractedJob(title="Quantum Wizard", company="Acme Robotics GmbH")]
    kept = validate(jobs, LINKEDIN_TEXT)
    assert len(kept) == 1  # the invented title is dropped
    assert kept[0].company == "" and kept[0].city == "Berlin" and kept[0].job_link == ""  # invented values blanked


def test_with_llm_fallback_off_the_zero_card_email_behaves_as_before(tmp_path) -> None:
    llm = FakeLLM({"alert_extract": [GOOD]})
    ctx = ctx_with(tmp_path, llm, "  alerts:\n    llm_fallback: false\n")
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([broken_linkedin()]))
    assert llm.calls == [] and summary.alert_cards == 0 and summary.alert_unprocessed == 1
    assert processed(ctx) == [] and any("stays unprocessed" in w for w in summary.warnings)
    ctx.close()


def test_a_cap_leaves_the_remaining_emails_unprocessed_with_a_warning(tmp_path) -> None:
    llm = FakeLLM({"alert_extract": [GOOD]})
    ctx = ctx_with(tmp_path, llm, "  alerts:\n    max_llm_extractions_per_run: 1\n")
    mailbox = FakeMailbox([broken_linkedin("<a@x>"), broken_linkedin("<b@x>")])
    summary = run_search(ctx, api_sources=[], mailbox=mailbox)
    assert len(llm.calls) == 1 and processed(ctx) == ["<a@x>"] and summary.alert_unprocessed == 1
    assert any("max_llm_extractions_per_run" in w for w in summary.warnings)
    ctx.close()


def test_a_model_that_finds_nothing_marks_the_email_processed_and_warns(tmp_path) -> None:
    ctx = ctx_with(tmp_path, FakeLLM({"alert_extract": [{"jobs": []}]}))
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([broken_linkedin()]))
    assert processed(ctx) == ["<broken@x>"] and summary.alert_cards == 0
    assert any("found no job either" in w and "linkedin.com" in w for w in summary.warnings)
    ctx.close()


def test_a_failing_model_leaves_the_email_unprocessed(tmp_path) -> None:
    ctx = ctx_with(tmp_path, FakeLLM({"alert_extract": [RuntimeError("boom")]}))
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([broken_linkedin()]))
    assert processed(ctx) == [] and summary.alert_unprocessed == 1
    ctx.close()


def test_the_model_calls_are_counted_on_the_shared_meter(tmp_path) -> None:
    ctx = ctx_with(tmp_path, FakeLLM({"alert_extract": [GOOD]}))
    run_search(ctx, api_sources=[], mailbox=FakeMailbox([broken_linkedin()]))
    assert ctx.meter.calls == 1 and ctx.meter.usages[0].task == "alert_extract"
    ctx.close()


def test_a_workwise_card_without_a_company_is_completed_from_its_excerpt(tmp_path) -> None:
    llm = FakeLLM({"alert_extract": [{"jobs": [
        {"title": "Backend Developer (m/w/d)", "company": "Muster Software", "city": "Berlin", "job_link": ""}]}]})
    ctx = ctx_with(tmp_path, llm)
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([workwise_mail()]))
    assert summary.alert_llm_extracted == 1
    job = ctx.conn.execute("SELECT * FROM jobs").fetchone()
    assert job["company"] == "Muster Software"
    ctx.close()


def test_an_ungrounded_company_leaves_the_card_unnamed(tmp_path) -> None:
    llm = FakeLLM({"alert_extract": [{"jobs": [
        {"title": "Backend Developer (m/w/d)", "company": "Made Up GmbH", "city": "", "job_link": ""}]}]})
    ctx = ctx_with(tmp_path, llm)
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([workwise_mail()]))
    assert summary.alert_llm_extracted == 0
    assert ctx.conn.execute("SELECT company FROM jobs").fetchone()["company"].startswith("(company")
    ctx.close()


def test_which_cards_need_the_fallback() -> None:
    base = dict(source="linkedin-alert", title="Backend Developer", company="Acme")
    assert not card_needs_fallback(AlertCard(**base))
    assert card_needs_fallback(AlertCard(**{**base, "company": "", "company_unknown": True}))
    assert card_needs_fallback(AlertCard(**{**base, "company": "(company not in the alert email)"}))
    assert card_needs_fallback(AlertCard(**{**base, "title": ""}))
    assert card_needs_fallback(AlertCard(**{**base, "title": "Backend", "title_truncated": True}))
    assert not card_needs_fallback(AlertCard(**{**base, "title": "Backend Developer Java", "title_truncated": True}))


def test_unknown_senders_are_read_only_when_opted_in(tmp_path) -> None:
    msg = EmailMessage()
    msg["From"] = "Jobs <noreply@newjobboard.example>"
    msg["Subject"] = "Neue Jobs fuer dich"
    msg["Message-ID"] = "<u@x>"
    msg.set_content(LINKEDIN_TEXT)
    off = ingest_alerts(FakeMailbox([("<u@x>", msg)]), "f", lambda m: False, lambda *a: None,
                        fallback=lambda e: [])
    assert off.unrecognised == 1
    seen: list[UnparsedEmail] = []

    def fallback(email: UnparsedEmail) -> list[AlertCard]:
        seen.append(email)
        return [AlertCard(source="llm-alert", title="Data Scientist", company="Beta Analytics AG")]

    marked: list[tuple] = []
    on = ingest_alerts(FakeMailbox([("<u@x>", msg)]), "f", lambda m: False, lambda *a: marked.append(a),
                       fallback=fallback, fallback_unknown_senders=True)
    assert on.unrecognised == 0 and len(on.cards) == 1 and marked and seen[0].parser is None


# --- 3b: the excerpt goes to the search ----------------------------------------------------------------------------

EXCERPT = "Backend Developer (m/w/d)\nNeu bei Muster Software: Verstaerkung fuer das Team in Berlin"
OWN = "https://karriere.muster-software.de/jobs/backend-developer-m-w-d"


def ad(heading: str = "Backend Developer (m/w/d)", org: str = "Muster Software GmbH") -> str:
    ld = json.dumps({"@type": "JobPosting", "title": heading, "hiringOrganization": {"name": org},
                     "description": "Du entwickelst Dienste in Python."})
    return (f"<html><head><title>{heading}</title><script type='application/ld+json'>{ld}</script></head>"
            f"<body><h1>{heading}</h1><p>{org}, Berlin. Du entwickelst Dienste in Python.</p></body></html>")


def nameless_job(ctx: RunContext, excerpt: str | None = EXCERPT) -> str:
    card = AlertCard(source="workwise-alert", title="Backend Developer (m/w/d)", company="", company_unknown=True,
                     locations=["Berlin"], source_job_id="1002", email_excerpt=excerpt, message_id="<ww@x>")
    return ingest(ctx.repo, card_to_raw(card)).job_id


def test_a_workwise_card_without_a_company_resolves_through_the_excerpt_search_and_needs_review(tmp_path) -> None:
    ctx = ctx_with(tmp_path, None)
    job_id = nameless_job(ctx)
    api, calls = search_client({"employer's own job ad": [OWN]})
    summary = find_alert_ads(ctx, fetch=FakeWeb({OWN: ad()}), api=api)
    assert summary.direct == [job_id] and summary.no_company == [] and summary.searched == 1
    [call] = calls
    assert EXCERPT in call["objective"] and len(call["objective"]) <= OBJECTIVE_MAX_CHARS
    assert EMAIL not in json.dumps(call)
    job = ctx.repo.get_job(job_id)
    assert job["application_url"] == OWN and job["company"] == "Muster Software GmbH"
    assert job["needs_review"] == 1 and EMAIL_TEXT_REVIEW in json.loads(job["needs_review_reasons"])
    raw = json.loads(ctx.repo.sources(job_id)[0]["raw"])
    assert raw["confidence"] == "from_email_text"
    ctx.close()


def test_a_from_email_text_job_is_not_sent_automatically_and_keeps_its_flag(tmp_path) -> None:
    ctx = ctx_with(tmp_path, None)
    job_id = nameless_job(ctx)
    api, _ = search_client({"employer's own job ad": [OWN]})
    find_alert_ads(ctx, fetch=FakeWeb({OWN: ad()}), api=api)
    elig = check_eligibility(ctx.repo, job_id, ctx.config.email, bundle=None)
    assert not elig.ok and any("flagged needs_review" in r for r in elig.reasons)
    ctx.repo.set_needs_review(job_id, [])  # what prepare does when it rewrites the flag...
    assert email_text_reasons(ctx.repo, job_id) == [EMAIL_TEXT_REVIEW]  # ...and puts this reason back
    ctx.close()


def test_the_excerpt_search_rejects_a_page_whose_employer_the_email_does_not_name(tmp_path) -> None:
    ctx = ctx_with(tmp_path, None)
    job_id = nameless_job(ctx)
    api, _ = search_client({"employer's own job ad": [OWN]})
    summary = find_alert_ads(ctx, fetch=FakeWeb({OWN: ad(org="Other Corp GmbH")}), api=api)
    assert job_id in summary.not_found and summary.direct == []
    assert ctx.repo.get_job(job_id)["needs_review"] == 0 and not ctx.repo.get_job(job_id)["application_url"]
    ctx.close()


def test_a_nameless_card_without_an_excerpt_or_with_the_fallback_off_is_not_searched(tmp_path) -> None:
    for extra, excerpt in (("", None), ("  alerts:\n    llm_fallback: false\n", EXCERPT)):
        ctx = ctx_with(tmp_path / ("a" if not extra else "b"), None, extra)
        job_id = nameless_job(ctx, excerpt)
        api, calls = search_client({"employer's own job ad": [OWN]})
        summary = find_alert_ads(ctx, fetch=FakeWeb({OWN: ad()}), api=api)
        assert calls == [] and summary.no_company == [job_id]
        ctx.close()


def test_judge_is_relaxed_only_on_the_company_check() -> None:
    html = ad()
    ok = judge(html, OWN, company="", title="Backend Developer (m/w/d)", excerpt=EXCERPT)
    assert ok.ok and ok.company == "Muster Software GmbH"
    assert not judge(html, OWN, company="", title="Backend Developer (m/w/d)").ok  # no excerpt: no relaxation
    assert not judge(html, OWN, company="", title="Frontend Developer", excerpt=EXCERPT).ok  # the title still counts
    closed = html.replace("<body>", "<body><p>Diese Stelle ist leider nicht mehr verfuegbar.</p>")
    assert not judge(closed, OWN, company="", title="Backend Developer (m/w/d)", excerpt=EXCERPT).ok
    assert not judge(ad(org="Other Corp"), OWN, company="", title="Backend Developer (m/w/d)", excerpt=EXCERPT).ok
    assert judge(html, OWN, company="Muster Software", title="Backend Developer (m/w/d)").ok  # named: unchanged


def test_the_objective_is_capped() -> None:
    objective, queries = build_excerpt_search("Backend Developer (m/w/d)", "Berlin", "x" * 10_000)
    assert len(objective) == OBJECTIVE_MAX_CHARS and "Backend Developer" in queries[0]


def test_the_coverage_line_ends_with_the_llm_count() -> None:
    s = AlertResolutionSummary(mode="search_first", cards=3, llm_extracted=2)
    assert s.coverage_line().endswith("2 needed LLM extraction")


def test_config_defaults_and_the_alert_extract_task_follows_evaluate(tmp_path) -> None:
    ctx = ctx_with(tmp_path, None)
    alerts = ctx.config.sources.alerts
    assert (alerts.llm_fallback, alerts.max_llm_extractions_per_run, alerts.llm_unknown_senders) == (True, 20, False)
    assert ctx.config.llm.resolve("alert_extract")[:2] == ctx.config.llm.resolve("evaluate")[:2]
    assert isinstance(ctx.config, Config) and AlertExtractor(ctx, max_calls=1).calls == 0
    ctx.close()
