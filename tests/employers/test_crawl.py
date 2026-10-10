"""Bounded careers traversal (tasks 3.4, 3.5)."""

from __future__ import annotations

from autoapp.employers import listings as L
from autoapp.employers.crawl import CareersCrawler, RunBudget
from autoapp.employers.scope import SiteScope
from tests.employers.webfake import FakeWeb, fixture, job_page, listing_page

TENANT = "https://cbc-cologne.onapply.de/"
OFFICIAL = ["cbc-cologne.de"]
NAMES = ["CBC Cologne Business Center GmbH"]


def crawler(web, scope_urls, *, max_pages=20, max_openings=50, run=None, want=None, names=NAMES, official=OFFICIAL):
    scope = SiteScope()
    for url in scope_urls:
        scope.add(url)
    return CareersCrawler(web, scope, max_pages=max_pages, max_openings=max_openings,
                          run=run or RunBudget(pages=100, openings=100), names=names, official_hosts=official, want=want)


def test_onapply_listing_and_exact_detail():
    web = FakeWeb({TENANT: fixture("onapply_listing.html"),
                   TENANT + "details/346291.html": fixture("onapply_detail.html")})
    want = lambda link: 0 if "Data Engineer" in link.title else None  # noqa: E731
    res = crawler(web, [TENANT], want=want).scan([TENANT])
    assert res.status == "complete" and res.pages == 2 and res.reason is None
    assert len(res.listings) == 9
    by_ref = {item.ref: item for item in res.listings}
    de = by_ref["346291"]
    assert de.title.startswith("(Junior) Data Engineer") and de.description and "Köln" in de.locations
    assert by_ref["284857"].description is None  # not wanted, so not fetched
    assert web.calls == [TENANT, TENANT + "details/346291.html"]


def test_pagination_uses_budget_and_resumes_without_duplicates():
    pages = {
        "https://jobs.example.com/": listing_page([("Data Engineer", "/job/1001"), ("Backend Dev", "/job/1002")],
                                                    nxt="https://jobs.example.com/?page=2"),
        "https://jobs.example.com/?page=2": listing_page([("ML Engineer", "/job/1003")],
                                                         nxt="https://jobs.example.com/?page=3", base=""),
        "https://jobs.example.com/?page=3": listing_page([("QA Tester", "/job/1004")]),
    }
    web = FakeWeb(pages)
    run = RunBudget(pages=100, openings=100)
    first = crawler(web, ["https://jobs.example.com/"], max_pages=2, run=run).scan(["https://jobs.example.com/"])
    assert first.status == "partial" and "page limit" in first.reason and first.pages == 2
    assert len(first.listings) == 3 and first.checkpoint
    calls_before = len(web.calls)
    second = crawler(web, ["https://jobs.example.com/"], max_pages=5, run=run).scan(
        [], resume=first.checkpoint, known=first.listings)
    assert second.status == "complete"
    assert sorted(i.ref for i in second.listings if i.ref) == ["1001", "1002", "1003", "1004"]
    assert len(second.listings) == 4
    assert len(web.calls) - calls_before == 1  # only the unread third page


def test_run_budget_caps_pages_across_employers():
    web = FakeWeb({"https://jobs.example.com/": listing_page([("A Dev", "/job/1")], nxt="https://jobs.example.com/?page=2"),
                   "https://jobs.example.com/?page=2": listing_page([("B Dev", "/job/2")])})
    run = RunBudget(pages=1, openings=100)
    res = crawler(web, ["https://jobs.example.com/"], run=run).scan(["https://jobs.example.com/"])
    assert res.status == "partial" and "run" in res.reason and len(web.calls) == 1


def test_opening_limit_is_partial_not_empty():
    entries = [(f"Role {i}", f"/job/{2000 + i}") for i in range(10)]
    web = FakeWeb({"https://jobs.example.com/": listing_page(entries)})
    res = crawler(web, ["https://jobs.example.com/"], max_openings=4).scan(["https://jobs.example.com/"])
    assert res.status == "partial" and "opening limit" in res.reason and len(res.listings) == 4


def test_redirects_consume_budget_and_loops_terminate():
    web = FakeWeb({"https://a.example.com/": ("redirect", "https://a.example.com/b"),
                   "https://a.example.com/b": ("redirect", "https://a.example.com/")})
    res = crawler(web, ["https://a.example.com/"]).scan(["https://a.example.com/"])
    assert res.pages == 2 and res.status == "partial" and "no readable listing" in res.reason


def test_redirect_leaving_scope_is_not_followed():
    web = FakeWeb({"https://a.example.com/": ("redirect", "https://evil.example.org/x")})
    res = crawler(web, ["https://a.example.com/"]).scan(["https://a.example.com/"])
    assert web.calls == ["https://a.example.com/"]
    assert any("leaves the employer scope" in n for n in res.notes)


