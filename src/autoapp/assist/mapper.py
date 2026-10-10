"""Deterministic German/English mapping from form fields to prepared values.

The mapper never touches a browser. It matches the normalised field label (then the ``name`` and
``id`` attributes, then the input type) against a synonym table and produces ``Assignment``s.
Fields with no match are returned in ``MappingResult.unmatched`` so another component (an LLM)
can handle them later.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from autoapp.assist.fields import FieldInfo

if TYPE_CHECKING:
    from autoapp.config.models import Profile

AssignKind = Literal["fill", "select", "check", "upload"]

TEXT_TYPES = frozenset({"text", "email", "tel", "url", "search", "textarea", "number", "date", ""})


# ----------------------------------------------------------------------------- inputs


@dataclass
class FillValues:
    """Everything the fill step may put into a form (profile data and application files)."""

    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    linkedin_url: str | None = None
    xing_url: str | None = None
    salary_expectation: str | None = None
    earliest_start: str | None = None
    notice_period: str | None = None
    work_authorization: str | None = None
    salutation: str | None = None
    gender: str | None = None
    motivation: str | None = None
    language_levels: dict[str, str] = field(default_factory=dict)
    standard_answers: dict[str, str] = field(default_factory=dict)
    cv: Path | None = None
    cover_letter_pdf: Path | None = None
    certificates: list[Path] = field(default_factory=list)
    work_references: list[Path] = field(default_factory=list)  # Arbeitszeugnisse
    other_documents: list[Path] = field(default_factory=list)  # documents with the role "other"
    work_samples: list[Path] = field(default_factory=list)  # documents with the role "writing_sample"
    bundle_pdf: Path | None = None
    company: str = ""  # the employer, to recognise "have you worked at <company> before?"
    language: str = "de"  # the application's language: "Nein" or "No" in a plain text box

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @classmethod
    def from_profile(
        cls,
        profile: Profile,
        *,
        salary_expectation: str | None = None,
        motivation: str | None = None,
        salutation: str | None = None,
        gender: str | None = None,
        cv: Path | None = None,
        cover_letter_pdf: Path | None = None,
        certificates: list[Path] | None = None,
        work_references: list[Path] | None = None,
        other_documents: list[Path] | None = None,
        work_samples: list[Path] | None = None,
        bundle_pdf: Path | None = None,
        company: str = "",
        language: str | None = None,
    ) -> FillValues:
        """Build values from a loaded profile plus the application folder's artifacts."""
        p = profile.personal
        return cls(
            first_name=p.first_name,
            last_name=p.last_name,
            email=p.email,
            phone=p.phone,
            street=p.street,
            postal_code=p.postal_code,
            city=p.city,
            country=p.country,
            linkedin_url=p.linkedin_url,
            xing_url=p.xing_url,
            salary_expectation=salary_expectation,
            earliest_start=profile.availability.earliest_start,
            notice_period=profile.availability.notice_period,
            work_authorization=profile.work_authorization,
            salutation=salutation,
            gender=gender,
            motivation=motivation,
            language_levels=dict(profile.language_levels),
            standard_answers=dict(profile.standard_answers),
            cv=cv,
            cover_letter_pdf=cover_letter_pdf,
            certificates=list(certificates or []),
            work_references=list(work_references or []),
            other_documents=list(other_documents or []),
            work_samples=list(work_samples or []),
            bundle_pdf=bundle_pdf,
            company=company,
            language=language or "de",
        )


@dataclass
class Assignment:
    """One action on one field: fill text, select an option, tick a radio/checkbox or upload."""

    field_id: str
    kind: AssignKind
    value: str | None = None
    files: list[Path] = field(default_factory=list)


@dataclass
class MappingResult:
    assignments: list[Assignment] = field(default_factory=list)
    unmatched: list[FieldInfo] = field(default_factory=list)
    # Fields whose answer is "no" and that need nothing put in (an unticked "I was referred" box).
    left_empty: list[FieldInfo] = field(default_factory=list)
    # Fields answered by a fixed rule (how found, referral, prior employment): never handed to the model.
    fixed_ids: set[str] = field(default_factory=set)


