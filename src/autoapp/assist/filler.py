"""Fill the current page's form fields and highlight the result.

Safety is structural: this module only uses ``fill``, ``select_option``, ``check`` and
``set_input_files``. It has no code path that activates buttons or links, and it never touches
consent checkboxes (privacy, terms, legal declarations), which are left for the user.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from autoapp.assist.fields import FieldInfo, collect_fields
from autoapp.assist.mapper import Assignment, FillValues, map_fields

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

CONSENT_PATTERN = re.compile(
    r"datenschutz|privacy|einwillig|consent|agb|terms|zustimm|akzeptier|best(?:ä|ae)tige|i agree|ich stimme",
    re.IGNORECASE,
)

ACTION_TIMEOUT_MS = 4000
STATE_ATTR = "data-autoapp-state"

_STYLE_JS = r"""
([items, attr]) => {
  if (!document.getElementById("autoapp-style")) {
    const st = document.createElement("style");
    st.id = "autoapp-style";
    st.textContent =
      "[" + attr + '="filled"]{outline:3px solid #1a9c3c !important;outline-offset:2px !important}' +
      "[" + attr + '="needs"]{outline:3px solid #d62828 !important;outline-offset:2px !important}';
    (document.head || document.documentElement).appendChild(st);
  }
  for (const [sel, state] of items) {
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      const hidden = !(r.width > 0 || r.height > 0);
      const target = hidden ? ((el.labels && el.labels[0]) || el.parentElement || el) : el;
      for (const t of new Set([el, target])) {
        if (state === "clear") {
          if (t.getAttribute(attr) === "needs") t.removeAttribute(attr);
        } else {
          t.setAttribute(attr, state);
        }
      }
    }
  }
}
"""


@dataclass
class FillReport:
    """Outcome of one fill trigger."""

    filled: list[str] = field(default_factory=list)
    needs_user: list[str] = field(default_factory=list)
    skipped_restricted: bool = False
    message: str = ""
    errors: list[str] = field(default_factory=list)


def is_restricted(url: str, restricted_domains: Iterable[str]) -> bool:
    """True if the URL's host equals a restricted domain or is a subdomain of one."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    for domain in restricted_domains:
        d = domain.strip().lower().lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def is_consent(f: FieldInfo) -> bool:
    """Consent/legal checkboxes and radio groups, which are never touched."""
    if f.type not in ("checkbox", "radio"):
        return False
    text = " ".join([f.label, f.name, f.html_id, *(t for _, t in f.options)])
    return CONSENT_PATTERN.search(text) is not None


def _display(f: FieldInfo) -> str:
    return (f.label or f.name or f.html_id or f.field_id).strip()


def _is_empty(f: FieldInfo) -> bool:
    if f.type == "checkbox":
        return not f.checked
    return f.value.strip() == ""


def _already_set(f: FieldInfo) -> bool:
    """Fields the user (or an earlier fill) already filled are not overwritten."""
    if f.type == "checkbox":
        return f.checked
    if f.type == "select":
        return bool(f.value) and bool(f.options) and f.value != f.options[0][0]
    return f.value.strip() != ""


def _locator(page: Page, f: FieldInfo) -> Locator:
    return page.frames[f.frame_index].locator(f.selector)


def _apply(page: Page, f: FieldInfo, a: Assignment) -> None:
    loc = _locator(page, f)
    if a.kind == "fill":
        loc.first.fill(a.value or "", timeout=ACTION_TIMEOUT_MS)
    elif a.kind == "select":
        loc.first.select_option(value=a.value or "", timeout=ACTION_TIMEOUT_MS)
    elif a.kind == "check":
        if f.type == "radio":
            values = [v for v, _ in f.options]
            if a.value not in values:
                raise ValueError(f"no radio option with value {a.value!r}")
            loc.nth(values.index(a.value)).check(force=True, timeout=ACTION_TIMEOUT_MS)
        else:
            loc.first.check(force=True, timeout=ACTION_TIMEOUT_MS)
    elif a.kind == "upload":
        loc.first.set_input_files([str(p) for p in a.files], timeout=ACTION_TIMEOUT_MS)
    else:  # pragma: no cover - guarded by the Literal type
        raise ValueError(f"unknown assignment kind {a.kind!r}")


