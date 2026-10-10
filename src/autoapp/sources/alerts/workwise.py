"""Workwise job-alert parser (provisional, until a real alert email is available as a template).

It picks every link to a Workwise job page (``workwise.io/job/<id>-<slug>``) out of the email, also when the
link is wrapped in a tracking address that carries it URL-encoded. The title is the link text when it reads like
a title, otherwise it is made from the address and marked as shortened; the company is not taken from the email.
The fetch step then reads the job page, which names the title, the company and the full description.
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from html import unescape
from urllib.parse import unquote

from bs4 import BeautifulSoup

from autoapp.sources.alerts._mail import html_text, plain_text
from autoapp.sources.alerts.models import AlertCard

_JOB = re.compile(r"workwise\.io/job/(\d+)(?:-([a-z0-9-]+))?", re.I)
# Link texts that are buttons, not titles.
_GENERIC = re.compile(r"^(job )?(ansehen|anschauen|details|mehr|more|view|zum job|jetzt bewerben|bewerben|apply|"
                      r"zur stelle|stelle ansehen|job ansehen|view job|mehr erfahren)\b", re.I)
SENDER_DOMAINS = ("workwise.io", "campusjaeger.de")


def _title_from_slug(slug: str | None) -> str:
    words = (slug or "").replace("-m-w-d", " (m/w/d)").replace("-w-m-d", " (w/m/d)").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else "Workwise job"


class WorkwiseParser:
    """Parses mail from any ``@workwise.io`` (or the former ``@campusjaeger.de``) address."""

    name = "workwise"

    def handles(self, from_addr: str) -> bool:
        domain = from_addr.lower().rsplit("@", 1)[-1]
        return any(domain == d or domain.endswith("." + d) for d in SENDER_DOMAINS)

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        found: dict[str, tuple[str, str | None]] = {}  # job id -> (link text, slug)
        html = html_text(msg)
        if html:
            for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
                href = unescape(unquote(unquote(str(anchor["href"]))))
                match = _JOB.search(href)
                if not match:
                    continue
                text = " ".join(anchor.get_text(" ", strip=True).split())
                job_id, slug = match.group(1), match.group(2)
                old = found.get(job_id)
                if old is None or (not old[0] and text) or (_GENERIC.match(old[0]) and text and not _GENERIC.match(text)):
                    found[job_id] = (text, slug or (old[1] if old else None))
        for match in _JOB.finditer(unquote(plain_text(msg))):
            found.setdefault(match.group(1), ("", match.group(2)))
        cards = []
        for job_id, (text, slug) in found.items():
            readable = len(text) >= 6 and not _GENERIC.match(text)
            cards.append(AlertCard(
                source="workwise-alert", title=text if readable else _title_from_slug(slug),
                title_truncated=not readable, company="", company_unknown=True, source_job_id=job_id,
                url=f"https://www.workwise.io/job/{job_id}" + (f"-{slug}" if slug else ""), section="match",
            ))
        return cards
