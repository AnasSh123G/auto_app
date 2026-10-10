"""Scan a verified employer's careers site, keeping snapshots and the site mapping between runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from autoapp.config.models import EmployerCareersConfig
from autoapp.employers import listings as listings_mod
from autoapp.employers.budget import PageMeter
from autoapp.employers.crawl import CareersCrawler, RunBudget, Want
from autoapp.employers.repo import EmployerRepo, Listing
from autoapp.employers.scope import SiteScope, bare_host
from autoapp.fetch.http_fetcher import FetchResult

Fetch = Callable[[str], FetchResult]
Emit = Callable[..., None]


@dataclass
class ScanReport:
    status: str  # reused | complete | partial | failed
    reused: bool = False
    scan_id: int | None = None
    listings: list[Listing] = field(default_factory=list)
    reason: str | None = None
    pages: int = 0
    moved: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    invalidated: bool = False
    unsupported: list[str] = field(default_factory=list)
    resumed: bool = False


class EmployerScanner:
    def __init__(self, repo: EmployerRepo, config: EmployerCareersConfig, fetch: Fetch, run: RunBudget,
                 meter: PageMeter, emit: Emit, *, now: Callable[[], datetime] | None = None,
                 run_id: str | None = None) -> None:
        self.repo, self.config, self.fetch, self.run, self.meter, self.emit = repo, config, fetch, run, meter, emit
        self._now = now or (lambda: datetime.now(UTC))
        self.run_id = run_id

    def _crawler(self, employer, sites, want: Want | None) -> CareersCrawler:  # noqa: ANN001
        scope = SiteScope.from_sites([s for s in sites if s["status"] != "invalid"])
        names = [a["alias"] for a in self.repo.aliases(employer["id"])]
        hosts = [bare_host(employer["website"])] if employer["website"] else []
        return CareersCrawler(self.fetch, scope, max_pages=max(1, self.meter.left),
                              max_openings=self.config.max_openings_per_employer, run=self.run, names=names,
                              official_hosts=hosts, want=want)

    def scan(self, employer, *, again: bool = False, want: Want | None = None) -> ScanReport:  # noqa: ANN001
        eid = employer["id"]
        fresh = None if again else self.repo.fresh_complete_scan(eid, self.config.scan_ttl_hours, self._now())
        if fresh is not None:
            return ScanReport("reused", True, fresh["id"], self.repo.listings(eid), pages=0)
        sites = self.repo.sites(eid)
        site_urls = [s["url"] for s in sites if s["kind"] in ("careers", "ats")]
        website = employer["website"]
        starts = site_urls or ([website] if website else [])
        if not starts:
            return ScanReport("failed", reason="no website saved for this employer")
        resume, known = None, None
        previous = self.repo.latest_scan(eid)
        if not again and previous is not None and previous["status"] == "partial" and previous["checkpoint"]:
            resume, known = json.loads(previous["checkpoint"]), self.repo.listings(eid)
        scan_id = self.repo.start_scan(eid, self.run_id)
        self.emit("employer_scan_started", employer["display_name"])
        result = self._crawler(employer, sites, want).scan(starts, resume=resume, known=known)
        moved: list[str] = []
        recovered = False
        gone_saved = [g for g in result.gone if g in site_urls]
        if gone_saved and not result.listings and website and self.meter.left > 0:
            # The saved careers address moved: look for the new one from the official homepage, no new search.
            moved = gone_saved
            for url in moved:
                self.repo.mark_site(eid, url, "moved", "no longer found (404/410)")
            retry = self._crawler(employer, self.repo.sites(eid), want).scan([website])
            retry.pages += result.pages
            retry.notes = result.notes + retry.notes
            recovered = bool(retry.listings or retry.listing_urls)
            result = retry
        invalidated = False
        if website and website in result.gone and not result.listings and not recovered and not employer["pinned"]:
            self.repo.invalidate(eid, "the saved website no longer exists (404/410) and offers no careers link")
            invalidated = True
        for host, why in result.blocked.items():
            for site in self.repo.sites(eid):
                if bare_host(site["url"]) == bare_host(host):
                    self.repo.mark_site(eid, site["url"], "blocked", why)
        for site in result.new_sites:
            self.repo.add_site(eid, site.url, _origin(site.url), kind="ats", tenant_path=site.tenant_path,
                               referrer_url=site.referrer, evidence=site.evidence)
        has_entry = any(s["kind"] in ("careers", "ats") and s["status"] == "ok" for s in self.repo.sites(eid))
        if result.listing_urls and not has_entry:  # remember where the vacancies were found
            url = result.listing_urls[0]
            kind = "careers" if not website or bare_host(url) == bare_host(website) else "ats"
            self.repo.add_site(eid, url, _origin(url), kind=kind, referrer_url=website)
        written = self.repo.save_listings(eid, scan_id, result.listings) if result.listings else 0
        status = "failed" if result.status == "failed" and not result.listings else result.status
        if status == "complete":
            self.repo.prune_listings(eid, scan_id)
        self.repo.finish_scan(scan_id, status, pages=result.pages, openings=written, reason=result.reason,
                              checkpoint=result.checkpoint)
        if status != "failed":
            self.repo.clear_failure(eid)
        self.emit("employer_scanned", employer["display_name"], status=status, pages=result.pages, openings=written)
        return ScanReport(status, False, scan_id, self.repo.listings(eid), result.reason, result.pages, moved,
                          result.blocked, invalidated, result.unsupported, resume is not None)


    def read_detail(self, employer, listing: Listing) -> Listing | None:  # noqa: ANN001
        """Read one vacancy page inside the employer's scope (redirects included), costing pages."""
        scope = SiteScope.from_sites([s for s in self.repo.sites(employer["id"]) if s["status"] != "invalid"])
        wrapped = self.meter.wrap(self.fetch)
        url = listing.url
        for _ in range(3):
            if not scope.allows(url):
                return None
            page = wrapped(url)
            if page.redirect_url:
                url = page.redirect_url
                continue
            if not page.ok:
                return None
            item = listings_mod.listing_from_detail(page.html or "", page.final_url or url,
                                                    hint=listings_mod.ListingLink(listing.url, listing.title,
                                                                                  listing.locations[0] if listing.locations else None,
                                                                                  listing.ref))
            if item is not None:
                item.url = listing.url
            return item
        return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"

