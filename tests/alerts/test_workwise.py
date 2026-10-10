"""Workwise alert cards: the company comes from the email when the card shows it."""

from __future__ import annotations

from email.message import EmailMessage

from autoapp.sources.alerts.workwise import WorkwiseParser


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
