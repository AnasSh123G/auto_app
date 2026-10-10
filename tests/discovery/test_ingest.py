import email
from email import policy
from pathlib import Path

from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.discovery.ingest import card_to_raw, distance_km, ingest
from autoapp.sources.alerts.registry import parse_email
from autoapp.sources.models import RawJob
from autoapp.sources.stepstone_links import ResolvedLink

FIXTURES = Path(__file__).parent.parent / "fixtures" / "alerts"


def _repo(tmp_path):
    return Repo(open_db(tmp_path / "t.db"))


def _cards(name):
    msg = email.message_from_bytes((FIXTURES / name).read_bytes(), policy=policy.default)
    return parse_email(msg).cards


def test_same_job_from_two_sources_is_one_job(tmp_path):
    repo = _repo(tmp_path)
    a = ingest(repo, RawJob(source="arbeitsagentur", source_job_id="10001-1", title="Backend Developer (m/w/d)",
                            company="Muster GmbH", location="Berlin", description="Volltext"))
    b = ingest(repo, RawJob(source="stepstone-alert", source_job_id="13850318", title="Backend Developer (w/m/d)",
                            company="Muster GmbH", location="Berlin",
                            url="https://www.stepstone.de/stellenangebote--13850318-inline.html"))
    assert a.created and not b.created and b.matched_by == "fingerprint"
    assert a.job_id == b.job_id
    assert {s["source"] for s in repo.sources(a.job_id)} == {"arbeitsagentur", "stepstone-alert"}
    assert len(repo.jobs_by_status("new")) == 1


def test_repeat_of_same_source_id_links(tmp_path):
    repo = _repo(tmp_path)
    raw = RawJob(source="arbeitsagentur", source_job_id="x1", title="Dev", company="A GmbH", location="Bonn")
    first, again = ingest(repo, raw), ingest(repo, raw.model_copy(update={"description": "later text"}))
    assert first.job_id == again.job_id and again.matched_by == "source"
    assert repo.get_job(first.job_id)["description"] == "later text"  # gaps get filled


def test_linkedin_fixture_4flow_becomes_one_job_with_two_cities(tmp_path):
    repo = _repo(tmp_path)
    outcomes = [ingest(repo, card_to_raw(c)) for c in _cards("linkedin_alert.eml")]
    assert len(outcomes) == 6
    assert len({o.job_id for o in outcomes}) == 5
    flow = [o for o in outcomes if repo.get_job(o.job_id)["company"] == "4flow"]
    assert flow[0].job_id == flow[1].job_id and flow[1].matched_by == "group"
    job = repo.get_job(flow[0].job_id)
    cities = {job["location"], *(r["city"] for r in repo.locations(job["id"]))}
    assert cities == {"Düsseldorf", "Rüsselsheim"}
    assert job["location"] == "Düsseldorf"  # first seen without a search location


def test_primary_city_is_closest_to_search_location(tmp_path):
    repo = _repo(tmp_path)
    for c in _cards("linkedin_alert.eml"):
        ingest(repo, card_to_raw(c), home_city="Frankfurt am Main")
    job = next(j for j in repo.jobs_by_status("new") if j["company"] == "4flow")
    assert job["location"] == "Rüsselsheim"
    assert [r["city"] for r in repo.locations(job["id"])] == ["Düsseldorf"]
    assert distance_km("Frankfurt am Main", "Rüsselsheim") < distance_km("Frankfurt am Main", "Düsseldorf")


def test_stepstone_card_never_keeps_tracking_link(tmp_path):
    repo = _repo(tmp_path)
    cards = _cards("stepstone_alert.eml")
    ok = ResolvedLink("13850318", "https://www.stepstone.de/stellenangebote--13850318-inline.html")
    r1 = ingest(repo, card_to_raw(cards[0], ok))
    r2 = ingest(repo, card_to_raw(cards[1], ResolvedLink(None, None, "unexpected response 404")))
    j1, j2 = repo.get_job(r1.job_id), repo.get_job(r2.job_id)
    assert j1["job_url"].endswith("13850318-inline.html") and j1["salary_estimate_min"] == 75000
    assert j1["ad_salary_min"] is None  # an estimate is never an employer-stated range
    assert j2["job_url"] is None
    rows = repo.conn.execute("SELECT url, raw FROM job_sources").fetchall()
    assert not any("click.stepstone.de" in (r["url"] or "") + (r["raw"] or "") for r in rows)
    recommended = [card_to_raw(c) for c in cards if c.section == "recommended"]
    assert len(recommended) == 4 and all(r.source == "stepstone-alert-recommended" for r in recommended)
