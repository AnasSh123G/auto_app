"""End to end (task 6.1): alert email -> independent lookup -> careers listing -> exact recovery -> a second
relevant vacancy -> filtering and evaluation -> form-finder entry; then a later run reusing the verified site."""

from __future__ import annotations

import hashlib
from email.message import EmailMessage

from autoapp.fetch.http_fetcher import FetchResult
from autoapp.llm.fake import FakeLLM
from autoapp.mail.connection import FakeMailer
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.steps.context import RunContext
from autoapp.steps.pipeline import run_pipeline
from tests.employers.identity_world import search_client
from tests.employers.test_service import CBC, EMP_CONFIG, TENANT, cbc_pages
from tests.employers.webfake import FakeWeb, job_page
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import verdict
from tests.steps.test_prepare_step import LETTER, FakeRenderer, factory

EVAL = verdict(90, apply_email=None, requirements=[
    {"text": "Python", "proficiency": "intermediate", "core": True, "status": "matched", "evidence": "Python"}])
FORM = ("<html><body><h1>Bewerbung</h1><form method='post'><label for='n'>Name</label><input id='n' name='name'>"
        "<label for='e'>E-Mail</label><input id='e' type='email' name='email'><input type='file' name='cv'>"
        "<button>Bewerben</button></form></body></html>")


class WebAsHttp:
    """The shared fetcher's interface over a fake web (the form finder and detail fetch use it)."""

    disabled_hosts: dict = {}

    def __init__(self, web: FakeWeb) -> None:
        self.web = web

    def fetch_once(self, url: str, **kwargs) -> FetchResult:
        return self.web(url)

    def fetch(self, url: str) -> FetchResult:
        return self.web(url)

    def close(self) -> None:
        pass


def alert_mail(title: str, job_id: str) -> tuple[str, EmailMessage]:
    msg = EmailMessage()
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["Subject"] = f"{title} at {CBC}"
    msg["Message-ID"] = f"<alert-{job_id}@example.com>"
    msg.set_content(
        "Your job alert for Data Engineer in Germany\nNew jobs match your preferences.\n\n"
        f"{title}\n{CBC}\nKöln\nApply with resume & profile\n"
        f"View job: https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=XYZ\n\n"
        "---------------------------------------------------------\n\nSee all jobs on LinkedIn: https://www.linkedin.com/jobs\n")
    return msg["Message-ID"], msg


def hash_tree(root) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def make_ctx(ws):
    ctx = RunContext.open(ws)
    ctx.mode = "review"
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [EVAL] * 4})
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 4})
    return ctx


def pages():
    web = cbc_pages()
    web[TENANT + "details/346291.html"] = web[TENANT + "details/346291.html"]  # the probed detail page
    web[TENANT + "bewerbung/346291.html"] = FORM
    web[TENANT + "bewerbung/298422.html"] = FORM
    web[TENANT + "details/298422.html"] = job_page("(Junior) Data Scientist (m/w/d) in Vollzeit oder Teilzeit", ref="298422",
                                                   org=CBC).replace("</body>", "<a href='/bewerbung/298422.html'>Jetzt bewerben</a></body>")
    return web


def test_alert_to_prepared_form_application_then_a_later_run_without_search(tmp_path):
    ws = make_workspace(tmp_path, config=EMP_CONFIG)  # keywords: Junior Data Engineer, Data Scientist
    ctx = make_ctx(ws)
    web = FakeWeb(pages())
    ctx._http = WebAsHttp(web)
    api, calls = search_client(["https://www.cbc-cologne.de/karriere"])
    title = "(Junior) Data Engineer / BI Developer (m/w/d) in Vollzeit oder Teilzeit"
    s = run_pipeline(ctx, api_sources=[], mailbox=FakeMailbox([alert_mail(title, "4471")]),
                     renderer_factory=factory(FakeRenderer([1, 1])), mailer=FakeMailer(),
                     employer_fetch=web, employer_api=api)
    assert s.errors == [], s.errors
    assert len(calls) == 1  # one independent lookup, from company, title and city alone
    alert = ctx.conn.execute("SELECT j.* FROM jobs j JOIN job_sources s ON s.job_id = j.id WHERE s.source = 'linkedin-alert'").fetchone()
    assert alert["job_url"].startswith("https://www.linkedin.com/")
    assert "Partnerschaftliches" in alert["description"]  # recovered without reading LinkedIn
    assert s.employers.matched == 1 and s.employers.recovered == 1 and len(s.employers.related_created) == 1
    # The alert is evaluated first, and a second relevant vacancy followed through the same pipeline.
    assert s.evaluate.evaluated == 2 and alert["status"] in ("approved", "prepared", "drafted")
    related = ctx.repo.get_job(s.employers.related_created[0])
    assert related["title"].startswith("(Junior) Data Scientist") and related["id"] != alert["id"]
    # The employer posting is the form-finder entry; discovery itself never claims a form.
    app = ctx.repo.get_application(alert["id"])
    assert app is not None and app["form_status"] == "found"
    assert app["form_reached_url"] == TENANT + "bewerbung/346291.html"
    assert not any("linkedin" in u for u in web.calls)
    seed = ctx.conn.execute("SELECT * FROM employer_seeds WHERE job_id = ?", (alert["id"],)).fetchone()
    assert seed["outcome"] == "matched" and seed["posting_url"] == TENANT + "details/346291.html"
    assert app["form_entry_url"] == TENANT + "details/346291.html"  # the employer posting was the form-finder entry
    assert alert["application_url"] == TENANT + "bewerbung/346291.html"  # and the finder moved on to the actual form
    ctx.repo.update_job(alert["id"], notes="my own note")
    folder = ws.applications_dir
    before = hash_tree(folder)
    events_before = ctx.conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    ctx.close()

    # A later run in the same workspace: another alert from the same employer, nothing new to search.
    ctx2 = make_ctx(ws)
    web2 = FakeWeb(pages())
    ctx2._http = WebAsHttp(web2)
    api2, calls2 = search_client(["https://www.cbc-cologne.de/karriere"])
    other = "Cloud Platform Engineer (m/w/d)"
    s2 = run_pipeline(ctx2, api_sources=[], mailbox=FakeMailbox([alert_mail(other, "9100")]),
                      renderer_factory=factory(FakeRenderer([1, 1])), mailer=FakeMailer(),
                      employer_fetch=web2, employer_api=api2)
    # No website search; the one job its careers site does not list gets one search for its own ad.
    assert s2.errors == [] and [c["objective"][:15] for c in calls2] == ["Find the job ad"]
    assert s2.employers.employers[0].source == "cache" and s2.employers.employers[0].scan.reused
    kept = ctx2.repo.get_job(alert["id"])
    assert kept["notes"] == "my own note" and kept["application_url"] == alert["application_url"]
    after = hash_tree(folder)
    assert {k: v for k, v in after.items() if k in before} == before  # prepared packages byte for byte
    assert ctx2.conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] >= events_before
    assert ctx2.conn.execute("SELECT COUNT(*) FROM jobs WHERE company = ?", (CBC,)).fetchone()[0] == 3  # alert, related, new alert
    ctx2.close()
