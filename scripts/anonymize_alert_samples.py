"""Turn the real alert emails in ``samples/`` into anonymised fixtures in ``tests/fixtures/alerts/``.

Run from the repository root: ``uv run python scripts/anonymize_alert_samples.py``.

What is kept: From, Subject, Date and the text/plain card structure. Everything else is rebuilt:
the recipient becomes ``Max Mustermann <max.mustermann@example.com>``, the Message-ID is a dummy,
all tracking headers (Received, DKIM, ARC, X-*, ...) and the HTML part are dropped, and every
tracking URL is replaced by a deterministic dummy. The samples contain personal data and must
never be copied verbatim into the repository.
"""

from __future__ import annotations

import re
import sys
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
FIXTURES = ROOT / "tests" / "fixtures" / "alerts"

DUMMY_NAME = "Max Mustermann"
DUMMY_ADDR = "max.mustermann@example.com"

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_LINKEDIN_JOB_RE = re.compile(r"/jobs/view/(\d+)")
_ADDR_RE = re.compile(r"[\w.+-]+@(?:gmail|googlemail|outlook|hotmail|live)\.[a-z.]+", re.IGNORECASE)


def _name_parts(msg: EmailMessage) -> tuple[str, list[str]]:
    """Return the recipient's display name and its words (first and last names)."""
    display, _ = parseaddr(str(msg["To"] or ""))
    return display, [p for p in re.split(r"\s+", display) if len(p) >= 3]


def _scrub_names(text: str, display: str, parts: list[str]) -> str:
    if display:
        text = re.sub(re.escape(display), DUMMY_NAME, text, flags=re.IGNORECASE)
    if parts:
        # Greeting ("Hallo Anas,") and any stray remaining occurrence of a name part.
        text = re.sub(r"(?m)^(Hallo )[^\n,]+,", rf"\1{DUMMY_NAME},", text)
        for part in parts:
            text = re.sub(rf"\b{re.escape(part)}\b", "Mustermann", text, flags=re.IGNORECASE)
    text = re.sub(r"(?m)^(This email was intended for ).*$", rf"\1{DUMMY_NAME} (Software Engineer)", text)
    return _ADDR_RE.sub(DUMMY_ADDR, text)


def _linkedin_url(match: re.Match[str]) -> str:
    url = match.group(0)
    job = _LINKEDIN_JOB_RE.search(url)
    if job:
        return f"https://www.linkedin.com/comm/jobs/view/{job.group(1)}/?trackingId=DUMMY"
    base = url.split("?", 1)[0]
    return f"{base}?trackingId=DUMMY"


def _scrub_linkedin_urls(text: str) -> str:
    return _URL_RE.sub(_linkedin_url, text)


def _scrub_stepstone_urls(text: str) -> str:
    counter = iter(range(1, 10_000))

    def repl(match: re.Match[str]) -> str:
        url = match.group(0)
        if "click.stepstone.de" not in url:
            return "https://www.stepstone.de/DUMMY"
        n = next(counter)
        return f"https://click.stepstone.de/f/a/DUMMY{n}~~/AAAA~/DUMMY{n}"

    return _URL_RE.sub(repl, text)


def indeed_dummy_link(jk: str | None) -> str:
    """A synthetic cts.indeed.com link whose payload holds only the job key."""
    import base64
    import gzip
    import json

    if not jk:
        return "https://cts.indeed.com/v3/DUMMY"
    payload = json.dumps({"u": f"https://de.indeed.com/rc/clk?jk={jk}&from=DUMMY"}).encode()
    seg = base64.urlsafe_b64encode(gzip.compress(payload, mtime=0)).decode().rstrip("=")
    return f"https://cts.indeed.com/v3/{seg}"


def _scrub_indeed(text: str) -> str:
    from autoapp.sources.alerts.indeed import job_key

    def repl(match: re.Match[str]) -> str:
        url = match.group(0)
        if "cts.indeed.com" in url:
            return indeed_dummy_link(job_key(url))
        return "https://de.indeed.com/DUMMY"

    text = re.sub(r"https?://\S+", repl, text)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.strip().lower()
        if low.startswith("halten sie ihr indeed-profil aktuell") and i + 1 < len(lines):
            lines[i + 1] = DUMMY_NAME  # the profile owner's name
        elif low.startswith("letzte jobs:"):
            lines[i] = "Letzte Jobs: Beispieljob - https://de.indeed.com/DUMMY"
        elif low.startswith("mindestgrundgehalt:"):
            lines[i] = "Mindestgrundgehalt: 50.000 € pro Jahr - https://de.indeed.com/DUMMY"
    return "\n".join(lines) + "\n"


