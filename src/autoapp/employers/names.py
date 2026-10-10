"""Employer-name normalisation for candidate retrieval (never an identity key on its own)."""

from __future__ import annotations

import re
import unicodedata

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "à": "a"})
_LEGAL_FORMS = frozenset({
    "gmbh", "ag", "se", "kg", "ug", "mbh", "ohg", "gbr", "ev", "e", "v", "kgaa", "co", "inc", "ltd", "llc",
    "plc", "sa", "bv", "nv", "gmbhcokg", "haftungsbeschraenkt",
})
_FILLER = frozenset({"und", "and", "the", "der", "die", "das"})


def normalize_name(name: str) -> str:
    """Case-folded, umlaut-transliterated, punctuation-free name; legal forms are kept."""
    text = unicodedata.normalize("NFKC", name or "").casefold().translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text.replace("&", " and "))
    return " ".join(text.split())


def legal_form(name: str) -> str:
    tokens = [t for t in normalize_name(name).split() if t in _LEGAL_FORMS]
    return " ".join(tokens)


def core_name(name: str) -> str:
    """The name without legal forms and filler words, used only to retrieve candidates."""
    tokens = [t for t in normalize_name(name).split() if t not in _LEGAL_FORMS and t not in _FILLER]
    return " ".join(tokens)


def host_labels(host: str) -> list[str]:
    """Registrable-looking labels of a host, e.g. ``jobs.cbc-cologne.de`` -> ``['jobs', 'cbc-cologne', 'de']``."""
    return [p for p in (host or "").lower().strip(".").split(".") if p]
