"""Deterministic style linter and dash fixer for generated prose (design decision 9).

The linter never calls an LLM. Phrase and connector lists are packaged text files
(``phrases_de.txt``, ``phrases_en.txt``, ``connectors.txt``): one lowercase entry per line,
``#`` starts a comment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

ViolationKind = Literal["dash", "phrase", "exclamation", "question", "connectors", "length"]

MAX_CONNECTORS = 2

_DASH_RE = re.compile(r"[–—]|\s-\s")
_WORD_RE = re.compile(r"\b\w[\w'-]*\b")


@dataclass(frozen=True)
class Violation:
    """One style problem: its kind and a human-readable detail for the rewrite prompt."""

    kind: ViolationKind
    detail: str


# ------------------------------------------------------------------ list loading


def parse_list(text: str) -> list[str]:
    """Parse a list file: one phrase per line, blank lines and ``#`` comments ignored."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            out.append(line)
    return out


def _read_packaged(name: str) -> list[str]:
    return parse_list(resources.files("autoapp.writing").joinpath(name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def builtin_phrases() -> tuple[str, ...]:
    """Built-in German and English phrases (both are always checked)."""
    seen: dict[str, None] = {}
    for name in ("phrases_de.txt", "phrases_en.txt"):
        for phrase in _read_packaged(name):
            seen.setdefault(phrase)
    return tuple(seen)


@lru_cache(maxsize=1)
def builtin_connectors() -> tuple[str, ...]:
    """Built-in connector openers checked for overuse."""
    return tuple(_read_packaged("connectors.txt"))


# ------------------------------------------------------------------ helpers


def _norm(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def _term_regex(term: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in _norm(term).lower().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def count_words(text: str) -> int:
    """Count words as ``\\b\\w[\\w'-]*\\b`` tokens (Unicode-aware)."""
    return len(_WORD_RE.findall(text))


def _snippet(text: str, start: int, end: int, pad: int = 25) -> str:
    return " ".join(text[max(0, start - pad) : end + pad].split())


# ------------------------------------------------------------------ lint


def lint(
    text: str,
    *,
    language: str,
    max_words: int | None,
    banned_extra: Iterable[str] = (),
    allowed: Iterable[str] = (),
    phrases: Iterable[str] | None = None,
    connectors: Iterable[str] | None = None,
) -> list[Violation]:
    """Check ``text`` (the letter body) and return all violations.

    ``language`` is accepted for the caller's convenience; phrases of both languages are always
    checked, whatever the letter language. ``phrases`` and ``connectors`` default to the packaged
    lists. Effective phrases are ``(phrases + banned_extra) - allowed``.
    """
    del language  # both phrase lists apply regardless of letter language
    violations: list[Violation] = []

    for m in _DASH_RE.finditer(text):
        violations.append(
            Violation("dash", f"dash used as punctuation: …{_snippet(text, m.start(), m.end())}…")
        )

    base = builtin_phrases() if phrases is None else tuple(phrases)
    allowed_set = {a.strip().lower() for a in allowed}
    effective: dict[str, None] = {}
    for p in [*base, *banned_extra]:
        p = p.strip().lower()
        if p and p not in allowed_set:
            effective.setdefault(p)
    normed = _norm(text)
    for phrase in effective:
        m = _term_regex(phrase).search(normed)
        if m:
            violations.append(Violation("phrase", f"banned phrase: {phrase!r}"))

    for m in re.finditer(r"!", text):
        violations.append(
            Violation("exclamation", f"exclamation mark: …{_snippet(text, m.start(), m.end())}…")
        )
    for m in re.finditer(r"\?", text):
        violations.append(Violation("question", f"question mark: …{_snippet(text, m.start(), m.end())}…"))

    conns = builtin_connectors() if connectors is None else tuple(connectors)
    counts: dict[str, int] = {}
    for c in conns:
        c = c.strip().lower()
        if not c or c in counts:
            continue
        n = len(_term_regex(c).findall(normed))
        if n:
            counts[c] = n
    total = sum(counts.values())
    if total > MAX_CONNECTORS:
        listed = ", ".join(f"{c} x{n}" for c, n in counts.items())
        violations.append(
            Violation("connectors", f"{total} connector openers (at most {MAX_CONNECTORS} allowed): {listed}")
        )

    if max_words is not None:
        words = count_words(text)
        if words > max_words:
            violations.append(Violation("length", f"{words} words, limit is {max_words}"))

    return violations


# ------------------------------------------------------------------ dash fixer

_SENT = "\x00"  # marks a comma we introduce, so existing commas are never altered
_NUM_RANGE = re.compile(r"(?<=\d)(?:[ \t]*[–—][ \t]*|[ \t]+-[ \t]+)(?=\d)")
_LINE_LEAD = re.compile(r"(?m)^[ \t]*[–—-](?:[ \t]+|$)")
_SPACED = re.compile(r"[ \t]*[–—][ \t]*|[ \t]+-(?:[ \t]+|(?=\n|$))")


def fix_dashes(text: str) -> str:
    """Replace dash punctuation deterministically; in-word hyphens are never touched.

    Spaced or unspaced em/en dashes and spaced hyphens become a comma (or are dropped at line
    starts and before punctuation); numeric ranges become ``2019-2021``. Afterwards ``lint``
    reports no dash violations.
    """
    out = _NUM_RANGE.sub("-", text)
    out = _LINE_LEAD.sub("", out)
    out = _SPACED.sub(_SENT, out)
    out = re.sub(r"[–—]", _SENT, out)  # any leftover (e.g. at the very start/end)
    out = re.sub(r"\s-\s", _SENT, out)  # safety net: guarantees no spaced hyphen remains
    out = re.sub(_SENT + "{2,}", _SENT, out)
    out = re.sub(r"(?m)^" + _SENT + r"[ \t]*", "", out)
    out = re.sub(r",[ \t]*" + _SENT, _SENT, out)  # the marker becomes the comma
    out = re.sub(_SENT + r"[ \t]*(?=[.!?;:,])", "", out)
    out = re.sub(_SENT + r"[ \t]*(?=\n[ \t]*\n|\Z)", ".", out)
    out = re.sub(_SENT + r"[ \t]*(?=\n)", ",", out)
    out = out.replace(_SENT, ", ")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r",[ \t]*,+", ",", out)
    return out