def build(sample: EmailMessage, kind: str, message_id: str) -> EmailMessage:
    """Build an anonymised message (text/plain only) from a real sample."""
    display, parts = _name_parts(sample)
    body = sample.get_body(("plain",))
    if body is None:
        raise ValueError("sample has no text/plain part")
    text = body.get_content()
    if kind == "linkedin":
        text = _scrub_linkedin_urls(text)
    elif kind == "stepstone":
        text = _scrub_stepstone_urls(text)
    else:
        text = _scrub_indeed(text)
    text = _scrub_names(text, display, parts)
    return _compose(sample["From"], str(sample["Subject"]), str(sample["Date"]), message_id, text)


def _compose(from_: str, subject: str, date: str, message_id: str, text: str) -> EmailMessage:
    out = EmailMessage(policy=policy.default)
    out["From"] = from_
    out["To"] = f"{DUMMY_NAME} <{DUMMY_ADDR}>"
    out["Subject"] = subject
    out["Date"] = date
    out["Message-ID"] = message_id
    out.set_content(text, charset="utf-8", cte="quoted-printable")
    return out


LINKEDIN_ZERO = """\
Your job alert for Machine Learning Engineer in Germany
No new jobs match your preferences today.

---------------------------------------------------------

See all jobs on LinkedIn:  https://www.linkedin.com/comm/jobs/search-results/?trackingId=DUMMY

----------------------------------------

This email was intended for Max Mustermann (Software Engineer)
You are receiving Job Alert emails.

Manage your job alerts:  https://www.linkedin.com/comm/jobs/alerts?trackingId=DUMMY
Unsubscribe: https://www.linkedin.com/job-alert-email-unsubscribe?trackingId=DUMMY
"""

STEPSTONE_ZERO = """\
Schau dir deine neusten Treffer an!
-------------------------------------------------

Hallo Max Mustermann,

wir haben aktuell keine neuen Jobs fuer deine Suche gefunden.

Weitere passende Jobs anzeigen
https://click.stepstone.de/f/a/DUMMY1~~/AAAA~/DUMMY1

-------------------------------------------------

E-Mail-Einstellungen verwalten
https://click.stepstone.de/f/a/DUMMY2~~/AAAA~/DUMMY2

Diese E-Mail abbestellen
https://click.stepstone.de/f/a/DUMMY3~~/AAAA~/DUMMY3
"""


INDEED_ZERO = """\
Guten Tag,

Halten Sie Ihr Indeed-Profil aktuell
Max Mustermann
Profil bearbeiten: https://de.indeed.com/DUMMY

E-Mail-Einstellungen verwalten: https://de.indeed.com/DUMMY
Abbestellen: https://de.indeed.com/DUMMY
"""


def _find(kind: str) -> EmailMessage:
    needle = {"linkedin": "linkedin.com", "stepstone": "stepstone.de", "indeed": "indeed.com"}[kind]
    for path in sorted(SAMPLES.glob("*.eml")):
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        if needle in str(msg["From"]).lower():
            return msg  # type: ignore[return-value]
    raise SystemExit(f"no {kind} sample found in {SAMPLES}")


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    jobs = {
        "linkedin": (
            "linkedin_alert.eml",
            "linkedin_zero.eml",
            LINKEDIN_ZERO,
            "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
            "Your job alert for Machine Learning Engineer in Germany",
        ),
        "indeed": (
            "indeed_match.eml",
            "indeed_zero.eml",
            INDEED_ZERO,
            "Indeed <donotreply@match.indeed.com>",
            "Neue Jobvorschläge",
        ),
        "stepstone": (
            "stepstone_alert.eml",
            "stepstone_zero.eml",
            STEPSTONE_ZERO,
            "Stepstone <info@jobagent.stepstone.de>",
            "Keine neuen Treffer",
        ),
    }
    for kind, (name, zero_name, zero_text, zero_from, zero_subject) in jobs.items():
        msg = build(_find(kind), kind, f"<dummy-{kind}-alert@example.com>")
        (FIXTURES / name).write_bytes(msg.as_bytes())
        zero = _compose(
            zero_from,
            zero_subject,
            "Thu, 08 Oct 2026 07:15:39 +0000",
            f"<dummy-{kind}-zero@example.com>",
            zero_text,
        )
        (FIXTURES / zero_name).write_bytes(zero.as_bytes())
        print(f"wrote {name} and {zero_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