# ------------------------------------------------------------------------ normalisation

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NOISE = re.compile(r"\((optional|freiwillig|required|pflichtfeld|pflicht)\)")


def norm(text: str) -> str:
    """Lowercase, fold umlauts, drop ``*``, ``:`` and ``(optional)``-style hints."""
    t = text.lower().translate(_FOLD)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _NOISE.sub(" ", t)
    t = t.replace("*", " ").replace(":", " ")
    return re.sub(r"\s+", " ", t).strip()


def _norm_attr(text: str) -> str:
    """Normalise a ``name``/``id`` attribute (``firstName``, ``first_name``, ``user[email]``)."""
    t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    t = re.sub(r"[_\-.\[\]]+", " ", t)
    return norm(t)


def _has_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


@dataclass(frozen=True)
class _Rule:
    key: str
    exact: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    applies: Literal["text", "file", "both"] = "text"


def _r(key: str, exact: tuple[str, ...] = (), contains: tuple[str, ...] = (), applies: str = "text") -> _Rule:
    return _Rule(key, tuple(norm(e) for e in exact), tuple(norm(c) for c in contains), applies)  # type: ignore[arg-type]


# Order matters: more specific rules come first.
_RULES: tuple[_Rule, ...] = (
    _r("linkedin", contains=("linkedin",)),
    _r("xing", contains=("xing",)),
    _r(
        "full_name",
        exact=("name", "your name", "ihr name", "full name", "fullname", "vollstaendiger name"),
        contains=("vor- und nachname", "vor und nachname", "vorname und nachname", "first and last name"),
    ),
    _r("first_name", contains=("vorname", "first name", "firstname", "given name", "forename", "rufname")),
    _r("last_name", contains=("nachname", "last name", "lastname", "surname", "family name", "familienname")),
    _r("email", contains=("e-mail", "email", "e mail", "mailadresse")),
    _r("phone", exact=("tel",), contains=("telefon", "mobil", "phone", "mobile", "handy", "rufnummer", "telephone")),
    _r("postal_code", contains=("plz", "postleitzahl", "postal code", "postcode", "post code", "zip")),
    _r("street", contains=("strasse", "street", "address line", "adresse", "address", "anschrift", "hausnummer")),
    _r("city", exact=("ort", "stadt", "city", "town"), contains=("wohnort", "city", "stadt", "town")),
    _r("country", exact=("land", "country"), contains=("country of residence", "wohnsitzland")),
    _r(
        "salary",
        contains=(
            "gehaltsvorstellung", "gehaltsvorstellungen", "gehaltswunsch", "gehaltserwartung", "gehalt",
            "wunschgehalt", "salary", "desired salary", "expected salary", "salary expectation",
        ),
    ),
    _r("notice", contains=("kuendigungsfrist", "notice period", "notice")),
    _r(
        "start",
        contains=(
            "eintrittstermin", "eintrittsdatum", "fruehester eintritt", "fruehestmoeglicher eintritt",
            "eintritt", "verfuegbarkeit", "verfuegbar ab", "availability", "earliest start", "start date",
            "startdatum", "starttermin", "fruehester start", "available from", "ab wann",
        ),
    ),
    _r("salutation", exact=("anrede", "salutation"), contains=("anrede", "salutation")),
    _r("gender", contains=("geschlecht", "gender")),
    _r(
        "work_authorization",
        contains=(
            "arbeitserlaubnis", "arbeitsgenehmigung", "work authorization", "work authorisation",
            "work permit", "aufenthaltstitel", "right to work", "eligible to work", "visa",
        ),
    ),
    _r(
        "lang_de",
        contains=(
            "deutschkenntnisse", "kenntnisse deutsch", "deutsch niveau", "deutschniveau", "sprachniveau deutsch",
            "german level", "german proficiency", "german language", "german skills", "level of german",
            "level in german",
        ),
    ),
    _r(
        "lang_en",
        contains=(
            "englischkenntnisse", "kenntnisse englisch", "englisch niveau", "englischniveau",
            "sprachniveau englisch", "english level", "english proficiency", "english language",
            "english skills", "level of english", "level in english",
        ),
    ),
    # Uploads (and the cover letter, which is a file or a textarea).
    _r(
        "cover_letter",
        contains=(
            "anschreiben", "motivationsschreiben", "bewerbungsschreiben", "cover letter", "coverletter",
            "motivation letter", "letter of motivation", "motivation",
        ),
        applies="both",
    ),
    _r("cv", contains=("lebenslauf", "cv", "resume", "curriculum vitae"), applies="file"),
    _r(
        "work_references",
        contains=(
            "arbeitszeugnis", "arbeitszeugnisse", "zwischenzeugnis", "dienstzeugnis", "work reference",
            "work references", "employment reference", "employment references", "reference letter",
            "reference letters", "referenzschreiben", "referenzen", "references", "arbeitsreferenz",
        ),
        applies="file",
    ),
    _r(
        "work_samples",
        contains=(
            "arbeitsprobe", "arbeitsproben", "work sample", "work samples", "writing sample", "writing samples",
            "portfolio", "textprobe", "schreibprobe",
        ),
        applies="file",
    ),
    _r(
        "other_documents",
        exact=("andere", "other", "others", "sonstiges", "sonstige", "weitere", "additional", "misc", "diverse"),
        contains=(
            "weitere dokumente", "weitere unterlagen", "sonstige dokumente", "sonstige unterlagen",
            "andere dokumente", "andere unterlagen", "other documents", "additional documents",
            "other files", "additional files", "weitere dateien",
        ),
        applies="file",
    ),
    _r(
        "certificates",
        contains=(
            "zeugnis", "zeugnisse", "zertifikat", "zertifikate", "anlage", "anlagen", "attachment",
            "attachments", "certificate", "certificates", "nachweise", "urkunde", "urkunden", "diplom", "diploma",
            "degree certificate",
        ),
        applies="file",
    ),
    _r(
        "generic_upload",
        exact=("upload", "documents", "dokumente", "datei", "file", "unterlagen"),
        contains=(
            "unterlagen", "bewerbungsunterlagen", "bewerbungsmappe", "application documents", "upload",
            "dokumente", "documents", "datei hochladen", "hochladen",
        ),
        applies="file",
    ),
)


