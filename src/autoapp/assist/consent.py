"""The narrowly permitted preference write during otherwise read-only navigation."""

import json
from urllib.parse import urlsplit


def cookie_request_allowed(url: str, method: str, post_data: str | None, page_url: str) -> bool:
    """Only Workwise's reject-all consent request (and its preflight), from a Workwise page.

    Login, profile creation and application requests never qualify. Both request interception layers use this.
    """
    try:
        target, page = urlsplit(url), urlsplit(page_url)
        if (target.scheme != "https" or target.netloc != "api.workwise.io" or target.path != "/cookieConsents"
                or target.query or target.fragment or page.scheme != "https"
                or page.hostname not in {"workwise.io", "www.workwise.io"}
                or page.username or page.password or page.port not in (None, 443)):
            return False
        if method == "OPTIONS":
            return True
        if method != "POST":
            return False
        body = json.loads(post_data or "")
    except (ValueError, TypeError):
        return False
    return (isinstance(body, dict) and set(body) == {"essential", "statistics", "marketing", "external_media"}
            and all(value is False for value in body.values()))
