"""Deterministic letter frame: subject, salutation, closing, German date line."""

from __future__ import annotations

import re
from datetime import date

GERMAN_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)
ENGLISH_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_FEMALE = ("Frau", "Ms", "Ms.", "Mrs", "Mrs.")
_GENDER_TAG = re.compile(r"\s*[(\[]\s*(?:[mwdfx]\s*/\s*)+[mwdfx]\s*[)\]]|\s*\((?:all genders|gn|a\*)\)", re.I)


def date_line(city: str, day: date, language: str) -> str:
    """'Berlin, 7. Oktober 2026' or 'Berlin, 7 October 2026'."""
    if language == "de":
        return f"{city}, {day.day}. {GERMAN_MONTHS[day.month - 1]} {day.year}"
    return f"{city}, {day.day} {ENGLISH_MONTHS[day.month - 1]} {day.year}"


def clean_title(title: str) -> str:
    """Drop gender tags such as (m/w/d) for use in a subject line."""
    return _GENDER_TAG.sub("", title).strip(" -–—")


def subject(title: str, reference: str | None, language: str) -> str:
    t = clean_title(title)
    if language == "de":
        s = f"Bewerbung als {t}"
        return f"{s}, Kennziffer {reference}" if reference else s
    s = f"Application for the position of {t}"
    return f"{s}, reference {reference}" if reference else s


def salutation(contact_person: str | None, language: str) -> str:
    """Personal salutation when the contact's title is known, otherwise the neutral form."""
    c = (contact_person or "").strip()
    m = re.match(r"^(Frau|Herr|Ms\.?|Mrs\.?|Mr\.?)\s+(?:(Dr\.|Prof\.(?:\s*Dr\.)?)\s+)?(.+)$", c)
    if language == "de":
        if m:
            title, acad, name = m.group(1), m.group(2), m.group(3)
            last = name.split()[-1]
            prefix = f"{acad} " if acad else ""
            if title in _FEMALE:
                return f"Sehr geehrte Frau {prefix}{last},"
            return f"Sehr geehrter Herr {prefix}{last},"
        if c and len(c.split()) >= 2:
            return f"Guten Tag {c},"
        return "Sehr geehrte Damen und Herren,"
    if m:
        title, acad, name = m.group(1), m.group(2), m.group(3)
        last = name.split()[-1]
        honorific = "Ms" if title in _FEMALE else "Mr"
        if acad and "Dr" in acad:
            honorific = "Dr"
        return f"Dear {honorific} {last},"
    if c and len(c.split()) >= 2:
        return f"Dear {c},"
    return "Dear Hiring Team,"


def closing(language: str) -> str:
    return "Mit freundlichen Grüßen" if language == "de" else "Kind regards"
