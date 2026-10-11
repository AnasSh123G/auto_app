"""Search-first alert handling: the direct own-ad pass before fetching and evaluating (alert-resolution spec)."""

from __future__ import annotations

from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.employers.service import AlertResolutionSummary, find_alert_ads
from autoapp.sources.alerts.models import AlertCard
from autoapp.steps.context import RunContext
from tests.employers.identity_world import search_client
from tests.employers.test_posting import ANGULAR, OWN, PAGES, TEXT, ad
from tests.employers.test_service import EMP_CONFIG
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace


def _ctx(tmp_path, config=EMP_CONFIG, **kw):
    return RunContext.open(make_workspace(tmp_path, config=config, alert_mode="search_first", **kw))


def _alert(ctx, source="linkedin-alert", company="CHEFS CULINAR", sid="4477", title="Software Engineer (m/w/d)",
           unknown=False):
    card = AlertCard(source=source, title=title, company="" if unknown else company, company_unknown=unknown,
                     locations=["Weeze"], source_job_id=sid, url=f"https://www.example-board.test/job/{sid}")
    return ingest(ctx.repo, card_to_raw(card)).job_id


def _search(results):
    return search_client({"official employer website": ["https://www.chefsculinar.se/"], "Find the job ad": results})


def test_linkedin_and_workwise_alert_jobs_get_the_employer_ad(tmp_path):
    ctx = _ctx(tmp_path)
    linkedin = _alert(ctx)
    workwise = _alert(ctx, source="workwise-alert", sid="9001", title="Senior Software Engineer (m/w/d)")  # searched, not found
    web = FakeWeb({**PAGES, OWN: ad(TEXT, "Software Engineer (m/w/d)")})
    api, calls = _search([OWN])
    summary = find_alert_ads(ctx, fetch=web, api=api)
    assert linkedin in summary.direct and summary.searched == 2 and workwise in summary.not_found
    for job_id in summary.direct:
        job = ctx.repo.get_job(job_id)
        assert job["application_url"] == OWN and "agilen Vorgehen" in job["description"] and job["status"] == "new"
    assert not any("example-board.test" in c for c in web.calls)
    assert all("Find the job ad" in c["objective"] for c in calls)
    ctx.close()


def test_a_job_with_a_description_or_no_company_is_not_searched(tmp_path):
    ctx = _ctx(tmp_path)
    has_text = _alert(ctx, sid="1")
    ctx.repo.update_job(has_text, description=TEXT)
    nameless = _alert(ctx, source="workwise-alert", sid="2", unknown=True)
    api, calls = _search([OWN])
    summary = find_alert_ads(ctx, fetch=FakeWeb(PAGES), api=api)
    assert calls == [] and summary.no_company == [nameless] and summary.searched == 0
    assert ctx.alert_notes[nameless] == "alert does not name the employer"
    ctx.close()


def test_platform_mode_does_nothing(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path, config=EMP_CONFIG))  # pinned to platform
    _alert(ctx)
    api, calls = _search([OWN])
    summary = find_alert_ads(ctx, fetch=FakeWeb(PAGES), api=api)
    assert not summary.active and calls == [] and summary.lines() == []
    ctx.close()


def test_missing_search_key_warns_once_and_requests_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    ctx = _ctx(tmp_path)
    ids = [_alert(ctx, sid=str(i), title=f"Role {i} (m/w/d)") for i in range(3)]
    events: list = []
    ctx.events.subscribe(events.append)
    web = FakeWeb(PAGES)
    summary = find_alert_ads(ctx, fetch=web)
    warnings = [e for e in events if e.kind == "warning"]
    assert len(warnings) == 1 and "3 alert jobs wait for the search key" in warnings[0].detail
    assert web.calls == [] and set(summary.deferred) == set(ids)
    assert all("search key missing" in ctx.alert_notes[i] for i in ids)
    ctx.close()


