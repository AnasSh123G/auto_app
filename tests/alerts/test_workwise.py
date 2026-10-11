"""Workwise alert cards: the company comes from the email when the card shows it."""

from __future__ import annotations

import json
from email.message import EmailMessage

from autoapp.sources.alerts import FakeMailbox, Redactor
from autoapp.sources.alerts.excerpt import card_excerpts
from autoapp.sources.alerts.workwise import WorkwiseParser
from autoapp.steps.context import RunContext
from autoapp.steps.search import run_search
from tests.alerts.conftest import load_eml
from tests.helpers import make_workspace


def mail(html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Workwise <jobs@workwise.io>"
    msg["Subject"] = "Neue Jobs für dich"
    msg.set_content("Neue Jobs")
    msg.add_alternative(html, subtype="html")
    return msg


CARD = """<html><body><table>
<tr><td><a href="https://www.workwise.io/job/1001-backend-developer-m-w-d">Backend Developer (m/w/d)</a>
<div>Muster GmbH</div><div>Berlin</div><a href="https://www.workwise.io/job/1001-backend-developer-m-w-d">Job ansehen</a></td></tr>
<tr><td><a href="https://www.workwise.io/job/1002-data-engineer">Data Engineer (m/w/d)</a></td></tr>
</table></body></html>"""


def test_card_with_a_company_line_names_the_company():
    cards = {c.source_job_id: c for c in WorkwiseParser().parse(mail(CARD))}
    one = cards["1001"]
    assert one.title == "Backend Developer (m/w/d)" and one.company == "Muster GmbH" and not one.company_unknown


def test_card_without_a_company_keeps_it_unknown():
    two = {c.source_job_id: c for c in WorkwiseParser().parse(mail(CARD))}["1002"]
    assert two.company == "" and two.company_unknown


# --- the real layout: one jobsuche?id= link per card (anonymised fixture) -------------------------------------------

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJjYW5kaWRhdGVfaWQiOjEyMywibG9naW4iOjF9.c2lnbmF0dXJl"


def real_mail() -> EmailMessage:
    return load_eml("workwise_alert.eml")


def with_tokens(msg: EmailMessage) -> EmailMessage:
    """The fixture with realistic JWT-like eTT / lT tokens in its links, as the real email has them."""
    html = msg.get_body(("html",)).get_content().replace("DUMMY", JWT)
    out = EmailMessage()
    for key in ("From", "To", "Subject", "Date", "Message-ID"):
        out[key] = msg[key]
    out.set_content(html, subtype="html")
    return out


def test_the_real_alert_gives_both_cards_with_a_token_free_url():
    cards = WorkwiseParser().parse(with_tokens(real_mail()))
    assert [(c.title, c.company, c.locations, c.source_job_id, c.url) for c in cards] == [
        ("IT-System Engineer für IT-Infrastruktur (m/w/d)", "Beispiel Software GmbH", ["Essen"], "117974",
         "https://www.workwise.io/job/117974"),
        ("Softwareentwickler (m/w/d)", "Beispiel Entwicklung GmbH", ["Rednitzhembach"], "124834",
         "https://www.workwise.io/job/124834"),
    ]
    assert all(not c.company_unknown and not c.title_truncated and c.source == "workwise-alert" for c in cards)
    assert [(c.work_mode, c.working_time) for c in cards] == [("hybrid", "full_time"), ("hybrid", "any")]
    assert all(c.salary_estimate_min is None and "€" not in str(c.badges) for c in cards)  # the 65.000 € is not an estimate


def test_the_real_alert_is_a_job_alert_but_workwise_account_mail_is_not():
    assert WorkwiseParser().is_job_alert(real_mail())
    account = EmailMessage()
    account["From"] = "Workwise <jobs@workwise.io>"
    account.set_content("x")
    account.add_alternative(
        '<a href="https://www.workwise.io/jobsuche?eTT=a&amp;lT=b&amp;search_id=5">Alle passenden Jobs anschauen</a>'
        '<a href="https://www.workwise.io/profil?eTT=a&amp;lT=b">Profil</a>', subtype="html")
    assert not WorkwiseParser().is_job_alert(account)
    assert WorkwiseParser().parse(account) == []


def test_excerpts_are_per_card_and_carry_no_token():
    msg = with_tokens(real_mail())
    cards = WorkwiseParser().parse(msg)
    first, second = card_excerpts(msg, cards, Redactor())
    assert cards[0].title in first and "Essen" in first and cards[1].title not in first
    assert cards[1].title in second and "Rednitzhembach" in second and cards[0].title not in second
    for excerpt in (first, second):
        assert not any(bad in excerpt for bad in ("eyJ", "eTT", "lT=", JWT, "?"))


def test_a_stored_workwise_card_has_no_token_anywhere(tmp_path):
    ctx = RunContext.open(make_workspace(tmp_path))
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<ww@x>", with_tokens(real_mail()))]))
    assert summary.alert_cards == 2
    rows = ctx.conn.execute("SELECT raw, url FROM job_sources WHERE source = 'workwise-alert'").fetchall()
    assert len(rows) == 2
    for row in rows:
        blob = f"{row['raw']} {row['url']}"
        assert not any(bad in blob for bad in ("eyJ", "eTT", "lT=")), blob
        assert json.loads(row["raw"])["email_excerpt"]
    ctx.close()
