"""applications.csv export and two-way sync of ``status`` and ``notes`` (design decision 13)."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autoapp.db.repo import Repo
from autoapp.db.status import CSV_STATUSES, Status
from autoapp.formfinder.finder import LABELS
from autoapp.sources.arbeitnow import source_link

COLUMNS = [
    "id", "status", "company", "job_title", "location", "work_mode", "source",
    "job_url", "application_url", "channel", "contact_person", "contact_email", "reference",
    "ad_salary_range", "salary_expectation", "fit_score", "assumed_skills", "needs_review",
    "date_found", "date_prepared", "date_applied", "folder", "notes", "form_status", "form_note",
    "employer_site", "employer_posting", "employer_outcome",
    "arbeitnow_url",
]  # fmt: skip

EMPLOYER_OUTCOMES = {
    "matched": "posting verified at the employer", "conflict": "requisition conflict, check it",
    "unresolved": "several postings fit, not chosen", "not_found": "not on the careers site",
    "not_found_partial": "not seen (partial scan)", "employer_ambiguous": "employer name ambiguous",
    "employer_unresolved": "employer site not verified", "scan_failed": "careers site not readable",
}

EDIT_NOTE = "edited in CSV"
_BERLIN = ZoneInfo("Europe/Berlin")

_QUERY = """
SELECT j.*, a.folder, a.channel, a.contact_person, a.contact_email, a.reference,
       a.salary_expectation, a.assumed_skills, a.prepared_at, a.applied_at, a.form_status, a.form_note,
       es.outcome AS employer_outcome, es.posting_url AS employer_posting, em.website AS employer_site,
       (SELECT e.fit_score FROM evaluations e WHERE e.job_id = j.id AND e.is_current = 1
        ORDER BY e.id DESC LIMIT 1) AS fit_score,
       (SELECT group_concat(source, ', ') FROM
          (SELECT DISTINCT s.source AS source FROM job_sources s WHERE s.job_id = j.id ORDER BY s.id)
       ) AS sources
FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
     LEFT JOIN employer_seeds es ON es.job_id = j.id LEFT JOIN employers em ON em.id = es.employer_id
WHERE j.status IN ({marks})
ORDER BY j.discovered_at, j.id
"""


def _date(iso: str | None) -> str:
    """Local (Berlin) calendar date of a UTC ISO timestamp."""
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_BERLIN).date().isoformat()


def _salary_range(low: int | None, high: int | None) -> str:
    if low is None and high is None:
        return ""
    if low is None or high is None or low == high:
        return str(low if low is not None else high)
    return f"{low}-{high}"


def _skills(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    return ", ".join(str(s) for s in data) if isinstance(data, list) else str(data)


def _row(r: sqlite3.Row, arbeitnow_url: str | None = None) -> dict[str, str]:
    def s(v: object) -> str:
        return "" if v is None else str(v)

    return {
        "id": r["id"],
        "status": r["status"],
        "company": s(r["company"]),
        "job_title": s(r["title"]),
        "location": s(r["location"]),
        "work_mode": s(r["work_mode"]),
        "source": s(r["sources"]),
        "arbeitnow_url": arbeitnow_url or "",
        "job_url": s(r["job_url"]),
        "application_url": s(r["application_url"]),
        "channel": s(r["channel"]),
        "contact_person": s(r["contact_person"]),
        "contact_email": s(r["contact_email"]),
        "reference": s(r["reference"]),
        "ad_salary_range": _salary_range(r["ad_salary_min"], r["ad_salary_max"]),
        "salary_expectation": s(r["salary_expectation"]),
        "fit_score": s(r["fit_score"]),
        "assumed_skills": _skills(r["assumed_skills"]),
        "needs_review": "yes" if r["needs_review"] else "",
        "date_found": _date(r["discovered_at"]),
        "date_prepared": _date(r["prepared_at"]),
        "date_applied": _date(r["applied_at"]),
        "folder": s(r["folder"]),
        "notes": s(r["notes"]),
        "form_status": LABELS[r["form_status"]] if r["form_status"] else "",
        "form_note": s(r["form_note"]),
        "employer_site": s(r["employer_site"]),
        "employer_posting": s(r["employer_posting"]),
        "employer_outcome": EMPLOYER_OUTCOMES.get(r["employer_outcome"] or "", s(r["employer_outcome"])),
    }


def export_csv(
    conn: sqlite3.Connection,
    path: Path | str,
    delimiter: str = ";",
    include_skipped: bool = False,
) -> int:
    """Write the CSV atomically (UTF-8 with BOM) and record what was exported.

    Rows are jobs at ``review`` or later (plus ``skipped`` if ``include_skipped``).
    Returns the number of rows written.
    """
    path = Path(path)
    statuses = list(CSV_STATUSES) + ([Status.SKIPPED] if include_skipped else [])
    values = [s.value for s in statuses]
    rows = conn.execute(_QUERY.format(marks=", ".join("?" for _ in values)), values).fetchall()

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=delimiter)
            writer.writeheader()
            for r in rows:
                sources = conn.execute("SELECT source, url FROM job_sources WHERE job_id=? ORDER BY id",
                                       (r["id"],)).fetchall()
                writer.writerow(_row(r, source_link(sources)))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    with conn:
        conn.execute("UPDATE jobs SET csv_last_status = NULL, csv_last_notes = NULL")
        conn.executemany(
            "UPDATE jobs SET csv_last_status = ?, csv_last_notes = ? WHERE id = ?",
            [(r["status"], r["notes"] or "", r["id"]) for r in rows],
        )
    return len(rows)


def sync_csv(conn: sqlite3.Connection, path: Path | str, delimiter: str = ";") -> list[str]:
    """Import the user's edits to ``status`` and ``notes`` from the existing CSV.

    A cell differing from the value last exported is a user edit; an unchanged cell
    means the database wins. Returns warnings (invalid statuses, unknown rows).
    """
    path = Path(path)
    if not path.exists():
        return []
    warnings: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        if reader.fieldnames is None or "id" not in reader.fieldnames:
            return [f"{path.name}: no 'id' column found (wrong delimiter?); edits not imported"]
        csv_rows = list(reader)

    repo = Repo(conn)
    for row in csv_rows:
        job_id = (row.get("id") or "").strip()
        if not job_id:
            continue
        job = repo.get_job(job_id)
        if job is None:
            warnings.append(f"row {job_id}: unknown job id, ignored")
            continue
        if job["csv_last_status"] is None:
            continue  # never exported: nothing to compare against

        csv_status = (row.get("status") or "").strip()
        if "status" in row and csv_status != job["csv_last_status"]:
            try:
                repo.transition(job_id, Status.parse(csv_status), note=EDIT_NOTE)
            except ValueError:
                warnings.append(
                    f"row {job_id}: ignored invalid status {csv_status!r} "
                    f"(keeping {job['status']!r}); notes edits are still imported"
                )

        csv_notes = row.get("notes") or ""
        if "notes" in row and csv_notes != (job["csv_last_notes"] or ""):
            with conn:
                conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (csv_notes or None, job_id))
    return warnings
