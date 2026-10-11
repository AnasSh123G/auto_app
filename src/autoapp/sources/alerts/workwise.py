"""Workwise job-alert parser.

The real alert email is HTML only; each job card is ONE link to ``workwise.io/jobsuche?eTT=..&id=<jobId>&lT=..``
whose text lines are the title, the company, the city and then attributes ("Homeoffice m\u00f6glich", "40 Stunden",
a salary). ``eTT`` is an e-mail tracking token and ``lT`` a login token: they are never stored, the card url is the
token-free ``https://www.workwise.io/job/<id>``. Links without ``id=`` ("Alle passenden Jobs anschauen") are no cards.

For other layouts it also picks every link to a Workwise job page (``workwise.io/job/<id>-<slug>``) out of the email, also when the
link is wrapped in a tracking address that carries it URL-encoded. The title is the link text when it reads like
a title, otherwise it is made from the address and marked as shortened. The company is the line after the title in
the card around the link when there is one; otherwise it stays unknown (the job page names it, but the search-first
alert mode does not read job-board pages).
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from html import unescape
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from autoapp.sources.alerts._mail import html_text, link_text, plain_text
from autoapp.sources.alerts.models import AlertCard

_JOB = re.compile(r"workwise\.io/job/(\d+)(?:-([a-z0-9-]+))?", re.I)
# A jobsuche link that names one job (``id=<digits>``); ``search_id=`` of the "all jobs" button does not match.
_SEARCH_JOB = re.compile(r"workwise\.io/jobsuche\?(?:[^\s\"'<>]*&)?id=(\d+)", re.I)
_HOURS = re.compile(r"^(?:(\d+)\s*bis\s*)?(\d+)\s*(?:stunden|std)\b", re.I)
_ATTRIBUTE = re.compile(r"homeoffice|home office|stunden|\u20ac|^remote$", re.I)
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


def _job_search_id(href: str) -> str | None:
    """The job id of a ``workwise.io/jobsuche?...&id=<id>`` card link, else None."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if (parts.hostname or "").lower().removeprefix("www.") != "workwise.io" or parts.path.rstrip("/") != "/jobsuche":
        return None
    ids = parse_qs(parts.query).get("id", [])
    return ids[0] if len(ids) == 1 and ids[0].isdigit() else None


def _hours_to_time(line: str) -> str | None:
    match = _HOURS.match(line)
    if not match:
        return None
    low, high = int(match.group(1) or match.group(2)), int(match.group(2))
    return "any" if low != high and low < 35 <= high else "full_time" if low >= 35 else "part_time"


def _search_card(anchor, job_id: str) -> AlertCard | None:  # noqa: ANN001
    """One card of the real layout: title, company, city, then attribute lines."""
    lines = [" ".join(x.split()) for x in anchor.get_text("\n", strip=True).split("\n")]
    lines = [x for x in lines if x]
    if len(lines) < 2:
        return None
    city = lines[2] if len(lines) > 2 and not _ATTRIBUTE.search(lines[2]) else None
    card = AlertCard(source="workwise-alert", title=lines[0], company=lines[1], company_unknown=False,
                     locations=[city] if city else [], source_job_id=job_id,
                     url=f"https://www.workwise.io/job/{job_id}", section="match")
    for line in lines[3 if city else 2:]:
        low = line.lower()
        if "homeoffice" in low or "home office" in low:
            card.work_mode = "hybrid"
        elif low == "remote":
            card.work_mode = "remote"
        elif (time := _hours_to_time(line)) is not None:
            card.working_time = time
        elif "\u20ac" not in line:  # a salary line is left out: AlertCard holds platform estimates only
            card.badges.append(line)
    return card


def _title_from_slug(slug: str | None) -> str:
    words = (slug or "").replace("-m-w-d", " (m/w/d)").replace("-w-m-d", " (w/m/d)").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else "Workwise job"


class WorkwiseParser:
    """Parses mail from any ``@workwise.io`` (or the former ``@campusjaeger.de``) address."""

    name = "workwise"

    def handles(self, from_addr: str) -> bool:
        domain = from_addr.lower().rsplit("@", 1)[-1]
        return any(domain == d or domain.endswith("." + d) for d in SENDER_DOMAINS)

    def is_job_alert(self, msg: EmailMessage) -> bool:
        text = link_text(msg)
        # a job alert links to workwise.io/job/<id> or to a jobsuche link that carries a job id
        return not text.strip() or _JOB.search(text) is not None or _SEARCH_JOB.search(text) is not None

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        found: dict[str, tuple[str, str | None]] = {}  # job id -> (link text, slug)
        companies: dict[str, str] = {}
        html = html_text(msg)
        search_cards: dict[str, AlertCard] = {}
        if html:
            for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
                href = unescape(unquote(unquote(str(anchor["href"]))))
                search_id = _job_search_id(unescape(str(anchor["href"])))
                if search_id is not None:
                    card = _search_card(anchor, search_id)
                    if card is not None and search_id not in search_cards:
                        search_cards[search_id] = card
                    continue
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
        cards = list(search_cards.values())
        for job_id, (text, slug) in found.items():
            if job_id in search_cards:
                continue
            readable = len(text) >= 6 and not _GENERIC.match(text)
            cards.append(AlertCard(
                source="workwise-alert", title=text if readable else _title_from_slug(slug),
                title_truncated=not readable, company=companies.get(job_id, ""), company_unknown=job_id not in companies,
                source_job_id=job_id,
                url=f"https://www.workwise.io/job/{job_id}" + (f"-{slug}" if slug else ""), section="match",
            ))
        return cards
