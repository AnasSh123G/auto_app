"""Finding an alert job's own ad: "<company> <job title>", then the page that really is this ad."""

from __future__ import annotations

from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.employers.hints import build_search
from autoapp.employers.posting import judge
from autoapp.employers.service import find_own_ads, run_discovery
from autoapp.employers.verify import Candidate, candidate_origins
from autoapp.sources.alerts.models import AlertCard
from autoapp.steps.context import RunContext
from tests.employers.identity_world import search_client
from tests.employers.test_service import EMP_CONFIG
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace

TEXT = ("Du planst und entwickelst im agilen Vorgehen neue Web-Applikationen für den internen und externen Gebrauch. "
        "Du entwickelst im Team oder eigenverantwortlich neue Softwarefeatures und vollständige Anwendungen. "
        "Du analysierst und prüfst die Anforderungen der internationalen Fachbereiche und setzt sie technisch um. "
        "Du begleitest unsere Anwendungen von der ersten Idee bis zum stabilen Betrieb in der Produktion.")
OTHER = ("Du entwickelst unsere Angular Oberflächen und Dotnet Dienste für die Warenwirtschaft weiter. "
         "Du arbeitest eng mit dem Einkauf zusammen und betreust bestehende Schnittstellen zu Lieferanten. "
         "Du dokumentierst deine Arbeit sorgfältig und schulst die Anwender in den Niederlassungen vor Ort.")
OWN = "https://www.chefsculinar.de/software-engineer-m-w-d-40624.htm"
ANGULAR = "https://www.chefsculinar.de/software-engineer-angular-net-8-m-w-d-43288.htm"


def ad(text: str, heading: str = "Software Engineer (m/w/d)") -> str:
    return (f"<html><head><title>{heading} | CHEFS CULINAR</title></head><body><h1>{heading}</h1>"
            f"<p>CHEFS CULINAR West GmbH &amp; Co. KG, Weeze</p><h2>Deine Aufgaben</h2><p>{text}</p></body></html>")


SWEDEN = {"https://www.chefsculinar.se/": "<html><body><h1>Chefs Culinar Sverige</h1></body></html>"}
PAGES = {**SWEDEN, ANGULAR: ad(OTHER, "Software Engineer - Angular/.NET 8 (m/w/d)"), OWN: ad(TEXT)}


def _setup(tmp_path, description: str | None = TEXT):
    ws = make_workspace(tmp_path, config=EMP_CONFIG)
    ctx = RunContext.open(ws)
    card = AlertCard(source="linkedin-alert", title="Software Engineer (m/w/d)", company="CHEFS CULINAR",
                     locations=["Weeze"], source_job_id="4477", url="https://www.linkedin.com/jobs/view/4477/")
    job_id = ingest(ctx.repo, card_to_raw(card)).job_id
    ctx.repo.update_job(job_id, application_url=card.url, **({"description": description} if description else {}))
    if description:
        ctx.repo.transition(job_id, "approved", note="fit")  # only jobs worth applying to are searched
    events: list = []
    ctx.events.subscribe(events.append)
    return ctx, job_id, events


def _search(ad_results):
    return search_client({"official employer website": ["https://www.chefsculinar.se/"], "Find the job ad": ad_results})


def test_the_ad_with_the_jobs_own_text_is_taken_not_another_ad_with_the_same_title(tmp_path):
    ctx, job_id, events = _setup(tmp_path)
    web = FakeWeb(PAGES)
    api, calls = _search(["https://www.linkedin.com/jobs/view/4477/", ANGULAR, OWN])
    run_discovery(ctx, fetch=web, api=api)  # the website check finds only the Swedish site: no match there
    s = find_own_ads(ctx, fetch=web, api=api)
    job = ctx.repo.get_job(job_id)
    assert job["application_url"] == OWN and s.found == [job_id]
    assert "https://www.linkedin.com/jobs/view/4477/" not in web.calls  # never opened
    seed = ctx.conn.execute("SELECT outcome, posting_url FROM employer_seeds WHERE job_id = ?", (job_id,)).fetchone()
    assert tuple(seed) == ("matched", OWN)
    ad_call = next(c for c in calls if c["objective"].startswith("Find the job ad"))
    assert ad_call["search_queries"][0] == '"CHEFS CULINAR" "Software Engineer (m/w/d)"'
    found = next(e for e in events if e.kind == "employer_matched")
    assert OWN in found.detail and "CHEFS CULINAR Software Engineer (m/w/d)" in found.detail
    assert any("own ads: 1 of 1" in line for line in s.lines())
    # The next run leaves the matched job alone: no search, no page.
    api2, calls2 = _search([OWN])
    web2 = FakeWeb({})
    assert find_own_ads(ctx, fetch=web2, api=api2).searched == 0 and calls2 == [] and web2.calls == []
    ctx.close()


