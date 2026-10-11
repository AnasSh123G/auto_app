"""The quick test checklist (run-modes spec): OK / FAILED / SKIPPED with reasons, built from a run summary."""

from __future__ import annotations

from autoapp.config.models import load_config
from autoapp.employers.service import AlertResolutionSummary, DiscoverySummary, EmployerReport
from autoapp.steps import send as send_step
from autoapp.steps.evaluate import EvaluateSummary
from autoapp.steps.fetch import FetchSummary
from autoapp.steps.pipeline import RunSummary
from autoapp.steps.prepare import PrepareSummary
from autoapp.steps.quicktest import FAILED, OK, SKIPPED, WARN, FormLink, build_report
from autoapp.steps.search import SearchSummary, SourceReport
from tests.helpers import make_workspace

ENV = {"PARALLEL_API_KEY": "pk"}


def config(tmp_path, extra="", mode="search_first"):
    from tests.helpers import CONFIG

    return load_config(make_workspace(tmp_path, config=CONFIG + extra, alert_mode=mode))


def healthy() -> RunSummary:
    search = SearchSummary(alert_emails=2, alert_parsers={"linkedin": 6, "stepstone": 12})
    search.sources = {
        "alerts": SourceReport(found=18, new=15), "arbeitsagentur": SourceReport(found=2, new=2),
        "adzuna": SourceReport(found=2, new=2), "workwise": SourceReport(found=2, new=1),
    }
    alerts = AlertResolutionSummary(mode="search_first", searched=2, direct=["a"], via_careers=["b"], search_requests=3)
    emp = DiscoverySummary(enabled=True, employers=[EmployerReport("X", "e1", "verified", source="cache")], pages=4)
    return RunSummary(
        search=search, alerts=alerts, employers=emp, fetch=FetchSummary(fetched=1),
        evaluate=EvaluateSummary(evaluated=4), prepare=PrepareSummary(prepared=2, form_outcomes={"found": 1}),
        test=send_step.TestRunSummary(sent={"j": "jobs@firma.de"}, own_address="me@example.com"),
    )


def by_name(report):
    return {c.name: c for c in report.checks}


def test_a_healthy_run_is_all_ok_except_the_disabled_source(tmp_path):
    report = build_report(config(tmp_path), healthy(), ENV)
    checks = by_name(report)
    assert not report.failed
    assert checks["source arbeitnow"].status == SKIPPED and checks["source arbeitnow"].reason == "disabled"
    for name in ("alert mailbox", "alert parser linkedin", "alert parser stepstone", "source adzuna", "search key",
                 "employer lookup", "reading pages", "AI model (evaluation)", "letter writing", "form finding", "test email"):
        assert checks[name].status == OK, (name, checks[name])
    text = "\n".join(report.lines())
    assert "OK       alert mailbox" in text and "SKIPPED  source arbeitnow: disabled" in text and "0 FAILED" in text


def test_a_rejected_adzuna_key_fails_that_line_only(tmp_path):
    summary = healthy()
    summary.search.sources["adzuna"] = SourceReport(note="error: Adzuna rejected the app ID or key (HTTP 401)")
    report = build_report(config(tmp_path), summary, ENV)
    checks = by_name(report)
    assert report.failed and checks["source adzuna"].status == FAILED and "401" in checks["source adzuna"].reason
    assert checks["source workwise"].status == OK and checks["letter writing"].status == OK


def test_no_alert_email_skips_the_alert_lines_and_the_rest_still_reports(tmp_path):
    summary = healthy()
    summary.search.alert_emails, summary.search.alert_parsers = 0, {}
    report = build_report(config(tmp_path), summary, ENV)
    checks = by_name(report)
    assert checks["alert mailbox"].status == SKIPPED and checks["alert mailbox"].reason == "no alert email in the folder"
    assert not report.failed and checks["source adzuna"].status == OK


