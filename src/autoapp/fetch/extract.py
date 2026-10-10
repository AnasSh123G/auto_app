"""Extract a job posting from page HTML: JSON-LD ``JobPosting`` first, then per-site selectors,
then the largest text block. Pure functions, no network."""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from pydantic import BaseModel, Field

_BLOCK_TAGS = {"p", "div", "section", "article", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}
_ANNUAL_FACTOR = {"YEAR": 1, "ANNUAL": 1, "MONTH": 12, "WEEK": 52}
_MIN_DESCRIPTION_CHARS = 200  # a selector hit shorter than this is probably not the ad body


class ExtractedJob(BaseModel):
    """Fields recovered from a job page. ``ad_salary_*`` is annual EUR as stated in the markup."""

    method: Literal["json-ld", "selectors", "fallback", "none"] = "none"
    title: str | None = None
    company: str | None = None
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    description: str | None = None
    posted_at: date | None = None
    ad_salary_min: int | None = None
    ad_salary_max: int | None = None
    work_mode: str | None = None
    working_time: str | None = None
    contract_type: str | None = None
    application_url: str | None = None


# ----------------------------------------------------------------------------- text


def html_to_text(fragment: str | Tag) -> str:
    """Convert HTML (or a parsed tag) to readable plain text with line breaks and bullet points."""
    soup = BeautifulSoup(fragment, "html.parser") if isinstance(fragment, str) else fragment
    for t in soup.find_all(["script", "style", "noscript", "template"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, NavigableString("\n- "))
    for tag in soup.find_all(list(_BLOCK_TAGS)):
        tag.append(NavigableString("\n"))
    text = soup.get_text()
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _description_to_text(value: str) -> str:
    """JSON-LD descriptions are HTML, entity-escaped HTML, or plain text."""
    if "<" not in value and "&lt;" in value:
        value = html_lib.unescape(value)
    if "<" in value:
        return html_to_text(value)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in html_lib.unescape(value).splitlines()]
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- JSON-LD


def _flatten(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [d for item in node for d in _flatten(item)]
    if isinstance(node, dict):
        out = [node]
        if "@graph" in node:
            out.extend(_flatten(node["@graph"]))
        return out
    return []


def _is_job_posting(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(str(k).rsplit("/", 1)[-1] == "JobPosting" for k in kinds)


def _find_job_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            continue
        for node in _flatten(data):
            if _is_job_posting(node):
                return node
    return None


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".")) if value not in (None, "") else None
    except ValueError:
        return None


def _salary(base: Any) -> tuple[int | None, int | None]:
    """Annual EUR (min, max) from a schema.org ``baseSalary``; hourly pay and other currencies give none."""
    if isinstance(base, list):
        base = base[0] if base else None
    if not isinstance(base, dict):
        return None, None
    currency = str(base.get("currency") or "EUR").upper()
    value = base.get("value", base)
    if isinstance(value, list):
        value = value[0] if value else None
    unit = str(base.get("unitText") or "").upper()
    if isinstance(value, dict):
        currency = str(value.get("currency") or currency).upper()
        unit = str(value.get("unitText") or unit).upper()
        low, high = _num(value.get("minValue")), _num(value.get("maxValue"))
        single = _num(value.get("value"))
    else:
        low = high = None
        single = _num(value)
    if currency != "EUR":
        return None, None
    if low is None and high is None and single is not None:
        low = high = single
    factor = _ANNUAL_FACTOR.get(unit)
    if factor is None or (low is None and high is None):
        return None, None
    lo = int(round(low * factor)) if low is not None else None
    hi = int(round(high * factor)) if high is not None else None
    return lo or hi, hi or lo


def _parse_date(value: Any) -> date | None:
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _localities(location: Any) -> list[str]:
    out: list[str] = []
    for loc in location if isinstance(location, list) else [location]:
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address") or {}
        city = addr.get("addressLocality") if isinstance(addr, dict) else addr if isinstance(addr, str) else None
        if city and city not in out:
            out.append(str(city).strip())
    return out


def _from_json_ld(node: dict[str, Any]) -> ExtractedJob:
    org = node.get("hiringOrganization")
    company = org.get("name") if isinstance(org, dict) else org if isinstance(org, str) else None
    cities = _localities(node.get("jobLocation"))
    low, high = _salary(node.get("baseSalary"))
    types = node.get("employmentType") or []
    types = [str(t).upper() for t in (types if isinstance(types, list) else [types])]
    full, part = "FULL_TIME" in types, "PART_TIME" in types
    working_time = "full_time" if full and not part else "part_time" if part and not full else None
    contract = "fixed_term" if {"TEMPORARY", "CONTRACTOR", "CONTRACT"} & set(types) else (
        "permanent" if "PERMANENT" in types else None
    )
    remote = str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    desc = node.get("description")
    url = node.get("url")
    return ExtractedJob(
        method="json-ld",
        title=(node.get("title") or node.get("name") or None),
        company=company,
        location=cities[0] if cities else None,
        locations=cities,
        description=_description_to_text(desc) if isinstance(desc, str) and desc.strip() else None,
        posted_at=_parse_date(node.get("datePosted")),
        ad_salary_min=low,
        ad_salary_max=high,
        work_mode="remote" if remote else None,
        working_time=working_time,
        contract_type=contract,
        application_url=url if node.get("directApply") and isinstance(url, str) else None,
    )


# ---------------------------------------------------------------------------- selectors

