"""Identity verification and cache-first resolution (tasks 3.1, 3.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autoapp.config.models import EmployerCareersConfig
from tests.employers.identity_world import Rig, group_of, seed, world

CBC = "https://www.cbc-cologne.de/karriere"


def test_search_candidate_is_verified_with_name_and_city(tmp_path):
    rig = Rig(tmp_path, world(), [CBC, "https://www.kununu.com/de/cbc"])
    res = rig.resolver.resolve(group_of(seed()))
    assert (res.status, res.source, res.searches) == ("verified", "search", 1)
    emp = rig.repo.get(res.employer_id)
    assert emp["status"] == "verified" and emp["website"] == "https://www.cbc-cologne.de/"
    assert emp["provenance"] == "search" and "Köln" in emp["evidence"]
    assert rig.repo.sites(res.employer_id)[0]["kind"] == "website"
    assert "https://www.kununu.com/de/cbc" not in rig.web.calls  # a directory is never an identity


def test_good_hostname_and_rank_alone_are_not_identity(tmp_path):
    # The first result has a name-like host, but its imprint names another company.
    rig = Rig(tmp_path, world(), [["https://www.cbc-cologne-business.example/"], ["https://www.cbc-cologne-business.example/"]])
    res = rig.resolver.resolve(group_of(seed()))
    assert res.status == "unresolved" and "name not found" in (res.reason or "")
    assert rig.repo.get(res.employer_id)["status"] == "unresolved" and rig.repo.get(res.employer_id)["website"] is None


def test_name_without_independent_evidence_is_not_trusted(tmp_path):
    web = world()
    rig = Rig(tmp_path, web, ["https://www.acme-hamburg.example/"])
    res = rig.resolver.resolve(group_of(seed("Acme GmbH", "Data Engineer", "Berlin")))
    assert res.status == "unresolved" and "no independent evidence" in (res.reason or "")


def test_two_namesakes_that_cannot_be_told_apart_are_ambiguous(tmp_path):
    web = world()
    rig = Rig(tmp_path, web, [["https://www.acme-gmbh.example/", "https://www.acme-hamburg.example/"]] * 2)
    res = rig.resolver.resolve(group_of(seed("Acme GmbH", "Data Engineer", None)))
    assert res.status == "ambiguous" and "cannot tell them apart" in res.reason
    emp = rig.repo.get(res.employer_id)
    assert emp["status"] == "ambiguous" and emp["website"] is None
    assert rig.repo.sites(res.employer_id) == []


def test_city_hint_tells_namesakes_apart(tmp_path):
    rig = Rig(tmp_path, world(), [["https://www.acme-gmbh.example/", "https://www.acme-hamburg.example/"]])
    res = rig.resolver.resolve(group_of(seed("Acme GmbH", "Data Engineer", "Hamburg")))
    assert res.status == "verified"
    assert rig.repo.get(res.employer_id)["website"] == "https://www.acme-hamburg.example/"


def test_other_legal_form_is_a_different_employer(tmp_path):
    rig = Rig(tmp_path, world(), [["https://www.acme-gmbh.example/"], ["https://www.acme-ag.example/"]])
    first = rig.resolver.resolve(group_of(seed("Acme GmbH", "Dev", "Berlin")))
    second = rig.resolver.resolve(group_of(seed("Acme AG", "Dev", "Berlin", job_id="b2")))
    assert first.status == second.status == "verified" and first.employer_id != second.employer_id
    assert len(rig.search_calls) == 2


def test_trusted_employer_link_needs_no_search(tmp_path):
    rig = Rig(tmp_path, world(), [])
    s = seed(application_url="https://www.cbc-cologne.de/bewerben", job_url="https://www.linkedin.com/jobs/view/1")
    res = rig.resolver.resolve(group_of(s))
    assert (res.status, res.source, res.searches) == ("verified", "trusted_link", 0) and rig.search_calls == []
    assert not any("linkedin" in c for c in rig.web.calls)


def test_later_alert_in_a_new_run_makes_zero_search_calls_and_zero_page_requests(tmp_path):
    rig = Rig(tmp_path, world(), [CBC])
    first = rig.resolver.resolve(group_of(seed()))
    assert first.status == "verified" and len(rig.search_calls) == 1
    rig.conn.close()
    later = rig.fresh_resolver(results=[CBC])  # reopened workspace, new run
    calls_before = len(later.web.calls)
    res = later.resolver.resolve(group_of(seed(title="Data Scientist (m/w/d)", job_id="a2")))
    assert (res.status, res.source, res.employer_id) == ("verified", "cache", first.employer_id)
    assert later.search_calls == [] and len(later.web.calls) == calls_before


def test_alert_name_variants_reuse_the_saved_alias(tmp_path):
    rig = Rig(tmp_path, world(), [CBC])
    rig.resolver.resolve(group_of(seed("CBC Cologne Business Center GmbH")))
    res = rig.resolver.resolve(group_of(seed("CBC Cologne Business Center")))  # LinkedIn drops the legal form
    assert res.source == "cache" and len(rig.search_calls) == 1


def test_failure_is_cached_for_24_hours_and_again_bypasses_it(tmp_path):
    rig = Rig(tmp_path, world(), [["https://www.cbc-cologne-business.example/"]] * 4)
    group = group_of(seed())
    first = rig.resolver.resolve(group)
    assert first.status == "unresolved" and len(rig.search_calls) == 2  # first + refined, never more
    again = rig.resolver.resolve(group)
    assert again.kind == "failure_cached" and len(rig.search_calls) == 2
    forced = rig.fresh_resolver(results=[CBC])
    res = forced.resolver.resolve(group, again=True)
    assert res.status == "verified" and len(forced.search_calls) == 1
    later = rig.fresh_resolver(results=[CBC])
    later.resolver._now = lambda: datetime.now(UTC) + timedelta(hours=25)
    assert later.resolver.resolve(group_of(seed(city="Köln"))).status == "verified"


def test_second_search_is_a_refined_query_and_a_third_is_never_sent(tmp_path):
    rig = Rig(tmp_path, world(), [["https://www.cbc-cologne-business.example/"], [CBC]])
    res = rig.resolver.resolve(group_of(seed()))
    assert res.status == "verified" and len(rig.search_calls) == 2
    assert set(rig.search_calls[0]["search_queries"]) != set(rig.search_calls[1]["search_queries"])
    assert rig.gateway.remaining_for(group_of(seed()).key) == 0


def test_missing_key_defers_without_negative_cache_but_cached_sites_still_resolve(tmp_path):
    rig = Rig(tmp_path, world(), [CBC], key="")
    res = rig.resolver.resolve(group_of(seed()))
    assert res.status == "deferred" and res.kind == "no_key" and rig.search_calls == []
    assert rig.repo.all() == []  # nothing is remembered as a failure of the employer
    ok = Rig(tmp_path, world(), [CBC], db_name="b.db")
    ok.resolver.resolve(group_of(seed()))
    ok.conn.close()
    nokey = ok.fresh_resolver(results=[CBC], key="")
    assert nokey.resolver.resolve(group_of(seed(job_id="z"))).source == "cache"


def test_auth_failure_stops_search_but_not_cached_employers(tmp_path):
    rig = Rig(tmp_path, world(), [CBC], status=401)
    res = rig.resolver.resolve(group_of(seed()))
    assert res.status == "deferred" and res.kind == "auth"
    again = rig.resolver.resolve(group_of(seed("Acme GmbH", "Dev", "Berlin")))
    assert again.kind == "auth" and len(rig.search_calls) == 1


def test_cap_defers_without_discarding(tmp_path):
    cfg = EmployerCareersConfig(enabled=True, max_search_requests_per_run=1, max_search_requests_per_day=1,
                                max_search_requests_per_month=1)
    rig = Rig(tmp_path, world(), [["https://www.cbc-cologne-business.example/"]], config=cfg)
    first = rig.resolver.resolve(group_of(seed()))
    assert first.status == "deferred" and first.kind == "cap" and len(rig.search_calls) == 1
    second = rig.resolver.resolve(group_of(seed("Acme GmbH", "Dev", "Berlin")))
    assert second.status == "deferred" and second.kind == "cap"


def test_user_confirmed_site_is_pinned_and_refresh_proposes_instead_of_replacing(tmp_path):
    rig = Rig(tmp_path, world(), [["https://www.acme-hamburg.example/"]])
    group = group_of(seed("Acme GmbH", "Dev", "Hamburg"))
    res = rig.resolver.confirm(group, "https://www.acme-gmbh.example")
    emp = rig.repo.get(res.employer_id)
    assert emp["pinned"] == 1 and emp["provenance"] == "user" and emp["website"] == "https://www.acme-gmbh.example/"
    refreshed = rig.resolver.resolve(group, refresh_site=True)
    assert refreshed.kind == "proposal_pending"
    assert rig.repo.get(res.employer_id)["website"] == "https://www.acme-gmbh.example/"
    assert "confirm it with --website" in rig.repo.get(res.employer_id)["failure_reason"]


def test_unnamed_client_is_not_guessed(tmp_path):
    rig = Rig(tmp_path, world(), [CBC])
    res = rig.resolver.resolve(group_of(seed("Vertraulich")))
    assert res.status == "skipped" and rig.search_calls == []


def test_refresh_that_no_longer_supports_the_saved_site_withdraws_it(tmp_path):
    rig = Rig(tmp_path, world(), [CBC])
    first = rig.resolver.resolve(group_of(seed()))
    assert first.status == "verified" and rig.repo.sites(first.employer_id)
    # The imprint now names another company: the saved site was wrong (or changed hands).
    rig.web.pages["https://www.cbc-cologne.de/impressum"] = "<html><body>Impressum: Other Corp, Köln</body></html>"
    rig.conn.close()
    rig = rig.fresh_resolver(results=[CBC])  # a later run: `employers --discover --refresh-site`
    res = rig.resolver.resolve(group_of(seed()), refresh_site=True)
    emp = rig.repo.get(first.employer_id)
    assert res.status == "unresolved" and emp["status"] == "unresolved" and emp["website"] is None
    assert rig.repo.sites(first.employer_id) == [] and "names the employer" in emp["failure_reason"]
    assert "candidates" in emp["evidence"]  # what this lookup saw, for `autoapp employers`


def test_refresh_keeps_a_saved_site_that_is_only_unreachable(tmp_path):
    rig = Rig(tmp_path, world(), [CBC])
    first = rig.resolver.resolve(group_of(seed()))
    rig.web.pages["https://www.cbc-cologne.de/karriere"] = ("status", 503)
    rig.web.pages["https://www.cbc-cologne.de/"] = ("status", 503)
    rig.conn.close()
    rig = rig.fresh_resolver(results=[CBC])
    res = rig.resolver.resolve(group_of(seed()), refresh_site=True)
    emp = rig.repo.get(first.employer_id)
    assert res.status == "unresolved" and emp["status"] == "verified" and emp["website"] == "https://www.cbc-cologne.de/"


def test_confirming_another_website_drops_the_old_sites_snapshot(tmp_path):
    from tests.employers.test_scanner import SITE, make, pages_with_listing, scanner

    rig, eid = make(tmp_path, pages_with_listing(3))
    assert scanner(rig).scan(rig.repo.get(eid)).status == "complete" and len(rig.repo.listings(eid)) == 3
    rig.repo.verify(eid, SITE, provenance="search")  # same site: the snapshot stays
    assert len(rig.repo.listings(eid)) == 3 and rig.repo.fresh_complete_scan(eid, 24)
    rig.repo.verify(eid, "https://www.other-site.example/", provenance="user", pinned=True)
    assert rig.repo.listings(eid) == [] and rig.repo.fresh_complete_scan(eid, 24) is None


def test_a_run_without_pages_left_postpones_the_lookup_instead_of_failing_it(tmp_path):
    # The run's page budget is spent by earlier employers: no search is paid for and nothing is cached as a failure.
    rig = Rig(tmp_path, world(), [CBC], run_pages=0)
    res = rig.resolver.resolve(group_of(seed()))
    assert res.status == "deferred" and "max_pages_per_run" in res.reason and rig.search_calls == []
    later = rig.fresh_resolver(results=[CBC])  # the next run checks it normally
    assert later.resolver.resolve(group_of(seed())).status == "verified"


def test_pages_running_out_during_the_check_postpone_it_too(tmp_path):
    rig = Rig(tmp_path, world(), [CBC], run_pages=1)  # one page: the home page, not the imprint
    res = rig.resolver.resolve(group_of(seed()))
    assert res.status == "deferred" and len(rig.search_calls) == 1
    assert rig.fresh_resolver(results=[CBC]).resolver.resolve(group_of(seed())).status == "verified"