def test_parts_the_run_never_reached_are_skipped_not_ok(tmp_path):
    report = build_report(config(tmp_path), RunSummary(), ENV)
    checks = by_name(report)
    assert checks["AI model (evaluation)"].reason == "not reached" and checks["test email"].status == SKIPPED
    assert all(c.status != OK for n, c in checks.items() if n in ("letter writing", "form finding", "test email"))


def test_a_missing_search_key_fails_in_search_first_but_not_in_platform_mode(tmp_path):
    assert by_name(build_report(config(tmp_path / "a"), healthy(), {}))["search key"].status == FAILED
    platform = build_report(config(tmp_path / "b", mode="platform"), healthy(), {})
    assert by_name(platform)["search key"].status == SKIPPED


def test_failed_steps_and_failed_parts_set_the_failure_flag(tmp_path):
    summary = healthy()
    summary.prepare = PrepareSummary(failed=["j: the model refused"])
    summary.test = send_step.TestRunSummary(errors=["j: SMTP login failed"])
    summary.errors = ["send: boom"]
    report = build_report(config(tmp_path), summary, ENV)
    checks = by_name(report)
    assert report.failed and checks["letter writing"].status == FAILED and checks["test email"].status == FAILED
    assert checks["run steps"].status == FAILED and "boom" in checks["run steps"].reason


def test_a_held_test_email_is_skipped_with_its_reason(tmp_path):
    summary = healthy()
    summary.test = send_step.TestRunSummary(not_sent={"j": ["flagged needs_review: claim not verified"]})
    check = by_name(build_report(config(tmp_path), summary, ENV))["test email"]
    assert check.status == SKIPPED and "needs_review" in check.reason


def test_a_partial_source_that_found_jobs_is_ok_and_one_that_found_none_at_its_limit_is_skipped(tmp_path):
    summary = healthy()
    summary.search.sources["workwise"] = SourceReport(found=4, new=4, note="workwise: partial: page limit reached (40)")
    summary.search.sources["adzuna"] = SourceReport(note="adzuna: daily request limit reached")
    checks = by_name(build_report(config(tmp_path), summary, ENV))
    assert checks["source workwise"].status == OK and "partial" in checks["source workwise"].reason
    assert checks["source adzuna"].status == SKIPPED


def _links(*specs):
    return [FormLink(f"job{i}abc", source, url, status, note, path) for i, (source, url, status, note, path)
            in enumerate(specs)]


def test_each_prepared_form_job_gets_a_line_and_only_job_boards_fail(tmp_path):
    links = _links(
        ("arbeitsagentur", "https://careers.ejot.com/job/123#apply", "found", "employer page", "careers page"),
        ("linkedin-alert", "https://www.linkedin.com/jobs/view/1", "found", "", "job page"),
        ("arbeitsagentur", "https://jobs.partner-example.de/job/9", "partial", "partner board", "externeURL"),
        ("arbeitsagentur", None, "no_employer_link", "employer ad not found yet", "job page"),
    )
    report = build_report(config(tmp_path), healthy(), ENV, links)
    statuses = [c.status for c in report.form_links]
    assert statuses == [OK, FAILED, WARN, WARN] and report.failed
    text = "\n".join(report.lines())
    assert "form links:" in text and "WARN     job2ab  arbeitsagentur" in text and "[careers page]" in text
    assert "no link  no_employer_link" in text
    assert "2 WARN, 2 FAILED" not in text and "2 WARN, 1 FAILED" in text


def test_warn_links_do_not_set_the_failure_flag(tmp_path):
    links = _links(("arbeitsagentur", None, "no_employer_link", "not found", "job page"))
    report = build_report(config(tmp_path), healthy(), ENV, links)
    assert not report.failed and [c.status for c in report.form_links] == [WARN]


def test_no_form_jobs_means_no_form_links_section(tmp_path):
    assert "form links:" not in "\n".join(build_report(config(tmp_path), healthy(), ENV).lines())