def test_out_of_scope_links_are_never_requested():
    html = listing_page([("Data Engineer", "https://other.example.net/job/1")])
    web = FakeWeb({"https://jobs.example.com/": html})
    res = crawler(web, ["https://jobs.example.com/"]).scan(["https://jobs.example.com/"])
    assert web.calls == ["https://jobs.example.com/"] and res.listings == []


def test_javascript_only_directory_is_unsupported_not_empty():
    shell = "<html><body><div id='root'></div>" + "<script src='/a.js'></script>" * 6 + "</body></html>"
    web = FakeWeb({"https://jobs.example.com/": shell})
    res = crawler(web, ["https://jobs.example.com/"]).scan(["https://jobs.example.com/"])
    assert res.status == "partial" and res.unsupported and "unsupported JavaScript" in res.reason
    assert res.listings == []


def test_login_wall_and_challenge_are_blocks_without_import():
    login = "<html><body><form><input type='email' name='e'><input type='password' name='p'></form></body></html>"
    web = FakeWeb({"https://jobs.example.com/": login})
    res = crawler(web, ["https://jobs.example.com/"]).scan(["https://jobs.example.com/"])
    assert res.blocked == {"jobs.example.com": "login required"} and res.status == "partial" and not res.listings
    web = FakeWeb({"https://jobs.example.com/": "<html><body>Just a moment...</body></html>"})
    res = crawler(web, ["https://jobs.example.com/"]).scan(["https://jobs.example.com/"])
    assert "anti-bot challenge" in res.blocked["jobs.example.com"]


def test_http_403_blocks_the_host_and_is_not_worked_around():
    web = FakeWeb({"https://jobs.example.com/": ("blocked", "HTTP 403")})
    res = crawler(web, ["https://jobs.example.com/"]).scan(["https://jobs.example.com/"])
    assert res.blocked == {"jobs.example.com": "HTTP 403"} and web.calls == ["https://jobs.example.com/"]


def test_careers_link_from_homepage_then_declared_tenant_with_evidence():
    home = "<html><body><h1>CBC</h1><a href='/karriere'>Karriere</a></body></html>"
    karriere = ("<html><body><h1>Karriere</h1><div class='onapply-career-page-container' "
                f"data-url='{TENANT}feed/render.html?format=json'></div></body></html>")
    web = FakeWeb({"https://cbc-cologne.de/": home, "https://cbc-cologne.de/karriere": karriere,
                   TENANT: fixture("onapply_listing.html")})
    res = crawler(web, ["https://cbc-cologne.de/"]).scan(["https://cbc-cologne.de/"])
    assert res.status == "complete" and len(res.listings) == 9
    assert [s.url for s in res.new_sites] == [TENANT] and res.new_sites[0].evidence["kind"] == "link_to_official_site"
    assert res.new_sites[0].referrer == "https://cbc-cologne.de/karriere"


def test_tenant_without_identity_evidence_is_not_imported():
    karriere = f"<html><body><div class='onapply-career-page-container' data-url='{TENANT}feed'></div></body></html>"
    stranger = listing_page([("Data Engineer", "/details/555.html")], base=TENANT.rstrip("/"))
    web = FakeWeb({"https://acme.example.com/": karriere, TENANT: stranger})
    res = crawler(web, ["https://acme.example.com/"], names=["Acme GmbH"], official=["acme.example.com"]).scan(
        ["https://acme.example.com/"])
    assert res.new_sites == [] and res.listings == []
    assert any("no employer identity evidence" in n for n in res.notes)


def test_declared_rss_feed_is_read_and_counts_pages():
    home = ("<html><head><link rel='alternate' type='application/rss+xml' href='/jobs.rss'></head>"
            "<body><h1>Jobs</h1></body></html>")
    rss = ("<rss><channel><item><title>Data Engineer</title><link>https://jobs.example.com/job/777</link></item>"
           "</channel></rss>")
    web = FakeWeb({"https://jobs.example.com/": home, "https://jobs.example.com/jobs.rss": rss})
    res = crawler(web, ["https://jobs.example.com/"]).scan(["https://jobs.example.com/"])
    assert [i.title for i in res.listings] == ["Data Engineer"] and res.pages == 2


