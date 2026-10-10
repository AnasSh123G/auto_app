"""Snapshots, resumable scans, moved sites and blocks (tasks 3.2, 3.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autoapp.employers.scanner import EmployerScanner
from tests.employers.identity_world import Rig
from tests.employers.webfake import FakeWeb, fixture, listing_page

SITE = "https://jobs.example.com/"


def make(tmp_path, pages, *, run_pages=100, db="s.db", employer=True, careers=True, **cfg):
    from autoapp.config.models import EmployerCareersConfig

    rig = Rig(tmp_path, FakeWeb(pages), [], config=EmployerCareersConfig(enabled=True, **cfg), db_name=db, run_pages=run_pages)
    eid = None
    if employer:
        eid = rig.repo.create_employer("Jobs Example GmbH", status="verified", website=SITE, provenance="search")
        rig.repo.add_site(eid, SITE, SITE, kind="website")
        if careers:
            rig.repo.add_site(eid, SITE + "karriere", SITE, kind="careers")
    return rig, eid


def scanner(rig, **kw):
    return EmployerScanner(rig.repo, rig.config, rig.web, rig.run, rig.meter, lambda *a, **k: None, **kw)


def pages_with_listing(n=3):
    entries = [(f"Role {i}", f"/job/{3000 + i}") for i in range(n)]
    return {SITE: "<html><body><a href='/karriere'>Karriere</a></body></html>", SITE + "karriere": listing_page(entries)}


def test_complete_snapshot_is_reused_for_new_alerts_without_scanning(tmp_path):
    rig, eid = make(tmp_path, pages_with_listing())
    first = scanner(rig).scan(rig.repo.get(eid))
    assert first.status == "complete" and len(first.listings) == 3 and not first.reused
    calls = len(rig.web.calls)
    second = scanner(rig).scan(rig.repo.get(eid))
    assert second.reused and len(second.listings) == 3 and len(rig.web.calls) == calls
    assert rig.repo.latest_scan(eid)["id"] == first.scan_id


def test_again_and_expiry_bypass_the_snapshot_but_keep_the_site(tmp_path):
    rig, eid = make(tmp_path, pages_with_listing())
    scanner(rig).scan(rig.repo.get(eid))
    forced = scanner(rig).scan(rig.repo.get(eid), again=True)
    assert not forced.reused and forced.status == "complete" and rig.repo.get(eid)["website"] == SITE
    future = scanner(rig, now=lambda: datetime.now(UTC) + timedelta(hours=25))
    assert not future.scan(rig.repo.get(eid)).reused


def test_partial_snapshot_is_not_fresh_and_exposes_what_was_read(tmp_path):
    pages = {SITE: "<html><body><a href='/karriere'>Karriere</a></body></html>",
             SITE + "karriere": listing_page([("A Dev", "/job/1")], nxt=SITE + "karriere?page=2"),
             SITE + "karriere?page=2": listing_page([("B Dev", "/job/2")])}
    rig, eid = make(tmp_path, pages, max_pages_per_employer=1, max_pages_per_run=1)
    first = scanner(rig).scan(rig.repo.get(eid))
    assert first.status == "partial" and "page limit" in first.reason and len(first.listings) == 1
    assert rig.repo.fresh_complete_scan(eid, 24) is None
    rig.conn.close()
    nxt, _ = make(tmp_path, pages, employer=False, db="s.db", max_pages_per_employer=5, max_pages_per_run=5)
    resumed = scanner(nxt).scan(nxt.repo.get(eid))
    assert resumed.resumed and resumed.status == "complete" and len(resumed.listings) == 2
    assert nxt.web.calls == [SITE + "karriere?page=2"]  # only what was left unread


def test_discovered_listing_page_becomes_the_saved_careers_site(tmp_path):
    rig, eid = make(tmp_path, pages_with_listing(), careers=False)
    report = scanner(rig).scan(rig.repo.get(eid))
    assert report.status == "complete"
    assert [s["url"] for s in rig.repo.sites(eid) if s["kind"] == "careers"] == [SITE + "karriere"]


def test_moved_careers_page_is_found_from_the_homepage_without_search(tmp_path):
    pages = {SITE + "karriere": ("status", 404), SITE: "<html><body><a href='/jobs/'>Stellenangebote</a></body></html>",
             SITE + "jobs/": listing_page([("New Dev", "/job/55")])}
    rig, eid = make(tmp_path, pages)
    report = scanner(rig).scan(rig.repo.get(eid))
    assert report.status == "complete" and [item.title for item in report.listings] == ["New Dev"]
    assert report.moved == [SITE + "karriere"] and rig.search_calls == []
    sites = {s["url"]: s["status"] for s in rig.repo.sites(eid)}
    assert sites[SITE + "karriere"] == "moved" and sites[SITE + "jobs/"] == "ok"
    assert rig.repo.get(eid)["status"] == "verified"


def test_definitively_gone_website_allows_rediscovery_but_pinned_is_kept(tmp_path):
    gone = {SITE: ("status", 404), SITE + "karriere": ("status", 404)}
    rig, eid = make(tmp_path, gone)
    report = scanner(rig).scan(rig.repo.get(eid))
    assert report.invalidated and rig.repo.get(eid)["status"] == "invalid"
    rig2, eid2 = make(tmp_path, gone, db="p.db")
    rig2.repo.verify(eid2, SITE, provenance="user", pinned=True)
    assert not scanner(rig2).scan(rig2.repo.get(eid2)).invalidated
    assert rig2.repo.get(eid2)["status"] == "verified"


def test_blocked_site_keeps_its_mapping_and_tries_no_other_host(tmp_path):
    pages = {SITE + "karriere": ("blocked", "HTTP 403")}
    rig, eid = make(tmp_path, pages)
    report = scanner(rig).scan(rig.repo.get(eid))
    assert report.status == "partial" and report.blocked == {"jobs.example.com": "HTTP 403"}
    assert rig.web.calls == [SITE + "karriere"] and rig.search_calls == []
    emp = rig.repo.get(eid)
    assert emp["status"] == "verified" and emp["website"] == SITE
    assert {s["url"]: s["status"] for s in rig.repo.sites(eid)}[SITE + "karriere"] == "blocked"


def test_discovered_tenant_is_saved_with_provenance(tmp_path):
    from autoapp.config.models import EmployerCareersConfig

    pages = {"https://cbc-cologne.de/": "<html><body><a href='/karriere'>Karriere</a></body></html>",
             "https://cbc-cologne.de/karriere": ("<html><body><div class='onapply-career-page-container' "
                                                  "data-url='https://cbc-cologne.onapply.de/feed/render.html?format=json'></div></body></html>"),
             "https://cbc-cologne.onapply.de/": fixture("onapply_listing.html")}
    rig = Rig(tmp_path, FakeWeb(pages), [], config=EmployerCareersConfig(enabled=True))
    eid = rig.repo.create_employer("CBC Cologne Business Center GmbH", status="verified", website="https://cbc-cologne.de/")
    rig.repo.add_site(eid, "https://cbc-cologne.de/", "https://cbc-cologne.de/", kind="website")
    report = scanner(rig).scan(rig.repo.get(eid))
    assert report.status == "complete" and len(report.listings) == 9
    ats = [s for s in rig.repo.sites(eid) if s["kind"] == "ats"]
    assert [s["url"] for s in ats] == ["https://cbc-cologne.onapply.de/"]
    assert ats[0]["referrer_url"] == "https://cbc-cologne.de/karriere"
    nxt = scanner(rig)  # a later run starts at the saved tenant, not at the homepage
    rig.web.calls.clear()
    assert nxt.scan(rig.repo.get(eid), again=True).status == "complete"
    assert rig.web.calls[0].startswith("https://cbc-cologne.onapply.de/")


def test_complete_rescan_drops_closed_postings_but_partial_keeps_unread_ones(tmp_path):
    rig, eid = make(tmp_path, pages_with_listing(3))
    scanner(rig).scan(rig.repo.get(eid))
    rig.web.pages[SITE + "karriere"] = listing_page([("Role 0", "/job/3000")])  # two vacancies closed
    again = scanner(rig).scan(rig.repo.get(eid), again=True)
    assert again.status == "complete" and [x.url for x in again.listings] == [SITE + "job/3000"]
    rig.web.pages[SITE + "karriere"] = ("status", 503)
    partial = scanner(rig).scan(rig.repo.get(eid), again=True)
    assert partial.status != "complete" and [x.url for x in partial.listings] == [SITE + "job/3000"]
