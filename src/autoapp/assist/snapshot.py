"""An offline copy of the form page as it looks after filling (``filled_form.html`` in the application folder).

The copy is made from the live page: what was typed or chosen is written into the markup, scripts are removed so
nothing redraws or empties the fields, style sheets and small images are embedded so it still looks like the
page without a connection, and every link and the form's own address are made absolute. Nothing on the live page
is changed. Uploaded files cannot be stored in a page; the banner at the top lists the prepared files instead.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

NAME = "filled_form.html"
MARKER = "<!-- autoapp-source: "
MAX_IMAGE_BYTES = 150_000
MAX_INLINE_BYTES = 1_500_000
MAX_IMAGES = 40

_SNAPSHOT_JS = r"""
async ([banner, maxImage, maxInline, maxImages]) => {
  const here = document.baseURI;
  const abs = (u, base) => { try { return new URL(u, base || here).href; } catch (e) { return u; } };
  const root = document.documentElement.cloneNode(true);
  const pairs = (sel) => {
    const live = Array.from(document.querySelectorAll(sel));
    const copy = Array.from(root.querySelectorAll(sel));
    return live.map((el, i) => [el, copy[i]]).filter(([, c]) => c);
  };

  // What was typed or chosen becomes part of the markup (the copy only; the live page is left alone).
  for (const [el, c] of pairs("input")) {
    const t = (el.type || "text").toLowerCase();
    if (t === "checkbox" || t === "radio") {
      if (el.checked) c.setAttribute("checked", ""); else c.removeAttribute("checked");
    } else if (t !== "file" && t !== "password") {
      c.setAttribute("value", el.value);
    }
  }
  for (const [el, c] of pairs("textarea")) c.textContent = el.value;
  for (const [el, c] of pairs("select")) {
    const opts = Array.from(c.querySelectorAll("option"));
    Array.from(el.options).forEach((o, i) => {
      if (!opts[i]) return;
      if (o.selected) opts[i].setAttribute("selected", ""); else opts[i].removeAttribute("selected");
    });
  }

  // Nothing may run or load in the copy.
  root.querySelectorAll("script,iframe,object,embed,base,meta[http-equiv],link[rel~=preload],link[rel~=prefetch],"
    + "link[rel~=modulepreload],link[rel~=preconnect],link[rel~=dns-prefetch],link[rel~=icon],link[rel~=manifest]")
    .forEach((n) => n.remove());
  for (const el of root.querySelectorAll("*")) {
    for (const a of Array.from(el.attributes)) {
      if (a.name.toLowerCase().startsWith("on")) el.removeAttribute(a.name);
    }
  }

  // Style sheets are embedded; addresses inside them are made absolute.
  const fixUrls = (css, base) => css.replace(/url\(\s*(['"]?)(?!data:|https?:|#)([^'")]+)\1\s*\)/g,
    (m, q, u) => 'url("' + abs(u, base) + '")');
  const rulesOf = (sheet) => { try { return Array.from(sheet.cssRules).map((r) => r.cssText).join("\n"); } catch (e) { return null; } };
  for (const [el, c] of pairs("link[rel~=stylesheet]")) {
    const base = el.href || here;
    let css = el.sheet ? rulesOf(el.sheet) : null;
    if (css === null) {
      try { css = await (await fetch(base, { credentials: "omit" })).text(); } catch (e) { css = null; }
    }
    if (css === null) { c.setAttribute("href", base); continue; }
    const style = document.createElement("style");
    style.setAttribute("data-from", base);
    style.textContent = fixUrls(css, base);
    c.replaceWith(style);
  }
  for (const [el, c] of pairs("style")) {
    const css = el.sheet ? rulesOf(el.sheet) : null;
    if (css) c.textContent = fixUrls(css, here);
  }

  // Images: embedded while small, otherwise their absolute address (shown when online).
  const toData = (blob) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(blob); });
  let used = 0, count = 0;
  for (const [el, c] of pairs("img")) {
    const src = el.currentSrc || el.src;
    c.removeAttribute("srcset"); c.removeAttribute("loading");
    if (!src || src.startsWith("data:")) continue;
    c.setAttribute("src", src);
    if (count >= maxImages || used >= maxInline) continue;
    count++;
    try {
      const blob = await (await fetch(src, { credentials: "omit" })).blob();
      if (blob.size <= maxImage && used + blob.size <= maxInline) { c.setAttribute("src", await toData(blob)); used += blob.size; }
    } catch (e) { /* stays the absolute address */ }
  }
  root.querySelectorAll("picture source").forEach((n) => n.remove());

  // Links and the form's own address point at the real site, so the copy can be used for what it is.
  for (const [el, c] of pairs("a[href],area[href]")) c.setAttribute("href", abs(el.getAttribute("href")));
  for (const [el, c] of pairs("form")) {
    c.setAttribute("action", el.action || here);
    c.setAttribute("method", (el.method || "get").toLowerCase());
    c.removeAttribute("target");
  }
  for (const [el, c] of pairs("[src]:not(img)")) c.setAttribute("src", abs(el.getAttribute("src")));

  const head = root.querySelector("head") || root.insertBefore(document.createElement("head"), root.firstChild);
  const meta = document.createElement("meta"); meta.setAttribute("charset", "utf-8");
  head.insertBefore(meta, head.firstChild);
  const base = document.createElement("base"); base.setAttribute("href", here);
  head.insertBefore(base, meta.nextSibling);

  // The banner says what this is and lists the files to attach.
  const body = root.querySelector("body");
  if (body) {
    const box = document.createElement("div");
    box.setAttribute("style", "all:initial;display:block;box-sizing:border-box;margin:0 0 16px;padding:12px 16px;"
      + "background:#fff8d6;color:#222;border-bottom:2px solid #d9b800;font:14px/1.45 sans-serif");
    const add = (text) => { const p = document.createElement("p"); p.setAttribute("style", "margin:2px 0;display:block"); p.textContent = text; box.appendChild(p); return p; };
    add(banner.title).style.fontWeight = "bold";
    for (const line of banner.lines) add(line);
    if (banner.files.length) {
      const p = add("Files to attach: ");
      banner.files.forEach(([label, href], i) => {
        const a = document.createElement("a"); a.setAttribute("href", href); a.textContent = label; p.appendChild(a);
        if (i < banner.files.length - 1) p.appendChild(document.createTextNode(", "));
      });
    }
    body.insertBefore(box, body.firstChild);
  }
  return "<!DOCTYPE html>\n" + root.outerHTML;
}
"""


def snapshot_html(frame, *, url: str, title: str, files: list[tuple[str, str]], when: datetime | None = None) -> str:
    """The offline copy of ``frame``'s document. ``files`` are ``(label, file:// address)`` of the prepared files."""
    when = when or datetime.now().astimezone()
    banner = {
        "title": f"Offline copy of the application form for {title}",
        "lines": [
            f"Filled in by autoapp on {when:%Y-%m-%d %H:%M}. Nothing was submitted. Source: {url}",
            "Green outline: filled by autoapp. Red outline: still needs your answer. Check every field.",
            "Sending from this copy only works for a plain HTML form, and it never carries the files below: "
            "attach them yourself. If the site refuses it, submit in the live browser instead.",
        ],
        "files": [list(f) for f in files],
    }
    html = frame.evaluate(_SNAPSHOT_JS, [banner, MAX_IMAGE_BYTES, MAX_INLINE_BYTES, MAX_IMAGES])
    if not isinstance(html, str) or "<html" not in html.lower():
        raise ValueError("the page could not be copied")
    return html + f"\n{MARKER}{url} -->\n"


def save_snapshot(folder: Path, html: str, url: str) -> Path:
    """Write ``filled_form.html``. A copy of a different page (an earlier step of a multi-step form) is kept."""
    path = folder / NAME
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if f"{MARKER}{url} -->" not in old[-1000:]:
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
            path.replace(folder / f"filled_form.{stamp}.html")
    path.write_text(html, encoding="utf-8")
    return path
