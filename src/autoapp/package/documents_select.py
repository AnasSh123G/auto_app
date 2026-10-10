"""Choose which documents go into an application (spec: application-package, Document selection)."""

from __future__ import annotations

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
    extras: list[DocumentEntry] = field(default_factory=list)  # matched `other` documents (e.g. portfolio)


def _universal(d: DocumentEntry) -> bool:
    return d.language in (None, "any")


def _fits(d: DocumentEntry, language: str) -> bool:
    """A document is sent when it is universal or written in the letter's language."""
    return _universal(d) or d.language == language


def _pick_cv(profile: Profile, language: str) -> DocumentEntry | None:
    cvs = [d for d in profile.documents if d.role == "cv"]
    for pred in (lambda d: d.language == language, _universal, lambda d: True):
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
    """Pick the CV (letter language, else any), certificates and requested extras.

    A document in another language than the letter is left out unless it is marked ``any``.

    Certificates are attached when the ad requests certificates or references, or when
    ``always_attach_certificates`` is set. Writing samples are never attached; the photo only
    when requested. A requested item without a matching document is listed in ``missing``.
    """
    docs = [d for d in profile.documents if d.role == "cv" or _fits(d, letter_language)]
    sel = Selection(cv=_pick_cv(profile, letter_language))
    certs = [d for d in docs if d.role in ("certificate", "work_reference")]
    wanted: list[str] = []
    for r in requested:
        key = " ".join(r.lower().split())
        if key and key not in wanted:
            wanted.append(key)

    want_certs = always_attach_certificates or any(w in _CERT_WORDS for w in wanted)
    if want_certs:
        sel.certificates = certs

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
    return sel
