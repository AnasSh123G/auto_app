"""A fake public web for employer tests: pages by address, nothing ever leaves the process."""

from __future__ import annotations

from pathlib import Path

from autoapp.fetch.http_fetcher import FetchResult

FIX = Path(__file__).parent / "fixtures" / "web"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeWeb:
    """``pages`` maps an address to html, ``("redirect", target)``, ``("status", code)`` or ``("blocked", why)``."""

    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = dict(pages)
        self.calls: list[str] = []
        self.disabled: set[str] = set()

    def __call__(self, url: str) -> FetchResult:
        from urllib.parse import urlsplit

        self.calls.append(url)
        host = urlsplit(url).hostname or ""
        if host in self.disabled:
            return FetchResult(url=url, status=0, blocked=True, reason=f"{host} disabled for this run")
        page = self.pages.get(url)
        if page is None:
            return FetchResult(url=url, status=404, reason="HTTP 404")
        if isinstance(page, tuple):
            kind, value = page
            if kind == "redirect":
                return FetchResult(url=url, status=302, final_url=url, redirect_url=str(value))
            if kind == "status":
                return FetchResult(url=url, status=int(value), final_url=url, reason=f"HTTP {value}")
            if kind == "blocked":
                self.disabled.add(host)
                return FetchResult(url=url, status=403, final_url=url, blocked=True, reason=str(value))
        return FetchResult(url=url, status=200, html=str(page), final_url=url)


def job_page(title: str, *, ref: str | None = None, city: str = "Köln", org: str = "Acme GmbH") -> str:
    ident = f', "identifier": {{"@type": "PropertyValue", "value": "{ref}"}}' if ref else ""
    return f"""<html><head><script type="application/ld+json">
{{"@context": "https://schema.org", "@type": "JobPosting", "title": "{title}", "description": "<p>{title} bei {org}. {'Aufgaben und Anforderungen. ' * 12}</p>",
 "hiringOrganization": {{"@type": "Organization", "name": "{org}"}}, "jobLocation": {{"@type": "Place", "address": {{"addressLocality": "{city}"}}}},
 "datePosted": "2026-10-01"{ident}}}
</script></head><body><h1>{title}</h1><p>{'Beschreibung. ' * 40}</p></body></html>"""


def listing_page(entries: list[tuple[str, str]], *, nxt: str | None = None, base: str = "") -> str:
    cards = "".join(f'<li class="job"><h3><a href="{base}{href}">{title}</a></h3><span class="location">Köln</span></li>'
                    for title, href in entries)
    pager = f'<div class="pagination"><a rel="next" href="{nxt}">Weiter</a></div>' if nxt else ""
    return f"<html><body><h1>Offene Stellen</h1><ul>{cards}</ul>{pager}<footer><a href='/impressum'>Impressum</a></footer></body></html>"
