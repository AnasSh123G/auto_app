from __future__ import annotations

import csv
import sqlite3

import pytest
from freezegun import freeze_time
from rich.console import Console
from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.tracking.commands import list_by_status, mark, render_status, status_summary

runner = CliRunner()


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "t.db")


def _job(repo, status, **kw):
    return repo.insert_job(status=status, title=kw.pop("title", "Dev"), company=kw.pop("company", "ACME"), **kw)


def test_status_summary(conn):
    repo = Repo(conn)
    a = _job(repo, "approved")
    b = _job(repo, "approved")
    c = _job(repo, "prepared")
    r = _job(repo, "review")
    done = _job(repo, "sent")
    repo.set_needs_review(c, ["free mail"])
    repo.set_needs_review(done, ["old flag"])  # closed: not in queue
    with freeze_time("2026-05-10 21:30:00"):  # 23:30 Berlin on the 10th
        repo.transition(a, "sent")
    with freeze_time("2026-05-10 22:30:00"):  # 00:30 Berlin on the 11th
        repo.transition(b, "sent")
    with freeze_time("2026-05-11 08:00:00"):
        s = status_summary(conn)
    assert s.counts["sent"] == 3 and s.counts["prepared"] == 1 and s.counts["new"] == 0
    assert s.sent_today == 1  # only b; a was sent on the previous Berlin day
    assert {j["id"] for j in s.review_queue} == {r, c}
    out = Console(record=True, width=120)
    out.print(render_status(s))
    text = out.export_text()
    assert "Applications sent today: 1" in text and "Review queue: 2" in text


def test_list_and_mark(conn):
    repo = Repo(conn)
    jid = _job(repo, "sent")
    assert [j["id"] for j in list_by_status(conn, "sent")] == [jid]
    with pytest.raises(ValueError):
        list_by_status(conn, "bogus")
    mark(conn, jid, "rejected", "Absage")
    mark(conn, jid, "withdrawn", "later")
    job = repo.get_job(jid)
    assert job["status"] == "withdrawn" and job["notes"] == "Absage; later"
    assert repo.history(jid)[0]["note"] == "Absage"
    with pytest.raises(KeyError):
        mark(conn, "nope00", "sent")
    with pytest.raises(ValueError):
        mark(conn, jid, "maybe later")


# --- CLI ------------------------------------------------------------------


@pytest.fixture
def ws_args(tmp_path):
    return ["--workspace", str(tmp_path)]


def _seed(tmp_path):
    conn = open_db(tmp_path / "autoapp.db")
    repo = Repo(conn)
    jid = _job(repo, "sent", title="AI Engineer", company="Müller & Söhne GmbH")
    rev = _job(repo, "review", title="Data Scientist", company="Foo AG")
    conn.close()
    return jid, rev


def _csv_rows(tmp_path):
    with (tmp_path / "applications.csv").open(encoding="utf-8-sig", newline="") as fh:
        return {r["id"]: r for r in csv.DictReader(fh, delimiter=";")}


def test_cli_status_and_list(tmp_path, ws_args):
    jid, rev = _seed(tmp_path)
    res = runner.invoke(app, [*ws_args, "status"])
    assert res.exit_code == 0, res.output
    assert "sent" in res.output and "Review queue: 1" in res.output
    res = runner.invoke(app, [*ws_args, "list", "--status", "review"])
    assert res.exit_code == 0 and rev in res.output and "Data Scientist" in res.output
    assert jid not in res.output
    res = runner.invoke(app, [*ws_args, "list", "--status", "bogus"])
    assert res.exit_code == 2 and "unknown status" in res.output


def test_cli_status_on_empty_workspace(tmp_path, ws_args):
    res = runner.invoke(app, [*ws_args, "status"])
    assert res.exit_code == 0 and "no jobs yet" in res.output