def test_json_ld_item_list_and_detail_priority():
    ld = ('<script type="application/ld+json">{"@type": "ItemList", "itemListElement": ['
          '{"@type": "ListItem", "item": {"@type": "JobPosting", "title": "Data Engineer", "url": "/job/9001"}},'
          '{"@type": "ListItem", "item": {"@type": "JobPosting", "title": "Accountant", "url": "/job/9002"}}]}</script>')
    web = FakeWeb({"https://jobs.example.com/": f"<html><head>{ld}</head><body><h1>Jobs</h1></body></html>",
                   "https://jobs.example.com/job/9001": job_page("Data Engineer", ref="REQ-9001")})
    want = lambda link: 0 if "Engineer" in link.title else None  # noqa: E731
    res = crawler(web, ["https://jobs.example.com/"], want=want).scan(["https://jobs.example.com/"])
    by = {i.title: i for i in res.listings}
    assert by["Data Engineer"].description and by["Data Engineer"].ref == "9001"
    assert by["Accountant"].description is None and "https://jobs.example.com/job/9002" not in web.calls


def test_parsers_ignore_navigation_and_footers():
    html = ("<nav><a href='/jobs/'>Jobs</a></nav><body><a href='/job/123'>Mehr erfahren</a>"
            "<footer><a href='/job/999'>Datenschutz</a></footer></body>")
    assert L.html_listing_links(html, "https://x.example.com/") == []


def test_only_get_requests_and_blocks_are_shared_with_other_stages():
    import httpx

    from autoapp.config.models import DEFAULT_RESTRICTED_DOMAINS
    from autoapp.employers.guard import check_target
    from autoapp.fetch.http_fetcher import HttpFetcher

    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.host == "blocked.example.com":
            return httpx.Response(403)
        return httpx.Response(200, text=listing_page([("Data Engineer", "/job/1")]))

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)), min_delay_s=0, max_delay_s=0)
    resolver = lambda host: ["93.184.216.34"]  # noqa: E731
    guarded = lambda url: fetcher.fetch_once(url, check=lambda u: check_target(u, DEFAULT_RESTRICTED_DOMAINS, resolver))  # noqa: E731
    # The detail stage hits a 403 first: the host is off for everybody for the rest of the run.
    assert fetcher.fetch_once("https://blocked.example.com/job/5").blocked
    res = crawler(guarded, ["https://blocked.example.com/", "https://jobs.example.com/"]).scan(
        ["https://blocked.example.com/", "https://jobs.example.com/"])
    assert "blocked.example.com" in res.blocked and [i.title for i in res.listings] == ["Data Engineer"]
    assert set(methods) == {"GET"} and methods.count("GET") == 2  # one 403 earlier, one listing page


def test_a_login_popup_on_a_careers_page_is_not_a_wall():
    # chefsculinar.de: every page carries the customer portal's login box; the job list is still public.
    from autoapp.employers.crawl import blocking_wall

    popup = "<div class='modal'><form><input type='email'><input type='password'></form></div>"
    jobs = "".join(f"<a href='/job-{n}.htm'><h3>Stelle {n} (m/w/d)</h3><p>Weeze, IT, Vollzeit</p></a>" for n in range(30))
    assert blocking_wall(f"<html><body>{popup}<h1>Stellenangebote</h1>{jobs}</body></html>") is None
    assert blocking_wall(f"<html><body>{popup}<p>Bitte melden Sie sich an.</p></body></html>") == "login"


def test_over_the_opening_limit_the_wanted_vacancy_is_taken_first_and_its_page_read():
    # chefsculinar.de: 229 cards, links without "job" in the address, the alert's vacancy near the end.
    cards = "".join(f"<div class='teaser-karriere-element'><a class='icon-right_arrow' href='/koch-m-w-d-{n}.htm'>"
                    f"<h3>Koch (m/w/d)</h3><span>Ort {n}</span></a></div>" for n in range(1, 60))
    cards += ("<div class='teaser-karriere-element'><a class='icon-right_arrow' href='/software-engineer-m-w-d-40624.htm'>"
              "<h3>Software Engineer (m/w/d)</h3></a></div>")
    detail = "<html><body><h1>Software Engineer (m/w/d)</h1><p>Du planst und entwickelst neue Anwendungen.</p></body></html>"
    web = FakeWeb({"https://jobs.example.com/": f"<html><body><h1>Stellenangebote</h1>{cards}</body></html>",
                   "https://jobs.example.com/software-engineer-m-w-d-40624.htm": detail})
    want = lambda link: 0 if "Software Engineer" in link.title else None  # noqa: E731
    res = crawler(web, ["https://jobs.example.com/"], max_openings=10, want=want).scan(["https://jobs.example.com/"])
    urls = [item.url for item in res.listings]
    assert "https://jobs.example.com/software-engineer-m-w-d-40624.htm" in urls and len(urls) == 10
    assert "https://jobs.example.com/software-engineer-m-w-d-40624.htm" in web.calls  # its page was read
    assert res.status == "partial" and "opening limit reached (10 openings)" in res.reason
