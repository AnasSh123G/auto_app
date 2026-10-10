"""Collect fillable form fields from a Playwright page, including all frames.

Every collected element gets a ``data-autoapp-id`` attribute so later steps can address it with
a CSS selector that is unique within its frame. Radio buttons with the same ``name`` are merged
into one field whose ``options`` list the choices. Nothing here changes the page apart from
that bookkeeping attribute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Frame, Page

ID_ATTR = "data-autoapp-id"


@dataclass
class FieldInfo:
    """One form control (or radio group) as seen in the page."""

    field_id: str
    frame_index: int
    selector: str
    tag: str
    type: str
    name: str = ""
    html_id: str = ""
    label: str = ""
    placeholder: str = ""
    required: bool = False
    maxlength: int | None = None
    options: list[tuple[str, str]] = field(default_factory=list)
    multiple: bool = False
    value: str = ""
    checked: bool = False
    group_size: int = 1  # number of elements behind ``selector`` (>1 for radio groups)


# The script runs inside each frame. It returns plain dicts; ``collect_fields`` wraps them.
_COLLECT_JS = r"""
([frameIndex, idAttr]) => {
  const SKIP_INPUT = new Set(["hidden", "submit", "button", "reset", "image"]);
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const isControl = (n) => n.nodeType === 1 && /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(n.tagName);
  const hasControl = (n) => n.nodeType === 1 && (isControl(n) || n.querySelector("input,select,textarea"));

  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden") return false;
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (getComputedStyle(p).display === "none") return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0 || st.position === "fixed";
  };

  const labelText = (lab) => {
    const c = lab.cloneNode(true);
    c.querySelectorAll("input,select,textarea,button,script,style").forEach((n) => n.remove());
    return clean(c.textContent);
  };

  const root = (el) => el.getRootNode();
  const byId = (el, id) => root(el).getElementById ? root(el).getElementById(id) : document.getElementById(id);

  const precedingText = (el) => {
    let node = el;
    for (let depth = 0; depth < 4 && node; depth++) {
      for (let sib = node.previousSibling; sib; sib = sib.previousSibling) {
        if (sib.nodeType === 3) {
          const t = clean(sib.textContent);
          if (t) return t.slice(0, 200);
        } else if (sib.nodeType === 1 && !hasControl(sib) && !/^(SCRIPT|STYLE)$/.test(sib.tagName)) {
          const t = clean(sib.textContent);
          if (t) return t.slice(0, 200);
        }
      }
      node = node.parentElement;
      if (!node || /^(FORM|BODY)$/.test(node.tagName)) break;
    }
    return "";
  };

  const resolveLabel = (el) => {
    if (el.id) {
      const lab = root(el).querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && labelText(lab)) return labelText(lab);
    }
    const wrap = el.closest("label");
    if (wrap && labelText(wrap)) return labelText(wrap);
    const aria = clean(el.getAttribute("aria-label"));
    if (aria) return aria;
    const by = clean(el.getAttribute("aria-labelledby"));
    if (by) {
      const t = by.split(" ").map((i) => byId(el, i)).filter(Boolean).map((n) => clean(n.textContent)).join(" ");
      if (clean(t)) return clean(t);
    }
    const ph = clean(el.getAttribute("placeholder"));
    if (ph) return ph;
    if (el.name) return el.name;
    return precedingText(el);
  };

  const groupLabel = (el) => {
    const fs = el.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg && clean(lg.textContent)) return clean(lg.textContent);
    }
    const grp = el.closest('[role="radiogroup"],[role="group"]');
    if (grp) {
      const a = clean(grp.getAttribute("aria-label"));
      if (a) return a;
      const by = clean(grp.getAttribute("aria-labelledby"));
      if (by) {
        const t = by.split(" ").map((i) => byId(el, i)).filter(Boolean).map((n) => clean(n.textContent)).join(" ");
        if (clean(t)) return clean(t);
      }
    }
    // Preceding text of the first radio's container, skipping the option labels themselves.
    let node = el.closest("label") || el;
    const t = precedingText(node);
    return t || el.name || "";
  };

  const usable = (el) => {
    if (el.disabled || el.readOnly) return false;
    const type = (el.type || "text").toLowerCase();
    if (el.tagName === "INPUT" && SKIP_INPUT.has(type)) return false;
    if (visible(el)) return true;
    if (type === "file" || type === "checkbox" || type === "radio") {
      // Custom-styled controls are often hidden while their label or wrapper stays visible.
      const labs = Array.from(el.labels || []);
      return labs.some(visible) || (el.parentElement && visible(el.parentElement));
    }
    return false;
  };

  const used = new Set(Array.from(document.querySelectorAll("[" + idAttr + "]")).map((n) => n.getAttribute(idAttr)));
  let counter = 0;
  const newId = () => {
    let id;
    do { id = "f" + frameIndex + "-" + counter++; } while (used.has(id));
    used.add(id);
    return id;
  };
  const idOf = (el) => {
    let id = el.getAttribute(idAttr);
    if (!id) { id = newId(); el.setAttribute(idAttr, id); }
    return id;
  };

  const out = [];
  const radioGroups = new Map();
  for (const el of document.querySelectorAll("input,textarea,select")) {
    if (!usable(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.type || "text").toLowerCase() : tag;
    const base = {
      frame_index: frameIndex,
      tag, type,
      name: el.name || "",
      html_id: el.id || "",
      placeholder: el.getAttribute("placeholder") || "",
      required: el.required || el.getAttribute("aria-required") === "true",
      maxlength: el.maxLength >= 0 && tag !== "select" ? el.maxLength : null,
      multiple: !!el.multiple,
      value: el.value || "",
      checked: !!el.checked,
      options: [],
      group_size: 1,
    };
    if (type === "radio") {
      const form = el.form;
      const key = (form ? Array.prototype.indexOf.call(document.forms, form) : -1) + "|" + (el.name || idOf(el));
      let g = radioGroups.get(key);
      const optLabel = (() => {
        if (el.id) {
          const lab = root(el).querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (lab && labelText(lab)) return labelText(lab);
        }
        const wrap = el.closest("label");
        if (wrap && labelText(wrap)) return labelText(wrap);
        return clean(el.getAttribute("aria-label")) || el.value || "";
      })();
      if (!g) {
        g = Object.assign(base, {
          field_id: idOf(el), label: groupLabel(el), value: "", checked: false, _gid: null,
        });
        g._gid = g.field_id;
        radioGroups.set(key, g);
        out.push(g);
      } else {
        el.setAttribute(idAttr, g._gid);
        g.required = g.required || base.required;
      }
      el.setAttribute(idAttr, g._gid);
      g.options.push([el.value || "", optLabel]);
      g.group_size = g.options.length;
      if (el.checked) g.value = el.value || "";
      continue;
    }
    const info = Object.assign(base, { field_id: idOf(el), label: resolveLabel(el) });
    if (tag === "select") {
      info.options = Array.from(el.options).map((o) => [o.value, clean(o.textContent)]);
    }
    out.push(info);
  }
  for (const g of out) {
    g.selector = "[" + idAttr + '="' + g.field_id + '"]';
    delete g._gid;
  }
  return out;
}
"""


def _frame_fields(frame: Frame, index: int) -> list[FieldInfo]:
    try:
        raw: list[dict[str, Any]] = frame.evaluate(_COLLECT_JS, [index, ID_ATTR])
    except Exception:  # noqa: BLE001 - detached or navigating frames just yield nothing
        return []
    out: list[FieldInfo] = []
    for r in raw:
        out.append(
            FieldInfo(
                field_id=r["field_id"],
                frame_index=r["frame_index"],
                selector=r["selector"],
                tag=r["tag"],
                type=r["type"],
                name=r["name"],
                html_id=r["html_id"],
                label=r["label"],
                placeholder=r["placeholder"],
                required=bool(r["required"]),
                maxlength=r["maxlength"],
                options=[(str(v), str(t)) for v, t in r["options"]],
                multiple=bool(r["multiple"]),
                value=r["value"] or "",
                checked=bool(r["checked"]),
                group_size=int(r["group_size"]),
            )
        )
    return out


def collect_fields(page: Page, *, restricted_domains=(), scope=None) -> list[FieldInfo]:
    """Return all visible, enabled form fields in every frame of ``page``."""
    fields: list[FieldInfo] = []
    from autoapp.assist.filler import is_restricted

    for index, frame in enumerate(page.frames):
        if is_restricted(frame.url, restricted_domains) or (scope and scope.frame_index != index):
            continue
        collected = _frame_fields(frame, index)
        if scope:
            collected = [f for f in collected if frame.locator(f.selector).first.evaluate(
                "(node, selector) => !!node.closest(selector)", scope.selector
            )]
        fields.extend(collected)
    return fields