def test_cli_mark_rejected_reflected_in_csv(tmp_path, ws_args):
    jid, rev = _seed(tmp_path)
    res = runner.invoke(app, [*ws_args, "mark", jid, "rejected", "--note", "Absage per Mail"])
    assert res.exit_code == 0, res.output
    rows = _csv_rows(tmp_path)
    assert rows[jid]["status"] == "rejected" and rows[jid]["notes"] == "Absage per Mail"
    assert rows[jid]["company"] == "Müller & Söhne GmbH"
    assert rows[rev]["status"] == "review"
    conn = open_db(tmp_path / "autoapp.db")
    assert Repo(conn).get_job(jid)["status"] == "rejected"


def test_cli_mark_errors(tmp_path, ws_args):
    jid, _ = _seed(tmp_path)
    res = runner.invoke(app, [*ws_args, "mark", jid, "maybe later"])
    assert res.exit_code == 2 and "unknown status" in res.output
    res = runner.invoke(app, [*ws_args, "mark", "nope00", "sent"])
    assert res.exit_code == 1 and "no job" in res.output
    assert not (tmp_path / "applications.csv").exists()


def test_cli_imports_csv_edit_before_mark(tmp_path, ws_args):
    jid, rev = _seed(tmp_path)
    assert runner.invoke(app, [*ws_args, "mark", rev, "approved"]).exit_code == 0
    path = tmp_path / "applications.csv"
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    cols = lines[0].split(";")
    out = [lines[0]]
    for line in lines[1:]:
        cells = line.split(";")
        if cells[0] == jid:
            cells[cols.index("status")] = "interview"
        out.append(";".join(cells))
    path.write_text("\n".join(out) + "\n", encoding="utf-8-sig")
    res = runner.invoke(app, [*ws_args, "mark", rev, "prepared"])
    assert res.exit_code == 0, res.output
    rows = _csv_rows(tmp_path)
    assert rows[jid]["status"] == "interview" and rows[rev]["status"] == "prepared"
    conn = sqlite3.connect(tmp_path / "autoapp.db")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0] == "interview"


def test_status_summary_reports_llm_cost(tmp_path):
    from datetime import UTC, datetime

    from autoapp.db.repo import DbUsageRecorder, Repo
    from autoapp.db.schema import open_db
    from autoapp.llm.base import Usage
    from autoapp.llm.cost import PriceTable, format_cost
    from autoapp.tracking.commands import status_summary

    conn = open_db(tmp_path / "t.db")
    run_id = "run-1"
    Repo(conn).start_run(run_id)
    rec = DbUsageRecorder(conn, run_id)
    # 1M uncached input + 1M output on claude-sonnet-5-5 = 2 + 10 = $12
    rec.record(Usage(provider="anthropic", model="claude-sonnet-5-5", task="evaluate",
                     input_tokens=1_000_000, output_tokens=1_000_000))
    s = status_summary(conn, now=datetime.now(UTC), prices=PriceTable.load())
    assert format_cost(s.cost_today.total) == format_cost(12.0)
    assert format_cost(s.cost_last_run.total) == format_cost(12.0)

    rec.record(Usage(provider="x", model="no-such-model", task="write", input_tokens=10))
    s = status_summary(conn, now=datetime.now(UTC), prices=PriceTable.load())
    assert s.cost_today.total is None
    assert format_cost(s.cost_today.total) == "unknown (no price configured)"


def test_mark_apply_overrides_maybe_decision(tmp_path):
    import json

    from autoapp.db.repo import Repo
    from autoapp.db.schema import open_db
    from autoapp.tracking.commands import mark

    conn = open_db(tmp_path / "t.db")
    repo = Repo(conn)
    jid = repo.insert_job(title="Dev", company="A")
    repo.add_evaluation(jid, fit_score=68, decision="maybe", payload={"decision": "maybe", "fit_score": 68})
    repo.transition(jid, "review")
    mark(conn, jid, "apply")
    ev = repo.current_evaluation(jid)
    assert ev["decision"] == "apply" and json.loads(ev["payload"])["decision"] == "apply"
    assert repo.get_job(jid)["status"] == "approved"
