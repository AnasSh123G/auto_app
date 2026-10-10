"""Read the employer's own apply address from a LinkedIn, Indeed or StepStone job page, with your login.

Off by default (``sources.apply_links_with_login``). Without a login these platforms no longer show where a job is
really applied to, so the job stays "apply yourself". With this switched on for a platform, the app opens that one
job page in its browser profile (where you logged in with ``autoapp login <site>``), finds the "apply on the company
website" control and notes the address it leads to. Then the normal form finding and filling start on the employer's
site.

What it never does: type anything, log in, click an on-platform apply ("Easy Apply", "Schnellbewerbung", "Mit Indeed
bewerben"), submit a form (form posts are blocked), or load more than the job page and the first step of the apply
control. The employer page itself is not loaded: as soon as a navigation leaves the platform its address is taken
and the tab is closed. One job at a time, with the same 20-40 s pauses as the detail fetch.

Using your logged-in account with an automated browser is against these platforms' terms; at this pace the risk of
a restriction is low but not zero. That is why it is opt-in per platform.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from autoapp.fetch.browser_fetcher import _LOGIN_WALL_PATH, is_challenge, site_key

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

PLATFORMS = ("linkedin", "indeed", "stepstone")
PLATFORM_LABELS = {"linkedin": "LinkedIn", "indeed": "Indeed", "stepstone": "StepStone", "workwise": "Workwise"}
LOGIN_PAGES = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "stepstone": "https://www.stepstone.de/candidate/login",
    "workwise": "https://www.workwise.io/login",
}

# "Apply on the company's website" in the platforms' German and English wording.
_OFFSITE = re.compile(
    r"(auf|zur) (der )?(unternehmens|firmen|arbeitgeber)(web)?(seite|site|website)|"
    r"(on|to) (the )?company('s)? ?(web)?(site|page)|(firmen|unternehmens)website|externe? (seite|bewerbung)|"
    r"weiter zur bewerbung|zur bewerbung|apply externally", re.I)
# A generic apply control: used only when no on-platform marker is present.
_APPLY = re.compile(r"^\s*(jetzt )?(bewerben|apply( now)?|bewerbung starten|zur stellenanzeige)\b", re.I)
# Applying on the platform itself: never clicked.
_ON_PLATFORM = re.compile(
    r"einfach bewerben|easy apply|schnell ?bewerb|quick apply|mit indeed bewerben|apply with indeed|"
    r"indeed apply|ich bin interessiert|1-klick|one[- ]click|mit (deinem|ihrem) profil bewerben|"
    r"apply with (your )?profile|profil teilen", re.I)
# A "continue to the company site" step some platforms show after the apply click.
_CONTINUE = re.compile(r"^\s*(weiter|fortfahren|continue|continue applying|weiter zur bewerbung|"
                       r"zur unternehmens(web)?seite|continue to (the )?company('s)? ?(web)?site)\s*$", re.I)
_REDIRECT_PARAMS = ("url", "dest", "destination", "target", "redirect", "u")

_CANDIDATES_JS = """() => {
    const visible = (el) => {
        const r = el.getClientRects();
        if (!r.length) return false;
        const s = getComputedStyle(el);
        return s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled;
    };
    const out = [];
    document.querySelectorAll('[data-autoapp-apply]').forEach((el) => el.removeAttribute('data-autoapp-apply'));
    let n = 0;
    for (const el of document.querySelectorAll('a[href], button, [role="button"], [role="link"]')) {
        if (!visible(el)) continue;
        const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '')
            .replace(/\\s+/g, ' ').trim().slice(0, 120);
        if (!text) continue;
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        el.setAttribute('data-autoapp-apply', String(n));
        out.push({n, tag, text, type, href: tag === 'a' ? el.href : '', form: tag !== 'a' && !!(el.form || el.closest('form')),
                  dialog: !!el.closest('[role="dialog"], dialog, .modal, [aria-modal="true"]')});
        n += 1;
    }
    return out;
}"""


@dataclass(frozen=True)
class ApplyLink:
    status: Literal["found", "on_platform", "login_required", "blocked", "none", "failed"]
    url: str | None = None
    note: str = ""


def platform_of(url: str | None) -> str | None:
    """``linkedin`` / ``indeed`` / ``stepstone`` when ``url`` is a job page of one of them."""
    if not url:
        return None
    site = site_key(url)
    return site if site in PLATFORMS else None


def off_platform(url: str, site: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and site_key(url) != site


def unwrap_redirect(url: str, site: str) -> str | None:
    """The employer address hidden in a platform redirect link (``.../redir/redirect?url=https%3A...``)."""
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return None
    for key in _REDIRECT_PARAMS:
        for value in query.get(key, []):
            target = unquote(value)
            if off_platform(target, site):
                return target
    return None


def _offsite_href(element: dict, site: str) -> str | None:
    href = element.get("href") or ""
    if not href:
        return None
    if off_platform(href, site):
        return href
    return unwrap_redirect(href, site)


class ApplyLinkReader:
    """Reads apply addresses one job at a time in a given browser context (the caller owns the context)."""

    def __init__(self, context: BrowserContext, *, min_delay_s: float = 20, max_delay_s: float = 40,
                 wait_ms: int = 10_000, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None,
                 on_wait: Callable[[str, float], None] | None = None) -> None:
        self.context = context
        self._min, self._max, self._wait_ms = min_delay_s, max(max_delay_s, min_delay_s), wait_ms
        self._sleep, self._clock, self._rng = sleep, clock, rng or random.Random()
        self._last: dict[str, float] = {}
        self.on_wait = on_wait
        self.blocked: dict[str, str] = {}  # platforms that showed a challenge: not tried again in this run

    def _pace(self, site: str) -> None:
        last = self._last.get(site)
        if last is None:
            return
        remaining = self._rng.uniform(self._min, self._max) - (self._clock() - last)
        if remaining > 0:
            if self.on_wait is not None:
                self.on_wait(site, remaining)
            self._sleep(remaining)

    def read(self, url: str) -> ApplyLink:
        site = platform_of(url)
        if site is None:
            return ApplyLink("none", note="not a LinkedIn, Indeed or StepStone address")
        if site in self.blocked:
            return ApplyLink("blocked", note=f"{PLATFORM_LABELS[site]} blocked the app earlier in this run")
        self._pace(site)
        found: list[str] = []
        pages_before = set(self.context.pages)

        def on_request(request) -> None:  # noqa: ANN001 - also sees redirect hops, unlike a route
            if found or not request.is_navigation_request():
                return
            if off_platform(request.url, site):
                found.append(unwrap_redirect(request.url, site) or request.url)

        def guard(route) -> None:  # noqa: ANN001 - never send a form while looking
            request = route.request
            if request.is_navigation_request() and request.method not in ("GET", "HEAD"):
                route.abort()
            elif request.is_navigation_request() and off_platform(request.url, site):
                found.append(request.url)
                route.abort()  # the address is known: the employer page is not loaded
            else:
                route.fallback()

        self.context.on("request", on_request)
        self.context.route("**/*", guard)
        page = self.context.new_page()
        try:
            return self._read(page, url, site, found)
        except Exception as error:  # noqa: BLE001 - Playwright errors and closed windows
            return ApplyLink("failed", note=f"the page could not be read: {str(error).splitlines()[0][:120]}")
        finally:
            self._last[site] = self._clock()
            try:
                self.context.unroute("**/*", guard)
            except Exception:  # noqa: BLE001
                pass
            self.context.remove_listener("request", on_request)
            for extra in set(self.context.pages) - pages_before:
                try:
                    extra.close()
                except Exception:  # noqa: BLE001
                    pass

    def _read(self, page: Page, url: str, site: str, found: list[str]) -> ApplyLink:
        label = PLATFORM_LABELS[site]
        response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1500)
        status = response.status if response is not None else 0
        if is_challenge(status, page.title(), page.content()):
            self.blocked[site] = f"HTTP {status}" if status >= 400 else "robot check"
            return ApplyLink("blocked", note=f"{label} showed a robot check (HTTP {status})")
        if _LOGIN_WALL_PATH.match(urlsplit(page.url).path or ""):
            return ApplyLink("login_required", note=f"{label} asks for a login: run `autoapp login {site}` once")
        elements = page.evaluate(_CANDIDATES_JS)
        # An apply link that already shows where it goes needs no click.
        for element in elements:
            if _OFFSITE.search(element["text"]) or _APPLY.search(element["text"]):
                if not _ON_PLATFORM.search(element["text"]) and (href := _offsite_href(element, site)):
                    return ApplyLink("found", href, f"apply link read from the {label} page")
        offsite = [e for e in elements if _OFFSITE.search(e["text"]) and not _ON_PLATFORM.search(e["text"])]
        generic = [e for e in elements if _APPLY.search(e["text"]) and not _ON_PLATFORM.search(e["text"])]
        on_platform = [e for e in elements if _ON_PLATFORM.search(e["text"])]
        choices = [e for e in (offsite or ([] if on_platform else generic)) if e["type"] not in ("submit", "reset")
                   and not e["form"]]
        if not choices:
            if on_platform:
                return ApplyLink("on_platform", note=f"this job only takes applications on {label} itself "
                                                     f"(\"{on_platform[0]['text'][:40]}\")")
            if any(re.search(r"anmelden|einloggen|sign in|log in", e["text"], re.I) for e in elements[:15]):
                return ApplyLink("login_required", note=f"{label} shows no apply button without a login: "
                                                        f"run `autoapp login {site}` once")
            return ApplyLink("none", note=f"no apply button found on the {label} page")
        target = page.locator(f'[data-autoapp-apply="{choices[0]["n"]}"]').first
        target.click(timeout=5000)
        continued = False
        deadline = self._clock() + self._wait_ms / 1000
        while not found and self._clock() < deadline:
            page.wait_for_timeout(250)
            if continued:
                continue
            # Some platforms show "continue to the company site" first; that one step is taken, nothing else.
            for open_page in [p for p in self.context.pages if not p.is_closed()]:
                try:
                    dialog = [e for e in open_page.evaluate(_CANDIDATES_JS) if e["dialog"]]
                except Exception:  # noqa: BLE001 - a page that is navigating away
                    continue
                href = next((h for e in dialog if (h := _offsite_href(e, site))), None)
                if href:
                    found.append(href)
                    break
                step = next((e for e in dialog if _CONTINUE.search(e["text"]) and not _ON_PLATFORM.search(e["text"])
                             and e["type"] not in ("submit", "reset")), None)
                if step is not None:
                    open_page.locator(f'[data-autoapp-apply="{step["n"]}"]').first.click(timeout=5000)
                    continued = True
                    break
        if found:
            return ApplyLink("found", urljoin(url, found[0]), f"apply link read from the {label} page")
        if _LOGIN_WALL_PATH.match(urlsplit(page.url).path or ""):
            return ApplyLink("login_required", note=f"{label} asks for a login: run `autoapp login {site}` once")
        return ApplyLink("none", note=f"the {label} apply button did not lead to another website")


def open_reader_context(profile_dir: Path, *, headless: bool = True):
    """The app's browser profile (with your logins), for reading apply links. Returns (playwright, context)."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(str(profile_dir), headless=headless, locale="de-DE",
                                                    service_workers="block")
    return pw, context


