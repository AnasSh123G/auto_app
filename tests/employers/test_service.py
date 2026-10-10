"""Employer discovery end to end through ``run_discovery`` (tasks 5.1, 5.2, 6.1 fixtures)."""

from __future__ import annotations

import pytest

from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.employers.service import run_discovery
from autoapp.sources.alerts.models import AlertCard
from autoapp.steps.context import RunContext
from tests.employers.identity_world import search_client
from tests.employers.webfake import FakeWeb, fixture, job_page
from tests.helpers import CONFIG, make_workspace

TENANT = "https://cbc-cologne.onapply.de/"
CBC = "CBC Cologne Business Center GmbH"
EMP_CONFIG = CONFIG.replace("keywords: [Backend Developer]", "keywords: [Junior Data Engineer, Data Scientist]") + """\
sources:
  employer_careers:
    enabled: true
"""


def cbc_pages() -> dict[str, object]:
    pages: dict[str, object] = {
        "https://www.cbc-cologne.de/": ("<html><body><h1>CBC</h1><a href='/karriere'>Karriere</a>"
                                       "<a href='/impressum'>Impressum</a></body></html>"),
        "https://www.cbc-cologne.de/impressum": f"<html><body><h1>Impressum</h1><p>{CBC}, Otto-Gerig-Str. 5, 50679 Köln</p></body></html>",
        "https://www.cbc-cologne.de/karriere": ("<html><body><h1>Karriere</h1><div class='onapply-career-page-container' "
                                               f"data-url='{TENANT}feed/render.html?format=json'></div></body></html>"),
        TENANT: fixture("onapply_listing.html"),
        TENANT + "details/346291.html": fixture("onapply_detail.html"),
        TENANT + "details/298422.html": job_page("(Junior) Data Scientist (m/w/d) in Vollzeit oder Teilzeit", ref="298422", org=CBC),
    }
    return pages


def add_alert(ctx, title="(Junior) Data Engineer / BI Developer (m/w/d) in Vollzeit oder Teilzeit", sid="4471",
              status="description_unavailable"):
    card = AlertCard(source="linkedin-alert", title=title, company=CBC, locations=["Köln"], source_job_id=sid,
                     url=f"https://www.linkedin.com/jobs/view/{sid}/")
    out = ingest(ctx.repo, card_to_raw(card))
    ctx.repo.update_job(out.job_id, application_url=card.url)
    if status != "new":
        ctx.repo.transition(out.job_id, status, note="fetch failed")
    return out.job_id


@pytest.fixture
def ws(tmp_path):
    return make_workspace(tmp_path, config=EMP_CONFIG)


def open_ctx(ws):
    return RunContext.open(ws)


def test_first_run_verifies_recovers_and_imports_then_a_later_run_makes_no_search(ws):
    ctx = open_ctx(ws)
    alert = add_alert(ctx)
    web = FakeWeb(cbc_pages())
    api, calls = search_client(["https://www.cbc-cologne.de/karriere"])
    s = run_discovery(ctx, fetch=web, api=api)
    assert s.errors == [] and len(calls) == 1 and s.search_requests == 1
    assert s.matched == 1 and s.recovered == 1 and len(s.related_created) == 1
    job = ctx.repo.get_job(alert)
    assert job["status"] == "new" and job["description"] and "Partnerschaftliches" in job["description"]
    assert job["job_url"] == "https://www.linkedin.com/jobs/view/4471/"
    assert job["application_url"] == TENANT + "details/346291.html"
    related = ctx.repo.get_job(s.related_created[0])
    assert related["title"].startswith("(Junior) Data Scientist") and related["status"] == "new"
    assert ctx.repo.sources(related["id"])[0]["source"] == "employer-careers"
    assert any("1 matched" in line or "matched at the employer" in line for line in s.lines())
    assert ctx.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2  # the alert and one new employer job
    ctx.close()

    # A later run in a reopened workspace: another alert from the same employer.
    ctx2 = open_ctx(ws)
    other = add_alert(ctx2, title="Cloud Platform Engineer (m/w/d)", sid="9000")
    web2 = FakeWeb(cbc_pages())
    api2, calls2 = search_client(["https://www.cbc-cologne.de/karriere"])
    s2 = run_discovery(ctx2, fetch=web2, api=api2)
    assert calls2 == [] and s2.search_requests == 0 and s2.errors == []
    assert web2.calls == []  # the complete snapshot is fresh: not even a page request
    assert s2.employers[0].identity == "verified" and s2.employers[0].source == "cache"
    assert ctx2.repo.get_job(other)["status"] == "description_unavailable"  # not in the listing: untouched
    assert s2.employers[0].scan.reused
    ctx2.close()


