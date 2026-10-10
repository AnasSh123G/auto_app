"""URL canonicalisation, text normalisation and the dedup keys (fingerprint, group key)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that never identify a job.
_TRACKING_EXACT = {
    "trk", "trkinfo", "refid", "trackingid", "lipi", "midtoken", "midsig", "eid", "otptoken",
    "cid", "rcm", "from", "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "referrer",
    "campaign", "vjs", "tk", "refresh", "trackinginfo", "li_fat_id", "_ga",
}  # fmt: skip
_TRACKING_PREFIXES = ("utm_", "adjust_", "noti_", "wt_", "pk_", "mtm_", "hsa_", "ga_")

_LINKEDIN_ID = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})(?:[/?#]|$)")
_LINKEDIN_COMM = re.compile(r"/comm/jobs/view/(\d+)")


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_EXACT or k.startswith(_TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Return a stable form of ``url``: tracking params, fragment and trailing slash removed.

    LinkedIn job links collapse to ``https://www.linkedin.com/jobs/view/<id>/``.
    """
    url = url.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.endswith("linkedin.com"):
        m = _LINKEDIN_COMM.search(parts.path) or _LINKEDIN_ID.search(parts.path + "/")
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _is_tracking(k)]
    query.sort()
    path = parts.path.rstrip("/") or ""
    netloc = host + (f":{parts.port}" if parts.port and parts.port not in (80, 443) else "")
    scheme = (parts.scheme or "https").lower()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


# --------------------------------------------------------------------------- text

_GENDER_TAG = re.compile(
    r"[\(\[]\s*(?:m|w|d|f|x|div|gn|all|a)\*?"  # first token
    r"(?:\s*(?:/|,|\||\s)\s*(?:m|w|d|f|x|div|gn|\*|all\s+genders?))*"
    r"\s*[\)\]]"
    r"|[\(\[]\s*all\s+genders?\s*[\)\]]"
    r"|[\(\[]\s*a\*\s*[\)\]]",
    re.IGNORECASE,
)
_STAR_IN = re.compile(r"(?<=[a-zäöüß])(?:\*|/|:|_)in(?:nen)?\b", re.IGNORECASE)
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_WORD = re.compile(r"[^\w]+")

_LEGAL_FORMS = [
    r"gmbh\s*&\s*co\.?\s*kgaa", r"gmbh\s*&\s*co\.?\s*kg", r"se\s*&\s*co\.?\s*kgaa",
    r"se\s*&\s*co\.?\s*kg", r"ag\s*&\s*co\.?\s*kg", r"ug\s*\(haftungsbeschraenkt\)",
    r"gmbh", r"mbh", r"co\.?\s*kg", r"kgaa", r"kg", r"ag", r"se", r"ug", r"e\.\s?v\.?",
    r"ltd\.?", r"limited", r"inc\.?", r"llc", r"gbr", r"ohg",
]  # fmt: skip
_LEGAL_RE = re.compile(r"(?<![\w])(?:" + "|".join(_LEGAL_FORMS) + r")(?![\w])", re.IGNORECASE)


def _fold(text: str) -> str:
    return text.lower().translate(_UMLAUTS)


def _strip_gender(text: str) -> str:
    text = _GENDER_TAG.sub(" ", text)
    text = _STAR_IN.sub("", text)
    # "d/w/m" etc. without brackets directly after a dash
    text = re.sub(r"(?<![\w/])(?:[mwdfx]\s*/\s*){1,3}[mwdfx]\*?(?![\w/])", " ", text, flags=re.IGNORECASE)
    return text


def norm_text(text: str | None) -> str:
    """Lowercase, fold umlauts and ß, drop gender tags, collapse punctuation and whitespace."""
    if not text:
        return ""
    text = _strip_gender(text)
    text = _fold(text)
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def norm_company(name: str | None) -> str:
    """``norm_text`` plus removal of legal forms (GmbH, AG, SE, GmbH & Co. KG, e.V., ...)."""
    if not name:
        return ""
    text = _fold(name)
    text = _LEGAL_RE.sub(" ", text)
    return norm_text(text)


def _sha1(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def fingerprint(company: str | None, title: str | None, city: str | None) -> str:
    """sha1 of ``norm_company | norm_text(title) | norm_text(city)``."""
    return _sha1(norm_company(company), norm_text(title), norm_text(city))


def group_key(company: str | None, title: str | None) -> str:
    """sha1 of ``norm_company | norm_text(title)``: the same role posted in several cities."""
    return _sha1(norm_company(company), norm_text(title))
