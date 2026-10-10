"""A small fake world for identity tests: employer sites, a namesake, a directory and a search service."""

from __future__ import annotations

import httpx

from autoapp.config.models import EmployerCareersConfig
from autoapp.db.schema import open_db
from autoapp.employers.budget import PageMeter
from autoapp.employers.crawl import RunBudget
from autoapp.employers.gateway import SearchGateway
from autoapp.employers.parallel import ParallelSearch
from autoapp.employers.repo import EmployerRepo
from autoapp.employers.resolver import IdentityResolver, Seed, group_seeds
from tests.employers.webfake import FakeWeb


def site(name: str, city: str, host: str, *, extra_nav: str = "", legal: str | None = None) -> dict[str, str]:
    home = (f"<html><body><h1>{name}</h1><a href='/karriere'>Karriere</a><a href='/impressum'>Impressum</a>"
            f"{extra_nav}</body></html>")
    imprint = f"<html><body><h1>Impressum</h1><p>{legal or name}, Hauptstr. 1, 50679 {city}</p></body></html>"
    return {f"https://{host}/": home, f"https://{host}/impressum": imprint,
            f"https://{host}/karriere": "<html><body><h1>Karriere</h1><p>Aktuell keine Stellen.</p></body></html>"}


def world(extra: dict | None = None) -> FakeWeb:
    pages: dict[str, object] = {}
    pages.update(site("CBC Cologne Business Center GmbH", "Köln", "www.cbc-cologne.de"))
    pages.update(site("Acme GmbH", "Berlin", "www.acme-gmbh.example"))
    pages.update(site("Acme GmbH", "Hamburg", "www.acme-hamburg.example"))
    pages.update(site("Totally Other Corp", "Köln", "www.cbc-cologne-business.example"))  # name-like host, wrong company
    pages.update(site("Acme AG", "Berlin", "www.acme-ag.example"))
    pages["https://www.kununu.com/de/cbc"] = "<html><body>CBC Cologne Business Center GmbH Bewertungen</body></html>"
    pages.update(extra or {})
    return FakeWeb(pages)


def search_client(results: list[list[str]] | list[str] | dict[str, list[str]], *, status: int = 200, key: str = "pk-test"):
    """A Parallel client. A list answers call by call (the last repeats); a dict maps a fragment of the
    company name in the objective to its result urls."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        calls.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status)
        if isinstance(results, dict):
            urls = next((u for frag, u in results.items() if frag.casefold() in calls[-1]["objective"].casefold()), [])
        else:
            batches = results if results and isinstance(results[0], list) else [results]
            urls = batches[min(len(calls) - 1, len(batches) - 1)]
        return httpx.Response(200, json={"search_id": "s", "results": [{"url": u, "title": "t", "excerpts": []} for u in urls]})

    api = ParallelSearch(key, client=httpx.Client(transport=httpx.MockTransport(handler)))
    return api, calls


class Rig:
    def __init__(self, tmp_path, web: FakeWeb, results=None, *, key="pk-test", status=200, config=None, db_name="a.db",
                 run_pages=100):
        self.path = tmp_path / db_name
        self.conn = open_db(self.path)
        self.repo = EmployerRepo(self.conn)
        self.web = web
        self.config = config or EmployerCareersConfig(enabled=True)
        self.api, self.search_calls = search_client(results or [], key=key, status=status)
        self.gateway = SearchGateway(self.conn, self.config, self.api)
        self.run = RunBudget(pages=run_pages, openings=100)
        self.meter = PageMeter(self.run, self.config.max_pages_per_employer)
        self.events: list[tuple] = []
        self.resolver = IdentityResolver(self.repo, self.config, web, self.meter, self.gateway,
                                         lambda kind, detail=None, **data: self.events.append((kind, detail, data)))

    def fresh_resolver(self, **kw):
        """A new run on the same database (new meter, gateway and resolver)."""
        return Rig(self.path.parent, self.web, **kw, db_name=self.path.name)


def seed(company="CBC Cologne Business Center GmbH", title="(Junior) Data Engineer (m/w/d)", city="Köln", job_id="a1",
         **kw) -> Seed:
    return Seed(job_id=job_id, company=company, title=title, city=city, **kw)


def group_of(*seeds: Seed):
    return group_seeds(list(seeds))[0]