def test_a_miss_is_logged_with_its_reasons_and_not_searched_again_soon(tmp_path):
    ctx, job_id, events = _setup(tmp_path)
    web = FakeWeb(PAGES)
    api, _ = _search([ANGULAR])
    find_own_ads(ctx, fetch=web, api=api)
    line = next(e for e in events if e.kind == "posting_searched")
    assert "chefsculinar.de: a different ad with this title" in line.detail
    assert ctx.repo.get_job(job_id)["application_url"].startswith("https://www.linkedin.com/")
    api2, calls2 = _search([OWN])
    find_own_ads(ctx, fetch=web, api=api2)
    assert calls2 == [] and "not searched again" in [e for e in events if e.kind == "posting_searched"][-1].detail
    ctx.close()


def test_without_the_jobs_text_two_ads_with_the_title_are_not_guessed_between(tmp_path):
    ctx, job_id, events = _setup(tmp_path, description=None)
    ctx.repo.transition(job_id, "description_unavailable", note="linkedin page not read")
    same = "https://www.chefsculinar.de/software-engineer-m-w-d-99999.htm"
    web = FakeWeb({**PAGES, same: ad(OTHER)})
    api, _ = _search([OWN, same])
    find_own_ads(ctx, fetch=web, api=api)
    assert ctx.repo.get_job(job_id)["application_url"].startswith("https://www.linkedin.com/")
    assert "several ads with this title" in next(e for e in events if e.kind == "posting_searched").detail
    ctx.close()


def test_without_the_jobs_text_the_one_exact_heading_is_taken_and_gives_the_text(tmp_path):
    ctx, job_id, _ = _setup(tmp_path, description=None)
    ctx.repo.transition(job_id, "description_unavailable", note="linkedin page not read")
    web = FakeWeb(PAGES)
    api, _ = _search([ANGULAR, OWN])  # the Angular ad only contains the title; the other heading is exact
    find_own_ads(ctx, fetch=web, api=api)
    job = ctx.repo.get_job(job_id)
    assert job["application_url"] == OWN and "agilen Vorgehen" in job["description"] and job["status"] == "new"
    ctx.close()


def test_what_counts_as_the_ad():
    page = ad(TEXT)
    assert judge(page, OWN, company="CHEFS CULINAR", title="Software Engineer (m/w/d)", description=TEXT).ok
    listing = ("<html><head><title>Jobs | CHEFS CULINAR</title></head><body><h1>Offene Stellen</h1>"
               "<a href='/job/1'>Software Engineer (m/w/d)</a></body></html>")
    assert not judge(listing, OWN, company="CHEFS CULINAR", title="Software Engineer (m/w/d)").ok
    other = page.replace("CHEFS CULINAR West GmbH &amp; Co. KG", "Muster Logistik GmbH").replace(" | CHEFS CULINAR", "")
    assert judge(other, "https://www.muster.de/job", company="CHEFS CULINAR",
                 title="Software Engineer (m/w/d)").reason == "CHEFS CULINAR is not named on the page"
    closed = page.replace("<h2>", "<p>Diese Stelle ist leider nicht mehr verfügbar.</p><h2>")
    assert "nicht mehr verfügbar" in judge(closed, OWN, company="CHEFS CULINAR", title="Software Engineer (m/w/d)").reason


