"""Workwise job-alert parser (provisional, until a real alert email is available as a template).

It picks every link to a Workwise job page (``workwise.io/job/<id>-<slug>``) out of the email, also when the
link is wrapped in a tracking address that carries it URL-encoded. The title is the link text when it reads like
a title, otherwise it is made from the address and marked as shortened. The company is the line after the title in
the card around the link when there is one; otherwise it stays unknown (the job page names it, but the search-first
alert mode does not read job-board pages).
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
# Lines of a card that are not a company: buttons, badges, locations, salaries, dates.
_NOT_COMPANY = re.compile(r"^(neu|new|remote|hybrid|vollzeit|teilzeit|werkstudent\w*|praktikum|\d+[\d.,\s]*(€|eur|tage|days?|h)\b|"
                          r"vor \d+|\d{1,2}\.\d{1,2}\.)", re.I)


def _company_near(anchor, text: str) -> str | None:  # noqa: ANN001
    """The company line of the card around a job link: the first plausible line after the title line."""
    node = anchor
    for _ in range(4):
        node = node.parent
        if node is None or node.name in ("body", "html"):
            return None
        if len({a.get("href") for a in node.select("a[href]") if _JOB.search(unescape(unquote(unquote(str(a["href"]))))) }) > 1:
            return None  # reached a container holding several cards
        lines = [" ".join(line.split()) for line in node.get_text("\n", strip=True).split("\n")]
        lines = [line for line in lines if line]
        if text and text in lines:
            for line in lines[lines.index(text) + 1:]:
                if _GENERIC.match(line) or _NOT_COMPANY.match(line) or len(line) < 2 or len(line) > 80:
                    continue
                return line
    return None


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
        companies: dict[str, str] = {}
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
                if job_id not in companies and text and not _GENERIC.match(text):
                    company = _company_near(anchor, text)
                    if company:
                        companies[job_id] = company
        for match in _JOB.finditer(unquote(plain_text(msg))):
            found.setdefault(match.group(1), ("", match.group(2)))
        cards = []
        for job_id, (text, slug) in found.items():
            readable = len(text) >= 6 and not _GENERIC.match(text)
            cards.append(AlertCard(
                source="workwise-alert", title=text if readable else _title_from_slug(slug),
                title_truncated=not readable, company=companies.get(job_id, ""), company_unknown=job_id not in companies,
                source_job_id=job_id,
                url=f"https://www.workwise.io/job/{job_id}" + (f"-{slug}" if slug else ""), section="match",
            ))
        return cards
