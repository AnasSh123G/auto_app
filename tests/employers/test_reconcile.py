"""Attaching exact postings, deduplication and related openings (tasks 4.2, 4.3, 4.5)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from autoapp.config.models import DEFAULT_RESTRICTED_DOMAINS, SearchProfile
from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.discovery.ingest import EMPLOYER_SOURCE, card_to_raw, ingest, job_requisitions
from autoapp.employers.reconcile import Reconciler, keyword_hit, posting_source_id, wanted_priority
from autoapp.employers.repo import EmployerRepo, Listing
from autoapp.sources.alerts.models import AlertCard
from autoapp.sources.models import RawJob

TODAY = date(2026, 10, 9)
CBC = "CBC Cologne Business Center GmbH"


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "w.db")
    repo, emp = Repo(conn), EmployerRepo(conn)
    eid = emp.create_employer(CBC, status="verified", website="https://cbc-cologne.de/", provenance="search")
    events: list = []
    rec = Reconciler(repo, emp, lambda kind, detail=None, **data: events.append((kind, detail, data)),
                     restricted_domains=DEFAULT_RESTRICTED_DOMAINS, today=TODAY)
    return repo, emp, emp.get(eid), rec, events


def alert_job(repo, *, title="(Junior) Data Engineer (m/w/d)", company=CBC, city="Köln", status="description_unavailable",
              url="https://www.linkedin.com/jobs/view/4471/", description=None, truncated=False, sid="4471"):
    card = AlertCard(source="linkedin-alert", title=title, company=company, locations=[city] if city else [],
                     source_job_id=sid, url=url, title_truncated=truncated)
    out = ingest(repo, card_to_raw(card))
    repo.update_job(out.job_id, application_url=url, **({"description": description} if description else {}))
    if status != "new":
        repo.transition(out.job_id, status, note="test")
    return repo.get_job(out.job_id)


def posting(title="(Junior) Data Engineer (m/w/d)", ref="346291", loc="Köln", desc="Aufgaben " * 40, **kw):
    return Listing(url=f"https://cbc-cologne.onapply.de/details/{ref}.html", title=title, ref=ref, locations=[loc],
                   description=desc, **kw)


def test_exact_match_recovers_description_and_replaces_platform_only_entry(world):
    repo, emp, employer, rec, events = world
    job = alert_job(repo)
    out = rec.reconcile_seed(employer, job, [posting(), posting("Accountant", "9")], scan_partial=False)
    assert (out.outcome, out.recovered, out.entry_replaced) == ("matched", True, True)
    after = repo.get_job(job["id"])
    assert after["status"] == "new" and after["description"].startswith("Aufgaben")
    assert after["job_url"] == "https://www.linkedin.com/jobs/view/4471/"  # the ad identity is preserved
    assert after["application_url"] == "https://cbc-cologne.onapply.de/details/346291.html"
    seed = emp.seed(job["id"])
    assert seed["outcome"] == "matched" and seed["replaced_application_url"] == "https://www.linkedin.com/jobs/view/4471/"
    assert [s["source"] for s in repo.sources(job["id"])].count(EMPLOYER_SOURCE) == 1
    assert [e for e in repo.history(job["id"])][-1]["note"] == "description recovered from the employer posting"
    assert len(repo.jobs_by_status("new", "description_unavailable")) == 1  # no duplicate job


def test_explicit_apply_link_is_preferred_as_entry_and_never_a_restricted_one(world):
    repo, emp, employer, rec, _ = world
    job = alert_job(repo)
    rec.reconcile_seed(employer, job, [posting(apply_url="https://www.stepstone.de/apply/1")], scan_partial=False)
    assert repo.get_job(job["id"])["application_url"] == "https://cbc-cologne.onapply.de/details/346291.html"
    job2 = alert_job(repo, title="Data Scientist", sid="4472", url="https://www.linkedin.com/jobs/view/4472/")
    rec.reconcile_seed(employer, job2, [posting("Data Scientist", "5", apply_url="https://cbc-cologne.onapply.de/bewerbung/5.html")],
                       scan_partial=False)
    assert repo.get_job(job2["id"])["application_url"] == "https://cbc-cologne.onapply.de/bewerbung/5.html"


def test_valid_found_form_and_employer_entry_are_preserved(world):
    repo, emp, employer, rec, _ = world
    job = alert_job(repo, url="https://www.linkedin.com/jobs/view/4471/", status="new")
    repo.update_job(job["id"], application_url="https://other.example/form")
    out = rec.reconcile_seed(employer, repo.get_job(job["id"]), [posting()], scan_partial=False)
    assert out.outcome == "matched" and not out.entry_replaced
    assert repo.get_job(job["id"])["application_url"] == "https://other.example/form"
    job2 = alert_job(repo, title="Data Scientist", sid="9", url="https://www.linkedin.com/jobs/view/9/", status="new")
    repo.upsert_application(job2["id"], form_status="found", form_entry_url="https://www.linkedin.com/jobs/view/9/")
    rec.reconcile_seed(employer, repo.get_job(job2["id"]), [posting("Data Scientist", "7")], scan_partial=False)
    assert repo.get_job(job2["id"])["application_url"] == "https://www.linkedin.com/jobs/view/9/"  # found form kept


def test_prepared_job_gets_provenance_only(world):
    repo, emp, employer, rec, _ = world
    job = alert_job(repo, description="my edited text", status="new")
    repo.update_job(job["id"], notes="user note")
    repo.add_evaluation(job["id"], provider="p", model="m", fit_score=90, decision="apply", payload="{}")
    repo.transition(job["id"], "approved")
    repo.transition(job["id"], "prepared")
    before = dict(repo.get_job(job["id"]))
    out = rec.reconcile_seed(employer, repo.get_job(job["id"]), [posting()], scan_partial=False)
    after = dict(repo.get_job(job["id"]))
    assert out.outcome == "matched" and not out.recovered
    for key in ("status", "description", "application_url", "notes", "job_url", "title"):
        assert after[key] == before[key]
    assert emp.seed(job["id"])["posting_url"] == "https://cbc-cologne.onapply.de/details/346291.html"


def test_only_unavailable_to_new_transition_is_allowed(world):
    repo, emp, employer, rec, _ = world
    for status in ("filtered", "skipped", "expired"):
        job = alert_job(repo, status="new", sid=status, url=f"https://www.linkedin.com/jobs/view/{status}/")
        repo.transition(job["id"], status)
        rec.reconcile_seed(employer, repo.get_job(job["id"]), [posting()], scan_partial=False)
        assert repo.get_job(job["id"])["status"] == status


def test_unresolved_and_not_found_never_change_the_job(world):
    repo, emp, employer, rec, events = world
    job = alert_job(repo)
    out = rec.reconcile_seed(employer, job, [posting(ref="1"), posting(ref="2")], scan_partial=False)
    assert out.outcome == "unresolved" and repo.get_job(job["id"])["status"] == "description_unavailable"
    assert json.loads(emp.seed(job["id"])["candidates"]) and any(e[0] == "employer_unresolved" for e in events)
    gone = rec.reconcile_seed(employer, job, [posting("Accountant", "3")], scan_partial=False)
    assert gone.outcome == "not_found" and "may be closed" in gone.reason
    partial = rec.reconcile_seed(employer, job, [posting("Accountant", "3")], scan_partial=True)
    assert partial.outcome == "not_found_partial" and "partial scan" in partial.reason
    assert repo.get_job(job["id"])["status"] == "description_unavailable"


def test_a_second_requisition_conflicts_instead_of_merging(world):
    repo, emp, employer, rec, _ = world
    job = alert_job(repo, status="new")
    rec.reconcile_seed(employer, job, [posting(ref="100")], scan_partial=False)
    out = rec.reconcile_seed(employer, repo.get_job(job["id"]), [posting(ref="200", loc="Köln")], scan_partial=False)
    assert out.outcome == "conflict"
    assert len(job_requisitions(repo, job["id"])) == 1
    assert repo.get_job(job["id"])["needs_review"] == 1
    assert "employer requisitions conflict" in repo.get_job(job["id"])["needs_review_reasons"]


# --- deduplication (4.3) ---------------------------------------------------------------------


def employer_raw(eid, ref, title="Data Engineer", city="Köln", url=None):
    url = url or f"https://cbc-cologne.onapply.de/details/{ref}.html"
    return RawJob(source=EMPLOYER_SOURCE, source_job_id=f"emp:{eid}:{ref}", title=title, company=CBC, location=city,
                  locations=[city], url=url, description="text " * 30)


def test_two_requisitions_with_same_title_and_city_stay_two_jobs(world):
    repo, emp, employer, rec, _ = world
    a = ingest(repo, employer_raw(employer["id"], "1"))
    b = ingest(repo, employer_raw(employer["id"], "2"))
    assert a.created and b.created and a.job_id != b.job_id


def test_one_requisition_in_two_cities_is_one_job_with_two_locations(world):
    repo, emp, employer, rec, _ = world
    a = ingest(repo, employer_raw(employer["id"], "7", city="Köln", url="https://x.example/job/7?loc=koeln"))
    b = ingest(repo, employer_raw(employer["id"], "7", city="Berlin", url="https://x.example/job/7?loc=berlin"))
    assert not b.created and a.job_id == b.job_id and b.matched_by == "source"
    assert {r["city"] for r in repo.locations(a.job_id)} == {"Berlin"}


def test_alert_does_not_pick_arbitrarily_between_distinct_requisitions(world):
    repo, emp, employer, rec, _ = world
    a = ingest(repo, employer_raw(employer["id"], "1"))
    b = ingest(repo, employer_raw(employer["id"], "2"))
    alert = ingest(repo, RawJob(source="linkedin-alert", source_job_id="55", title="Data Engineer (m/w/d)", company=CBC,
                                location="Köln", url="https://www.linkedin.com/jobs/view/55/"))
    assert alert.created and alert.job_id not in (a.job_id, b.job_id)


def test_alert_still_merges_with_a_single_employer_job_and_old_behaviour_is_unchanged(world):
    repo, emp, employer, rec, _ = world
    a = ingest(repo, employer_raw(employer["id"], "1"))
    alert = ingest(repo, RawJob(source="linkedin-alert", source_job_id="56", title="Data Engineer (m/w/d)", company=CBC,
                                location="Köln", url="https://www.linkedin.com/jobs/view/56/"))
    assert not alert.created and alert.job_id == a.job_id
    x = ingest(repo, RawJob(source="arbeitsagentur", source_job_id="a1", title="Backend Dev", company="Muster GmbH", location="Bonn"))
    y = ingest(repo, RawJob(source="adzuna", source_job_id="z1", title="Backend Dev", company="Muster GmbH", location="Bonn"))
    assert x.job_id == y.job_id


def test_legacy_conflicts_are_flagged_not_split(world):
    repo, emp, employer, rec, _ = world
    a = ingest(repo, employer_raw(employer["id"], "1"))
    repo.add_source(a.job_id, EMPLOYER_SOURCE, f"emp:{employer['id']}:2", "https://x/2", {})
    assert rec.flag_reference_conflicts() == [a.job_id]
    assert repo.get_job(a.job_id)["needs_review"] == 1
    assert len(job_requisitions(repo, a.job_id)) == 2  # history is not split


# --- related openings (4.5) -------------------------------------------------------------------

DE = SearchProfile(name="data", keywords=["Junior Data Engineer", "BI Developer"], exclude_keywords=["senior"])
ML = SearchProfile(name="ml", keywords=["Machine Learning Engineer"])


def test_related_roles_are_imported_unrelated_and_closed_are_not(world):
    repo, emp, employer, rec, events = world
    listings = [
        posting("Data Engineer (m/w/d)", "10"), posting("BI Developer (m/w/d)", "11"), posting("Buchhalter:in", "12"),
        posting("Senior Data Engineer", "13"), posting("Data Engineer (m/w/d)", "14", expires_at="2026-09-01T00:00:00+00:00"),
    ]
    out = rec.import_related(employer, listings, [DE, ML], attached_urls=set(), seed_job_ids=["x"])
    titles = sorted(repo.get_job(j)["title"] for j in out.created)
    assert titles == ["BI Developer (m/w/d)", "Data Engineer (m/w/d)"]
    assert (out.unrelated, out.filtered, out.closed) == (1, 1, 1)
    job = repo.get_job(out.created[0])
    assert job["company"] == CBC and job["status"] == "new" and job["search_profile"] == "data"
    src = repo.sources(out.created[0])[0]
    assert src["source"] == EMPLOYER_SOURCE and json.loads(src["raw"])["employer_id"] == employer["id"]
    again = rec.import_related(employer, listings, [DE, ML], attached_urls=set(), seed_job_ids=["x"])
    assert again.created == [] and again.linked == 2  # repeats yield no new jobs


def test_matched_alert_posting_is_not_imported_twice(world):
    repo, emp, employer, rec, _ = world
    job = alert_job(repo)
    mine = posting("(Junior) Data Engineer (m/w/d)", "346291")
    out = rec.reconcile_seed(employer, job, [mine], scan_partial=False)
    related = rec.import_related(employer, [mine, posting("BI Developer (m/w/d)", "11")], [DE],
                                 attached_urls={out.posting_url}, seed_job_ids=[job["id"]])
    assert len(related.created) == 1 and related.skipped_attached == 1
    assert len(repo.jobs_by_status("new", "description_unavailable")) == 2


def test_multi_profile_match_creates_one_job_with_all_profiles_recorded(world):
    repo, emp, employer, rec, _ = world
    both = SearchProfile(name="both", keywords=["Data Engineer"])
    out = rec.import_related(employer, [posting("Data Engineer (m/w/d)", "20")], [DE, both], attached_urls=set(),
                             seed_job_ids=[])
    assert len(out.created) == 1
    raw = json.loads(repo.sources(out.created[0])[0]["raw"])
    assert raw["profiles"] == ["data", "both"] and repo.get_job(out.created[0])["search_profile"] == "data"


def test_profile_failure_does_not_suppress_another_profiles_match(world):
    repo, emp, employer, rec, _ = world
    strict = SearchProfile(name="strict", keywords=["Data Engineer"], exclude_keywords=["engineer"])
    loose = SearchProfile(name="loose", keywords=["Data Engineer"])
    out = rec.import_related(employer, [posting("Data Engineer", "30")], [strict, loose], attached_urls=set(), seed_job_ids=[])
    assert len(out.created) == 1 and repo.get_job(out.created[0])["search_profile"] == "loose"


def test_unknown_salary_is_not_fabricated_and_does_not_filter(world):
    repo, emp, employer, rec, _ = world
    rich = SearchProfile(name="rich", keywords=["Data Engineer"], min_salary=70000)
    out = rec.import_related(employer, [posting("Data Engineer", "40")], [rich], attached_urls=set(), seed_job_ids=[])
    job = repo.get_job(out.created[0])
    assert len(out.created) == 1 and job["ad_salary_min"] is None and job["ad_salary_max"] is None


def test_keyword_rules_and_detail_priority():
    assert keyword_hit(DE, Listing(url="u", title="Data Engineer (m/w/d)"))
    assert not keyword_hit(DE, Listing(url="u", title="Buchhalter"))
    assert not keyword_hit(DE, Listing(url="u", title="Accountant", description="we use BI Developer tools"))
    assert keyword_hit(DE, Listing(url="u", title="Teamleiter", description="Junior Data Engineer und BI Developer gesucht"))
    from autoapp.employers.listings import ListingLink

    prio = wanted_priority(["(Junior) Data Engineer (m/w/d)"], [DE])
    assert prio(ListingLink("u", "(Junior) Data Engineer (w/m/d)")) == 0
    assert prio(ListingLink("u", "BI Developer")) == 1 and prio(ListingLink("u", "Buchhalter")) is None


def test_source_id_is_employer_scoped(world):
    repo, emp, employer, rec, _ = world
    a = posting(ref="5")
    other = emp.create_employer("Other GmbH", status="verified")
    assert posting_source_id(employer["id"], a) != posting_source_id(other, a)
