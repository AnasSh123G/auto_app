"""Public hints for employer search: only employer, job title and city, never personal data."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[^\s<>\"']+@[^\s<>\"']+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# A tracking id or token has digits; a long hyphenated title ("AI-Application-Developer") has none.
_TOKEN = re.compile(r"\b(?=[A-Za-z_\-+/=%]*\d)[A-Za-z0-9_\-+/=%]{24,}\b")
_PHONE = re.compile(r"\+?\d[\d\s()/\-]{7,}\d")
_BAD_CHARS = re.compile(r"[^\w\s.&,'()/+\-ÄÖÜäöüß]", re.UNICODE)
_GENDER = re.compile(r"\(?\b[mwfd]\s*/\s*[mwfd](?:\s*/\s*[mwfd])?\b\)?|\*in\b|\(all genders\)", re.IGNORECASE)


def sanitize_hint(text: str | None, *, max_len: int = 80) -> str:
    """Remove anything that is not a plain public name: emails, links, tokens, phone numbers."""
    value = text or ""
    for pattern in (_EMAIL, _URL, _TOKEN, _PHONE):
        value = pattern.sub(" ", value)
    value = _BAD_CHARS.sub(" ", value)
    return " ".join(value.split())[:max_len].strip()


def clean_title(title: str | None) -> str:
    """The job title without gender markers such as (m/w/d)."""
    return sanitize_hint(_GENDER.sub(" ", title or ""))


_LEGAL_SUFFIX = re.compile(r"[\s,]+(?:(?:gmbh|ag|se|ug|ltd\.?|b\.?v\.?)?\s*&\s*co\.?\s*kg(?:aa)?|gmbh|ag|se|kg|ug(?:\s*\(haftungsbeschränkt\))?|mbh|ohg|gbr|e\.?\s?v\.?|"
                           r"kgaa|inc\.?|ltd\.?|llc|plc)\.?\s*$", re.IGNORECASE)


def short_name(name: str) -> str:
    """The employer name without its legal form, so queries stay a few words long."""
    value = sanitize_hint(name)
    stripped = _LEGAL_SUFFIX.sub("", value).strip(" ,")
    return stripped or value


def build_search(company: str, *, title: str | None = None, city: str | None = None,
                 refined: bool = False) -> tuple[str, list[str]]:
    """The objective and keyword queries for one employer lookup (refined = the second, narrower try)."""
    name = short_name(company)
    place = sanitize_hint(city, max_len=40)
    role = clean_title(title)
    objective = ("Find the official employer website and careers page of the company named "
                 f"{name!r}" + (f" located in or near {place}" if place else "") +
                 ". Prefer the employer's own domain over job boards and directories.")
    if refined:
        queries = [f"{name} Impressum", f"{name} Karriere {role}".strip()]
        if place:
            queries.append(f"{name} {place} Unternehmen")
    else:
        # The ad's own title in quotes finds pages on the employer's site even when its name is shared abroad.
        full = sanitize_hint(title, max_len=80).replace('"', "")
        queries = [f'"{name}" "{full}"' if full else f"{name} careers", f"{name} Karriere Stellenangebote"]
        if place:
            queries.append(f"{name} jobs {place}")
    return objective, [q[:120] for q in queries if q.strip()]