def _mark(page: Page, items_by_frame: dict[int, list[list[str]]]) -> None:
    for idx, items in items_by_frame.items():
        if not items or idx >= len(page.frames):
            continue
        try:
            page.frames[idx].evaluate(_STYLE_JS, [items, STATE_ATTR])
        except Exception:  # noqa: BLE001 - a frame that navigated away is not worth failing for
            continue


def fill_page(
    page: Page,
    values: FillValues,
    *,
    restricted_domains: Iterable[str],
    extra_assignments: Iterable[Assignment] = (),
    override_assignments: Iterable[Assignment] = (),
    scope=None,
) -> FillReport:
    """Fill the page's fields from ``values`` and highlight what was filled or still needs the user."""
    restricted = list(restricted_domains)
    if is_restricted(page.url, restricted):
        host = urlparse(page.url).hostname or page.url
        return FillReport(
            skipped_restricted=True,
            message=(
                f"Filling is disabled on {host} (restricted domain). Nothing was filled; "
                "use the apply.html in the application folder instead."
            ),
        )

    fields = collect_fields(page, restricted_domains=restricted, scope=scope)
    by_id = {f.field_id: f for f in fields}
    consent_ids = {f.field_id for f in fields if is_consent(f)}

    candidates = [f for f in fields if f.field_id not in consent_ids and not _already_set(f)]
    mapping = map_fields(candidates, values)
    assignments = list(mapping.assignments)
    mapped = {a.field_id for a in assignments} | {f.field_id for f in mapping.left_empty}
    # The model's choices replace the rules' facts, but never a fixed answer, a consent box or a prefilled field.
    chosen = {a.field_id: a for a in override_assignments
              if a.field_id in by_id and a.field_id not in consent_ids and a.field_id not in mapping.fixed_ids
              and not _already_set(by_id[a.field_id])}
    if chosen:
        assignments = [chosen.pop(a.field_id, a) for a in assignments]
        assignments += list(chosen.values())
        mapped |= {a.field_id for a in assignments}
    for extra in extra_assignments:
        # LLM-provided answers only fill known, non-consent fields that have no deterministic value.
        if (extra.field_id in by_id and extra.field_id not in consent_ids and extra.field_id not in mapped
                and not _already_set(by_id[extra.field_id])):
            assignments.append(extra)
            mapped.add(extra.field_id)

    report = FillReport()
    filled_ids: list[str] = []
    for a in assignments:
        f = by_id[a.field_id]
        try:
            _apply(page, f, a)
        except Exception as exc:  # noqa: BLE001 - one bad field must not stop the others
            report.errors.append(f"{_display(f)}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
            continue
        filled_ids.append(f.field_id)
        report.filled.append(_display(f))

    # Re-read the page: it shows what is really filled now and what is still empty.
    after = {f.field_id: f for f in collect_fields(page, restricted_domains=restricted, scope=scope)}
    needs: list[str] = []
    marks: dict[int, list[list[str]]] = {}
    for fid, f in after.items():
        consent = fid in consent_ids
        needs_input = (consent and _is_empty(f)) or (_is_empty(f) and (f.required or fid not in mapped))
        if needs_input:
            state = "needs"
            if _display(f) not in needs:
                needs.append(_display(f))
        elif fid in filled_ids:
            state = "filled"
        else:
            state = "clear"
        marks.setdefault(f.frame_index, []).append([f.selector, state])
    report.needs_user = needs
    _mark(page, marks)

    parts = [f"Filled {len(report.filled)} field(s)."]
    if needs:
        parts.append("Needs your input: " + "; ".join(needs) + ".")
    if report.errors:
        parts.append("Could not fill: " + "; ".join(report.errors) + ".")
    parts.append("Please review the form and send the application yourself; no buttons were pressed.")
    report.message = " ".join(parts)
    return report
