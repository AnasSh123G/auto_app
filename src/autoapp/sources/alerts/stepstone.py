"""StepStone job-agent alert parser (plain-text part).

Card layout, one line each, blank lines are noise::

    [Passt hervorragend]            optional leading marker
    <title>                         "..." suffix means the title was truncated
    https://click.stepstone.de/...  tracking link, resolved to the real URL later
    <company>
    <locations>                     comma separated, may end with ", ..."
    [<n> Mitarbeiter]               company size
    [<contract type>]               "Feste Anstellung", "Ausbildung, Studium", ...
    [<work mode / working time>]    "Homeoffice möglich, Vollzeit"
    [<from> - <to> €/Jahr|Monat (geschätzt für Vollzeit)]
    [Neu]                           optional trailing marker
"""

from __future__ import annotations

import re
from email.message import EmailMessage

from autoapp.sources.alerts._mail import link_text, plain_text
from autoapp.sources.alerts.models import AlertCard

_URL = re.compile(r"^https?://(?:click|www)\.stepstone\.de/\S+$")
_JOB_LINK = re.compile(r"click\.stepstone\.de/|stepstone\.de/(?:stellenangebote|jobs?/)", re.IGNORECASE)
_DASHES = re.compile(r"^-{5,}$")
_SECTION = re.compile(r"^Andere Bewerber interessierten sich auch f\S+r diese Jobs$", re.IGNORECASE)
_FOOTER = (
    "weitere passende jobs anzeigen",
    "e-mail-einstellungen verwalten",
    "diese e-mail abbestellen",
)
_SIZE = re.compile(r"^\d[\d.]*\+?(?:\s*-\s*\d[\d.]*)?\s+Mitarbeiter", re.IGNORECASE)
_SALARY = re.compile(r"(\d[\d.]*)\s*-\s*(\d[\d.]*)\s*€\s*/\s*(Jahr|Monat)", re.IGNORECASE)
_WORK = re.compile(r"Vollzeit|Teilzeit|Homeoffice|Remote", re.IGNORECASE)
_LEADING_MARKERS = {"passt hervorragend"}
_TRAILING_MARKERS = {"neu"}
_ELLIPSIS = re.compile(r"(?:\.{3}|…)$")


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()


def _is_truncated(text: str) -> bool:
    return bool(_ELLIPSIS.search(text))


def _strip_ellipsis(text: str) -> str:
    return _ELLIPSIS.sub("", text).strip()


def _parse_amount(text: str) -> int:
    return int(text.replace(".", ""))


def _parse_work_line(line: str) -> tuple[str | None, str | None]:
    """Return (work_mode, working_time) from e.g. 'Homeoffice möglich, Teilzeit, Vo...'."""
    full = part = False
    mode: str | None = None
    for raw in line.split(","):
        token = raw.strip()
        low = token.lower()
        if "homeoffice" in low or "remote" in low:
            mode = "hybrid" if "möglich" in low else "remote"
            continue
        stem = _strip_ellipsis(token).lower() if _is_truncated(token) else low
        if not stem:
            continue
        if "vollzeit".startswith(stem):
            full = True
        elif "teilzeit".startswith(stem):
            part = True
    working_time = "any" if full and part else "full_time" if full else "part_time" if part else None
    return mode, working_time


def _parse_locations(line: str) -> tuple[list[str], bool]:
    parts = [p.strip() for p in line.split(",") if p.strip()]
    truncated = False
    out: list[str] = []
    for p in parts:
        if _is_truncated(p):
            truncated = True  # drop the ellipsis and any half-cut name such as "Hambur..."
        else:
            out.append(p)
    return out, truncated


class StepStoneParser:
    """Parses mail from any ``@jobagent.stepstone.de`` address."""

    name = "stepstone"

    def handles(self, from_addr: str) -> bool:
        return from_addr.lower().endswith("@jobagent.stepstone.de")

    def is_job_alert(self, msg: EmailMessage) -> bool:
        text = link_text(msg)
        return not text.strip() or _JOB_LINK.search(text) is not None  # a job card links through click.stepstone.de

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        lines = [n for n in (_norm(ln) for ln in plain_text(msg).splitlines()) if n]
        stop = next((i for i, ln in enumerate(lines) if ln.lower() in _FOOTER), len(lines))
        section_idx = next((i for i, ln in enumerate(lines[:stop]) if _SECTION.match(ln)), None)
        titles = [i for i in range(stop - 1) if _URL.match(lines[i + 1]) and not _URL.match(lines[i])]
        cards: list[AlertCard] = []
        for n, i in enumerate(titles):
            bounds = [stop, *titles[n + 1 : n + 2]]
            if section_idx is not None and section_idx > i:
                bounds.append(section_idx)
            end = min(bounds)
            recommended = section_idx is not None and i > section_idx
            card = self._parse_card(lines, i, end, recommended, first=(n == 0))
            if card is not None:
                cards.append(card)
        return cards

    @staticmethod
    def _parse_card(lines: list[str], i: int, end: int, recommended: bool, first: bool) -> AlertCard | None:
        raw_title = lines[i]
        if _DASHES.match(raw_title) or raw_title.lower() in _LEADING_MARKERS | _TRAILING_MARKERS:
            return None
        badges: list[str] = []
        # Leading markers sit directly above the title.
        j = i - 1
        while j >= 0 and lines[j].lower() in _LEADING_MARKERS:
            badges.insert(0, lines[j])
            j -= 1
        if first and j >= 0 and lines[j].lower() in _TRAILING_MARKERS:
            badges.insert(0, lines[j])
        body: list[str] = []
        for ln in lines[i + 2 : end]:
            low = ln.lower()
            if low in _TRAILING_MARKERS:
                badges.append(ln)
            elif low in _LEADING_MARKERS or _DASHES.match(ln):
                continue
            else:
                body.append(ln)
        if not body:
            return None
        title_truncated = _is_truncated(raw_title)
        title = _strip_ellipsis(raw_title) if title_truncated else raw_title
        company = body.pop(0)
        card = AlertCard(
            source="stepstone-alert-recommended" if recommended else "stepstone-alert",
            title=title,
            title_truncated=title_truncated,
            company=company,
            url=lines[i + 1],
            badges=badges,
            section="recommended" if recommended else "match",
        )
        if body and not (_SIZE.match(body[0]) or _SALARY.search(body[0]) or _WORK.search(body[0])):
            card.locations, card.locations_truncated = _parse_locations(body.pop(0))
        for ln in body:
            salary = _SALARY.search(ln)
            if salary:
                factor = 12 if salary.group(3).lower() == "monat" else 1
                card.salary_estimate_min = _parse_amount(salary.group(1)) * factor
                card.salary_estimate_max = _parse_amount(salary.group(2)) * factor
            elif _SIZE.match(ln):
                card.company_size = ln
            elif _WORK.search(ln):
                card.work_mode, card.working_time = _parse_work_line(ln)
            elif card.contract_type is None:
                card.contract_type = ln
        return card
