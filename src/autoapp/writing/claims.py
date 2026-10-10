"""Claim check for generated application texts (cover-letter-writing spec, "Claim check").

* ``cv`` / ``profile`` / ``instructions`` claims need an evidence quote that occurs in
  that source (case- and whitespace-insensitive).
* ``assumed_basic`` claims must name a requirement the evaluation classified as
  bridgeable, only for decision ``apply``, and at most ``max_assumed`` of them.
* Independently of what the model tagged, every number in the text must occur in a
  source (CV, profile, instructions or the ad). This catches untagged inventions such
  as "5 Jahre Erfahrung".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping

from autoapp.writing.schema import Claim

_QUOTES = str.maketrans({"„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", " ": " "})
_NUMBER = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)*)(?:\s?%)?")
_SOURCE_NAMES = {"cv": "the CV", "profile": "the profile", "instructions": "your writing instructions"}


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    t = re.sub(r"[‐-―]", "-", t)
    return re.sub(r"\s+", " ", t).strip().strip(".,;:!?\"'()")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUMBER.finditer(text):
        raw = m.group(1)
        out.add(re.sub(r"[.,]", "", raw) if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw) else raw.replace(",", "."))
    return out


def unsupported_numbers(text: str, sources: Iterable[str]) -> list[str]:
    """Numbers in ``text`` that no source contains."""
    known: set[str] = set()
    for s in sources:
        known |= _numbers(s or "")
    return sorted(n for n in _numbers(text) if n not in known)


def check_claims(
    claims: Iterable[Claim],
    *,
    sources: Mapping[str, str],
    bridgeable: Iterable[str],
    decision: str,
    max_assumed: int,
) -> list[str]:
    """Return human-readable problems; empty means every claim is acceptable.

    ``sources`` maps ``cv``, ``profile`` and ``instructions`` to their text.
    """
    norm_sources = {k: normalize(v or "") for k, v in sources.items()}
    bridge = {normalize(b): b for b in bridgeable}
    problems: list[str] = []
    assumed = 0
    for c in claims:
        if c.type == "assumed_basic":
            assumed += 1
            req = normalize(c.requirement or "")
            if decision != "apply":
                problems.append(f'"{c.text}": skills outside the CV may only be claimed for a close match')
            elif not req or not any(req == b or req in b or b in req for b in bridge):
                problems.append(
                    f'"{c.text}": not backed by the CV, profile or instructions, and not one of the '
                    "bridgeable requirements; remove it"
                )
            continue
        ev = normalize(c.evidence or "")
        src = norm_sources.get(c.type, "")
        if not ev:
            problems.append(f'"{c.text}": no evidence quote from {_SOURCE_NAMES[c.type]}; remove it or quote the source')
        elif ev not in src:
            problems.append(
                f'"{c.text}": the quoted evidence does not appear in {_SOURCE_NAMES[c.type]}; remove the claim '
                "or quote the source exactly"
            )
    if assumed > max_assumed:
        problems.append(f"{assumed} skills outside the CV are claimed; at most {max_assumed} are allowed")
    return problems
