"""Bounded model-driven browser fallback after an unresolved HTTP form walk."""

import logging
import time
from contextlib import contextmanager

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright

from autoapp.assist.agent_navigate import _scrub_url, model_chooser, navigate_with_model
from autoapp.assist.filler import is_restricted
from autoapp.assist.navigate import NavigationGuard
from autoapp.employers.scope import DIRECTORY_HOSTS, host_matches, is_directory
from autoapp.formfinder.finder import Outcome, _record
from autoapp.formfinder.links import valid_url
from autoapp.llm.base import LLMError

log = logging.getLogger("autoapp")
_RETRY = frozenset({"partial", "none", "needs_browser"})


@contextmanager
def open_context(browser=None):
    """Fresh contexts; reuse preparation's PDF browser to avoid nesting Playwright event loops."""
    if browser is not None:
        context = browser.new_context(service_workers="block")
        try:
            yield context
        finally:
            context.close()
        return
    with sync_playwright() as driver:
        owned = driver.chromium.launch(headless=True)
        try:
            context = owned.new_context(service_workers="block")
            try:
                yield context
            finally:
                context.close()
        finally:
            owned.close()


def eligible(url: str | None, restricted: list[str]) -> bool:
    return bool(url and valid_url(url) and not is_restricted(url, restricted)
                and (not is_directory(url) or host_matches(url, {"workwise.io"})))


def fallback(ctx, entry: str | None, http: Outcome, *, title: str, company: str, reference: str | None = None,
             job_id: str | None = None, browser=None) -> Outcome:
    assist = ctx.config.assist
    if (http.status not in _RETRY or not assist.browser_fallback or assist.navigate_mode != "model"
            or ctx.dry_run or getattr(ctx, "_browser_fallback_unavailable", False)
            or getattr(ctx, "_form_find_auth_failed", False) or ctx.meter.exhausted):
        return http
    # Stay with the vacancy's original entry: a failed HTTP hop may be a generic/irrelevant continuation.
    if not eligible(entry, assist.restricted_domains):
        return http
    try:
        ctx.meter.check()
        ctx.llm("form_find")  # No browser/network work when this task has no usable model configuration.
    except LLMError as error:
        ctx._browser_fallback_unavailable = True
        ctx.emit("warning", detail=f"browser form fallback unavailable: {error}")
        return http
    personal = ctx.profile.personal
    private = [v for v in (personal.email, personal.phone, personal.street, personal.first_name, personal.last_name) if v]
    hops = [{"url": _scrub_url(entry, private), "reason": "HTTP unresolved; browser fallback"}]
    started = time.monotonic()
    restricted = list(set(assist.restricted_domains) | (DIRECTORY_HOSTS - {"workwise.io"}))
    outcome = None
    try:
        with open_context(browser) as context:
            page = context.new_page()
            guard = NavigationGuard(context, restricted)
            try:
                _, outcome = navigate_with_model(page, guard, entry, choose=model_chooser(ctx), title=title,
                                                company=company, reference=reference,
                                                max_steps=assist.navigate_max_steps, private=private, hops=hops)
            finally:
                guard.close()
    except BrowserError as error:
        # A missing Chromium installation, or failed launch, should never prevent package preparation.
        ctx._browser_fallback_unavailable = True
        ctx.emit("warning", detail=f"browser form fallback unavailable: {str(error).splitlines()[0]}")
        log.warning("browser form fallback unavailable: %s", str(error).splitlines()[0])
    result = http if outcome is None or outcome.status == "unreachable" else outcome
    if outcome is not None and outcome.note.startswith("conflicting"):
        result = Outcome("partial", entry, http.reached, outcome.note)
    if job_id is not None:
        _record(ctx, job_id, hops, outcome or http, round((time.monotonic() - started) * 1000), strategy="browser")
    return result
