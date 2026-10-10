"""SQLite schema and ``PRAGMA user_version`` migrations (design decision 3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_V1 = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'new',
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    work_mode TEXT,
    working_time TEXT,
    contract_type TEXT,
    job_url TEXT,
    canonical_url TEXT,
    application_url TEXT,
    description TEXT,
    posted_at TEXT,
    ad_salary_min INTEGER,
    ad_salary_max INTEGER,
    salary_estimate_min INTEGER,
    salary_estimate_max INTEGER,
    fingerprint TEXT,
    group_key TEXT,
    search_profile TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notes TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    needs_review_reasons TEXT,
    csv_last_status TEXT,
    csv_last_notes TEXT
);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_canonical_url ON jobs(canonical_url);
CREATE INDEX idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX idx_jobs_group_key ON jobs(group_key);

CREATE TABLE job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_job_id TEXT,
    url TEXT,
    raw TEXT,
    seen_at TEXT NOT NULL,
    UNIQUE (source, source_job_id)
);
CREATE INDEX idx_job_sources_job ON job_sources(job_id);

CREATE TABLE job_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    city TEXT NOT NULL,
    source_job_id TEXT,
    url TEXT,
    UNIQUE (job_id, city)
);

CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    fit_score INTEGER,
    decision TEXT,
    payload TEXT,
    is_current INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_evaluations_job ON evaluations(job_id, is_current);

CREATE TABLE applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    folder TEXT,
    channel TEXT,
    contact_person TEXT,
    contact_email TEXT,
    reference TEXT,
    salary_expectation TEXT,
    assumed_skills TEXT,
    language TEXT,
    prepared_at TEXT,
    applied_at TEXT
);

CREATE TABLE send_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    recipient TEXT
);
CREATE INDEX idx_send_attempts_job ON send_attempts(job_id);

CREATE TABLE status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    at TEXT NOT NULL,
    note TEXT
);
CREATE INDEX idx_status_events_job ON status_events(job_id);
CREATE INDEX idx_status_events_at ON status_events(to_status, at);

CREATE TABLE processed_emails (
    message_id TEXT PRIMARY KEY,
    sender TEXT,
    processed_at TEXT NOT NULL,
    cards INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    run_id TEXT
);
CREATE INDEX idx_llm_usage_created ON llm_usage(created_at);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT
);
"""

# Request counts per API source and day, so free-tier limits hold across runs.
_V2 = """
CREATE TABLE api_usage (
    source TEXT NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, day)
);
"""

# Run events and live counters for the statistics page and the live run view.
_V3 = """
CREATE TABLE run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    job_id TEXT,
    company TEXT,
    title TEXT,
    detail TEXT,
    data TEXT
);
CREATE INDEX idx_run_events_run ON run_events(run_id, id);
CREATE INDEX idx_run_events_at ON run_events(at);
ALTER TABLE runs ADD COLUMN progress TEXT;
"""

# Drafts saved to the user's mailbox in review-first runs (one current draft per job).
_V4 = """
CREATE TABLE drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    recipient TEXT,
    subject TEXT,
    state TEXT NOT NULL CHECK (state IN ('saved', 'replaced')),
    created_at TEXT NOT NULL,
    replaced_at TEXT
);
CREATE INDEX idx_drafts_job ON drafts(job_id, state);
"""

# Ordered migration steps; step N brings the database from version N-1 to N.
# Never edit an released step; append a new one.
_V5 = """
ALTER TABLE applications ADD COLUMN form_entry_url TEXT;
ALTER TABLE applications ADD COLUMN form_reached_url TEXT;
ALTER TABLE applications ADD COLUMN form_status TEXT;
ALTER TABLE applications ADD COLUMN form_checked_at TEXT;
ALTER TABLE applications ADD COLUMN form_note TEXT;
"""

# Employer careers discovery (add-employer-career-discovery): verified employers, their careers
# locations, alert-job associations, scans and the listing snapshots kept between runs.
_V6 = """
CREATE TABLE employers (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    legal_name TEXT,
    website TEXT,
    status TEXT NOT NULL CHECK (status IN ('verified', 'ambiguous', 'unresolved', 'invalid')),
    provenance TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    evidence TEXT,
    failure_reason TEXT,
    failure_at TEXT,
    checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_employers_status ON employers(status);

CREATE TABLE employer_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    norm TEXT NOT NULL,
    core TEXT NOT NULL,
    hint TEXT NOT NULL DEFAULT '',
    source TEXT,
    UNIQUE (employer_id, norm, hint)
);
CREATE INDEX idx_employer_aliases_core ON employer_aliases(core);

CREATE TABLE employer_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    origin TEXT NOT NULL,
    tenant_path TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('website', 'careers', 'ats')),
    referrer_url TEXT,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'blocked', 'moved', 'invalid')),
    failure_reason TEXT,
    checked_at TEXT,
    UNIQUE (employer_id, url)
);

CREATE TABLE employer_seeds (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    employer_id TEXT REFERENCES employers(id) ON DELETE SET NULL,
    outcome TEXT NOT NULL,
    posting_url TEXT,
    posting_ref TEXT,
    reason TEXT,
    candidates TEXT,
    replaced_application_url TEXT,
    checked_at TEXT NOT NULL
);
CREATE INDEX idx_employer_seeds_employer ON employer_seeds(employer_id);

CREATE TABLE employer_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
    run_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
    pages INTEGER NOT NULL DEFAULT 0,
    openings INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    checkpoint TEXT
);
CREATE INDEX idx_employer_scans_employer ON employer_scans(employer_id, id);

CREATE TABLE employer_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
    scan_id INTEGER REFERENCES employer_scans(id) ON DELETE SET NULL,
    url TEXT NOT NULL,
    ref TEXT,
    title TEXT NOT NULL,
    locations TEXT,
    work_mode TEXT,
    description TEXT,
    posted_at TEXT,
    expires_at TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    apply_url TEXT,
    seen_at TEXT NOT NULL,
    UNIQUE (employer_id, url)
);
CREATE INDEX idx_employer_listings_ref ON employer_listings(employer_id, ref);
"""

# The direct "<company> <job title>" search per alert job (employers/posting.py): its last result.
_V7 = """
CREATE TABLE posting_searches (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    found INTEGER NOT NULL,
    url TEXT,
    reason TEXT,
    checked_at TEXT NOT NULL
);
"""

MIGRATIONS: list[str] = [_V1, _V2, _V3, _V4, _V5, _V6, _V7]


def current_version(conn: sqlite3.Connection) -> int:
    """Return the database's ``PRAGMA user_version``."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, migrations: list[str] | None = None) -> int:
    """Apply pending migration steps (each in one transaction) and return the version."""
    steps = MIGRATIONS if migrations is None else migrations
    version = current_version(conn)
    if version > len(steps):
        raise RuntimeError(
            f"database schema version {version} is newer than this program supports ({len(steps)})"
        )
    for n in range(version, len(steps)):
        conn.executescript(f"BEGIN;\n{steps[n]}\nPRAGMA user_version = {n + 1};\nCOMMIT;")
    return len(steps)


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating and migrating if needed) the database at ``path``.

    Rows are ``sqlite3.Row``, foreign keys are enforced and WAL is enabled.
    Opening an up-to-date database again is a no-op.
    """
    in_memory = str(path) == ":memory:"
    if not in_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn
