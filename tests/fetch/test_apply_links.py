"""Reading the employer's apply address from a platform job page with the login (fake platform pages)."""

from __future__ import annotations

import random

import pytest

from autoapp.fetch.apply_links import ApplyLinkReader, platform_of, unwrap_redirect

pytestmark = pytest.mark.browser
EMPLOYER = "https://jobs.employer.example/apply/1"  # .example never resolves: nothing leaves the machine


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def context(browser):
    ctx = browser.new_context()
    yield ctx
    ctx.close()


def serve(context, pages: dict) -> list[str]:
    """Fake platform pages: ``{url: html | (status, html, headers)}``; everything else is refused."""
    requested: list[str] = []

    def handler(route):
        url = route.request.url
        requested.append(url)
        entry = pages.get(url)
        if entry is None:
            route.abort()
            return
        status, body, headers = entry if isinstance(entry, tuple) else (200, entry, {})
        route.fulfill(status=status, body=body, headers={"content-type": "text/html; charset=utf-8", **headers})

    context.route("**/*", handler)
    return requested


def reader(context, **kw):
    return ApplyLinkReader(context, min_delay_s=0, max_delay_s=0, wait_ms=3000, sleep=lambda s: None,
                           rng=random.Random(0), **kw)


def page(body: str, title: str = "Python Developer | LinkedIn") -> str:
    return f"<html><head><title>{title}</title></head><body><h1>Python Developer</h1>{body}</body></html>"


def test_a_visible_company_link_is_read_without_clicking_and_unwrapped(context):
    redirect = "https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fjobs.employer.example%2Fapply%2F1"
    serve(context, {"https://www.linkedin.com/jobs/view/1/": page(
        f'<a href="{redirect}">Auf Unternehmenswebsite bewerben</a><button onclick="window.clicked=1">Speichern</button>')})
    link = reader(context).read("https://www.linkedin.com/jobs/view/1/")
    assert (link.status, link.url) == ("found", EMPLOYER)


def test_an_apply_button_that_opens_a_window_gives_the_address_without_loading_it(context):
    requested = serve(context, {"https://www.stepstone.de/stellenangebote--2.html": page(
        f"<button type='button' onclick=\"window.open('{EMPLOYER}')\">Jetzt bewerben</button>", "Job | StepStone")})
    before = len(context.pages)
    link = reader(context).read("https://www.stepstone.de/stellenangebote--2.html")
    assert (link.status, link.url) == ("found", EMPLOYER)
    assert EMPLOYER not in requested  # the employer page was not loaded
    assert len(context.pages) == before  # the windows it opened are closed again


def test_a_continue_step_is_taken_once(context):
    serve(context, {"https://de.indeed.com/viewjob?jk=3": page(
        "<button type='button' onclick=\"document.body.insertAdjacentHTML('beforeend', "
        "'<div role=dialog><button type=button onclick=&quot;window.steps=(window.steps||0)+1; "
        f"location.href=\\'{EMPLOYER}\\'&quot;>Weiter</button></div>')\">Auf Unternehmensseite bewerben</button>",
        "Job | Indeed")})
    link = reader(context).read("https://de.indeed.com/viewjob?jk=3")
    assert (link.status, link.url) == ("found", EMPLOYER)


def test_easy_apply_is_never_clicked(context):
    serve(context, {"https://www.linkedin.com/jobs/view/4/": page(
        "<button type='button' onclick='window.easy=1'>Einfach bewerben</button>")})
    r = reader(context)
    link = r.read("https://www.linkedin.com/jobs/view/4/")
    assert link.status == "on_platform" and "only takes applications on LinkedIn itself" in link.note


def test_buttons_inside_forms_and_logins_and_robot_checks(context):
    serve(context, {
        "https://www.linkedin.com/jobs/view/5/": page("<form method=post action=/x><button>Bewerben</button></form>"),
        "https://www.linkedin.com/jobs/view/6/": (302, "", {"location": "https://www.linkedin.com/authwall?trk=x"}),
        "https://www.linkedin.com/authwall?trk=x": page("<a href='/login'>Anmelden</a>", "Anmelden | LinkedIn"),
        "https://www.linkedin.com/jobs/view/7/": page("<p>verify you are human</p>", "Just a moment..."),
    })
    r = reader(context)
    assert r.read("https://www.linkedin.com/jobs/view/5/").status == "none"  # a form button is never pressed
    login = r.read("https://www.linkedin.com/jobs/view/6/")
    assert login.status == "login_required" and "autoapp login linkedin" in login.note
    assert r.read("https://www.linkedin.com/jobs/view/7/").status == "blocked"
    later = r.read("https://www.linkedin.com/jobs/view/1/")  # not even opened after the robot check
    assert later.status == "blocked" and "earlier in this run" in later.note


def test_pages_of_one_site_are_paced(context):
    serve(context, {f"https://www.linkedin.com/jobs/view/{i}/": page("<p>no button</p>") for i in (8, 9)})
    waits, slept = [], []
    r = ApplyLinkReader(context, min_delay_s=20, max_delay_s=20, wait_ms=1000, sleep=slept.append,
                        on_wait=lambda site, s: waits.append((site, round(s))))
    r.read("https://www.linkedin.com/jobs/view/8/")
    r.read("https://www.linkedin.com/jobs/view/9/")
    assert len(slept) == 1 and waits and waits[0][0] == "linkedin" and 15 <= waits[0][1] <= 20


def test_addresses():
    assert platform_of("https://de.linkedin.com/jobs/view/1") == "linkedin"
    assert platform_of("https://de.indeed.com/viewjob?jk=1") == "indeed"
    assert platform_of("https://jobs.employer.example/1") is None
    assert unwrap_redirect("https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fa.example%2Fx", "linkedin") \
        == "https://a.example/x"
    assert unwrap_redirect("https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fwww.linkedin.com%2Fx",
                           "linkedin") is None
