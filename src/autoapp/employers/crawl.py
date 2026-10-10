"""Bounded, read-only careers crawler (employer-career-discovery spec).

Only public GETs through an injected fetcher (which applies the target guard, pacing and host
blocks). It never runs scripts, never activates buttons or forms, and never launches a browser.
Every request, redirect, pagination step and feed read consumes the page budget.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from autoapp.employers import listings as L
from autoapp.employers.names import core_name
from autoapp.employers.repo import Listing
from autoapp.employers.scope import (
    SiteScope,
    ats_tenant_path,
    bare_host,
    host_matches,
    is_directory,
    is_shared_root,
)
from autoapp.fetch.http_fetcher import FetchResult
from autoapp.formfinder.detect import usable

Fetch = Callable[[str], FetchResult]
Want = Callable[[L.ListingLink], "int | None"]

MAX_DEPTH = 3
MAX_TENANT_PROBES = 2
_CHALLENGE = re.compile(r"just a moment|verify you are human|checking your browser|cf-chl-|challenge-platform|"
                        r"recaptcha|hcaptcha|access denied", re.I)


@dataclass
class RunBudget:
    """Pages and openings left for the whole run, shared by every employer scan."""

    pages: int
    openings: int

    def take_page(self) -> bool:
        if self.pages <= 0:
            return False
        self.pages -= 1
        return True

    def take_opening(self) -> bool:
        if self.openings <= 0:
            return False
        self.openings -= 1
        return True


@dataclass
class NewSite:
    url: str
    tenant_path: str
    referrer: str
    evidence: dict


@dataclass
class ScanResult:
    status: str = "partial"  # complete | partial | failed
    reason: str | None = None
    pages: int = 0
    listings: list[Listing] = field(default_factory=list)
    new_sites: list[NewSite] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checkpoint: dict | None = None
    listing_pages: int = 0
    gone: list[str] = field(default_factory=list)  # addresses answering 404/410
    listing_urls: list[str] = field(default_factory=list)  # pages that held vacancy listings


def blocking_wall(html: str) -> str | None:
    """``'login'`` or ``'challenge'`` when the page asks for credentials or an anti-bot check."""
    soup = BeautifulSoup(html or "", "html.parser")
    fields = [node for node in soup.select('input[type="password"]') if usable(node)]
    if fields:
        # A login box in the header or a pop-up (a customer portal) does not hide the page: a wall leaves little else.
        for node in fields:
            (node.find_parent("form") or node).decompose()
        for node in soup.select("script, style, noscript, template"):
            node.decompose()
        if len(soup.get_text(" ", strip=True)) < 800 and len(soup.find_all("a", href=True)) < 15:
            return "login"
    visible = BeautifulSoup(html or "", "html.parser")
    for node in visible.select("script, style"):
        node.decompose()
    text = visible.get_text(" ", strip=True)
    if _CHALLENGE.search(html or "") and len(text) < 600:
        return "challenge"
    return None


def tenant_evidence(html: str, names: list[str], official_hosts: list[str]) -> dict | None:
    """Public identity evidence on a page of a candidate ATS tenant: the employer's own name or a link
    back to its verified website."""
    org, same_as = L.hiring_organization(html)
    cores = {core_name(n) for n in names if core_name(n)}
    if org and core_name(org) in cores:
        return {"kind": "hiring_organization", "name": org}
    soup = BeautifulSoup(html or "", "html.parser")
    for anchor in soup.find_all("a", href=True):
        if any(host_matches(str(anchor["href"]), {bare_host(h)}) for h in official_hosts if h):
            return {"kind": "link_to_official_site", "href": str(anchor["href"])[:200]}
    if same_as and any(host_matches(same_as, {bare_host(h)}) for h in official_hosts if h):
        return {"kind": "same_as", "href": same_as}
    return None


@dataclass
class _Task:
    url: str
    kind: str  # page | tenant | feed
    depth: int = 0
    referrer: str | None = None

    def dump(self) -> list:
        return [self.url, self.kind, self.depth, self.referrer]


class CareersCrawler:
    def __init__(self, fetch: Fetch, scope: SiteScope, *, max_pages: int, max_openings: int, run: RunBudget,
                 names: list[str], official_hosts: list[str], want: Want | None = None) -> None:
        self.fetch, self.scope = fetch, scope
        self.max_pages, self.max_openings, self.run = max_pages, max_openings, run
        self.names, self.official_hosts, self.want = names, official_hosts, want

    # ------------------------------------------------------------------------------------------

    def scan(self, start_urls: list[str], *, resume: dict | None = None,
             known: list[Listing] | None = None) -> ScanResult:
        """Scan from ``start_urls``, or continue a partial scan from its ``resume`` checkpoint and the
        ``known`` listings of its snapshot (those already holding a description are not fetched again)."""
        res = ScanResult()
        visited: set[str] = set(resume.get("visited", [])) if resume else set()
        queue: deque[_Task] = deque(
            _Task(*t) for t in resume["pending"]) if resume else deque(_Task(u, "page") for u in start_urls)
        details: dict[str, L.ListingLink] = {}
        seen: dict[str, Listing] = {}
        probes = 0
        limit_reason: str | None = None
        for item in known or []:
            seen[item.url] = item
            if not item.description:
                details[item.url] = L.ListingLink(item.url, item.title, item.locations[0] if item.locations else None,
                                                  item.ref)

        def budget_left() -> str | None:
            if res.pages >= self.max_pages:
                return f"page limit reached ({self.max_pages} pages for this employer)"
            if self.run.pages <= 0:
                return "page limit reached for this run"
            return None

        def add_listing(item: Listing) -> bool:
            if item.url in seen:
                return True
            if len(seen) >= self.max_openings:
                return False
            if not self.run.take_opening():
                return False
            seen[item.url] = item
            return True

        while queue:
            task = queue.popleft()
            key = task.url.split("#", 1)[0]
            if key in visited:
                continue
            if task.kind != "tenant" and not self.scope.allows(key):
                res.notes.append(f"skipped {urlsplit(key).hostname}: outside the verified employer scope")
                continue
            if (limit_reason := budget_left()):
                queue.appendleft(task)
                break
            visited.add(key)
            self.run.take_page()
            res.pages += 1
            page = self.fetch(key)
            host = urlsplit(key).hostname or ""
            if page.rejected:
                res.notes.append(f"not requested: {page.reason}")
                continue
            if page.blocked:
                res.blocked[host] = page.reason or "blocked"
                continue
            if page.redirect_url:
                target = page.redirect_url.split("#", 1)[0]
                if task.kind == "tenant" or self.scope.allows(target):
                    queue.appendleft(_Task(target, task.kind, task.depth, task.referrer))
                else:
                    res.notes.append(f"redirect to {urlsplit(target).hostname} leaves the employer scope")
                continue
            if not page.ok:
                res.notes.append(f"{host}: {page.reason or f'HTTP {page.status}'}")
                if page.status in (404, 410):
                    res.gone.append(key)
                if task.kind == "page" and res.pages == 1:
                    res.status, res.reason = "failed", page.reason or f"HTTP {page.status}"
                continue
            html = page.html or ""
            final = (page.final_url or key).split("#", 1)[0]
            wall = blocking_wall(html)
            if wall:
                res.blocked[host] = "login required" if wall == "login" else "anti-bot challenge"
                continue
            if task.kind == "feed":
                for link in L.parse_feed(html, final):
                    self._take_link(link, seen, details, add_listing)
                continue
            if task.kind == "tenant":
                evidence = tenant_evidence(html, self.names, self.official_hosts)
                tenant = ats_tenant_path(final)
                if evidence is None or tenant is None or (is_shared_root(final) and not tenant):
                    res.notes.append(f"{host}: no employer identity evidence; not imported")
                    continue
                if self.scope.add(final, tenant) is None:
                    continue
                res.new_sites.append(NewSite(final, tenant, task.referrer or "", evidence))
            # A listing/careers page of the verified scope.
            links = L.listing_links(html, final)
            if links:
                res.listing_pages += 1
                res.listing_urls.append(final)
                if self.want is not None and len(seen) + len(links) > self.max_openings:
                    # More openings than the limit: the ones the alerts ask for are taken first.
                    links = sorted(links, key=lambda lk: (1, 0) if (rank := self.want(lk)) is None else (0, rank))
                for link in links:
                    if not self._take_link(link, seen, details, add_listing):
                        limit_reason = f"opening limit reached ({self.max_openings} openings)"
                        break
                if limit_reason:
                    break
                for nxt in L.pagination_links(html, final):
                    queue.append(_Task(nxt, "page", task.depth, final))
            elif L.looks_js_only(html):
                res.unsupported.append(final)
            for feed in L.feed_links(html, final):
                queue.append(_Task(feed, "feed", task.depth, final))
            for tenant_url in L.declared_tenants(html, final):
                if not self.scope.allows(tenant_url):
                    queue.append(_Task(tenant_url, "tenant", task.depth, final))
            if not links and task.depth < MAX_DEPTH:
                for url, _text in L.careers_links(html, final)[:5]:
                    if self.scope.allows(url):
                        queue.append(_Task(url, "page", task.depth + 1, final))
                    elif probes < MAX_TENANT_PROBES and not is_directory(url) and bare_host(url) not in {
                            bare_host(h) for h in self.official_hosts}:
                        probes += 1
                        queue.append(_Task(url, "tenant", task.depth + 1, final))

        # Details for wanted vacancies, best first, within the remaining budget.
        pending_details: list[tuple[int, L.ListingLink]] = []
        if self.want is not None and not (limit_reason or "").startswith("page limit"):
            for link in details.values():
                if urlsplit(link.url).path.lower().endswith(".pdf"):
                    continue  # a vacancy published as a document: its card is the listing
                priority = self.want(link)
                if priority is not None:
                    pending_details.append((priority, link))
            pending_details.sort(key=lambda p: p[0])
        done_details = 0
        for _priority, link in pending_details:
            if (out_of_pages := budget_left()):
                limit_reason = limit_reason or out_of_pages
                break
            if not self.scope.allows(link.url) or link.url in visited:
                continue
            visited.add(link.url)
            self.run.take_page()
            res.pages += 1
            page = self.fetch(link.url)
            while page.redirect_url and self.scope.allows(page.redirect_url) and res.pages < self.max_pages \
                    and self.run.take_page():
                res.pages += 1
                page = self.fetch(page.redirect_url)
            if page.blocked:
                res.blocked[urlsplit(link.url).hostname or ""] = page.reason or "blocked"
                continue
            if not page.ok or blocking_wall(page.html or ""):
                res.notes.append(f"detail not readable: {urlsplit(link.url).path}")
                continue
            item = L.listing_from_detail(page.html or "", (page.final_url or link.url), hint=link)
            if item is not None:
                item.url = link.url  # the listing keeps the address it was announced under
                seen[link.url] = item
                done_details += 1
        res.listings = list(seen.values())
        self._finish(res, queue, limit_reason, details, visited, pending_details, done_details)
        return res

    # ------------------------------------------------------------------------------------------

    def _take_link(self, link: L.ListingLink, seen: dict[str, Listing], details: dict[str, L.ListingLink],
                   add) -> bool:  # noqa: ANN001
        if not self.scope.allows(link.url):
            return True
        existing = seen.get(link.url)
        if existing is not None:
            if not existing.description:
                details.setdefault(link.url, link)
            return True
        listing = Listing(url=link.url, title=link.title, ref=link.ref, locations=[link.location] if link.location else [])
        if not add(listing):
            return False
        details.setdefault(link.url, link)
        return True

    @staticmethod
    def _finish(res: ScanResult, queue: deque[_Task], limit_reason: str | None, details: dict, visited: set[str],
                pending_details: list, done_details: int) -> None:
        if res.status == "failed":
            return
        reasons = []
        if limit_reason:
            reasons.append(limit_reason)
        if res.blocked:
            reasons.append("blocked: " + ", ".join(f"{h} ({r})" for h, r in res.blocked.items()))
        if res.unsupported:
            reasons.append("unsupported JavaScript listing: " + ", ".join(urlsplit(u).hostname or u for u in res.unsupported))
        if res.listing_pages == 0 and not res.listings:
            reasons.append("no readable listing found")
        if pending_details and done_details < len(pending_details) and not limit_reason:
            reasons.append("some vacancy details could not be read")
        if queue and not limit_reason:
            reasons.append("pages left unread")
        res.reason = "; ".join(reasons) or None
        res.status = "partial" if reasons else "complete"
        if res.status == "partial":
            res.checkpoint = {
                "visited": sorted(visited),
                "pending": [t.dump() for t in queue],
            }
