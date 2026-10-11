"""Parser for Indeed emails (job matches from ``match.indeed.com``, alerts from other
``indeed.com`` senders).

Indeed links go through ``cts.indeed.com/v3/<payload>``, where the payload is
base64url-encoded gzip JSON whose ``u`` field is the target URL containing the job key
``jk``. The key is decoded **offline**: no request is made, and nothing else from the
payload (account ID, click tokens) is kept.

Card layout in the text part (one or more cards)::

    <title>
    <company>
    <location>
    Arbeitsort: Hybrides Arbeiten       (optional, since 2026-10)
    Anstellungsart: Vollzeit            (optional)
    Leistungen:                         (optional, followed by "- ..." bullets)
    Job anzeigen: <tracking link>

The footer ("Letzte Jobs: ...", "Mindestgrundgehalt: ...") describes the user's own
Indeed profile and never becomes a card.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit

from autoapp.sources.alerts._mail import link_text, plain_text
from autoapp.sources.alerts.models import AlertCard

_ANCHOR = re.compile(r"^(Job anzeigen|View job|Stelle ansehen)\s*:\s*(\S+)", re.IGNORECASE)
_ATTRIBUTE = re.compile(r"^(Anstellungsart|Job type|Leistungen|Benefits|Gehalt|Salary|Arbeitsort|Work location|"
                        r"Arbeitszeit|Schedule)\s*:", re.IGNORECASE)
_BULLET = re.compile(r"^[-•*]\s+")
_JK = re.compile(r"^[0-9a-f]{16}$")
_POSTCODE = re.compile(r"^\d{5}\s+")
_CONTRACT_WORDS = ("fest", "befrist", "unbefrist", "freelance", "freiberuf", "praktikum", "ausbildung",
                   "werkstudent", "permanent", "temporary", "contract", "internship", "minijob")
_DIRECT_JOB = re.compile(r"indeed\.[a-z.]+/(?:viewjob|rc/clk|pagead/clk)", re.IGNORECASE)
_URLS = re.compile(r"https?://[^\s<>\"')\]]+")
_MONEY = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)\s*€")


def job_key(url: str) -> str | None:
    """The Indeed job key from a tracking or direct link, decoded locally."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.lower()
    if host == "cts.indeed.com" and "/v3/" in parts.path:
        seg = parts.path.split("/v3/", 1)[1].split("/", 1)[0]
        try:
            data = json.loads(gzip.decompress(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))))
        except (ValueError, OSError, json.JSONDecodeError):
            return None
        target = data.get("u") if isinstance(data, dict) else None
        return job_key(target) if isinstance(target, str) and "cts.indeed.com" not in target else None
    if host.endswith("indeed.com") or host.endswith("indeed.de"):
        jk = (parse_qs(parts.query).get("jk") or [None])[0]
        return jk if jk and _JK.match(jk) else None
    return None


def canonical_job_url(jk: str) -> str:
    return f"https://de.indeed.com/viewjob?jk={jk}"


def _working_time(text: str) -> str | None:
    t = text.lower()
    full, part = "vollzeit" in t or "full-time" in t or "full time" in t, "teilzeit" in t or "part-time" in t
    return "any" if full and part else "full_time" if full else "part_time" if part else None


def _work_mode(place: str) -> str | None:
    """``Arbeitsort: Hybrides Arbeiten`` / ``Remote`` / ``Vor Ort``."""
    if "hybrid" in place:
        return "hybrid"
    if "remote" in place or "homeoffice" in place or "home office" in place:
        return "remote"
    if "vor ort" in place or "on-site" in place or "onsite" in place:
        return "onsite"
    return None


def _estimate(lines: list[str]) -> tuple[int | None, int | None]:
    """Only salaries Indeed marks as estimated; employer figures come from the job page."""
    for ln in lines:
        if "€" in ln and ("geschätzt" in ln.lower() or "estimated" in ln.lower()):
            nums = [int(n.replace(".", "")) for n in _MONEY.findall(ln)]
            if nums:
                mult = 12 if "monat" in ln.lower() or "month" in ln.lower() else 1
                return min(nums) * mult, max(nums) * mult
    return None, None


class IndeedParser:
    name = "indeed"

    def handles(self, from_addr: str) -> bool:
        domain = from_addr.rsplit("@", 1)[-1].lower()
        return domain == "indeed.com" or domain.endswith(".indeed.com")

    def is_job_alert(self, msg: EmailMessage) -> bool:
        text = link_text(msg)
        if not text.strip() or _DIRECT_JOB.search(text):
            return True
        # Tracking links carry the job key in their payload; one without a key (verify, unsubscribe) is no job.
        return any(job_key(url) for url in _URLS.findall(text))

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        from autoapp.sources.alerts._mail import from_address

        match_mail = from_address(msg).endswith("@match.indeed.com")
        lines = [ln.strip() for ln in plain_text(msg).splitlines()]
        cards: list[AlertCard] = []
        seen: set[str] = set()
        block_start = 0
        for i, line in enumerate(lines):
            m = _ANCHOR.match(line)
            if not m:
                continue
            block = [ln for ln in lines[block_start:i] if ln]
            block_start = i + 1
            card = self._card(block, m.group(2), match_mail)
            if card is None or (card.source_job_id or card.title) in seen:
                continue
            seen.add(card.source_job_id or card.title)
            cards.append(card)
        return cards

    @staticmethod
    def _card(block: list[str], link: str, match_mail: bool) -> AlertCard | None:
        first_attr = next((k for k, ln in enumerate(block) if _ATTRIBUTE.match(ln) or _BULLET.match(ln)), len(block))
        head = block[:first_attr]
        attrs = block[first_attr:]
        if len(head) < 2:
            return None
        if len(head) >= 3:
            title, company, location = head[-3], head[-2], head[-1]
        else:
            title, company, location = head[-2], head[-1], ""
        location = _POSTCODE.sub("", location)
        employment = next((ln.split(":", 1)[1].strip() for ln in attrs
                           if ln.lower().startswith(("anstellungsart", "job type"))), None)
        benefits = " ".join(attrs).lower()
        place = next((ln.split(":", 1)[1].strip().lower() for ln in attrs
                      if ln.lower().startswith(("arbeitsort", "work location"))), "")
        low, high = _estimate(attrs)
        jk = job_key(link)
        return AlertCard(
            source="indeed-match" if match_mail else "indeed-alert",
            title=title,
            company=company,
            locations=[location] if location else [],
            source_job_id=jk,
            url=canonical_job_url(jk) if jk else None,
            contract_type=employment if employment and any(w in employment.lower() for w in _CONTRACT_WORDS) else None,
            working_time=_working_time(employment or ""),
            work_mode=_work_mode(place) or ("hybrid" if "homeoffice" in benefits or "home office" in benefits else None),
            salary_estimate_min=low,
            salary_estimate_max=high,
            section="recommended" if match_mail else "match",
        )
