"""Pure parsers for public careers pages: no network, no script execution, no form handling."""

from __future__ import annotations

import json
import re
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

from autoapp.employers.repo import Listing
from autoapp.fetch.extract import extract_job

_JOB_PATH = re.compile(
    r"/(?:jobs?|stelle|stellen|stellenangebot(?:e)?|vacanc(?:y|ies)|positions?|details?|openings?|postings?|"
    r"karriere|careers?|offene-stellen|job-?offers?|jobangebote?|ausschreibung(?:en)?)/[^/?#]+", re.I)
_GENERIC_TEXT = re.compile(
    r"^(mehr (?:erfahren|infos?|lesen)|details?|zur stelle|zur stellenanzeige|jetzt bewerben|bewerben|apply(?: now)?|"
    r"read more|learn more|view (?:job|details?)|show more|weiterlesen|ansehen|more|open|öffnen)\s*[»›>→.]*$", re.I)
_NAV_TEXT = re.compile(
    r"^(start(?:seite)?|home|karriere|careers?|jobs?|über uns|about(?: us)?|kontakt|contact|impressum|datenschutz|"
    r"privacy|login|anmelden|registrieren|alle (?:stellen|jobs)|zurück|back|weiter|next|prev(?:ious)?|menü|menu|"
    r"zum seitenanfang|cookie.*|sprache.*|english|deutsch|suche|search|newsletter)$", re.I)
_CAREERS_HINT = re.compile(r"karriere|careers?|stellenangebot|stellenmarkt|offene[- ]stellen|jobs?\b|"
                           r"join[- ]us|arbeiten[- ]bei|vacanc|work with us|jobportal", re.I)
_PAGER_TEXT = re.compile(r"^(?:\d{1,3}|weiter|next|nächste(?: seite)?|[›»>→]|mehr (?:stellen|jobs) laden)$", re.I)
_PAGE_PARAM = re.compile(r"[?&](?:page|seite|p|pg|start|offset)=\d+|/(?:page|seite)/\d+", re.I)
_REF_TEXT = re.compile(
    r"(?:Referenz(?:nummer)?|Kennziffer|Job[- ]?ID|Req(?:uisition)?[- ]?(?:ID|Nr\.?|No\.?)|Reference(?: number)?|"
    r"Stellen-?ID|Ref\.?-?Nr\.?)\s*[:#]?\s*([A-Za-z0-9][\w./-]{2,30})", re.I)
_URL_REFS = (
    re.compile(r"/(?:details?|bewerbung)/(\d{3,})(?:\.html)?(?:$|[/?#])"),
    re.compile(r"/job/(\d{3,})(?:$|[/?#-])"),
    re.compile(r"/jobs?/(\d{3,})(?:$|[/?#-])"),
    re.compile(r"/(\d{6,})-[\w-]+(?:$|[/?#])"),
    re.compile(r"[?&](?:jobid|job_id|id|reqid|ref|jid)=([\w-]{3,30})", re.I),
)
_MAX_LISTINGS = 500
# An action link inside a vacancy card: "Bewerben für St. Ingbert", "Apply in Berlin".
_ACTION_TEXT = re.compile(r"^(?:jetzt\s+)?(?:bewerben|apply(?:\s+now)?)(?:\s+(?:für|fuer|in|for|at)\s+(?P<loc>.{2,60}))?$", re.I)
_CARD_CLASS = re.compile(r"job|stelle|vacanc|position|opening|posting|karriere|career", re.I)
# A vacancy address that carries the gender tag of its title: ``/software-engineer-m-w-d-40624.htm``.
_GENDER_SLUG = re.compile(r"[-_/](?:m-w-d|w-m-d|m-f-d|f-m-d|d-m-w|d-w-m|m-w-x|w-m-x|mwd|wmd|mfd|gn)(?:[-_./]|$)", re.I)
_TITLE_CLASS = re.compile(r"(?:^|[-_])(?:title|titel|headline)$", re.I)
# A vacancy title names a role or carries a gender tag; categories, stories and city pages do not.
_GENDER_HINT = re.compile(r"\(\s*[mwdfx]\s*[/,|]|\(\s*all genders?|\(\s*gn\s*\)|[a-zäöüß](?:\*|:|/)in\b", re.I)
_ROLE_HINT = re.compile(
    r"(?:entwickler|engineer(?!ing)|developer|\bdev\b|manager|berater|consultant|architekt|architect|analyst|scientist|referent|"
    r"sachbearbeit|techniker|technician|ingenieur|administrator|spezialist|specialist|leiter|leitung|\blead\b|"
    r"assisten[tz]|assistant|praktik|\bintern\b|internship|werkstudent|trainee|azubi|ausbildung|duales studium|"
    r"mitarbeiter\b|mitarbeiter(?:in|innen)\b|kaufmann|kauffrau|fachkraft|designer|controller|buchhalter|accountant|"
    r"jurist|koordinator|coordinator|expert|monteur|mechatroniker|elektroniker|konstrukteur|programmier|programmer|"
    r"owner|officer|associate|director|\bhead of\b|operator|redakteur|editor|recruiter|devops|\bsre\b|tester|"
    r"product owner|scrum master|teamlead|betreuer|pfleger|erzieher|sekretär|volontär|professor|doktorand|phd)", re.I)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def is_job_path(path: str) -> bool:
    return bool(_JOB_PATH.search(path))