def _match_rule(text: str, *, is_file: bool) -> str | None:
    t = norm(text)
    if not t:
        return None
    for rule in _RULES:
        if rule.applies == "text" and is_file:
            continue
        if rule.applies == "file" and not is_file:
            continue
        if t in rule.exact or any(_has_word(t, c) for c in rule.contains):
            return rule.key
    return None


def _match_attr(text: str, *, is_file: bool) -> str | None:
    return _match_rule(_norm_attr(text), is_file=is_file) if text else None


# ----------------------------------------------------------------------- value helpers


def truncate_at_word(text: str, maxlength: int | None) -> str:
    """Cut ``text`` to ``maxlength`` characters, preferring a word boundary."""
    if maxlength is None or len(text) <= maxlength:
        return text
    if maxlength <= 0:
        return ""
    cut = text[:maxlength]
    if text[maxlength].isspace():
        return cut.rstrip()
    space = cut.rfind(" ")
    if space > 0:
        return cut[:space].rstrip(" ,;:-")
    return cut


def _numeric(text: str) -> str | None:
    """First whole number in ``text`` (German thousands dots and decimal commas handled)."""
    m = re.search(r"\d[\d.,\s]*", text)
    if not m:
        return None
    s = m.group(0).strip()
    s = re.sub(r"[,.]\d{1,2}$", "", s)  # drop decimals
    digits = re.sub(r"\D", "", s)
    return digits or None


def _iso_date(text: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", text)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}"
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*", text)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return None


_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"deutschland", "germany", "de", "bundesrepublik deutschland", "ger"}),
    frozenset({"oesterreich", "austria", "at"}),
    frozenset({"schweiz", "switzerland", "ch"}),
    frozenset({"herr", "mr", "mr.", "mister", "male", "maennlich", "m"}),
    frozenset({"frau", "ms", "ms.", "mrs", "mrs.", "miss", "female", "weiblich", "w", "f"}),
    frozenset({"divers", "diverse", "non-binary", "nonbinary", "other", "sonstige"}),
    frozenset({"native", "muttersprache", "muttersprachlich", "native speaker"}),
)


