from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from freezegun import freeze_time

from autoapp.db.repo import DbUsageRecorder, Repo
from autoapp.db.schema import open_db
from autoapp.db.status import Status
from autoapp.llm.base import Usage


@pytest.fixture
def repo(tmp_path):
    return Repo(open_db(tmp_path / "t.db"))


def _job(repo, **kw):
    kw.setdefault("title", "AI Engineer")
    kw.setdefault("company", "4flow")
    return repo.insert_job(**kw)


def test_insert_and_get(repo):
    jid = _job(repo, location="Berlin", fingerprint="fp1", canonical_url="https://x/1")
    assert re.fullmatch(r"[0-9a-f]{6}", jid)
    job = repo.get_job(jid)
    assert job["status"] == "new" and job["location"] == "Berlin"
    assert job["discovered_at"].endswith("+00:00")
    assert repo.get_job("nope00") is None


def test_insert_validates(repo):
    with pytest.raises(ValueError):
        repo.insert_job(title="x", company="y", status="bogus")
    with pytest.raises(TypeError):
        repo.insert_job(title="x", company="y", nonsense=1)
    with pytest.raises(ValueError):
        repo.insert_job(title="x", company="")


def test_ids_unique(repo):
    ids = {_job(repo) for _ in range(50)}
    assert len(ids) == 50


def test_find_by_keys(repo):
    jid = _job(repo, fingerprint="fp1", canonical_url="https://x/1", group_key="g1")
    repo.add_source(jid, "adzuna", "42", "https://adzuna/42", raw={"a": 1})
    assert repo.find_by_source("adzuna", "42")["id"] == jid
    assert repo.find_by_source("adzuna", "43") is None
    assert repo.find_by_canonical_url("https://x/1")["id"] == jid
    assert repo.find_by_fingerprint("fp1")["id"] == jid
    assert repo.find_by_group_key("g1")["id"] == jid
    assert repo.find_by_fingerprint("zzz") is None


def test_group_key_window(repo):
    with freeze_time("2026-01-01"):
        jid = _job(repo, group_key="g")
    with freeze_time("2026-01-20"):
        assert repo.find_by_group_key("g")["id"] == jid
    with freeze_time("2026-03-01"):
        assert repo.find_by_group_key("g") is None
        assert repo.find_by_group_key("g", now=datetime(2026, 1, 15, tzinfo=UTC))["id"] == jid


def test_add_source_dedups(repo):
    jid = _job(repo)
    assert repo.add_source(jid, "linkedin-alert", "9", "u") is True
    assert repo.add_source(jid, "linkedin-alert", "9", "u") is False
    assert repo.add_source(jid, "manual") is True
    assert len(repo.sources(jid)) == 2


def test_locations(repo):
    jid = _job(repo, location="Düsseldorf")
    assert repo.add_location(jid, "Rüsselsheim", "77", "https://u")
    assert not repo.add_location(jid, "Rüsselsheim")
    assert [r["city"] for r in repo.locations(jid)] == ["Rüsselsheim"]


def test_history_has_three_timestamped_events(repo):
    jid = _job(repo, status=Status.REVIEW)
    with freeze_time("2026-02-01 10:00:00"):
        repo.transition(jid, "approved")
    with freeze_time("2026-02-01 11:00:00"):
        repo.transition(jid, Status.PREPARED)
    with freeze_time("2026-02-02 09:30:00"):
        repo.transition(jid, Status.SENT, note="via smtp")
    events = repo.history(jid)
    assert [(e["from_status"], e["to_status"]) for e in events] == [
        ("review", "approved"), ("approved", "prepared"), ("prepared", "sent"),
    ]  # fmt: skip
    assert [e["at"] for e in events] == [
        "2026-02-01T10:00:00+00:00", "2026-02-01T11:00:00+00:00", "2026-02-02T09:30:00+00:00",
    ]  # fmt: skip
    assert events[2]["note"] == "via smtp"
    assert repo.get_job(jid)["status"] == "sent"


def test_unknown_status_rejected(repo):
    jid = _job(repo)
    with pytest.raises(ValueError):
        repo.transition(jid, "maybe later")
    assert repo.get_job(jid)["status"] == "new"
    assert repo.history(jid) == []
    with pytest.raises(ValueError):
        repo.jobs_by_status("bogus")
    with pytest.raises(KeyError):
        repo.transition("nope00", "sent")


def test_transition_to_same_status_is_noop(repo):
    jid = _job(repo)
    assert repo.transition(jid, "new") is False
    assert repo.history(jid) == []


def test_jobs_by_status(repo):
    a = _job(repo, status="review")
    b = _job(repo, status="approved")
    _job(repo, status="new")
    assert {j["id"] for j in repo.jobs_by_status("review", Status.APPROVED)} == {a, b}
    assert repo.jobs_by_status() == []


def test_needs_review(repo):
    jid = _job(repo)
    repo.set_needs_review(jid, ["free-mail address", "assumed skill"])
    job = repo.get_job(jid)
    assert job["needs_review"] == 1
    assert job["needs_review_reasons"] == '["free-mail address", "assumed skill"]'
    repo.set_needs_review(jid, [])
    job = repo.get_job(jid)
    assert job["needs_review"] == 0 and job["needs_review_reasons"] is None


def test_evaluation_current_flag(repo):
    jid = _job(repo)
    repo.add_evaluation(jid, fit_score=60, decision="review", payload={"x": 1})
    repo.add_evaluation(jid, fit_score=80, decision="approve")
    assert repo.current_evaluation(jid)["fit_score"] == 80
    assert repo.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 2


def test_upsert_application(repo):
    jid = _job(repo)
    repo.upsert_application(jid, folder="a/b", assumed_skills=["Docker"])
    repo.upsert_application(jid, channel="email")
    app = repo.get_application(jid)
    assert app["folder"] == "a/b" and app["channel"] == "email"
    assert app["assumed_skills"] == '["Docker"]'


def test_db_usage_recorder(repo):
    repo.start_run("run-1")
    rec = DbUsageRecorder(repo.conn, "run-1")
    rec.record(Usage("anthropic", "m", "evaluate", 10, 20, 30, 40,
                     created_at=datetime(2026, 1, 1, 12, tzinfo=UTC)))  # fmt: skip
    row = repo.conn.execute("SELECT * FROM llm_usage").fetchone()
    assert tuple(row)[1:] == ("anthropic", "m", "evaluate", 10, 20, 30, 40, "2026-01-01T12:00:00+00:00", "run-1")
    repo.finish_run("run-1", {"jobs": 3})
    assert repo.conn.execute("SELECT finished_at, summary FROM runs").fetchone()["summary"] == '{"jobs": 3}'
