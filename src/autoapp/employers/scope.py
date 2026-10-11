"""Which addresses an employer scan may read: the verified website and authorised ATS tenants/paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Shared ATS hosts whose tenants live in the path; the tenant segment(s) must be part of the scope.
_PATH_TENANT_HOSTS = {
    "boards.greenhouse.io": 1, "job-boards.greenhouse.io": 1, "jobs.lever.co": 1, "apply.workable.com": 1,
    "careers.smartrecruiters.com": 1, "jobs.smartrecruiters.com": 1, "jobs.ashbyhq.com": 1, "join.com": 2,
}
# Shared hosts that must never become an employer scope on their own.
SHARED_HOSTS = frozenset({
    *_PATH_TENANT_HOSTS, "onapply.de", "www.onapply.de", "personio.de", "softgarden.io", "smartrecruiters.com",
    "workday.com", "myworkdayjobs.com", "successfactors.com", "successfactors.eu", "icims.com", "taleo.net",
})
# Job boards, directories and aggregators: never an employer's own site, whatever their ranking.
DIRECTORY_HOSTS = frozenset({
    "kununu.com", "glassdoor.com", "glassdoor.de", "xing.com", "indeed.com", "indeed.de", "stepstone.de",
    "linkedin.com", "monster.de", "meinestadt.de", "stellenanzeigen.de", "jobware.de", "jobs.de", "jooble.org",
    "adzuna.de", "adzuna.com", "arbeitsagentur.de", "jobbörse.de", "kalaydo.de", "gelbeseiten.de", "dasoertliche.de",
    "northdata.de", "firmenwissen.de", "creditreform.de", "wikipedia.org", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "tiktok.com", "bing.com", "google.com", "yelp.com", "trustpilot.com",
    "hrb.de", "unternehmen24.info", "wlw.de", "europages.de", "yellowpages.com", "dnb.com", "zoominfo.com",
    "crunchbase.com", "jobted.de", "talent.com", "jobrapido.com", "whatjobs.com", "ziprecruiter.com", "welcometothejungle.com",
    "tracxn.com", "owler.com", "pitchbook.com", "rocketreach.co", "cbinsights.com", "wellfound.com", "companyhouse.de",
    "handelsregister.de", "unternehmensregister.de", "workwise.io", "jobvector.de", "bundesanzeiger.de", "firmenabc.at", "dnb.de", "lusha.com",
})


def bare_host(url_or_host: str) -> str:
    host = (urlsplit(url_or_host).hostname if "/" in url_or_host else url_or_host) or ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "edu"})


def registrable(url_or_host: str) -> str:
    """``careers.acme.co.uk`` -> ``acme.co.uk``; ``karriere.acme.de`` -> ``acme.de`` (no public-suffix list)."""
    labels = bare_host(url_or_host).split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def site_family(url_or_host: str) -> str:
    """One company's hosts across subdomains and country domains (``4flow.com``, ``careers.4flow.com``,
    ``4flow.de`` -> ``4flow``). A tenant on a shared ATS host is its own family."""
    if is_shared_host(url_or_host):
        return bare_host(url_or_host)
    return registrable(url_or_host).split(".")[0]


def host_matches(host: str, domains: frozenset[str] | set[str]) -> bool:
    host = bare_host(host)
    return any(host == d or host.endswith("." + d) for d in domains)


def is_directory(url: str) -> bool:
    return host_matches(url, DIRECTORY_HOSTS)


def is_partner_board(partner: str | None, company: str | None) -> bool:
    """An Arbeitsagentur ad's ``allianzpartnerName`` (``jobvector.de``) names the site that carries the ad. It is a
    board unless it is the employer itself; the Arbeitsagentur as the partner means a direct posting there."""
    name = (partner or "").strip().lower()
    if not name:
        return False
    family = site_family(name)
    if family == "arbeitsagentur":
        return False
    squashed = "".join(ch for ch in (company or "").lower() if ch.isalnum())
    return not (len(family) >= 3 and family in squashed)


def is_shared_host(url: str) -> bool:
    return host_matches(url, SHARED_HOSTS)


def ats_tenant_path(url: str) -> str | None:
    """Tenant path of a path-based shared ATS host (``/acme``), ``''`` for any other host, None if the
    address is on a path-based host but names no tenant."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    depth = _PATH_TENANT_HOSTS.get(host)
    if depth is None:
        return ""
    segments = [s for s in parts.path.split("/") if s]
    if host == "join.com":
        return "/" + "/".join(segments[:2]) if len(segments) >= 2 and segments[0] == "companies" else None
    return "/" + "/".join(segments[:depth]) if len(segments) >= depth else None


def is_shared_root(host: str) -> bool:
    """The bare shared platform itself (``onapply.de``, ``boards.greenhouse.io``), never a tenant."""
    return bare_host(host) in SHARED_HOSTS


@dataclass(frozen=True)
class ScopeEntry:
    host: str  # bare host (no www.)
    tenant_path: str = ""

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = bare_host(url)
        if not (host == self.host or host.endswith("." + self.host)):
            return False
        if not self.tenant_path:
            return True
        path = parts.path.lower()
        prefix = self.tenant_path.lower()
        return path == prefix or path.startswith(prefix + "/")


@dataclass
class SiteScope:
    entries: list[ScopeEntry] = field(default_factory=list)

    @classmethod
    def from_sites(cls, rows) -> SiteScope:  # noqa: ANN001 - sqlite rows or objects with .url/.tenant_path
        scope = cls()
        for row in rows:
            scope.add(row["url"], row["tenant_path"] or "")
        return scope

    def add(self, url: str, tenant_path: str = "") -> ScopeEntry | None:
        """Add a site; a shared platform root, or a path-tenant host without a tenant, is refused."""
        host = bare_host(url)
        if not host or (is_shared_root(host) and not tenant_path):
            return None
        entry = ScopeEntry(host, tenant_path)
        if entry not in self.entries:
            self.entries.append(entry)
        return entry

    def allows(self, url: str) -> bool:
        return any(e.allows(url) for e in self.entries)