def test_disabled_source_changes_nothing(tmp_path):
    ws = make_workspace(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: false"))
    ctx = open_ctx(ws)
    add_alert(ctx)
    web, (api, calls) = FakeWeb(cbc_pages()), search_client([])
    s = run_discovery(ctx, fetch=web, api=api)
    assert not s.enabled and s.lines() == [] and web.calls == [] and calls == []
    assert ctx.conn.execute("SELECT COUNT(*) FROM employers").fetchone()[0] == 0
    forced = run_discovery(ctx, fetch=web, api=api, force=True)
    assert forced.enabled and 1 <= len(calls) <= 2


def test_filtered_submitted_and_closed_alerts_are_not_eligible(ws):
    ctx = open_ctx(ws)
    for n, status in enumerate(("filtered", "submitted", "expired", "sent", "skipped")):
        add_alert(ctx, title=f"Role {n}", sid=f"s{n}", status="new")
        job_id = ctx.conn.execute("SELECT id FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        ctx.repo.transition(job_id, status)
    web, (api, calls) = FakeWeb(cbc_pages()), search_client(["https://www.cbc-cologne.de/"])
    s = run_discovery(ctx, fetch=web, api=api)
    assert s.employers == [] and calls == [] and web.calls == []


def test_search_unconfigured_keeps_other_work_and_reports_why(ws):
    ctx = open_ctx(ws)
    add_alert(ctx)
    web = FakeWeb(cbc_pages())
    api, calls = search_client([], key="")
    s = run_discovery(ctx, fetch=web, api=api)
    assert s.errors == [] and calls == [] and web.calls == []
    assert s.employers[0].identity == "deferred" and "PARALLEL_API_KEY" in (s.search_stop or "")
    assert len(s.deferred_jobs) == 1 and ctx.conn.execute("SELECT COUNT(*) FROM employers").fetchone()[0] == 0


def test_expected_failures_are_isolated_per_employer(ws):
    ctx = open_ctx(ws)
    add_alert(ctx)
    other = AlertCard(source="linkedin-alert", title="Data Scientist", company="Other Corp GmbH", locations=["Berlin"],
                      source_job_id="777", url="https://www.linkedin.com/jobs/view/777/")
    out = ingest(ctx.repo, card_to_raw(other))
    ctx.repo.transition(out.job_id, "description_unavailable")
    web = FakeWeb(cbc_pages())
    api, calls = search_client({"CBC": ["https://www.cbc-cologne.de/karriere"], "Other Corp": ["https://www.other.example/"]})
    s = run_discovery(ctx, fetch=web, api=api)
    by = {e.name: e for e in s.employers}
    assert by[CBC].identity == "verified" and by["Other Corp GmbH"].identity == "unresolved"
    assert s.errors == [] and by[CBC].matched


def test_unexpected_error_is_reported_not_swallowed(ws, monkeypatch):
    ctx = open_ctx(ws)
    add_alert(ctx)
    from autoapp.employers import service

    def boom(*a, **k):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(service.EmployerScanner, "scan", boom)
    api, _ = search_client(["https://www.cbc-cologne.de/karriere"])
    s = run_discovery(ctx, fetch=FakeWeb(cbc_pages()), api=api)
    assert s.errors and "parser exploded" in s.errors[0]


def test_employer_limit_defers_remaining_alerts_without_discarding(tmp_path):
    ws = make_workspace(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: true\n    max_employers_per_run: 1"))
    ctx = open_ctx(ws)
    a = add_alert(ctx)
    card = AlertCard(source="linkedin-alert", title="Data Scientist", company="Zweite GmbH", locations=["Berlin"],
                     source_job_id="778", url="https://www.linkedin.com/jobs/view/778/")
    b = ingest(ctx.repo, card_to_raw(card)).job_id
    ctx.repo.transition(b, "description_unavailable")
    ctx.conn.execute("UPDATE jobs SET discovered_at = '2026-10-01T00:00:00+00:00' WHERE id = ?", (a,))
    ctx.conn.execute("UPDATE jobs SET discovered_at = '2026-10-01T00:00:05+00:00' WHERE id = ?", (b,))
    ctx.conn.commit()
    api, calls = search_client(["https://www.cbc-cologne.de/karriere"])
    s = run_discovery(ctx, fetch=FakeWeb(cbc_pages()), api=api)
    assert [e.name for e in s.employers] == [CBC] and s.deferred_jobs == {b: "employer limit reached for this run"}
    assert ctx.repo.get_job(b)["status"] == "description_unavailable"
    assert a in s.employers[0].matched


def test_recovered_job_is_refiltered_with_the_employer_facts(tmp_path):
    cfg = EMP_CONFIG.replace("keywords: [Junior Data Engineer, Data Scientist]",
                             "keywords: [Junior Data Engineer, Data Scientist]\n    exclude_keywords: [Partnerschaftliches]")
    ws = make_workspace(tmp_path, config=cfg)
    ctx = open_ctx(ws)
    add_alert(ctx)
    api, _ = search_client(["https://www.cbc-cologne.de/karriere"])
    run_discovery(ctx, fetch=FakeWeb(cbc_pages()), api=api)
    # Exclusion keywords apply to titles; this posting's title is fine, so it must be back to new.
    assert ctx.conn.execute("SELECT status FROM jobs WHERE company = ? AND title LIKE '(Junior) Data Engineer%'",
                            (CBC,)).fetchone()[0] == "new"


def test_later_alert_for_a_listed_vacancy_reads_only_its_page_and_never_searches(ws):
    ctx = open_ctx(ws)
    add_alert(ctx)
    api, calls = search_client(["https://www.cbc-cologne.de/karriere"])
    run_discovery(ctx, fetch=FakeWeb(cbc_pages()), api=api)
    ctx.close()
    ctx2 = open_ctx(ws)
    other = add_alert(ctx2, title="Werkstudent (m/w/d) Data Analytics & BI", sid="9001")
    pages = cbc_pages()
    pages[TENANT + "details/346298.html"] = job_page("Werkstudent (m/w/d) Data Analytics & BI", ref="346298", org=CBC)
    web = FakeWeb(pages)
    api2, calls2 = search_client([])
    s = run_discovery(ctx2, fetch=web, api=api2)
    assert calls2 == [] and web.calls == [TENANT + "details/346298.html"] and s.matched == 1
    assert ctx2.repo.get_job(other)["status"] == "new" and ctx2.repo.get_job(other)["description"]
