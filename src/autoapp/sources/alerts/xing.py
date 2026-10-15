"""XING saved-search emails, including their opaque /m/ tracking links.

Parse the card text without following links. Opaque links and their recipient tokens
are discarded; only direct job URLs supply a stable source ID and stored address.
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from html import unescape
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from autoapp.sources.alerts._mail import html_text, link_text, plain_text
from autoapp.sources.alerts.excerpt import looks_like_alert
from autoapp.sources.alerts.models import AlertCard

_SEPARATOR = re.compile(r"^\s*[-~]{5,}\s*$", re.MULTILINE)
_DIRECT = re.compile(r"/jobs/([\w-]+)-(\d+)/?$")
_TRACKING = re.compile(r"/m/[\w-]+/?$")
_GENERIC = re.compile(r"^(?:im browser|abbestellen|unsubscribe|profil|alle (?:jobs|suchergebnisse|stellen)|jobs ansehen|"
                      r"zur suche|suchauftrag|job ansehen|jetzt bewerben|mehr erfahren|view job)\b", re.I)
_BADGE = re.compile(r"^(?:zu den ersten gehören|neu|dringend gesucht|be an early applicant)$", re.I)
_ATTRIBUTE = re.compile(r"^(?:vollzeit|teilzeit|full[ -]time|part[ -]time|home[ -]?office|remote|hybrid|"
                        r"praktikum|werkstudent|befristet|unbefristet|\d[\d.,\s]*\s*(?:€|eur))", re.I)
_TIMES = ((r"\b(?:vollzeit|full[ -]time)(?=\W|\d|$)", "full_time"),
          (r"\b(?:teilzeit|part[ -]time)(?=\W|\d|$)", "part_time"))


def _link(href: str) -> tuple[str | None, str | None] | None:
    """A validated XING job/tracking link; tracking links return no persistent identity."""
    try:
        parts = urlsplit(href.strip())
        host = (parts.hostname or "").lower()
        if (parts.scheme not in ("http", "https") or parts.username or parts.password
                or parts.port not in (None, 80, 443)
                or not (host == "xing.com" or host.endswith(".xing.com"))):
            return None
    except ValueError:
        return None
    direct = _DIRECT.fullmatch(parts.path)
    if direct:
        return direct.group(2), f"https://www.xing.com{parts.path.rstrip('/')}"
    return (None, None) if _TRACKING.fullmatch(parts.path) else None


def _lines(text: str) -> list[str]:
    return [line for raw in unescape(text).splitlines() if (line := " ".join(raw.split()))]


def _card(title: str, fields: list[str], href: str, badges: list[str]) -> AlertCard | None:
    identity = _link(href)
    if identity is None or len(title) < 4 or _GENERIC.search(title):
        return None
    # Positional fields of the observed template: company, city, then attributes.
    company = fields[0] if fields and not _ATTRIBUTE.match(fields[0]) else ""
    rest = fields[1:] if company else fields
    city = rest[0] if company and rest and not _ATTRIBUTE.match(rest[0]) else ""
    attributes = " ".join(rest[1:] if city else rest)
    times = [value for pattern, value in _TIMES if re.search(pattern, attributes, re.I)]
    work_mode = "remote" if re.search(r"\bremote\b", attributes, re.I) else (
        "hybrid" if re.search(r"home[ -]?office möglich|hybrid", attributes, re.I) else None)
    job_badges = [badge for badge in badges if badge.casefold() != "neu"]
    if identity == (None, None) and (not company or not (times or work_mode or job_badges)):
        return None  # opaque account links need additional evidence of a job card
    return AlertCard(source="xing-alert", title=title, company=company, company_unknown=not company,
                     locations=[city] if city else [], source_job_id=identity[0], url=identity[1],
                     working_time="any" if len(times) > 1 else times[0] if times else None,
                     work_mode=work_mode, badges=badges)


def _html_fields(anchor: Tag, title: str) -> tuple[list[str], list[str]]:
    """Use the nearest table containing this card, stopping before a multi-card container."""
    for table in anchor.find_parents("table", limit=5):
        targets = {str(a["href"]) for a in table.select("a[href]") if _link(str(a["href"])) is not None}
        if len(targets) > 1:
            break
        lines = _lines(table.get_text("\n", strip=True))
        if title in lines:
            at = lines.index(title)
            fields = [line for line in lines[at + 1:] if not _GENERIC.search(line)]
            if fields:
                return fields, [line for line in lines[:at] if _BADGE.fullmatch(line)]
    return [], []


def _html_cards(html: str) -> list[AlertCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for anchor in soup.select("a[href]"):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        href = str(anchor["href"])
        if len(title) < 4 or _GENERIC.search(title) or _link(href) is None:
            continue
        fields, badges = _html_fields(anchor, title)
        card = _card(title, fields, href, badges)
        if card is not None:
            cards.append(card)
    return cards


def _plain_cards(text: str) -> list[AlertCard]:
    cards = []
    for block in _SEPARATOR.split(text):
        lines = _lines(block)
        for index, line in enumerate(lines):
            if index == 0 or not line.startswith("=>"):
                continue
            card = _card(lines[index - 1], lines[index + 1:], line[2:].strip(),
                         [value for value in lines[:index - 1] if _BADGE.fullmatch(value)])
            if card is not None:
                cards.append(card)
    return cards


class XingParser:
    """Parse alerts from XING sender domains without network or API access."""

    name = "xing"

    def handles(self, from_addr: str) -> bool:
        local, separator, domain = from_addr.lower().rpartition("@")
        return bool(local and separator and (domain == "xing.com" or domain.endswith(".xing.com")))

    def is_job_alert(self, msg: EmailMessage) -> bool:
        text = link_text(msg)
        return (not text.strip() or bool(self.parse(msg))
                or looks_like_alert(str(msg["Subject"] or ""), text))

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        # Use both alternatives: a partial HTML layout must not hide readable plain
        # cards. Plain-text order also keeps per-card excerpts aligned with the mail.
        cards = _plain_cards(plain_text(msg)) + _html_cards(html_text(msg))
        unique = {}
        for card in cards:
            key = ("id", card.source_job_id) if card.source_job_id else (
                "role", card.title.casefold(), card.company.casefold(),
                tuple(city.casefold() for city in card.locations))
            unique.setdefault(key, card)
        return list(unique.values())
