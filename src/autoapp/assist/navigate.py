import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as BrowserError

from autoapp.assist.consent import cookie_request_allowed
from autoapp.assist.filler import is_restricted
from autoapp.formfinder.detect import detect
from autoapp.formfinder.finder import Outcome
from autoapp.formfinder.links import APPLY, EXCLUDED, NO_EMAIL, links, valid_url


@dataclass
class ApplicationScope:
    frame_index: int
    selector: str


def application_scope(page, restricted, *, company=None, reference=None, title=None):
    wall = None
    for index, frame in enumerate(page.frames):
        if is_restricted(frame.url, restricted):
            continue
        if frame.parent_frame and not frame.frame_element().is_visible():
            continue
        frame.evaluate("""() => {
            document.querySelectorAll('form,[data-application],[role="form"]').forEach(
                (node, i) => node.setAttribute('data-autoapp-scope', i));
        }""")
        rendered = frame.evaluate("""() => {
            const copy = document.documentElement.cloneNode(true);
            const nodes = [...document.querySelectorAll('input,textarea,select')];
            copy.querySelectorAll('input,textarea,select').forEach((node, i) => {
                if (!nodes[i].getClientRects().length || getComputedStyle(nodes[i]).visibility === 'hidden')
                    node.setAttribute('hidden', '');
            });
            const heads = [...document.querySelectorAll('h1')];
            copy.querySelectorAll('h1').forEach((node, i) => {
                if (!heads[i].getClientRects().length || getComputedStyle(heads[i]).visibility === 'hidden')
                    node.setAttribute('hidden', '');
            });
            return {html: copy.outerHTML, text: document.body ? document.body.innerText : ''};
        }""")
        result = detect(rendered["html"], company=company, reference=reference, title=title,
                        visible_text=rendered["text"])
        if result.status in ("login_required", "captcha", "partial"):
            wall = result
            if index == 0:
                return None, wall
        if result.status == "found":
            return ApplicationScope(index, result.scope), result
    return None, wall


class NavigationGuard:
    """Chromium interception covers redirected destinations, unlike page routes."""

    def __init__(self, context, restricted):
        self.context = context
        self.restricted = restricted
        self.navigation = True
        self.pages = set()
        self.attaching = set()
        self.sessions = []
        self.documents = 0
        self.pending_starts = {}
        self.max_documents = 5  # pages one navigation may load (the model's navigation allows more steps)
        self.reason = None
        self.status = None
        self._route_handler = self._route
        context.route("**/*", self._route_handler)
        for page in context.pages:
            self.attach(page)

    def _route(self, route):
        if route.request.is_navigation_request():
            try:
                owned = route.request.frame.page in self.pages
            except BrowserError:
                owned = False
            if not owned and self.navigation:
                self.reason, self.status = "untracked popup refused", "partial"
                route.abort()
                return
        try:
            page_url = route.request.frame.page.url
        except BrowserError:
            page_url = ""  # Worker traffic cannot qualify for a page-specific preference exception.
        if is_restricted(route.request.url, self.restricted):
            if route.request.is_navigation_request():
                self.reason, self.status = "restricted browser target", "restricted"
            route.abort()
        elif (self.navigation and route.request.method not in ("GET", "HEAD")
              and not cookie_request_allowed(route.request.url, route.request.method, route.request.post_data,
                                             page_url)):
            if route.request.is_navigation_request():
                self.reason, self.status = "submission refused during navigation", "partial"
            route.abort()
        else:
            route.continue_()

    def attach(self, page):
        if page in self.pages or page in self.attaching:
            return
        self.attaching.add(page)
        session = self.context.new_cdp_session(page)
        self.sessions.append((page, session))
        session.send("Network.enable")
        session.send("Network.setBypassServiceWorker", {"bypass": True})

        def paused(event):
            request = event["request"]
            forbidden = is_restricted(request["url"], self.restricted)
            navigation = event.get("resourceType") == "Document"
            if self.navigation and navigation:
                if self.pending_starts.get(page) == request["url"]:
                    self.pending_starts.pop(page)
                else:
                    self.documents += 1
            over_budget = self.navigation and navigation and self.documents > self.max_documents
            submitting = (self.navigation and request["method"] not in ("GET", "HEAD")
                          and not cookie_request_allowed(request["url"], request["method"],
                                                         request.get("postData"), page.url))
            if forbidden or over_budget or submitting:
                if navigation:
                    self.status = "restricted" if forbidden else "partial"
                    self.reason = ("restricted browser target" if forbidden else
                                   self.over_budget_reason() if over_budget else "submission refused during navigation")
                session.send("Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"})
            else:
                session.send("Fetch.continueRequest", {"requestId": event["requestId"]})

        session.on("Fetch.requestPaused", paused)
        session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        self.pages.add(page)
        self.attaching.remove(page)

    def reset(self):
        self.documents = 0
        self.pending_starts.clear()
        self.reason = self.status = None

    def reserve_navigation(self, page, url):
        self.documents += 1
        self.pending_starts[page] = url
        if self.documents > self.max_documents:
            self.status, self.reason = "partial", self.over_budget_reason()
            return False
        return True

    def over_budget_reason(self) -> str:
        return f"no form within {self.max_documents - 1} steps"

    def close(self):
        self.context.unroute("**/*", self._route_handler)
        for page, session in self.sessions:
            if not page.is_closed():
                session.detach()