def test_a_miss_is_remembered_and_a_deferred_search_is_retried(tmp_path):
    ctx = _ctx(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: true\n    max_search_requests_per_run: 1"))
    first = _alert(ctx, sid="1", company="CHEFS CULINAR")
    second = _alert(ctx, sid="2", company="Other GmbH", title="Data Engineer (m/w/d)")
    web = FakeWeb({**PAGES})
    api, calls = _search([])  # nothing usable
    summary = find_alert_ads(ctx, fetch=web, api=api)
    assert len(calls) == 1  # the run's cap of one request is used by the first job
    assert len(summary.not_found) == 1 and len(summary.deferred) == 1
    missed, waiting = next(iter(summary.not_found)), next(iter(summary.deferred))
    assert {missed, waiting} == {first, second}
    assert ctx.alert_notes[missed].startswith("employer ad not found")
    # Next call: the first is a remembered miss (no search), the deferred second is searched again.
    api2, calls2 = _search([])
    again = find_alert_ads(ctx, fetch=web, api=api2)
    assert "last search" in again.not_found[missed]  # remembered: not searched again
    assert waiting not in again.not_found or calls2  # the deferred one was not remembered as a miss, it is retried
    ctx.close()


def test_summary_lines_for_the_spec_scenario():
    s = AlertResolutionSummary(mode="search_first", searched=10, direct=list("abcdef"), via_careers=["g", "h"],
                               not_found={"i": "employer ad not found: x"}, deferred={"j": "search cap reached"},
                               search_requests=14)
    text = "\n".join(s.lines())
    assert "6 found by searching company and title" in text and "2 on the employer's careers page" in text
    assert "1 not found" in text and "1 deferred" in text and "14 search requests" in text
    assert "alert job j waits: search cap reached" in text
    assert AlertResolutionSummary(mode="platform", searched=3).lines() == []


def test_a_linkedin_card_makes_one_posting_search_with_the_short_company_and_the_full_title(tmp_path):
    ctx = _ctx(tmp_path)
    job = _alert(ctx, company="CHEFS CULINAR GmbH & Co. KG")
    web = FakeWeb({**PAGES, OWN: ad(TEXT, "Software Engineer (m/w/d)")})
    api, calls = _search([OWN])
    summary = find_alert_ads(ctx, fetch=web, api=api)
    assert summary.direct == [job] and summary.searched == 1
    own = [c for c in calls if c["objective"].startswith("Find the job ad")]
    assert len(own) == 1 and own[0]["search_queries"][0] == '"CHEFS CULINAR" "Software Engineer (m/w/d)"'
    ctx.close()


def test_the_posting_search_is_keyed_by_the_job(tmp_path, monkeypatch):
    from autoapp.employers.gateway import SearchGateway

    keys: list[str] = []
    original = SearchGateway.search
    monkeypatch.setattr(SearchGateway, "search",
                        lambda self, key, objective, queries: keys.append(key) or original(self, key, objective, queries))
    ctx = _ctx(tmp_path)
    job = _alert(ctx)
    api, _ = _search([OWN])
    find_alert_ads(ctx, fetch=FakeWeb({**PAGES, OWN: ad(TEXT)}), api=api)
    assert keys == [f"posting:{job}"]
    ctx.close()


def test_a_stepstone_card_with_a_truncated_title_matches_the_ads_heading_by_prefix(tmp_path):
    ctx = _ctx(tmp_path)
    job = _alert(ctx, source="stepstone-alert", sid="s1", title="Software Engineer - Angular/.NET")
    ctx.conn.execute("UPDATE job_sources SET raw = json_set(raw, '$.title_truncated', json('true')) WHERE job_id = ?", (job,))
    web = FakeWeb({**PAGES})
    api, _ = _search([ANGULAR])
    summary = find_alert_ads(ctx, fetch=web, api=api)
    assert summary.direct == [job] and ctx.repo.get_job(job)["application_url"] == ANGULAR
    ctx.close()


def test_the_coverage_line_counts_cards_and_names_the_reasons():
    s = AlertResolutionSummary(mode="search_first", cards=12, searched=10, direct=list("abcdef"), via_careers=["g", "h"],
                               not_found={"i": "employer ad not found: x"}, deferred={"j": "search cap reached"},
                               no_company=["k"])
    line = s.coverage_line()
    assert line.startswith("alerts: 12 cards → 10 searched, 6 found directly, 2 via careers page, 3 waiting (")
    assert "search cap reached" in line and line.endswith("0 needed LLM extraction")
    assert s.lines()[0] == line and s.lines(cards=15)[0].startswith("alerts: 15 cards")