class RunLookup:
    """Apply-link reading during a run: only for switched-on platforms, within the per-run limit, reported as
    ``APPLY LINK`` lines. The browser is opened on first use and closed with the run."""

    def __init__(self, ctx, reader: ApplyLinkReader | None = None) -> None:  # noqa: ANN001 - RunContext
        self.ctx = ctx
        self.settings = ctx.config.sources.apply_links_with_login
        self._reader = reader
        self._pw = None
        self._context = None
        self.used = 0

    def wanted(self, url: str | None) -> str | None:
        """The platform of ``url`` when apply links are read there with the login, else ``None``."""
        site = platform_of(url)
        return site if self.settings.enabled_for(site) else None

    def _ensure(self) -> ApplyLinkReader:
        if self._reader is None:
            self._pw, self._context = open_reader_context(self.ctx.ws.browser_profile_dir)
            self._reader = ApplyLinkReader(self._context, min_delay_s=self.settings.min_delay_s,
                                           max_delay_s=self.settings.max_delay_s)
        return self._reader

    def read(self, job, url: str) -> ApplyLink | None:  # noqa: ANN001 - jobs row
        """The apply address for ``job``'s platform page, or ``None`` when it is not read (off or limit)."""
        site = self.wanted(url)
        if site is None:
            return None
        label = PLATFORM_LABELS[site]
        if self.used >= self.settings.max_per_run:
            self.ctx.emit("apply_link", job, detail=f"not read: {self.settings.max_per_run} {label}-style pages "
                                                    "were read in this run already (sources.apply_links_with_login)")
            return None
        self.used += 1
        reader = self._ensure()

        def on_wait(_site: str, seconds: float) -> None:
            self.ctx.emit("apply_link", job, detail=f"waiting {seconds:.0f} s before reading the next {label} page "
                                                    "(a pause between pages)")

        reader.on_wait = on_wait
        self.ctx.emit("apply_link", job, detail=f"reading the {label} job page with your login")
        link = reader.read(url)
        text = {"found": f"found: {link.url}", "on_platform": link.note, "login_required": link.note,
                "blocked": link.note, "none": link.note, "failed": link.note}[link.status]
        self.ctx.emit("apply_link", job, detail=text, status=link.status)
        return link

    def close(self) -> None:
        for close in (getattr(self._context, "close", None), getattr(self._pw, "stop", None)):
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001 - the browser may be gone already
                    pass
        self._context = self._pw = None