def navigate(page, guard, url, *, company="", reference=None):
    visited = set()
    dismissals = 0
    current = page
    reached = None
    usable = url
    try:
        while True:
            address = urlsplit(url)._replace(fragment="").geturl()
            if address in visited:
                return current, Outcome("partial", usable, reached, "navigation loop")
            if not valid_url(url) or is_restricted(url, guard.restricted):
                return current, Outcome("restricted", usable, reached, "restricted or unsupported browser target")
            visited.add(address)
            guard.attach(current)
            if not guard.reserve_navigation(current, address):
                return current, Outcome("partial", usable, reached, guard.reason)
            response = current.goto(url, wait_until="domcontentloaded", timeout=30000)
            reached = usable = current.url
            if guard.reason:
                return current, Outcome(guard.status, usable, reached, guard.reason)
            if response and response.status in (403, 429, 999):
                return current, Outcome("needs_browser", usable, reached, f"browser HTTP {response.status}; manual handoff")
            if response and response.status >= 400:
                return current, Outcome("unreachable", usable, reached, f"browser HTTP {response.status}")
            deadline = time.monotonic() + 10
            while True:
                if guard.reason:
                    return current, Outcome(guard.status, usable, reached, guard.reason)
                if valid_url(current.url) and not is_restricted(current.url, guard.restricted):
                    reached = usable = current.url
                scope, detection = application_scope(current, guard.restricted, company=company, reference=reference)
                if scope:
                    return current, Outcome("found", usable, reached, "application form found; press [f] to fill")
                if detection:
                    return current, Outcome(detection.status, usable, reached, detection.note)
                candidates = links(current.content(), current.url, guard.restricted)
                clear = [candidate for candidate in candidates if candidate.score >= 2]
                if clear:
                    break
                # Only explicit non-submit controls with declarative destinations are navigable.
                controls = current.locator('button[type="button"]:not([form])')
                action = None
                for index in range(controls.count()):
                    control = controls.nth(index)
                    if control.evaluate("(node) => !!node.form"):
                        continue
                    text = control.inner_text()
                    target = control.get_attribute("data-href")
                    if target and (APPLY.search(text) or NO_EMAIL.search(text)) and not EXCLUDED.search(text):
                        action = urljoin(current.url, target)
                        break
                    reject = text.strip().casefold() in (
                        "reject all", "reject optional cookies", "alle ablehnen", "nur notwendige cookies",
                    )
                    if reject and dismissals < 4:
                        control.click(timeout=4000)
                        dismissals += 1
                        break
                if action:
                    url = action
                    break
                if time.monotonic() >= deadline:
                    return current, Outcome("partial", usable, reached, "no apply button the app can safely click")
                current.wait_for_timeout(100)
            if clear:
                candidate = clear[0]
                url = candidate.url
                anchors = current.locator('a[target="_blank"]')
                if any(urljoin(current.url, anchors.nth(i).get_attribute("href") or "") == url
                       for i in range(anchors.count())):
                    current = guard.context.new_page()
    except BrowserError as error:
        return current, Outcome(guard.status or "unreachable", usable, reached,
                                guard.reason or f"browser navigation failed: {str(error).splitlines()[0]}")
