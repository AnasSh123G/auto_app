"""Verify an employer's official site from search results or a trusted link.

Search results are candidates, not proof. A site is trusted only with (1) the employer's own name
on a public company/legal/contact page and (2) an independent compatible hint (the alert's city on
that page, or the alert's job title on the site's own careers listing). A plausible hostname or a
good search rank alone never counts; namesakes that cannot be told apart stay ambiguous.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from autoapp.discovery.normalize import norm_text
from autoapp.employers import listings as L
from autoapp.employers.match import place_key, title_key
from autoapp.employers.names import core_name, legal_form, normalize_name
from autoapp.employers.scope import bare_host, is_directory, is_shared_root, registrable, site_family
from autoapp.fetch.http_fetcher import FetchResult

Fetch = Callable[[str], FetchResult]

# A company/legal page: the whole link text, or the last path segment, is one of these words.
_LEGAL_TEXT = re.compile(r"^(?:impressum|imprint|legal notice|rechtliches|kontakt|contact(?: us)?|über uns|ueber uns|"
                         r"about(?: us)?)$", re.I)
_LEGAL_PATH = re.compile(r"^(?:impressum|imprint|legal(?:[-_]notice)?|rechtliches|kontakt|contact(?:[-_]us)?|"
                         r"(?:ue|u|ü)ber[-_]uns|about(?:[-_]us)?)(?:\.\w+)?$", re.I)
_RECRUITER = re.compile(r"personaldienstleist|personalvermittlung|arbeitnehmerüberlassung|arbeitnehmerueberlassung|"
                        r"zeitarbeit|personalberatung|headhunt|executive search|staffing|recruitment agency|"
                        r"recruiting[- ]?plattform|recruiting platform|talentmarktplatz|talent marketplace", re.I)
_FORMS = r"gmbh|ag|se|kg|ug|mbh|ohg|gbr|kgaa|co|inc|ltd|llc|plc"
_UNNAMED = re.compile(r"vertraulich|confidential|anonym|unser kunde|our client|stealth|nicht genannt|\bkunde\b", re.I)


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str | None = None
    excerpts: tuple[str, ...] = ()


@dataclass
class Verification:
    status: str  # verified | ambiguous | unverified
    website: str | None = None
    evidence: dict = field(default_factory=dict)
    reason: str | None = None
    alternatives: list[str] = field(default_factory=list)
    pages: int = 0
    recruiter: bool = False


def is_unnamed_client(company: str) -> bool:
    return bool(_UNNAMED.search(company or "")) or len(normalize_name(company)) < 2


def page_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup.select("script, style"):
        node.decompose()
    return " ".join(soup.get_text(" ").split())


def name_evidence(text: str, names: list[str]) -> str | None:
    """``exact`` (full name incl. legal form), ``core`` (same name without a conflicting legal form),
    ``conflict`` (same core but a different legal form: a namesake) or None."""
    haystack = f" {normalize_name(text)} "
    result: str | None = None
    for name in names:
        full = normalize_name(name)
        if full and f" {full} " in haystack:
            return "exact"
        core, form = core_name(name), legal_form(name)
        if not core or f" {core} " not in haystack:
            continue
        match = re.search(rf" {re.escape(core)} ({_FORMS})\b", haystack)
        if form and match and match.group(1) != form.split()[0]:
            result = result or "conflict"
        else:
            result = "core" if result != "exact" else result
    return result


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


def _rank(candidate: Candidate, names: list[str]) -> int:
    host = bare_host(candidate.url).replace("-", " ").replace(".", " ")
    tokens = {t for n in names for t in core_name(n).split() if len(t) > 2}
    return -sum(1 for t in tokens if t in host)


# Domains of the countries the app looks for jobs in (and international ones), tried before other country domains.
_HOME_TLDS = frozenset({"de", "at", "ch", "com", "eu", "net", "org", "io"})


def _foreign(url: str) -> int:
    return 0 if bare_host(url).rsplit(".", 1)[-1] in _HOME_TLDS else 1


def candidate_origins(candidates: list[Candidate], names: list[str], *, limit: int = 3) -> list[str]:
    """Distinct plausible employer origins in a sensible order. Rank only orders the work."""
    ordered = sorted(enumerate(candidates), key=lambda p: (_rank(p[1], names), _foreign(p[1].url), p[0]))
    out: list[str] = []
    for _, cand in ordered:
        parts = urlsplit(cand.url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if is_directory(cand.url) or is_shared_root(cand.url):
            continue
        origin = _origin(cand.url)
        # One company, several hosts (www, careers.): checked once. Its country domains (.se, .de) are separate
        # companies in law, so each is checked on its own: the Swedish branch's imprint does not name the German one.
        if registrable(origin) not in {registrable(o) for o in out}:
            out.append(origin)
        if len(out) >= limit:
            break
    return out


def _legal_pages(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ").split())
        href = str(anchor["href"])
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        path = urlsplit(href).path
        last = [seg for seg in path.split("/") if seg][-1:] or [""]
        if L.is_job_path(path):
            continue  # a vacancy or job category, whatever its link text says
        if _LEGAL_TEXT.match(text) or _LEGAL_PATH.match(last[0]):
            urls.append(urljoin(base, href).split("#", 1)[0])
    ordered = sorted(dict.fromkeys(urls), key=lambda u: 0 if re.search(r"impressum|imprint", u, re.I) else 1)
    # A careers subdomain often links the company's legal notice on its main domain.
    return [u for u in ordered if site_family(u) == site_family(base)][:2]


def _same_city(text: str, city: str | None) -> bool:
    place = place_key(city)
    return bool(place) and len(place) > 2 and f" {place} " in f" {norm_text(text)} "


def _has_title(text: str, title: str | None, names: list[str]) -> bool:
    wanted = title_key(title, names[0] if names else None)
    return len(wanted.split()) >= 2 and f" {wanted} " in f" {norm_text(text)} "


class _Reader:
    """Fetches pages within a page allowance, following at most two redirects (each costs a page)."""

    def __init__(self, fetch: Fetch, allowance: int) -> None:
        self._fetch, self.left, self.spent = fetch, allowance, 0

    def get(self, url: str) -> tuple[FetchResult | None, str]:
        current = url
        for _ in range(3):
            if self.left <= 0:
                return None, current
            self.left -= 1
            self.spent += 1
            result = self._fetch(current)
            if result.redirect_url:
                current = result.redirect_url
                continue
            return result, (result.final_url or current)
        return None, current


def verify_origin(fetch: Fetch, origin: str, *, names: list[str], city: str | None, title: str | None,
                  max_pages: int) -> Verification:
    """Collect evidence for one candidate origin within ``max_pages`` requests."""
    reader = _Reader(fetch, max_pages)
    evidence: dict = {"origin": origin}
    home, base = reader.get(origin)
    origin = _origin(base)
    evidence["origin"] = origin
    if home is None or not home.ok:
        return Verification("unverified", origin, evidence, f"{bare_host(origin)}: not readable", pages=reader.spent)
    texts = [page_text(home.html or "")]
    legal_url = None
    for url in _legal_pages(home.html or "", base):
        legal, legal_base = reader.get(url)
        if legal is None:
            break
        if legal.ok:
            legal_text = page_text(legal.html or "")
            if name_evidence(legal_text, names):
                texts.insert(0, legal_text)
                legal_url = legal_base
                break
            texts.append(legal_text)
    combined = " ".join(texts)
    found = name_evidence(texts[0], names) if legal_url else name_evidence(combined, names)
    evidence["name_match"] = found
    if legal_url:
        evidence["legal_page"] = legal_url
    recruiter = evidence["recruiter"] = bool(_RECRUITER.search(combined))
    host = bare_host(origin)
    if found is None:
        return Verification("unverified", origin, evidence, f"{host}: employer name not found on the site",
                            pages=reader.spent)
    if found == "conflict":
        return Verification("unverified", origin, evidence, f"{host}: a company with the same name but another legal form",
                            pages=reader.spent)
    if not legal_url:
        return Verification("unverified", origin, evidence, f"{host}: no company/legal page names the employer",
                            pages=reader.spent)
    if recruiter:
        return Verification("unverified", origin, evidence,
                            f"{host}: a recruiting company or job platform, not the hiring employer",
                            pages=reader.spent, recruiter=True)
    corroboration = None
    if title and _has_title(page_text(home.html or ""), title, names):  # the site's own page lists the vacancy
        corroboration = {"kind": "vacancy_title", "page": base}
    elif title:
        for url, _text in L.careers_links(home.html or "", base)[:2]:
            if site_family(url) != site_family(base):
                continue
            listing, _ = reader.get(url)
            if listing is not None and listing.ok and _has_title(page_text(listing.html or ""), title, names):
                corroboration = {"kind": "vacancy_title", "page": url}
                break
    if corroboration is None and _same_city(combined, city):
        corroboration = {"kind": "city", "value": (city or "").split(",")[0]}
    if corroboration is None:
        return Verification("unverified", origin, evidence, f"{host}: no independent evidence (city or vacancy)",
                            pages=reader.spent, recruiter=recruiter)
    evidence["corroboration"] = corroboration
    return Verification("verified", origin, evidence, pages=reader.spent, recruiter=recruiter)


def verify_candidates(fetch: Fetch, candidates: list[Candidate], *, names: list[str], city: str | None,
                      title: str | None, max_pages: int) -> Verification:
    """Decide among candidate origins: one verified site, an ambiguity, or nothing trustworthy."""
    origins = candidate_origins(candidates, names)
    if not origins:
        return Verification("unverified", reason="search returned no usable employer website")
    results: list[Verification] = []
    spent = 0
    for origin in origins:
        left = max_pages - spent
        if left <= 0:
            break
        res = verify_origin(fetch, origin, names=names, city=city, title=title, max_pages=left)
        spent += res.pages
        results.append(res)
    verified = _one_per_family([r for r in results if r.status == "verified"])
    # A namesake is a site whose legal page names the employer; a name in passing or a recruiter is not.
    # One company's country domains (flow.de, flow.com) are checked one by one but are never namesakes.
    named = _one_per_family([r for r in results if r.evidence.get("name_match") in ("exact", "core")
                             and r.evidence.get("legal_page") and not r.evidence.get("recruiter")])
    if len(verified) >= 2:
        verified = _strongest(verified, names)
    if len(verified) == 1:  # the other same-name candidates lack the corroboration this one has
        winner = verified[0]
        winner.pages = spent
        return winner
    if len(verified) >= 2 or len(named) >= 2:
        return Verification("ambiguous", None, {"candidates": [r.evidence for r in named]},
                            "several companies with this name; the available evidence cannot tell them apart",
                            alternatives=[r.website or "" for r in (verified or named)], pages=spent)
    if len(named) == 1 and named[0].status != "verified" and len(results) > 1:
        named = []  # one named site without corroboration is unresolved, not ambiguous
    reasons = "; ".join(r.reason for r in results if r.reason) or "no candidate could be verified"
    return Verification("unverified", None, {"candidates": [r.evidence for r in results]}, reasons, pages=spent)


def _one_per_family(results: list[Verification]) -> list[Verification]:
    out: list[Verification] = []
    for r in results:
        family = site_family(r.website or r.evidence.get("origin") or "")
        if not family or family not in {site_family(o.website or o.evidence.get("origin") or "") for o in out}:
            out.append(r)
    return out


def _strongest(verified: list[Verification], names: list[str]) -> list[Verification]:
    """Among several verified namesakes, the vacancy on a site's own careers page outranks the city;
    then a domain that is exactly the employer's name outranks a longer one (``mittwald.de`` over
    ``mittwaldhotel.de``). Anything still tied stays ambiguous."""
    by_vacancy = [v for v in verified if v.evidence.get("corroboration", {}).get("kind") == "vacancy_title"]
    if len(by_vacancy) == 1:
        return by_vacancy
    pool = by_vacancy or verified
    slugs = {core_name(n).replace(" ", "") for n in names if core_name(n)}
    exact = [v for v in pool if site_family(v.website or "").replace("-", "") in slugs]
    return exact if len(exact) == 1 else pool
