"""Redacted excerpts of alert emails: what is kept of an email after its cards are parsed.

An excerpt is stored per card in ``job_sources.raw["email_excerpt"]`` (about 1,500 characters) and is what the LLM
fallback and the search-by-excerpt step read later, so everything personal is removed *before* it is stored: the
profile's name, address, e-mail and phone number, every e-mail address, unsubscribe and tracking links, and the query
string of every other link (job links stay, without their query).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from email.message import EmailMessage
from urllib.parse import urlsplit

from autoapp.sources.alerts._mail import html_text, plain_text
from autoapp.sources.alerts.models import AlertCard

EXCERPT_CHARS = 1500
BODY_CHARS = 6000  # the whole redacted email, for an email that gave no card at all

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TRACKING_HOST = re.compile(r"^(click|cts|track|tracking|trk|email|links?|url|e|em|mail|mailing|r|t|go|redirect|ct|"
                            r"u\d+|mandrillapp|sendgrid)\.|(?:^|\.)(?:sendgrid|mandrillapp|list-manage|mailchimp|"
                            r"mailgun|sparkpost|braze|customer\.io)\.", re.IGNORECASE)
_TRACKING_PATH = re.compile(r"unsub|abbestell|opt-?out|austragen|preferences|einstellungen|/track|/click|/uns/|"
                            r"/e/c/|/redirect|/ctrk|/wf/click", re.IGNORECASE)
_PLACEHOLDER = "[link removed]"
# The first line of a mail's footer: nothing from here on describes a card.
_FOOTER = re.compile(r"abbestell|unsubscribe|e-mail-einstellungen|email settings|m\u00f6chten sie mehr|weitere passende jobs|"
                     r"datenschutz|privacy|hilfebereich|help center|see all jobs|view all jobs", re.IGNORECASE)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _phone_pattern(phone: str) -> re.Pattern[str] | None:
    """A pattern that matches the phone number in any common spacing (``+49 30 123456``, ``030/123456``)."""
    digits = _digits(phone)
    if len(digits) < 6:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    local = digits[2:] if digits.startswith("49") else digits.lstrip("0")
    local = local if len(local) >= 6 else digits
    body = r"[\s./()-]*".join(re.escape(d) for d in local)
    return re.compile(rf"(?:(?:\+|00)\s*49[\s./()-]*)?0?[\s./()-]*{body}")


@dataclass
class Redactor:
    """Removes personal data and tracking links from text. Build it with the profile's personal block."""

    literals: list[str] = field(default_factory=list)  # names, street, e-mail addresses: matched case-insensitively
    phones: list[str] = field(default_factory=list)

    @classmethod
    def from_personal(cls, personal: object | None, *extra_addresses: str | None) -> Redactor:
        """``personal`` is ``config.Personal`` (or anything with the same attributes); extras are mailbox addresses."""
        literals: list[str] = [a for a in extra_addresses if a]
        phones: list[str] = []
        if personal is not None:
            first = str(getattr(personal, "first_name", "") or "").strip()
            last = str(getattr(personal, "last_name", "") or "").strip()
            literals += [f"{first} {last}", f"{last} {first}", f"{last}, {first}", first, last]
            literals += [str(getattr(personal, "email", "") or ""), str(getattr(personal, "street", "") or "")]
            phone = str(getattr(personal, "phone", "") or "")
            if phone:
                phones.append(phone)
        return cls(literals=[x.strip() for x in literals if len(x.strip()) >= 2], phones=phones)

    def _link(self, match: re.Match[str]) -> str:
        url = match.group(0)
        trail = ""
        while url and url[-1] in ".,;:!?":
            trail, url = url[-1] + trail, url[:-1]
        try:
            parts = urlsplit(url)
        except ValueError:
            return _PLACEHOLDER + trail
        host = (parts.hostname or "").lower()
        if _TRACKING_HOST.search(host) or _TRACKING_PATH.search(parts.path):
            return _PLACEHOLDER + trail
        return f"{parts.scheme}://{parts.netloc}{parts.path}" + trail  # no query, no fragment

    def redact(self, text: str) -> str:
        out = _URL.sub(self._link, text)
        for phone in self.phones:
            pattern = _phone_pattern(phone)
            if pattern is not None:
                out = pattern.sub("[phone]", out)
        out = _EMAIL.sub("[email]", out)
        for literal in sorted(self.literals, key=len, reverse=True):
            out = re.sub(rf"(?<!\w){re.escape(literal)}(?!\w)", "[personal]", out, flags=re.IGNORECASE)
        return out


def _text_lines(msg: EmailMessage) -> list[str]:
    text = plain_text(msg)
    if not text.strip():
        html = html_text(msg)
        if html:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href]"):
                href = str(a["href"])
                if a.get_text(strip=True) == "" or href not in a.get_text():
                    a.append(f" {href}")  # keep link targets: they are part of what the card says
            text = soup.get_text("\n", strip=True)
    return [ln for ln in (" ".join(raw.split()) for raw in text.splitlines()) if ln and not re.fullmatch(r"-{5,}", ln)]


def redacted_body(msg: EmailMessage, redactor: Redactor, *, limit: int = BODY_CHARS) -> str:
    """The whole email as redacted text (for an email that produced no card)."""
    return "\n".join(redactor.redact(ln) for ln in _text_lines(msg))[:limit]


def _find(lines: list[str], card: AlertCard, start: int) -> int | None:
    """Index of the line where ``card`` begins at or after ``start``: its title, else the line of its job id."""
    title = card.title.strip().lower()
    if title:
        for i in range(start, len(lines)):
            if lines[i].lower().startswith(title):
                return i
        if len(title) > 12:
            for i in range(start, len(lines)):
                if title in lines[i].lower():
                    return i
    job_id = card.source_job_id
    if job_id:
        for i in range(start, len(lines)):
            if job_id in lines[i]:
                return max(start, i - 3)
    return None


def card_excerpts(msg: EmailMessage, cards: Iterable[AlertCard], redactor: Redactor,
                  *, limit: int = EXCERPT_CHARS) -> list[str]:
    """One redacted excerpt per card, in order: the card's block of the email, at most ``limit`` characters.

    A card whose block cannot be located gets the start of the redacted email instead, so no card is left without
    text for the later steps.
    """
    cards = list(cards)
    lines = [redactor.redact(ln) for ln in _text_lines(msg)]
    starts: list[int | None] = []
    at = 0
    for card in cards:
        found = _find(lines, card, at)
        starts.append(found)
        if found is not None:
            at = found + 1
    whole = "\n".join(lines)[:limit]
    out: list[str] = []
    for n, start in enumerate(starts):
        if start is None:
            out.append(whole)
            continue
        end = next((s for s in starts[n + 1:] if s is not None and s > start), len(lines))
        end = next((i for i in range(start + 1, end) if _FOOTER.search(lines[i])), end)
        out.append("\n".join(lines[start:end])[:limit])
    return out
