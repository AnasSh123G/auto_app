"""Slugs and application folder names (``application_<company>_<title>_<shortid>``)."""

from __future__ import annotations

import re
import unicodedata

MAX_FOLDER_LEN = 99  # folder names stay under 100 characters
PREFIX = "application_"

_TRANSLIT = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ẞ": "SS",
}  # fmt: skip

_GENDER_TOKEN = r"(?:m|w|d|f|x|divers|diverse|gn|all genders|alle geschlechter|alle)"
_GENDER_TAG = re.compile(
    rf"[(\[]\s*{_GENDER_TOKEN}(?:\s*[/|*:,-]\s*{_GENDER_TOKEN})*\s*[)\]]"
    r"|(?<![\w/])[mwf]\s*/\s*[mwf]\s*/\s*[dx](?![\w/])",
    re.IGNORECASE,
)


def slugify(text: str, max_len: int = 60) -> str:
    """Lowercase ASCII, hyphen-separated slug of at most ``max_len`` characters.

    German umlauts and ß are transliterated, gender tags such as ``(m/w/d)`` are dropped and
    all punctuation (including ``&``) becomes a separator. Never ends in a hyphen.
    """
    text = _GENDER_TAG.sub(" ", text)
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[: max(max_len, 0)].strip("-")


def folder_name(company: str, title: str, short_id: str, marker: str | None = None) -> str:
    """Build ``application_<company>_<title>_<shortid>[_<MARKER>]``, under 100 characters in total.

    Company and title share the remaining space fairly: a short one keeps its full length and
    the other may use what is left over. A marker (see ``package.folders``) takes its space first.
    """
    sid = slugify(short_id, 32) or "x"
    tail = f"_{marker}" if marker else ""
    budget = MAX_FOLDER_LEN - len(PREFIX) - 2 - len(sid) - len(tail)
    comp = slugify(company, 200) or "unknown"
    ttl = slugify(title, 200) or "unknown"
    half = budget // 2
    if len(comp) + len(ttl) > budget:
        if len(comp) <= half:
            ttl = ttl[: budget - len(comp)]
        elif len(ttl) <= budget - half:
            comp = comp[: budget - len(ttl)]
        else:
            comp, ttl = comp[:half], ttl[: budget - half]
        comp = comp.strip("-") or "unknown"
        ttl = ttl.strip("-") or "unknown"
    return f"{PREFIX}{comp}_{ttl}_{sid}{tail}"