def _candidates(value: str) -> list[str]:
    """The value plus its known aliases (Deutschland/Germany, Herr/Mr, ...), normalised."""
    v = norm(value)
    out = [v]
    for group in _ALIAS_GROUPS:
        if v in group:
            out.extend(sorted(group - {v}))
    return out


def _pick_option(options: list[tuple[str, str]], value: str) -> str | None:
    """Return the option *value* whose text matches ``value`` (exact, then contains)."""
    cands = _candidates(value)
    usable = [(ov, norm(ot), norm(ov)) for ov, ot in options if ov != ""]  # skip placeholders
    for c in cands:
        for ov, ot, onv in usable:
            if c and (ot == c or onv == c):
                return ov
    for c in cands:
        if len(c) < 2:
            continue
        for ov, ot, _ in usable:
            if ot and (_has_word(ot, c) or (len(ot) >= 3 and _has_word(c, ot))):
                return ov
    return None


def _language_level(values: FillValues, lang: str) -> str | None:
    names = {"de": ("de", "german", "deutsch"), "en": ("en", "english", "englisch")}[lang]
    for k, v in values.language_levels.items():
        if k.strip().lower() in names:
            return v
    return None


def _text_value(key: str, values: FillValues) -> str | None:
    """The plain-text value for a rule key, or ``None`` if the profile has nothing for it."""
    simple: dict[str, str | None] = {
        "linkedin": values.linkedin_url,
        "xing": values.xing_url,
        "full_name": values.full_name,
        "first_name": values.first_name,
        "last_name": values.last_name,
        "email": values.email,
        "phone": values.phone,
        "postal_code": values.postal_code,
        "street": values.street,
        "city": values.city,
        "country": values.country,
        "salary": values.salary_expectation,
        "notice": values.notice_period,
        "start": values.earliest_start,
        "salutation": values.salutation,
        "gender": values.gender,
        "work_authorization": values.work_authorization,
        "cover_letter": values.motivation,
    }
    if key == "lang_de":
        return _language_level(values, "de")
    if key == "lang_en":
        return _language_level(values, "en")
    v = simple.get(key)
    return v.strip() if v and v.strip() else None


# ------------------------------------------------------------ questions with a fixed answer
#
# Three kinds of question are answered the same way for every application, without asking a model:
#   how_found         "Where did you hear about this position?"      -> LinkedIn
#   referral          "Were you referred by an employee?"             -> no
#   prior_employment  "Have you worked at <company> before?"          -> no

_HOW_FOUND = re.compile(
    r"how did you (hear|find|learn|come across|discover|know)|where did you (hear|find|see|learn)|hear(d)? about (us|this|the|our)"
    r"|how (do|did) you know (us|about)|wie haben sie (von|uns)|wie sind sie auf|woher (haben|kennen) sie"
    r"|aufmerksam geworden|auf uns aufmerksam|wo haben sie (die|diese|unsere|unser|den|das)\b.{0,30}(gefunden|gesehen|gelesen|erfahren)"
    r"|quelle der bewerbung|source of application|referral source|^(source|quelle)$"
)
_REFERRAL = re.compile(
    r"referral|referred|recommended by|recommended you|empfohlen\b|empfehlung\b|weiterempfehlung|mitarbeiterempfehlung"
)
_PRIOR_GENERIC = re.compile(
    r"\b(former|previous|ex|current)\s+(employee|worker)|\bpreviously\s+(employed|worked)"
    r"|\b(worked|employed|work)\b.{0,30}\b(for|at|with|by)\s+(us|our|this company|the company|the group)\b"
    r"|\b(ever|already|before)\b.{0,25}\b(worked|employed)\b.{0,25}\b(here|us|our company)\b"
    r"|ehemalige\w*\s+(mitarbeiter|beschaeftigte)|(fruehere|ehemalige)\w*\s+(mitarbeit|beschaeftigung|taetigkeit)"
    r"|\b(bereits|schon(\s+(einmal|mal))?|fruher|frueher|zuvor|jemals)\b.{0,40}\b(bei uns|fuer uns|in unserem unternehmen|im unternehmen)\b"
    r"|\bbei uns\s+(gearbeitet|beschaeftigt|taetig)|\b(bereits|schon)\b.{0,20}\b(beschaeftigt|taetig|gearbeitet)\b.{0,20}\bbei\b"
)
_APPLIED_BEFORE = re.compile(r"beworben|\bappl(y|ied|ication)\b|bewerbung")  # an earlier application is not employment
_NO_WORDS = frozenset({"no", "nein", "n", "nope", "keine", "none", "nicht", "no referral", "not applicable"})
_NO_START = re.compile(r"^(no|nein|nicht|keine|not|n/?a|i (was|am|have|did) not|ich (wurde|bin|habe) (nicht|kein))\b")
_HOW_FOUND_ANSWER = "LinkedIn"
_HOW_FOUND_FALLBACKS = (
    re.compile(r"linkedin"),
    re.compile(r"social|soziale|netzwerk|network"),
    re.compile(r"job ?(board|boerse|portal)|stellen(boerse|portal|markt)|online|internet|\bweb"),
    re.compile(r"^(other|others|sonstige\w*|andere\w*|anderes)\b"),
)