def looks_like_job_title(title: str) -> bool:
    return bool(_GENDER_HINT.search(title) or _ROLE_HINT.search(title))


_SESSION_PARAMS = frozenset({"sid", "sessionid", "session_id", "jsessionid", "phpsessid", "cfid", "cftoken", "s"})


def stable_url(url: str) -> str:
    """The address without fragment and session parameters (``?sid=...`` changes on every visit)."""
    parts = urlsplit(url.split("#", 1)[0])
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _SESSION_PARAMS]
    path = re.sub(r";jsessionid=[^/?#]*", "", parts.path, flags=re.I)
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


@dataclass(frozen=True)
class ListingLink:
    url: str
    title: str
    location: str | None = None
    ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", stable_url(self.url))


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _http(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def ref_from_url(url: str) -> str | None:
    for pattern in _URL_REFS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def ref_from_text(text: str) -> str | None:
    match = _REF_TEXT.search(text or "")
    return match.group(1).rstrip(".-/") if match else None


# --- JSON-LD ------------------------------------------------------------------------------------


def json_ld_nodes(html: str) -> list[dict]:
    nodes: list[dict] = []

    def walk(value) -> None:  # noqa: ANN001
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            nodes.append(value)
            for key in ("@graph", "itemListElement", "item", "mainEntity"):
                if key in value:
                    walk(value[key])

    for script in _soup(html).find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            walk(json.loads(script.string or script.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return nodes


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {str(x) for x in (t if isinstance(t, list) else [t])}


def json_ld_listing_links(html: str, base: str) -> list[ListingLink]:
    """Vacancies announced by JSON-LD ``ItemList`` entries and ``JobPosting`` nodes carrying a URL."""
    out: list[ListingLink] = []
    for node in json_ld_nodes(html):
        types = _types(node)
        url = node.get("url") or node.get("@id")
        if "JobPosting" in types and isinstance(url, str) and (title := _clean(node.get("title"))):
            absolute = urljoin(base, url)
            if _http(absolute):
                out.append(ListingLink(absolute, title, _first_locality(node), ref_from_url(absolute)))
    return out


def _first_locality(node: dict) -> str | None:
    loc = node.get("jobLocation")
    for item in loc if isinstance(loc, list) else [loc]:
        addr = item.get("address") if isinstance(item, dict) else None
        if isinstance(addr, dict) and addr.get("addressLocality"):
            return _clean(str(addr["addressLocality"]))
    return None


def valid_through(html: str) -> str | None:
    for node in json_ld_nodes(html):
        if "JobPosting" in _types(node) and node.get("validThrough"):
            return str(node["validThrough"])
    return None


def hiring_organization(html: str) -> tuple[str | None, str | None]:
    """(name, official website) of the hiring organization declared in JSON-LD."""
    for node in json_ld_nodes(html):
        org = node.get("hiringOrganization") if "JobPosting" in _types(node) else None
        if isinstance(org, dict):
            same = org.get("sameAs") or org.get("url")
            return (_clean(org.get("name")) or None, same if isinstance(same, str) else None)
    return None, None


def requisition_identifier(html: str) -> str | None:
    for node in json_ld_nodes(html):
        if "JobPosting" in _types(node):
            ident = node.get("identifier")
            value = ident.get("value") if isinstance(ident, dict) else ident
            if value:
                return str(value)[:80]
    return None


# --- HTML listings --------------------------------------------------------------------------------


def _heading(container: Tag) -> Tag | None:
    heading = container.find(re.compile(r"^h[1-6]$"))
    if heading is None:
        heading = container.find(lambda t: any(_TITLE_CLASS.search(c) for c in (t.get("class") or [])))
    return heading


def _heading_text(container: Tag) -> tuple[str, str | None]:
    """(title, location) from the first heading inside a listing card; a trailing ``span`` is the location."""
    heading = _heading(container)
    if heading is None:
        return "", None
    location = None
    span = heading.find("span")
    if span is not None:
        location = _clean(span.get_text(" ")) or None
        span.extract()
    return _clean(heading.get_text(" ")), location


def _card(anchor: Tag) -> Tag | None:
    node: Tag | None = anchor.parent
    for _ in range(5):
        if node is None or node.name in ("body", "html", "main"):
            return None
        if _heading(node) is not None:
            return node
        node = node.parent
    return None


def _in_job_card(anchor: Tag) -> bool:
    node: Tag | None = anchor.parent
    for _ in range(5):
        if node is None or node.name in ("body", "html", "main"):
            return False
        if any(_CARD_CLASS.search(c) for c in (node.get("class") or [])):
            return True
        node = node.parent
    return False


def _location_hint(card: Tag | None) -> str | None:
    if card is None:
        return None
    el = card.find(class_=re.compile(r"location|standort|ort\b|place|city", re.I))
    text = _clean(el.get_text(" ")) if el else ""
    if not text or re.fullmatch(r"[\(\[]?\s*(?:[mwdfx]\s*[/,|]\s*)+[mwdfx]\s*[\)\]]?", text, re.I):
        return None  # a gender tag is not a place
    return text


def html_listing_links(html: str, base: str) -> list[ListingLink]:
    """Static listing entries: anchors to job-detail addresses with a usable title."""
    soup = _soup(html)
    # <noscript> is kept: script-built career sites often serve their full static listing there.
    for node in soup.select("script, style, nav, footer, header"):
        node.decompose()
    seen: dict[str, ListingLink] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href)
        in_card = _in_job_card(anchor)
        path = urlsplit(url).path
        if not _http(url) or not (_JOB_PATH.search(path) or _GENDER_SLUG.search(path) or ref_from_url(url) or in_card):
            continue
        text = _clean(" ".join([anchor.get_text(" "), str(anchor.get("aria-label", ""))]))
        card = _card(anchor)
        title, location = "", None
        action = _ACTION_TEXT.match(text)
        if not text or _GENERIC_TEXT.match(text) or action:
            if card is not None:
                title, location = _heading_text(card)
            if action and action.group("loc"):
                location = _clean(action.group("loc"))
        elif not _NAV_TEXT.match(text) and 4 <= len(text) <= 160 and re.search(r"[A-Za-zÄÖÜäöü]{3}", text):
            title = text
            if card is not None and (card_title := _heading_text(card)[0]) and card_title.casefold() in text.casefold():
                title = card_title
        if not title or _NAV_TEXT.match(title) or len(title) > 200:
            continue
        if not looks_like_job_title(title) and not ref_from_url(url):
            continue
        link = ListingLink(url.split("#", 1)[0], title, location or _location_hint(card), ref_from_url(url))
        seen.setdefault(link.url, link)
        if len(seen) >= _MAX_LISTINGS:
            break
    return list(seen.values())


def listing_links(html: str, base: str) -> list[ListingLink]:
    """JSON-LD entries first, then static HTML entries, without duplicates."""
    merged: dict[str, ListingLink] = {}
    for link in [*json_ld_listing_links(html, base), *html_listing_links(html, base)]:
        merged.setdefault(link.url, link)
    return list(merged.values())


def pagination_links(html: str, base: str) -> list[str]:
    soup = _soup(html)
    found: list[str] = []
    for node in soup.select('link[rel~="next"], a[rel~="next"]'):
        if node.get("href"):
            found.append(urljoin(base, str(node["href"])))
    for container in soup.find_all(class_=re.compile(r"pagina|pager|paging|page-nav", re.I)):
        for anchor in container.find_all("a", href=True):
            if _PAGER_TEXT.match(_clean(anchor.get_text(" "))) or _PAGE_PARAM.search(str(anchor["href"])):
                found.append(urljoin(base, str(anchor["href"])))
    for anchor in soup.find_all("a", href=True):
        if _PAGE_PARAM.search(str(anchor["href"])) and _PAGER_TEXT.match(_clean(anchor.get_text(" "))):
            found.append(urljoin(base, str(anchor["href"])))
    unique = list(dict.fromkeys(u.split("#", 1)[0] for u in found if _http(u)))
    return [u for u in unique if u.split("#", 1)[0] != base.split("#", 1)[0]]


def careers_links(html: str, base: str) -> list[tuple[str, str]]:
    """Links that lead towards a careers/jobs section, best first."""
    soup = _soup(html)
    scored: dict[str, tuple[int, str]] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")) or href.lower().endswith(".pdf"):
            continue
        url = urljoin(base, href).split("#", 1)[0]
        text = _clean(" ".join([anchor.get_text(" "), str(anchor.get("aria-label", ""))]))
        if not _http(url) or not (_CAREERS_HINT.search(text) or _CAREERS_HINT.search(urlsplit(url).path)):
            continue
        score = (2 if _CAREERS_HINT.search(text) else 0) + (1 if _CAREERS_HINT.search(urlsplit(url).path) else 0)
        if url not in scored or scored[url][0] < score:
            scored[url] = (score, text)
    return [(u, t) for u, (s, t) in sorted(scored.items(), key=lambda kv: -kv[1][0])]


def declared_tenants(html: str, base: str) -> list[str]:
    """Career-site tenants declared by the page (``data-url`` of an onapply widget): origins only."""
    out: list[str] = []
    for node in _soup(html).find_all(attrs={"data-url": True}):
        classes = " ".join(node.get("class") or [])
        url = urljoin(base, str(node["data-url"]))
        if "onapply" in classes.lower() and _http(url):
            parts = urlsplit(url)
            out.append(f"{parts.scheme}://{parts.netloc}/")
    return list(dict.fromkeys(out))


def feed_links(html: str, base: str) -> list[str]:
    """RSS/Atom feeds explicitly declared by ``<link rel="alternate">``."""
    out = []
    for node in _soup(html).select('link[rel~="alternate"][href]'):
        kind = str(node.get("type", "")).lower()
        if "rss" in kind or "atom" in kind:
            out.append(urljoin(base, str(node["href"])))
    return list(dict.fromkeys(u for u in out if _http(u)))


def parse_feed(xml_text: str, base: str) -> list[ListingLink]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[ListingLink] = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = None
        for child in item:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "title":
                title = _clean(child.text)
            elif name == "link":
                link = child.get("href") or _clean(child.text)
        if title and link and _http(urljoin(base, link)) and looks_like_job_title(title):
            absolute = urljoin(base, link)
            out.append(ListingLink(absolute, title, None, ref_from_url(absolute)))
    return out


def looks_js_only(html: str) -> bool:
    """A page with (almost) no static text that builds its content with scripts, and no listings."""
    if json_ld_listing_links(html, "https://x.invalid/") or html_listing_links(html, "https://x.invalid/"):
        return False
    soup = _soup(html)
    scripts = len(soup.find_all("script"))
    for node in soup.select("script, style, noscript"):
        node.decompose()
    text = _clean(soup.get_text(" "))
    shell = soup.find(id=re.compile(r"^(?:root|app|__next|__nuxt)$")) is not None or "__NEXT_DATA__" in html
    return len(text) < 400 and (shell or scripts >= 5)


# --- detail pages --------------------------------------------------------------------------------


def listing_from_detail(html: str, url: str, *, hint: ListingLink | None = None) -> Listing | None:
    """A typed listing from a vacancy detail page (JSON-LD first, then the shared extractor)."""
    data = extract_job(html, url)
    title = data.title or (hint.title if hint else None)
    if not title:
        return None
    text = _clean(BeautifulSoup(html, "html.parser").get_text(" "))
    ref = ref_from_url(url) or requisition_identifier(html) or ref_from_text(text) or (hint.ref if hint else None)
    locations = data.locations or ([data.location] if data.location else []) or ([hint.location] if hint and hint.location else [])
    return Listing(
        url=url.split("#", 1)[0], title=title, ref=ref, locations=[loc for loc in locations if loc],
        work_mode=data.work_mode, description=data.description, posted_at=data.posted_at.isoformat() if data.posted_at else None,
        expires_at=valid_through(html), salary_min=data.ad_salary_min, salary_max=data.ad_salary_max,
        apply_url=data.application_url,
    )
