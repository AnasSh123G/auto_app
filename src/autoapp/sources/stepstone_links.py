"""Resolve StepStone alert tracking links to job IDs (design decision 4).

Every link in a StepStone alert is a SparkPost click-tracking redirect. One request,
*without following redirects*, returns a ``302`` whose ``Location`` is a StepStone
**automatic-login URL**::

    https://www.stepstone.de/v2/magiclink/exchange?magicLink=<JWT>&returnUrl=/stellenangebote----13850318-inline.html?...

The ``magicLink`` JWT logs into the user's StepStone account. This module therefore:

* never requests the login URL (redirects are never followed),
* reads only ``returnUrl`` and drops the rest of the ``Location`` header immediately,
* never logs, stores or returns the header or the token,
* raises no exception whose message contains the header.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

USER_AGENT = "autoapp/0.1 (personal job search tool)"
_JOB_ID = re.compile(r"-(\d{5,})-inline\.html")
_ALLOWED_TRACKING_HOSTS = ("click.stepstone.de",)


@dataclass(frozen=True)
class ResolvedLink:
    """Outcome of resolving one card link. Never contains the login token."""

    job_id: str | None
    url: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.job_id is not None


def canonical_job_url(job_id: str) -> str:
    return f"https://www.stepstone.de/stellenangebote--{job_id}-inline.html"


def job_id_from_location(location: str | None) -> str | None:
    """Extract the job ID from a redirect ``Location`` value without keeping anything else."""
    if not location:
        return None
    try:
        parts = urlsplit(location)
    except ValueError:
        return None
    candidates: list[str] = []
    # The job path is in returnUrl when StepStone sends a login redirect ...
    for value in parse_qs(parts.query).get("returnUrl", []):
        candidates.append(value)
    # ... or directly in the path if StepStone ever redirects straight to the ad.
    if parts.netloc.endswith("stepstone.de") and "magiclink" not in parts.path.lower():
        candidates.append(parts.path)
    for c in candidates:
        m = _JOB_ID.search(c)
        if m:
            return m.group(1)
    return None


class StepStoneLinkResolver:
    """Resolves click.stepstone.de links with one non-following GET each, paced politely."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = 15.0,
    ) -> None:
        self._client = client or httpx.Client(
            follow_redirects=False, timeout=timeout_s, headers={"User-Agent": USER_AGENT}
        )
        self._delay_s = delay_s
        self._sleep = sleep
        self._requests = 0

    def resolve(self, link: str) -> ResolvedLink:
        host = urlsplit(link).netloc.lower()
        if host not in _ALLOWED_TRACKING_HOSTS:
            return ResolvedLink(None, None, f"not a StepStone tracking link (host {host or 'missing'})")
        if self._requests and self._delay_s > 0:
            self._sleep(self._delay_s)
        self._requests += 1
        try:
            response = self._client.get(link, follow_redirects=False)
        except httpx.HTTPError as err:
            return ResolvedLink(None, None, f"request failed: {type(err).__name__}")
        location = response.headers.get("location")
        status = response.status_code
        # Drop the header from our hands right away; only the ID survives.
        job_id = job_id_from_location(location) if status in (301, 302, 303, 307, 308) else None
        del location
        response.close()
        if job_id is None:
            return ResolvedLink(None, None, f"unexpected response {status} (no job ID in the redirect)")
        return ResolvedLink(job_id, canonical_job_url(job_id))

    def close(self) -> None:
        self._client.close()
