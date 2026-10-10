import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from autoapp.assist.filler import is_restricted

APPLY = re.compile(r"bewerb|apply|application|zur stelle", re.I)
NO_EMAIL = re.compile(r"ohne.{0,20}e.?mail|without.{0,20}e.?mail|skip.{0,20}e.?mail", re.I)
EXCLUDED = re.compile(r"datenschutz|privacy|impressum|terms|share|teilen|newsletter|subscribe|"
                      r"absenden|abschicken|submit|send.?application|konto.?erstellen|create.?account", re.I)


@dataclass(frozen=True)
class Candidate:
    url: str
    text: str
    score: int


def valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username and not parts.password


def links(html: str, base: str, restricted: list[str]) -> list[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.select("a[href], iframe[src]"):
        url = urljoin(base, str(node.get("href") or node.get("src")))
        text = " ".join([node.get_text(" ", strip=True), str(node.get("aria-label", "")), str(node.get("title", ""))])
        if not valid_url(url) or EXCLUDED.search(text + " " + url):
            continue
        if node.name == "iframe" and not APPLY.search(text + " " + url):
            continue
        score = 3 if NO_EMAIL.search(text) else 2 if APPLY.search(text) else 1 if APPLY.search(urlsplit(url).path) else 0
        if is_restricted(url, restricted):
            # Keep clearly intended restricted targets so finding can report the handoff.
            if score:
                out.append(Candidate(url, text, score))
            continue
        out.append(Candidate(url, text, score))
    refresh = soup.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
    if refresh:
        match = re.search(r"url\s*=\s*['\"]?([^'\"]+)", str(refresh.get("content", "")), re.I)
        if match:
            out.insert(0, Candidate(urljoin(base, match.group(1).strip()), "meta refresh", 4))
    unique = {candidate.url: candidate for candidate in out}
    result = sorted(unique.values(), key=lambda candidate: -candidate.score)
    if len(result) == 1 and not soup.select_one("input, textarea, select") and len(soup.get_text(" ", strip=True)) < 300:
        result = [Candidate(result[0].url, result[0].text, max(result[0].score, 2))]
    return result
