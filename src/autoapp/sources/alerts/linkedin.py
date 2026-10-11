"""LinkedIn job-alert parser (plain-text part: cards separated by dashed lines)."""

from __future__ import annotations

import re
from email.message import EmailMessage

from autoapp.sources.alerts._mail import link_text, plain_text
from autoapp.sources.alerts.models import AlertCard

_SEPARATOR = re.compile(r"^\s*-{5,}\s*$", re.MULTILINE)
_VIEW_JOB = re.compile(r"^View job:\s*(\S+)", re.IGNORECASE)
_JOB_ID = re.compile(r"/jobs/view/(\d+)")
_BADGE = re.compile(
    r"^(Apply with resume & profile|\d+ (?:school alum\w*|connections?|(?:company )?alum\w*)|"
    r"This company is actively hiring|Actively recruiting|Easy Apply|Be an early applicant)$",
    re.IGNORECASE,
)
_HEADER = re.compile(r"^(Your job alert for|New jobs? match|\d+ new jobs?)", re.IGNORECASE)


class LinkedInParser:
    """Parses mail from ``jobalerts-noreply@linkedin.com``."""

    name = "linkedin"

    def handles(self, from_addr: str) -> bool:
        return from_addr.lower() == "jobalerts-noreply@linkedin.com"

    def is_job_alert(self, msg: EmailMessage) -> bool:
        text = link_text(msg)
        return not text.strip() or _JOB_ID.search(text) is not None  # a job alert links to /jobs/view/<id>

    def parse(self, msg: EmailMessage) -> list[AlertCard]:
        cards: list[AlertCard] = []
        for block in _SEPARATOR.split(plain_text(msg)):
            card = self._parse_block(block)
            if card is not None:
                cards.append(card)
        return cards

    @staticmethod
    def _parse_block(block: str) -> AlertCard | None:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        view = next((i for i, ln in enumerate(lines) if _VIEW_JOB.match(ln)), None)
        if view is None:
            return None  # footer or header-only block
        job_match = _JOB_ID.search(_VIEW_JOB.match(lines[view]).group(1))  # type: ignore[union-attr]
        if job_match is None:
            return None
        head = [ln for ln in lines[:view] if not _HEADER.match(ln)]
        badges = [ln for ln in head if _BADGE.match(ln)]
        fields = [ln for ln in head if not _BADGE.match(ln)][-3:]
        if len(fields) < 2:
            return None
        title, company = fields[0], fields[1]
        city = fields[2] if len(fields) > 2 else ""
        job_id = job_match.group(1)
        return AlertCard(
            source="linkedin-alert",
            title=title,
            company=company,
            locations=[city] if city else [],
            source_job_id=job_id,
            url=f"https://www.linkedin.com/jobs/view/{job_id}/",
            badges=badges,
            section="match",
        )
