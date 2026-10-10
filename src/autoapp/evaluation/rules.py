"""Code-side rules applied to the LLM's judgement (design decision 8).

The LLM classifies; these functions make the final, deterministic calls:
bridgeable re-check, dealbreaker re-check, decision thresholds and email grounding.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from autoapp.evaluation.schema import Decision, EvaluationResult, FinalEvaluation, Requirement

CEFR_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "NATIVE": 7}
_GERMAN_KEYS = ("de", "deutsch", "german")


def _level(value: str | None) -> int | None:
    if not value:
        return None
    return CEFR_ORDER.get(value.strip().upper())


def _skill_pattern(name: str) -> re.Pattern[str]:
    # Word-ish boundaries that also work for names like "C++" or "Node.js".
    return re.compile(rf"(?<![\w+#.]){re.escape(name.strip())}(?![\w+#])", re.IGNORECASE)


def reclassify_requirements(
    requirements: Iterable[Requirement], extra_skill_names: Iterable[str]
) -> tuple[list[Requirement], list[str]]:
    """Enforce the bridgeable rule and count profile extra skills as matched.

    A requirement can only stay ``bridgeable`` if its proficiency is basic/unspecified,
    it is not core and it names no years. Returns the new list and notes on changes.
    """
    patterns = [(n, _skill_pattern(n)) for n in extra_skill_names if n.strip()]
    out: list[Requirement] = []
    notes: list[str] = []
    for req in requirements:
        new = req
        if req.status != "matched":
            hit = next((n for n, p in patterns if p.search(req.text)), None)
            if hit:
                new = req.model_copy(update={"status": "matched", "evidence": f"profile extra skill: {hit}"})
                notes.append(f"'{req.text}' matched by profile extra skill '{hit}'")
        if new.status == "bridgeable" and (
            new.proficiency in ("intermediate", "advanced") or new.core or new.years_required
        ):
            why = (
                "is core to the role"
                if new.core
                else f"names {new.years_required} years"
                if new.years_required
                else f"asks for {new.proficiency} proficiency"
            )
            new = new.model_copy(update={"status": "missing"})
            notes.append(f"'{req.text}' is not bridgeable because it {why}")
        out.append(new)
    return out, notes


def german_dealbreaker(
    required: str | None, language_levels: Mapping[str, str], max_required: str | None
) -> str | None:
    """Return a dealbreaker text if the ad's German requirement exceeds the profile."""
    req = _level(required)
    if req is None:
        return None
    own = next((_level(v) for k, v in language_levels.items() if k.strip().lower() in _GERMAN_KEYS), None)
    if own is not None and req > own:
        return f"German {required} required, profile has {_name(own)}"
    cap = _level(max_required)
    if cap is not None and req > cap:
        return f"German {required} required, above the profile's limit of {max_required}"
    return None


def _name(level: int) -> str:
    return next(k for k, v in CEFR_ORDER.items() if v == level).replace("NATIVE", "native")


def decide(fit_score: int, dealbreakers: list[str], apply_threshold: int, maybe_threshold: int) -> Decision:
    """apply needs the score and no dealbreakers; maybe needs the lower score; else skip."""
    if fit_score >= apply_threshold and not dealbreakers:
        return "apply"
    if fit_score >= maybe_threshold:
        return "maybe"
    return "skip"




def ground_email(email: str | None, sources: Iterable[str | None]) -> str | None:
    """Keep ``email`` only if it appears verbatim (case-insensitive) in a source text."""
    if not email:
        return None
    needle = email.strip().lower()
    if not needle or "@" not in needle:
        return None
    for text in sources:
        if text and needle in text.lower():
            return email.strip()
    return None


def finalize(
    result: EvaluationResult,
    *,
    profile: Any,
    apply_threshold: int,
    maybe_threshold: int,
    source_texts: Iterable[str | None],
) -> FinalEvaluation:
    """Apply every code-side rule to the raw judgement.

    ``profile`` is the loaded profile model; only ``extra_skills``,
    ``language_levels`` and ``dealbreakers`` are read (attribute access).
    """
    notes: list[str] = []
    extra = [s.name for s in getattr(profile, "extra_skills", None) or []]
    requirements, n = reclassify_requirements(result.requirements, extra)
    notes += n

    bridgeable_texts = {r.text.strip().lower() for r in requirements if r.status == "bridgeable"}
    dealbreakers = [d for d in result.dealbreakers if d.strip().lower() not in bridgeable_texts]
    if len(dealbreakers) != len(result.dealbreakers):
        notes.append("removed dealbreakers that were bridgeable requirements")

    db = getattr(profile, "dealbreakers", None)
    german = german_dealbreaker(
        result.german_level_required,
        getattr(profile, "language_levels", None) or {},
        getattr(db, "max_german_level_required", None) if db else None,
    )
    if german and not any("german" in d.lower() or "deutsch" in d.lower() for d in dealbreakers):
        dealbreakers.append(german)
        notes.append(f"added dealbreaker: {german}")

    email = ground_email(result.apply_email, source_texts)
    channel = result.channel
    if result.apply_email and not email:
        notes.append(f"dropped application email '{result.apply_email}': not found in the ad")
    if channel == "email" and not email:
        # An email application needs an address from the ad; without one, fall back to the link.
        channel = "employer_form" if result.application_url else "unknown"
        notes.append(f"no application email address in the ad; channel set to {channel}")

    score = max(0, min(100, result.fit_score))
    fixed = result.model_copy(
        update={
            "requirements": requirements,
            "dealbreakers": dealbreakers,
            "apply_email": email,
            "channel": channel,
            "fit_score": score,
        }
    )
    return FinalEvaluation(
        result=fixed,
        decision=decide(score, dealbreakers, apply_threshold, maybe_threshold),
        fit_score=score,
        notes=notes,
    )