def test_company_names_and_long_hyphenated_titles_survive_the_query_cleaning():
    from autoapp.employers.hints import clean_title, short_name

    assert short_name("EJOT SE & Co. KG") == "EJOT" and short_name("Phoenix Contact GmbH & Co. KG") == "Phoenix Contact"
    assert short_name("Würth AG & Co. KG") == "Würth" and short_name("CHEFS CULINAR") == "CHEFS CULINAR"
    assert clean_title("AI-Application-Developer (m/w/d)") == "AI-Application-Developer"


def test_the_website_search_uses_the_ads_title_and_tries_each_country_domain():
    _, queries = build_search("CHEFS CULINAR", title="Software Engineer (m/w/d)", city="Weeze")
    assert queries[0] == '"CHEFS CULINAR" "Software Engineer (m/w/d)"'
    hits = [Candidate("https://www.chefsculinar.se/en"), Candidate(ANGULAR), Candidate("https://karriere.chefsculinar.de/x")]
    # The Swedish branch is one company, the German one another: both are checked, the German one first.
    assert candidate_origins(hits, ["CHEFS CULINAR"]) == ["https://www.chefsculinar.de/", "https://www.chefsculinar.se/"]


def test_an_arbeitsagentur_job_gets_the_employers_ad_even_with_employer_discovery_off(tmp_path):
    # arbeitsagentur.de/jobsuche/jobdetail/10001-1003808501-S -> stellenanzeigen.ejot.com/Vacancies/1864/Description/1
    from autoapp.sources.models import RawJob

    ws = make_workspace(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: false"))
    ctx = RunContext.open(ws)
    ba = "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1003808501-S"
    ejot = "https://stellenanzeigen.ejot.com/Vacancies/1864/Description/1"
    raw = RawJob(source="arbeitsagentur", source_job_id="10001-1003808501-S", title="AI-Application-Developer (m/w/d)",
                 company="EJOT SE & Co. KG", location="Bad Berleburg", url=ba, description=TEXT)
    job_id = ingest(ctx.repo, raw).job_id
    ctx.repo.transition(job_id, "approved", note="fit")
    page = ad(TEXT, "AI-Application-Developer (m/w/d)").replace("CHEFS CULINAR West GmbH &amp; Co. KG", "EJOT SE &amp; Co. KG")
    web = FakeWeb({ejot: page})
    api, calls = search_client({"Find the job ad": [ba, ejot]})
    events = []
    ctx.events.subscribe(events.append)
    s = find_own_ads(ctx, fetch=web, api=api)
    assert s.found == [job_id], [(e.kind, e.detail) for e in events]
    assert ctx.repo.get_job(job_id)["application_url"] == ejot
    assert calls[0]["search_queries"][0] == '"EJOT" "AI-Application-Developer (m/w/d)"' and ba not in web.calls
    ctx.close()


def test_employer_discovery_leaves_searches_for_the_own_ad_search(tmp_path):
    from autoapp.employers.gateway import SearchGateway

    ws = make_workspace(tmp_path, config=EMP_CONFIG.replace("enabled: true", "enabled: true\n    max_search_requests_per_run: 10\n    max_search_requests_per_day: 10"))
    ctx = RunContext.open(ws)
    cfg = ctx.config.sources.employer_careers
    api, _ = search_client({"x": []})
    discovery = SearchGateway(ctx.conn, cfg, api, reserve=cfg.reserved_for_own_ad_search)
    taken = 0
    while discovery._budget.take():
        taken += 1
    assert taken == 5  # half is kept: reserve is capped at half the per-run limit
    own_ads = SearchGateway(ctx.conn, cfg, api)
    assert own_ads._budget.take()
    ctx.close()


def test_a_workwise_page_is_never_taken_as_the_ad(tmp_path):
    from autoapp.employers.scope import is_directory

    assert is_directory("https://www.workwise.io/job/1001-software-engineer")
    ctx, job_id, _ = _setup(tmp_path)
    workwise = "https://www.workwise.io/job/1001-software-engineer"
    web = FakeWeb({**PAGES, workwise: ad(TEXT)})
    api, _ = _search([workwise])
    s = find_own_ads(ctx, fetch=web, api=api)
    assert s.found == [] and workwise not in web.calls
    ctx.close()
