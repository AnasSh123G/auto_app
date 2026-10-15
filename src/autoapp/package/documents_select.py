"""Choose which documents go into an application (spec: application-package, Document selection)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from autoapp.config.models import DocumentEntry, Profile

_CERT_WORDS = frozenset(
    {"certificates", "certificate", "references", "reference", "zeugnisse", "zeugnis",
     "zertifikate", "zertifikat", "referenzen", "arbeitszeugnisse", "arbeitszeugnis"}
)  # fmt: skip
_PHOTO_WORDS = frozenset({"photo", "photos", "foto", "fotos", "bewerbungsfoto", "picture"})
_ALWAYS_PRESENT = frozenset(
    {"cv", "resume", "lebenslauf", "cover letter", "cover_letter", "anschreiben", "motivation letter"}
)  # fmt: skip


@dataclass
class Selection:
    """Documents to attach; ``missing`` names requested items the user has no document for."""

    cv: DocumentEntry | None
    certificates: list[DocumentEntry] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    photo: DocumentEntry | None = None
    extras: list[DocumentEntry] = field(default_factory=list)  # remaining available documents for Other fields


def document_language(d: DocumentEntry) -> str | None:
    declared = getattr(d, "language", None)
    if declared:
        return declared
    name = d.file.casefold()
    for language, words in (("en", "en|english|englisch"), ("de", "de|german|deutsch")):
        if re.search(rf"(?<![a-z])(?:{words})(?![a-z])", name):
            return language
    return None


def document_matches_language(d: DocumentEntry, language: str) -> bool:
    return document_language(d) in (None, "any", language)


def _universal(d: DocumentEntry) -> bool:
    return document_language(d) in (None, "any")


def _pick_cv(profile: Profile, language: str) -> DocumentEntry | None:
    cvs = [d for d in profile.documents if d.role == "cv"]
    for pred in (lambda d: document_language(d) == language, _universal):
        for d in cvs:
            if pred(d):
                return d
    return None


def select_documents(
    profile: Profile,
    letter_language: str,
    requested: list[str],
    always_attach_certificates: bool,
) -> Selection:
    """Pick one matching CV and all matching-language or neutral supporting documents.

    Requested-item warnings are retained for kinds the profile does not contain. The legacy
    ``always_attach_certificates`` argument remains accepted, but all documents are selected now.
    """
    docs = [d for d in profile.documents if d.role != "cv" and document_matches_language(d, letter_language)]
    sel = Selection(cv=_pick_cv(profile, letter_language))
    if sel.cv is None:
        sel.missing.append(f"CV ({letter_language})")
    certs = [d for d in docs if d.role in ("certificate", "work_reference") or
             d.file.rsplit("/", 1)[-1].casefold().startswith("arbeitszeugnis")]
    sel.certificates = certs
    cert_files = {d.file for d in certs}
    sel.extras = [d for d in docs if d.file not in cert_files and d != sel.cv]
    wanted: list[str] = []
    for r in requested:
        key = " ".join(r.lower().split())
        if key and key not in wanted:
            wanted.append(key)

    for w in wanted:
        if w in _ALWAYS_PRESENT:
            continue
        if w in _CERT_WORDS:
            if not certs:
                sel.missing.append(w)
        elif w in _PHOTO_WORDS:
            photo = next((d for d in docs if d.role == "photo"), None)
            if photo is None:
                sel.missing.append(w)
            else:
                sel.photo = photo
        else:
            hits = [
                d for d in docs if d.role == "other" and (w in (d.label or "").lower() or w in d.file.lower())
            ]
            if hits:
                sel.extras.extend(h for h in hits if h not in sel.extras)
            else:
                sel.missing.append(w)
    if sel.photo is not None:
        sel.extras = [d for d in sel.extras if d != sel.photo]
    return sel