def _fixed_key(text: str, company: str = "") -> str | None:
    """``how_found`` / ``referral`` / ``prior_employment`` when the text asks one of those, else ``None``."""
    t = norm(text)
    if not t:
        return None
    if _HOW_FOUND.search(t):
        return "how_found"
    if _REFERRAL.search(t):
        return "referral"
    if _APPLIED_BEFORE.search(t):
        return None
    if _PRIOR_GENERIC.search(t):
        return "prior_employment"
    co = norm(company)
    if len(co) >= 3 and re.search(
            rf"\b(worked|employed|work|gearbeitet|beschaeftigt|taetig)\b.{{0,30}}\b{re.escape(co)}\b"
            rf"|\b{re.escape(co)}\b.{{0,30}}\b(worked|employed|gearbeitet|beschaeftigt|taetig|mitarbeiter|employee)\b", t):
        return "prior_employment"
    return None


def _pick_no(options: list[tuple[str, str]]) -> str | None:
    """The option that means "no": ``No``, ``Nein``, ``No, I was not referred``, ..."""
    usable = [(ov, norm(ot)) for ov, ot in options if ov != ""]
    for ov, ot in usable:
        if ot in _NO_WORDS:
            return ov
    for ov, ot in usable:
        if _NO_START.match(ot):
            return ov
    return None


def _pick_how_found(options: list[tuple[str, str]]) -> str | None:
    """LinkedIn if the list has it, otherwise the nearest general source (social media, job board, other)."""
    usable = [(ov, norm(ot)) for ov, ot in options if ov != ""]
    for pattern in _HOW_FOUND_FALLBACKS:
        for ov, ot in usable:
            if pattern.search(ot):
                return ov
    return None


def _fixed_assignment(key: str, f: FieldInfo, values: FillValues) -> Assignment | None:
    if f.type in ("select", "radio"):
        option = _pick_how_found(f.options) if key == "how_found" else _pick_no(f.options)
        return Assignment(f.field_id, "select" if f.type == "select" else "check", option) if option is not None else None
    if f.type not in TEXT_TYPES - {"number", "date"}:
        return None
    if key == "how_found":
        text = _HOW_FOUND_ANSWER
    elif key == "referral":
        text = "Keine" if values.language == "de" else "None"
    else:
        text = "Nein" if values.language == "de" else "No"
    return Assignment(f.field_id, "fill", truncate_at_word(text, f.maxlength))


# ------------------------------------------------------------------------------ mapping


def _file_assignment(key: str, f: FieldInfo, values: FillValues) -> Assignment | None:
    files: list[Path] = []
    if key == "cover_letter" and values.cover_letter_pdf:
        files = [values.cover_letter_pdf]
    elif key == "cv" and values.cv:
        files = [values.cv]
    elif key == "work_references":
        files = list(values.work_references)  # never guessed from other documents: left for you if there are none
    elif key == "work_samples":
        files = list(values.work_samples)
    elif key == "other_documents":
        files = list(values.other_documents)
        if not files and f.multiple:  # the old behaviour of "further documents": the certificates
            files = [*values.certificates, *values.work_references]
    elif key in ("certificates", "generic_upload"):
        if key == "certificates" and f.multiple and (values.certificates or values.work_references):
            files = [*values.certificates, *values.work_references]
        elif values.bundle_pdf:
            files = [values.bundle_pdf]
    if not f.multiple:
        files = files[:1]  # a single-file input takes one file; the others can be added by hand
    return Assignment(f.field_id, "upload", files=files) if files else None


