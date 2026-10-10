from __future__ import annotations

import csv

import pytest

from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.tracking.csv_export import COLUMNS, export_csv, sync_csv

SPEC_COLUMNS = (
    "id status company job_title location work_mode source job_url application_url channel "
    "contact_person contact_email reference ad_salary_range salary_expectation fit_score "
    "assumed_skills needs_review date_found date_prepared date_applied folder notes form_status form_note "
    "employer_site employer_posting employer_outcome arbeitnow_url"
).split()


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "t.db")


@pytest.fixture
def repo(conn):
    return Repo(conn)


def _read(path, delimiter=";"):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def _write(path, rows, delimiter=";"):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=delimiter)
        w.writeheader()
        w.writerows(rows)


def _job(repo, status="review", **kw):
    kw.setdefault("title", "AI Engineer")
    kw.setdefault("company", "Müller & Söhne GmbH")
    return repo.insert_job(status=status, **kw)


def test_column_order_matches_spec(conn, tmp_path):
    assert COLUMNS == SPEC_COLUMNS
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        assert next(csv.reader(fh, delimiter=";")) == SPEC_COLUMNS


def test_arbeitnow_secondary_link_and_old_csv_edits(conn, repo, tmp_path):
    jid = _job(repo, job_url="https://employer.example/job", notes="before")
    repo.add_source(jid, "manual", "m1", "https://employer.example/job")
    repo.add_source(jid, "arbeitnow", "a1", "https://www.arbeitnow.com/jobs/role")
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    rows = _read(path)
    assert rows[0]["job_url"] == "https://employer.example/job"
    assert rows[0]["arbeitnow_url"] == "https://www.arbeitnow.com/jobs/role"
    rows[0].pop("arbeitnow_url")
    rows[0]["notes"] = "old csv edit"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[c for c in COLUMNS if c != "arbeitnow_url"], delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    sync_csv(conn, path)
    assert repo.get_job(jid)["notes"] == "old csv edit"


def test_invalid_arbeitnow_attribution_not_exported(conn, repo, tmp_path):
    jid = _job(repo)
    repo.add_source(jid, "arbeitnow", "bad", "https://evil.test/jobs/x")
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    assert _read(path)[0]["arbeitnow_url"] == ""


def test_bom_and_umlaut_roundtrip(conn, repo, tmp_path):
    _job(repo, location="Köln", notes="Straße; \"quoted\"\nline2")
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    row = _read(path)[0]
    assert row["company"] == "Müller & Söhne GmbH"
    assert row["location"] == "Köln"
    assert row["notes"] == "Straße; \"quoted\"\nline2"


def test_delimiter_applied(conn, repo, tmp_path):
    _job(repo)
    path = tmp_path / "a.csv"
    export_csv(conn, path, delimiter=",")
    assert _read(path, ",")[0]["company"] == "Müller & Söhne GmbH"
    assert path.read_text(encoding="utf-8-sig").splitlines()[0].startswith("id,status,company")


def test_inclusion_rule(conn, repo, tmp_path):
    for st in ("new", "filtered", "description_unavailable", "evaluation_failed", "skipped"):
        _job(repo, status=st)
    shown = [_job(repo, status=st) for st in ("review", "approved", "sent", "expired")]
    path = tmp_path / "a.csv"
    assert export_csv(conn, path) == 4
    assert {r["id"] for r in _read(path)} == set(shown)
    assert export_csv(conn, path, include_skipped=True) == 5
    assert "skipped" in {r["status"] for r in _read(path)}


def test_fields_from_related_tables(conn, repo, tmp_path):
    jid = _job(repo, status="prepared", ad_salary_min=50000, ad_salary_max=60000, job_url="https://j/1",
               application_url="https://a/1", work_mode="hybrid")  # fmt: skip
    repo.add_source(jid, "arbeitsagentur", "1")
    repo.add_source(jid, "stepstone-alert", "2")
    repo.add_evaluation(jid, fit_score=55, decision="x")
    repo.add_evaluation(jid, fit_score=77, decision="y")
    repo.set_needs_review(jid, ["free mail"])
    repo.upsert_application(jid, folder="applications/x", channel="email", contact_person="Frau Z",
                            contact_email="z@x.de", reference="R-1", salary_expectation="65000",
                            assumed_skills=["Docker", "Kubernetes"],
                            prepared_at="2026-03-05T23:30:00+00:00")  # fmt: skip
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    r = _read(path)[0]
    assert r["source"] == "arbeitsagentur, stepstone-alert"
    assert r["ad_salary_range"] == "50000-60000"
    assert r["fit_score"] == "77"
    assert r["assumed_skills"] == "Docker, Kubernetes"
    assert r["needs_review"] == "yes"
    assert r["date_prepared"] == "2026-03-06"  # 23:30 UTC is already the 6th in Berlin
    assert (r["channel"], r["contact_email"], r["salary_expectation"], r["folder"]) == (
        "email", "z@x.de", "65000", "applications/x",
    )  # fmt: skip
    assert r["date_found"] and r["date_applied"] == ""


