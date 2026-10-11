"""Where an Arbeitsagentur job's application link comes from, and what to say when there is none.

An Arbeitsagentur page is a job board page, never the application page. The employer's own ad comes from the
direct "<company> <title>" search (``posting_searches``) and the careers-page lookup (``employer_seeds``). Only
when both ran and found nothing is the job ``no_employer_link``; a search that has not run yet (no
PARALLEL_API_KEY, a used-up cap, no company name) is not a miss and leaves the job ``restricted`` with the reason.
The one exception is an ad posted directly on the Arbeitsagentur, whose link is then allowed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from autoapp.assist.filler import is_restricted
from autoapp.employers.scope import bare_host, host_matches, is_directory, is_partner_board, site_family

NO_EMPLOYER_LINK = "no_employer_link"
ONLY_ON_ARBEITSAGENTUR = "only on the Arbeitsagentur"
NOT_FOUND_YET = "employer ad not found yet"
NOT_SEARCHED_YET = "employer ad not searched yet"
_ARBEITSAGENTUR_HOSTS = frozenset({"arbeitsagentur.de"})


@dataclass(frozen=True)
class EmployerLink:
    """``kind``: ``employer`` (``entry`` is the employer's address), ``direct`` (posted on the Arbeitsagentur
    only), ``missing`` (searched, not found), ``deferred`` (not searched yet)."""

    kind: str
    entry: str | None = None
    reason: str = ""


def _is_arbeitsagentur_page(url: str | None) -> bool:
    return bool(url) and host_matches(url, _ARBEITSAGENTUR_HOSTS)


def is_arbeitsagentur_job(ctx, job) -> bool:  # noqa: ANN001
    """A job that came from the Arbeitsagentur and whose own page is on it (an alert job that also appears there
    keeps the platform handling)."""
    if not _is_arbeitsagentur_page(job["job_url"]):
        return False
    return any(s["source"] == "arbeitsagentur" for s in ctx.repo.sources(job["id"]))


def _raw_facts(ctx, job) -> tuple[str | None, str | None]:  # noqa: ANN001
    """``(allianzpartnerName, externeURL)`` as the Arbeitsagentur search and detail calls stored them."""
    partner = external = None
    for source in ctx.repo.sources(job["id"]):
        if source["source"] != "arbeitsagentur":
            continue
        try:
            raw = json.loads(source["raw"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        detail = raw.get("detail") if isinstance(raw.get("detail"), dict) else {}
        partner = partner or detail.get("allianzpartnerName") or raw.get("allianzpartnerName")
        external = external or detail.get("externeURL") or detail.get("externeUrl") or raw.get("externeURL") \
            or raw.get("externeUrl")
    return partner, external


def _employer_side(ctx, job, url: str | None, restricted, partner: str | None) -> bool:  # noqa: ANN001
    """An address that can lead to the employer's own application: not a job board, directory, partner board or
    an Arbeitsagentur page."""
    if not isinstance(url, str) or not url.strip().lower().startswith(("http://", "https://")):
        return False
    if url == job["job_url"] or _is_arbeitsagentur_page(url) or is_restricted(url, restricted) or is_directory(url):
        return False
    if partner and is_partner_board(partner, job["company"]) and site_family(url) == site_family(partner):
        return False
    return True


def _recorded(ctx, sql: str, job_id: str):  # noqa: ANN001
    try:
        return ctx.repo.conn.execute(sql, (job_id,)).fetchone()
    except sqlite3.OperationalError:  # a workspace from before employer discovery
        return None


def _matched_posting(ctx, job_id: str) -> str | None:  # noqa: ANN001
    row = _recorded(ctx, "SELECT posting_url FROM employer_seeds WHERE job_id = ? AND outcome = 'matched'", job_id)
    return row["posting_url"] if row and row["posting_url"] else None


def _evaluation_url(ctx, job_id: str) -> str | None:  # noqa: ANN001
    evaluation = ctx.repo.current_evaluation(job_id)
    if not evaluation:
        return None
    try:
        return json.loads(evaluation["payload"]).get("result", {}).get("application_url")
    except (json.JSONDecodeError, AttributeError):
        return None


def employer_link(ctx, job, app=None) -> EmployerLink:  # noqa: ANN001
    """The employer-side address of an Arbeitsagentur job, or why there is none (see the module docstring)."""
    restricted = ctx.config.assist.restricted_domains if getattr(ctx, "config", None) is not None else ()
    partner, external = _raw_facts(ctx, job)
    posting = _matched_posting(ctx, job["id"])
    if posting:
        return EmployerLink("employer", posting)
    candidates = [app["form_entry_url"] if app else None, job["application_url"], _evaluation_url(ctx, job["id"])]
    for url in candidates:
        if _employer_side(ctx, job, url, restricted, partner):
            return EmployerLink("employer", url)

    search = _recorded(ctx, "SELECT reason FROM posting_searches WHERE job_id = ? AND found = 0", job["id"])
    careers = _recorded(ctx, "SELECT outcome, reason FROM employer_seeds WHERE job_id = ? AND outcome != 'matched'",
                        job["id"])
    if search is None or careers is None:
        # the search and careers lookup both have to have run before the job counts as "not found"
        if search is None:
            why = ("PARALLEL_API_KEY is not set in workspace/.env" if not os.environ.get("PARALLEL_API_KEY")
                   else "the search by company and title has not run for it yet (cap or company name)")
        else:
            why = "the employer's careers pages have not been looked at yet"
        return EmployerLink("deferred", reason=why)
    direct = not external and bool(partner) and site_family(partner) == "arbeitsagentur"
    if direct:
        return EmployerLink("direct", reason="posted directly on the Arbeitsagentur; the employer's site has no ad for it")
    found = (search["reason"] or "nothing usable").strip()
    seen = (careers["reason"] or careers["outcome"].replace("_", " ")).strip()
    return EmployerLink("missing", reason=f"searched by company and title ({found}); careers pages ({seen})")


def outcome_for(ctx, job, link: EmployerLink):  # noqa: ANN001
    """The form outcome of an Arbeitsagentur job that has no employer address to open. Nothing is fetched."""
    from autoapp.formfinder.finder import Outcome

    if link.kind == "direct":
        return Outcome("restricted", job["job_url"], None, f"{ONLY_ON_ARBEITSAGENTUR}: {link.reason}")
    if link.kind == "missing":
        return Outcome(NO_EMPLOYER_LINK, None, None, f"{NOT_FOUND_YET}: {link.reason}")
    host = bare_host(job["job_url"] or "") or "arbeitsagentur.de"
    return Outcome("restricted", None, None, f"restricted target: {host}; {NOT_SEARCHED_YET}: {link.reason}")
