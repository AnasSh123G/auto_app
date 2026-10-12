import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoapp.assist.consent import cookie_request_allowed
from autoapp.assist.navigate import NavigationGuard

PAGE = "https://www.workwise.io/job/123018"
API = "https://api.workwise.io/cookieConsents"
REJECT = dict.fromkeys(("essential", "statistics", "marketing", "external_media"), False)


def test_only_reject_all_preferences_and_the_exact_preflight_are_allowed():
    assert cookie_request_allowed(API, "POST", json.dumps(REJECT), PAGE)
    assert cookie_request_allowed(API, "OPTIONS", None, PAGE)
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert not cookie_request_allowed(API, method, json.dumps(REJECT), PAGE)


@pytest.mark.parametrize("payload", [
    {}, [], None, {**REJECT, "marketing": True}, {**REJECT, "essential": 0},
    {**REJECT, "email": "max@example.com"}, {"essential": False},
])
def test_other_payloads_never_qualify(payload):
    assert not cookie_request_allowed(API, "POST", json.dumps(payload), PAGE)
    assert not cookie_request_allowed(API, "POST", "not-json", PAGE)


@pytest.mark.parametrize("url", [
    "http://api.workwise.io/cookieConsents", API + "?email=max@example.com", API + "#x", API + "/register",
    "https://api.workwise.io/register", "https://api.workwise.io/applications", "https://api.workwise.io:444/cookieConsents",
    "https://api.workwise.io.evil.example/cookieConsents", "https://user:pass@api.workwise.io/cookieConsents",
])
def test_nearby_endpoints_and_spoofed_hosts_are_blocked(url):
    assert not cookie_request_allowed(url, "POST", json.dumps(REJECT), PAGE)
    assert not cookie_request_allowed(url, "OPTIONS", None, PAGE)


@pytest.mark.parametrize("page", ["https://workwise.io.evil.example/job/1", "http://www.workwise.io/job/1",
                                  "https://employer.example/job/1", "https://user:pass@workwise.io/job/1"])
def test_the_request_must_originate_on_workwise(page):
    assert not cookie_request_allowed(API, "POST", json.dumps(REJECT), page)
    assert not cookie_request_allowed(API, "OPTIONS", None, page)


@pytest.mark.parametrize("allowed", [True, False])
def test_the_page_route_guard_uses_the_same_cookie_policy(allowed):
    page = SimpleNamespace(url=PAGE)
    guard = NavigationGuard.__new__(NavigationGuard)
    guard.navigation, guard.restricted, guard.pages = True, [], {id(page)}
    request = SimpleNamespace(url=API, method="POST", post_data=json.dumps(REJECT if allowed else {"email": "max@x"}),
                              frame=SimpleNamespace(page=page), is_navigation_request=lambda: False)
    route = Mock(request=request)
    guard._route(route)
    assert route.continue_.called == allowed
    assert route.abort.called == (not allowed)


@pytest.mark.parametrize(("method", "body", "allowed"), [
    ("POST", json.dumps(REJECT), True),
    ("POST", json.dumps({**REJECT, "marketing": True}), False),
    ("POST", json.dumps({"email": "private@example.invalid"}), False),
])
def test_cdp_interception_applies_the_same_consent_only_policy(method, body, allowed):
    from autoapp.assist.navigate import NavigationGuard

    class Session:
        def __init__(self):
            self.handlers = {}
            self.sent = []

        def send(self, name, params=None):
            self.sent.append((name, params))

        def on(self, name, handler):
            self.handlers[name] = handler

        def detach(self):
            pass

    class Page:
        url = PAGE

        def is_closed(self):
            return False

    class Context:
        pages = []

        def __init__(self):
            self.session = Session()

        def route(self, *_args):
            pass

        def unroute(self, *_args):
            pass

        def new_cdp_session(self, _page):
            return self.session

    context, page = Context(), Page()
    guard = NavigationGuard(context, [])
    guard.attach(page)
    callback = context.session.handlers["Fetch.requestPaused"]
    callback({"requestId": "request-1", "resourceType": "Fetch", "request": {
        "url": API, "method": method, "postData": body,
    }})
    assert context.session.sent[-1][0] == ("Fetch.continueRequest" if allowed else "Fetch.failRequest")
    guard.close()