# host suffix -> field -> CSS selectors tried in order. Best effort; the markup changes often.
SITE_SELECTORS: dict[str, dict[str, list[str]]] = {
    "stepstone.de": {
        "title": ['[data-at="header-job-title"]', "h1"],
        "company": ['[data-at="metadata-company-name"]', '[data-at="header-company-name"]'],
        "location": ['[data-at="metadata-location"]', '[data-at="job-location"]'],
        "description": ['[data-at="job-ad-content"]', '[data-genesis-element="JOB_AD_CONTENT"]'],
    },
    "linkedin.com": {
        "title": ["h1.top-card-layout__title", "h1.topcard__title", "h1"],
        "company": ["a.topcard__org-name-link", ".top-card-layout__card .topcard__flavor"],
        "location": [".topcard__flavor--bullet", ".top-card-layout__second-subline .topcard__flavor"],
        "description": ["div.show-more-less-html__markup", "div.description__text"],
    },
    "indeed.com": {
        "title": ['[data-testid="jobsearch-JobInfoHeader-title"]', "h1.jobsearch-JobInfoHeader-title", "h1"],
        "company": ['[data-testid="inlineHeader-companyName"]', '[data-company-name="true"]'],
        "location": ['[data-testid="inlineHeader-companyLocation"]', '[data-testid="job-location"]'],
        "description": ["#jobDescriptionText", ".jobsearch-JobComponent-description"],
    },
}
SITE_SELECTORS["indeed.de"] = SITE_SELECTORS["indeed.com"]


def _site_table(url: str) -> dict[str, list[str]] | None:
    host = (urlsplit(url).hostname or "").lower()
    for suffix, table in SITE_SELECTORS.items():
        if host == suffix or host.endswith("." + suffix):
            return table
    return None


def _first(soup: BeautifulSoup, selectors: list[str]) -> Tag | None:
    for sel in selectors:
        try:
            tag = soup.select_one(sel)
        except Exception:  # invalid selector in the table; ignore
            continue
        if tag is not None and tag.get_text(strip=True):
            return tag
    return None


def _from_selectors(soup: BeautifulSoup, table: dict[str, list[str]]) -> ExtractedJob | None:
    desc_tag = _first(soup, table["description"])
    if desc_tag is None:
        return None
    description = html_to_text(desc_tag)
    if len(description) < _MIN_DESCRIPTION_CHARS and not _first(soup, table["title"]):
        return None
    texts = {k: (t.get_text(" ", strip=True) if (t := _first(soup, table[k])) else None) for k in ("title", "company", "location")}
    return ExtractedJob(
        method="selectors",
        title=texts["title"],
        company=texts["company"],
        location=texts["location"],
        locations=[texts["location"]] if texts["location"] else [],
        description=description,
    )


# ----------------------------------------------------------------------------- fallback


def _largest_block(soup: BeautifulSoup) -> Tag | None:
    body = soup.body or soup
    for t in body.find_all(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        t.decompose()

    def size(tag: Tag) -> int:
        return len(tag.get_text(" ", strip=True))

    node: Tag = body
    while True:
        total = size(node)
        children = [c for c in node.find_all(recursive=False) if isinstance(c, Tag)]
        best = max(children, key=size, default=None)
        if best is None or total == 0 or size(best) < 0.6 * total:
            break
        node = best
    return node if size(node) else None


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if isinstance(tag, Tag) and tag.get("content"):
            return str(tag["content"]).strip()
    return None


def _page_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if isinstance(h1, Tag) and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    og = _meta(soup, "og:title")
    if og:
        return og
    return soup.title.get_text(strip=True) if soup.title and soup.title.get_text(strip=True) else None


def _from_fallback(soup: BeautifulSoup) -> ExtractedJob:
    title = _page_title(soup)  # read before the block search removes header elements
    company = _meta(soup, "og:site_name")
    block = _largest_block(soup)
    return ExtractedJob(
        method="fallback",
        title=title,
        company=company,
        description=html_to_text(block) if block is not None else None,
    )


# ------------------------------------------------------------------------------ public


def extract_job(html: str, url: str) -> ExtractedJob:
    """Extract a job from page ``html`` fetched from ``url``.

    JSON-LD ``JobPosting`` wins; a per-site selector table and the largest text block fill in
    only what JSON-LD lacked (typically the description).
    """
    soup = BeautifulSoup(html, "html.parser")
    node = _find_job_posting(soup)
    result = _from_json_ld(node) if node else None
    if result and result.description:
        return result

    table = _site_table(url)
    stage = (_from_selectors(soup, table) if table else None) or _from_fallback(soup)
    if result is None:
        return stage
    # JSON-LD without a usable description: keep its structured fields, take the text from the later stage.
    result.description = stage.description
    result.title = result.title or stage.title
    result.company = result.company or stage.company
    result.location = result.location or stage.location
    if not result.locations and stage.locations:
        result.locations = stage.locations
    return result


# What job sites show on an ad that no longer takes applications (LinkedIn, StepStone, Indeed, applicant systems),
# in German and English. Phrases that do not occur in a normal job description.
_CLOSED = re.compile(
    r"no longer accepting applications|(?:es )?werden keine bewerbungen mehr angenommen|"
    r"bewerbungen werden nicht mehr angenommen|nimmt keine bewerbungen mehr an|"
    r"(?:diese|die) (?:stelle|stellenanzeige|anzeige|position) ist (?:leider )?(?:nicht mehr (?:verf(?:ü|ue)gbar|aktiv|"
    r"online|besetzbar)|bereits besetzt|abgelaufen)|"
    r"this (?:job|position|posting|vacancy) (?:is no longer available|has expired|has been filled|is closed)|"
    r"the employer is not accepting applications", re.I)


def closed_notice(html: str | None) -> str | None:
    """The page's own words when it says the ad is closed (\"No longer accepting applications\"), else ``None``."""
    if not html:
        return None
    text = " ".join(html_to_text(html).split())
    match = _CLOSED.search(text)
    return match.group(0) if match else None
