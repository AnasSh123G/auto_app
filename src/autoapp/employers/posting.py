"""Find an alert job's own ad directly: search "<company> <job title>" and take the employer's page for it.

This is the first try for every LinkedIn, StepStone or Indeed job, before the employer's website is looked up
(``resolver``) and its careers pages are scanned (``scanner``). A result is used only when its page really is
this ad: not a job board, not closed, the job title in the page's own title or heading, and the company named
on the page or in its address. The employer's applicant system (Personio, d.vinci, a group portal such as
``meine-karriere-im-handel.de``) counts as its page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from autoapp.employers.gateway import SearchGateway, SearchUnavailableForRun
from autoapp.employers.hints import clean_title, sanitize_hint, short_name
from autoapp.employers.parallel import SearchError
from autoapp.employers.scope import bare_host, is_directory, is_shared_root
from autoapp.fetch.extract import closed_notice, extract_job, html_to_text

MAX_PAGES = 5  # results read per job, in the search's order


@dataclass
class PostingHit:
    url: str
    description: str | None
    reason: str


@dataclass
class PostingMiss:
    reason: str
    deferred: bool = False  # the search could not run in this run (cap, key): not remembered as a miss


def _norm(text: str | None) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").casefold()).split())


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", text.casefold())


def build_posting_search(company: str, title: str, city: str | None) -> tuple[str, list[str]]:
    name, role = short_name(company), clean_title(title)
    full = sanitize_hint(title, max_len=80)  # with "(m/w/d)": the ad's exact title finds it best
    place = sanitize_hint(city, max_len=40)
    objective = (f"Find the job ad {full!r} of the company {name!r}" + (f" in {place}" if place else "")
                 + " on the employer's own careers website or applicant system, not on LinkedIn, Indeed, StepStone "
                   "or another job board.")
    queries = [f'"{name}" "{full}"', f"{name} {role} {place}".strip() if place else f"{name} {role} Stellenangebot"]
    return objective, [q[:120] for q in queries]


def _shingles(text: str | None) -> set[tuple[str, ...]]:
    words = re.findall(r"\w{3,}", (text or "").casefold())
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


MIN_SHINGLES = 30  # a description shorter than this is not compared
MIN_OVERLAP = 0.5  # share of the job's text found on the page


@dataclass
class Verdict:
    ok: bool
    reason: str
    exact: bool = False  # the page heading is exactly the job title
    overlap: float | None = None


def judge(html: str, url: str, *, company: str, title: str, description: str | None = None) -> Verdict:
    """Whether the page is this job's ad: not closed, the title in its own heading, the company named and,
    when the job's text is known, most of that text on the page."""
    notice = closed_notice(html)
    if notice:
        return Verdict(False, f"the ad says {notice!r}")
    role = _norm(clean_title(title))
    if not role:
        return Verdict(False, "no job title to compare")
    soup = BeautifulSoup(html, "html.parser")
    data = extract_job(html, url)
    heads = [_norm(clean_title(h)) for h in (data.title, soup.title.get_text(" ") if soup.title else None,
                                             *(h.get_text(" ") for h in soup.find_all("h1"))) if h]
    if not any(role in h for h in heads):
        return Verdict(False, "the job title is not the page's title or heading")
    name = short_name(company)
    page_text = html_to_text(html)
    text = _norm(page_text)
    key = _norm(name)
    named = bool(key) and (key in text or key in _norm(data.company) or _compact(name) in _compact(bare_host(url)))
    if not named:
        return Verdict(False, f"{name} is not named on the page")
    exact = role in heads
    wanted = _shingles(description)
    if len(wanted) < MIN_SHINGLES:
        return Verdict(True, "job title in the page heading and company named", exact)
    overlap = len(wanted & _shingles(page_text)) / len(wanted)
    if overlap < MIN_OVERLAP:
        return Verdict(False, f"a different ad with this title ({overlap:.0%} of the job's text on the page)", exact,
                       overlap)
    return Verdict(True, f"the same ad ({overlap:.0%} of the job's text on the page)", exact, overlap)


def page_is_the_ad(html: str, url: str, *, company: str, title: str, description: str | None = None) -> tuple[bool, str]:
    v = judge(html, url, company=company, title=title, description=description)
    return v.ok, v.reason


def find_posting(gateway: SearchGateway, fetch: Callable, *, job_id: str, company: str, title: str,
                 city: str | None, description: str | None = None) -> PostingHit | PostingMiss:
    objective, queries = build_posting_search(company, title, city)
    try:
        response = gateway.search(f"posting:{job_id}", objective, queries)
    except SearchUnavailableForRun as stop:
        return PostingMiss(stop.reason, deferred=True)
    except SearchError as err:
        return PostingMiss(f"search failed: {err}", deferred=True)
    seen: set[str] = set()
    why: list[str] = []
    good: list[tuple[str, Verdict, str | None]] = []
    compared = len(_shingles(description)) >= MIN_SHINGLES
    for hit in response.results:
        url = hit.url
        host = bare_host(url)
        if not url.startswith(("http://", "https://")) or is_directory(url) or is_shared_root(host) or url in seen:
            continue
        if len(seen) >= MAX_PAGES:
            break
        seen.add(url)
        page = fetch(url)
        if not page.ok or not page.html:
            why.append(f"{host}: not readable")
            continue
        final = page.final_url or url
        verdict = judge(page.html, final, company=company, title=title, description=description)
        if not verdict.ok:
            why.append(f"{host}: {verdict.reason}")
            continue
        good.append((final, verdict, extract_job(page.html, final).description))
        if compared:
            break  # the job's own text is on it: no doubt left
    if not compared and len(good) > 1:
        # Without the job's text, several pages with this title cannot be told apart unless one heading is exact.
        exact = [g for g in good if g[1].exact]
        if len(exact) != 1:
            return PostingMiss("several ads with this title, the job's text is not known to tell them apart: "
                               + ", ".join(g[0] for g in good[:3]))
        good = exact
    if good:
        url, verdict, text = good[0]
        return PostingHit(url, (text or "").strip() or None, f"{bare_host(url)}: {verdict.reason}")
    return PostingMiss("; ".join(why[:4]) or "the search found only job boards")


class PostingMemo:
    """Jobs whose direct search found nothing recently, so it is not paid for again in every run."""

    def __init__(self, conn, ttl_hours: int, now: Callable[[], datetime] | None = None) -> None:  # noqa: ANN001
        self.conn, self.ttl, self.now = conn, ttl_hours, now or (lambda: datetime.now(UTC))

    def recent_miss(self, job_id: str) -> str | None:
        row = self.conn.execute("SELECT reason, checked_at FROM posting_searches WHERE job_id = ? AND found = 0",
                                (job_id,)).fetchone()
        if row is None:
            return None
        checked = datetime.fromisoformat(row["checked_at"])
        return row["reason"] if self.now() - checked < timedelta(hours=self.ttl) else None

    def save(self, job_id: str, *, found: bool, url: str | None, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO posting_searches (job_id, found, url, reason, checked_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET found = excluded.found, url = excluded.url, "
                "reason = excluded.reason, checked_at = excluded.checked_at",
                (job_id, int(found), url, reason, self.now().isoformat()))
