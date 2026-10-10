"""Employer persistence (tasks 1.1, 1.2)."""

from __future__ import annotations

import sqlite3

import pytest

from autoapp.db import schema
from autoapp.db.schema import open_db
from autoapp.employers.names import core_name, legal_form, normalize_name
from autoapp.employers.repo import EmployerRepo, Listing


def test_migration_from_v5_keeps_everything(tmp_path):
    path = tmp_path / "a.db"
    raw = sqlite3.connect(path)
    for n, step in enumerate(schema.MIGRATIONS[:5]):
        raw.executescript(f"BEGIN;\n{step}\nPRAGMA user_version = {n + 1};\nCOMMIT;")
    raw.execute("INSERT INTO jobs (id, title, company, application_url, notes, discovered_at, updated_at) "
                "VALUES ('j1', 'Dev', 'Acme', 'https://acme.example/form', 'my note', 'x', 'x')")
    raw.commit()
    raw.close()
    conn = open_db(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(schema.MIGRATIONS) >= 6
    row = conn.execute("SELECT * FROM jobs WHERE id = 'j1'").fetchone()
    assert (row["application_url"], row["notes"]) == ("https://acme.example/form", "my note")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"employers", "employer_aliases", "employer_sites", "employer_seeds", "employer_scans",
            "employer_listings"} <= names


def test_names_keep_legal_form_but_share_a_core():
    assert normalize_name("Müller & Söhne GmbH") == "mueller and soehne gmbh"
    assert core_name("Müller & Söhne GmbH") == core_name("Mueller Soehne")
    assert legal_form("Acme AG") == "ag" and legal_form("Acme GmbH") == "gmbh"
    assert normalize_name("Acme AG") != normalize_name("Acme GmbH")


def test_round_trip_after_reopening(tmp_path):
    path = tmp_path / "a.db"
    conn = open_db(path)
    repo = EmployerRepo(conn)
    eid = repo.create_employer("CBC Cologne Business Center GmbH", status="verified",
                               website="https://www.cbc-cologne.de", provenance="search", evidence={"page": "imprint"})
    repo.add_alias(eid, "CBC Cologne", hint="köln")
    repo.add_site(eid, "https://cbc-cologne.onapply.de/", "https://cbc-cologne.onapply.de", kind="ats",
                  referrer_url="https://www.cbc-cologne.de/karriere")
    scan = repo.start_scan(eid, "run1")
    repo.save_listings(eid, scan, [Listing(url="https://cbc-cologne.onapply.de/details/1.html", title="Data Engineer",
                                           ref="346291", locations=["Köln"])])
    repo.finish_scan(scan, "complete", pages=3, openings=1)
    conn.close()

    repo = EmployerRepo(open_db(path))
    emp = repo.get(eid)
    assert (emp["status"], emp["website"], emp["provenance"]) == ("verified", "https://www.cbc-cologne.de", "search")
    assert [a["alias"] for a in repo.aliases(eid)] == ["CBC Cologne Business Center GmbH", "CBC Cologne"]
    assert repo.sites(eid)[0]["kind"] == "ats"
    assert repo.latest_scan(eid, complete_only=True)["pages"] == 3
    assert repo.listings(eid)[0].ref == "346291"


def test_namesakes_stay_separate():
    repo = EmployerRepo(open_db(":memory:"))
    a = repo.create_employer("Acme GmbH", status="verified", website="https://acme.de")
    b = repo.create_employer("Acme AG", status="verified", website="https://acme-ag.example")
    assert {r["id"] for r in repo.candidates("Acme")} == {a, b}
    repo.verify(a, "https://acme-gmbh.de", provenance="user", pinned=True)
    assert repo.get(b)["website"] == "https://acme-ag.example"


def test_shared_ats_origin_is_not_an_identity():
    repo = EmployerRepo(open_db(":memory:"))
    a = repo.create_employer("Alpha GmbH", status="verified")
    b = repo.create_employer("Beta GmbH", status="verified")
    repo.add_site(a, "https://jobs.ats.example/alpha", "https://jobs.ats.example", kind="ats", tenant_path="/alpha")
    repo.add_site(b, "https://jobs.ats.example/beta", "https://jobs.ats.example", kind="ats", tenant_path="/beta")
    assert [s["tenant_path"] for s in repo.sites(a)] == ["/alpha"]
    assert [s["tenant_path"] for s in repo.sites(b)] == ["/beta"]


def test_listing_write_failure_rolls_back_the_whole_batch():
    conn = open_db(":memory:")
    repo = EmployerRepo(conn)
    eid = repo.create_employer("Acme GmbH", status="verified")
    good = Listing(url="https://acme.example/1", title="One")
    bad = Listing(url="https://acme.example/2", title=None)  # type: ignore[arg-type]  # NOT NULL violation
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_listings(eid, None, [good, bad])
    assert repo.listings(eid) == []


def test_failure_does_not_downgrade_a_verified_employer():
    repo = EmployerRepo(open_db(":memory:"))
    eid = repo.create_employer("Acme GmbH", status="verified", website="https://acme.example")
    repo.record_failure(eid, "HTTP 403", status="unresolved")
    emp = repo.get(eid)
    assert emp["status"] == "verified" and emp["failure_reason"] == "HTTP 403"
    repo.invalidate(eid, "gone")
    assert repo.get(eid)["status"] == "invalid"
    pinned = repo.create_employer("Pinned GmbH", status="verified", pinned=True)
    repo.invalidate(pinned, "gone")
    assert repo.get(pinned)["status"] == "verified"


def test_seed_keeps_first_replaced_application_url():
    conn = open_db(":memory:")
    conn.execute("INSERT INTO jobs (id, title, company, discovered_at, updated_at) VALUES ('j1', 't', 'c', 'x', 'x')")
    repo = EmployerRepo(conn)
    repo.record_seed("j1", "matched", posting_url="https://acme.example/1", replaced_application_url="https://old")
    repo.record_seed("j1", "matched", posting_url="https://acme.example/1", replaced_application_url="https://newer")
    assert repo.seed("j1")["replaced_application_url"] == "https://old"