def test_atomic_write_leaves_no_temp_files(conn, repo, tmp_path):
    _job(repo)
    export_csv(conn, tmp_path / "a.csv")
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix != ".db" and "-wal" not in p.name
                  and "-shm" not in p.name) == ["a.csv"]  # fmt: skip


def test_export_records_last_values(conn, repo, tmp_path):
    jid = _job(repo, notes="n1")
    hidden = _job(repo, status="new")
    export_csv(conn, tmp_path / "a.csv")
    j = repo.get_job(jid)
    assert (j["csv_last_status"], j["csv_last_notes"]) == ("review", "n1")
    assert repo.get_job(hidden)["csv_last_status"] is None


# --- sync -----------------------------------------------------------------


def _edit(path, job_id, **changes):
    rows = _read(path)
    for r in rows:
        if r["id"] == job_id:
            r.update(changes)
    _write(path, rows)


def test_sync_missing_file(conn, tmp_path):
    assert sync_csv(conn, tmp_path / "none.csv") == []


def test_user_edit_sent_to_interview(conn, repo, tmp_path):
    jid = _job(repo, status="sent")
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    _edit(path, jid, status="interview")
    assert sync_csv(conn, path) == []
    assert repo.get_job(jid)["status"] == "interview"
    last = repo.history(jid)[-1]
    assert (last["from_status"], last["to_status"], last["note"]) == ("sent", "interview", "edited in CSV")
    export_csv(conn, path)
    assert _read(path)[0]["status"] == "interview"
    assert sync_csv(conn, path) == []
    assert len(repo.history(jid)) == 1


def test_invalid_status_ignored_but_notes_imported(conn, repo, tmp_path):
    jid = _job(repo, status="sent")
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    _edit(path, jid, status="maybe later", notes="called HR")
    warnings = sync_csv(conn, path)
    assert len(warnings) == 1 and jid in warnings[0] and "maybe later" in warnings[0]
    job = repo.get_job(jid)
    assert job["status"] == "sent" and job["notes"] == "called HR"
    export_csv(conn, path)
    row = _read(path)[0]
    assert (row["status"], row["notes"]) == ("sent", "called HR")
    assert sync_csv(conn, path) == []


def test_stale_csv_does_not_overwrite_newer_db_status(conn, repo, tmp_path):
    jid = _job(repo, status="prepared")
    path = tmp_path / "a.csv"
    export_csv(conn, path)  # CSV says prepared
    repo.transition(jid, "sent")  # system moves on
    repo.update_job(jid, notes="db note")
    assert sync_csv(conn, path) == []
    job = repo.get_job(jid)
    assert job["status"] == "sent" and job["notes"] == "db note"
    export_csv(conn, path)
    row = _read(path)[0]
    assert (row["status"], row["notes"]) == ("sent", "db note")


def test_unknown_id_warns_and_wrong_delimiter(conn, repo, tmp_path):
    jid = _job(repo)
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    rows = _read(path)
    rows.append({**rows[0], "id": "zzzzzz"})
    _write(path, rows)
    warnings = sync_csv(conn, path)
    assert len(warnings) == 1 and "zzzzzz" in warnings[0]
    assert "id" in sync_csv(conn, path, delimiter=",")[0]
    assert repo.get_job(jid)["status"] == "review"


def test_employer_columns_are_generated_and_editing_notes_and_status_still_works(conn, tmp_path):
    repo = Repo(conn)
    job = _job(repo, status="review", notes=None)
    conn.execute("INSERT INTO employers (id, display_name, website, status, created_at, updated_at) "
                 "VALUES ('emp-1', 'Müller GmbH', 'https://mueller.example/', 'verified', 'x', 'x')")
    conn.execute("INSERT INTO employer_seeds (job_id, employer_id, outcome, posting_url, checked_at) "
                 "VALUES (?, 'emp-1', 'matched', 'https://mueller.example/job/7', 'x')", (job,))
    conn.commit()
    path = tmp_path / "a.csv"
    export_csv(conn, path)
    row = _read(path)[0]
    assert row["employer_site"] == "https://mueller.example/"
    assert row["employer_posting"] == "https://mueller.example/job/7"
    assert row["employer_outcome"] == "posting verified at the employer"
    assert row["form_status"] == ""  # a verified posting is never reported as a found form
    # The user edits notes and status, and tries to edit a generated employer column: only the former is imported.
    row.update(notes="called them", status="approved", employer_site="https://evil.example/")
    _write(path, [row])
    sync_csv(conn, path)
    assert repo.get_job(job)["notes"] == "called them" and repo.get_job(job)["status"] == "approved"
    export_csv(conn, path)
    assert _read(path)[0]["employer_site"] == "https://mueller.example/"