def _text_assignment(key: str, f: FieldInfo, values: FillValues) -> Assignment | None:
    if key == "cover_letter" and f.type != "textarea":
        return None
    value = _text_value(key, values)
    if value is None:
        return None
    if f.type == "select":
        opt = _pick_option(f.options, value)
        return Assignment(f.field_id, "select", opt) if opt is not None else None
    if f.type == "radio":
        opt = _pick_option(f.options, value)
        return Assignment(f.field_id, "check", opt) if opt is not None else None
    if f.type == "number":
        num = _numeric(value)
        return Assignment(f.field_id, "fill", num) if num else None
    if f.type == "date":
        iso = _iso_date(value)
        return Assignment(f.field_id, "fill", iso) if iso else None
    if f.type not in TEXT_TYPES:
        return None
    return Assignment(f.field_id, "fill", truncate_at_word(value, f.maxlength))


def _type_key(f: FieldInfo) -> str | None:
    return {"email": "email", "tel": "phone"}.get(f.type)


def _standard_answer(f: FieldInfo, values: FillValues) -> Assignment | None:
    """Fall back to the profile's ``standard_answers`` when a key appears in the label."""
    if f.type not in TEXT_TYPES - {"number", "date"} | {"select", "radio"}:
        return None
    label = norm(f.label)
    best: tuple[int, str] | None = None
    for key, answer in values.standard_answers.items():
        k = norm(key.replace("_", " "))
        if len(k) >= 3 and answer and answer.strip() and _has_word(label, k) and (best is None or len(k) > best[0]):
            best = (len(k), answer.strip())
    if best is None:
        return None
    value = best[1]
    if f.type in ("select", "radio"):
        opt = _pick_option(f.options, value)
        if opt is None:
            return None
        return Assignment(f.field_id, "select" if f.type == "select" else "check", opt)
    return Assignment(f.field_id, "fill", truncate_at_word(value, f.maxlength))


def map_fields(fields: list[FieldInfo], values: FillValues) -> MappingResult:
    """Map fields to assignments deterministically; the rest lands in ``unmatched``."""
    file_inputs = [f for f in fields if f.type == "file"]
    only_one_file = len(file_inputs) == 1
    result = MappingResult()
    for f in fields:
        is_file = f.type == "file"
        fixed = None if is_file else next(
            (k for k in (_fixed_key(f.label, values.company), _fixed_key(_norm_attr(f.name), values.company),
                         _fixed_key(_norm_attr(f.html_id), values.company), _fixed_key(f.placeholder, values.company))
             if k), None)
        if fixed is not None:
            if f.type == "checkbox":  # "I was referred" stays unticked: that is the answer
                if fixed != "how_found":
                    result.left_empty.append(f)
                    result.fixed_ids.add(f.field_id)
                    continue
            else:
                assignment = _fixed_assignment(fixed, f, values)
                if assignment is not None:
                    result.assignments.append(assignment)
                    result.fixed_ids.add(f.field_id)
                    continue
        key = _match_rule(f.label, is_file=is_file) or _match_attr(f.name, is_file=is_file)
        key = key or _match_attr(f.html_id, is_file=is_file) or _match_rule(f.placeholder, is_file=is_file)
        if key is None and not is_file:
            key = _type_key(f)
        if key == "generic_upload" and not only_one_file:
            key = None
        if is_file and key is None and only_one_file and not f.label.strip():
            key = "generic_upload"

        assignment: Assignment | None = None
        if key is not None:
            assignment = _file_assignment(key, f, values) if is_file else _text_assignment(key, f, values)
        if assignment is None and not is_file and f.type != "checkbox":
            assignment = _standard_answer(f, values)
        if assignment is None:
            result.unmatched.append(f)
        else:
            result.assignments.append(assignment)
    return result
