import base64
import email
import gzip
import json
from email import policy
from email.message import EmailMessage
from pathlib import Path

from autoapp.db.repo import Repo
from autoapp.db.schema import open_db
from autoapp.discovery.ingest import card_to_raw, ingest
from autoapp.sources.alerts.indeed import IndeedParser, canonical_job_url, job_key
from autoapp.sources.alerts.registry import parse_email

FIXTURES = Path(__file__).parent.parent / "fixtures" / "alerts"


def _load(name):
    return email.message_from_bytes((FIXTURES / name).read_bytes(), policy=policy.default)


def _cts(payload: dict) -> str:
    seg = base64.urlsafe_b64encode(gzip.compress(json.dumps(payload).encode())).decode().rstrip("=")
    return f"https://cts.indeed.com/v3/{seg}"


def test_match_email_gives_one_card_with_offline_job_key():
    r = parse_email(_load("indeed_match.eml"))
    assert r.parser_name == "indeed" and r.warnings == []
    [card] = r.cards
    assert (card.title, card.company, card.locations) == ("Data Engineer (m/w/d)", "Intrum Deutschland GmbH", ["Essen"])
    assert card.source == "indeed-match" and card.section == "recommended"
    assert card.source_job_id == "5b574860bd63a20c"
    assert card.url == "https://de.indeed.com/viewjob?jk=5b574860bd63a20c"
    assert card.working_time == "full_time" and card.work_mode == "hybrid"
    assert card.contract_type is None  # "Vollzeit" is working time, not a contract type


def test_footer_profile_lines_are_not_cards():
    r = parse_email(_load("indeed_match.eml"))
    titles = " ".join(c.title for c in r.cards)
    assert "Beispieljob" not in titles and "Mindestgrundgehalt" not in titles


def test_zero_card_email_warns():
    r = parse_email(_load("indeed_zero.eml"))
    assert r.parser_name == "indeed" and r.cards == []
    assert any("parser may be outdated" in w for w in r.warnings)


def test_job_key_decoding_keeps_only_the_key():
    link = _cts({"u": "https://de.indeed.com/rc/clk?jk=0123456789abcdef&tk=SECRET&from=x",
                 "m": {"accountId": "999", "tk": "SECRET"}})
    assert job_key(link) == "0123456789abcdef"
    assert job_key("https://de.indeed.com/viewjob?jk=0123456789abcdef") == "0123456789abcdef"
    assert job_key("https://cts.indeed.com/v3/not-base64!!") is None
    assert job_key(_cts({"u": "https://evil.example.com/?jk=0123456789abcdef"})) is None
    assert job_key("https://de.indeed.com/viewjob?jk=nothex") is None
    assert canonical_job_url("0123456789abcdef").endswith("jk=0123456789abcdef")


def test_alert_sender_and_multi_card_layout():
    text = (
        "Neue Jobs für Ihre Suche\n\n"
        "Backend Developer (m/w/d)\nMuster GmbH\n10115 Berlin\n"
        "Gehalt: 55.000 € - 65.000 € pro Jahr (geschätzt)\n"
        f"Job anzeigen: {_cts({'u': 'https://de.indeed.com/rc/clk?jk=aaaaaaaaaaaaaaaa'})}\n\n"
        "Data Engineer\nBeispiel AG\nMünchen\nAnstellungsart: Teilzeit, Festanstellung\n"
        f"Job anzeigen: {_cts({'u': 'https://de.indeed.com/rc/clk?jk=bbbbbbbbbbbbbbbb'})}\n"
        f"Jetzt bewerben: {_cts({'u': 'https://de.indeed.com/rc/clk?jk=bbbbbbbbbbbbbbbb'})}\n"
    )
    msg = EmailMessage()
    msg["From"] = "Indeed <alert@jobalert.indeed.com>"
    msg.set_content(text)
    cards = IndeedParser().parse(msg)
    assert [c.source_job_id for c in cards] == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]
    assert all(c.source == "indeed-alert" and c.section == "match" for c in cards)
    assert (cards[0].salary_estimate_min, cards[0].salary_estimate_max) == (55000, 65000)
    assert cards[0].locations == ["Berlin"]
    assert cards[1].working_time == "part_time" and cards[1].contract_type == "Teilzeit, Festanstellung"


def test_ingested_job_stores_no_tracking_data(tmp_path):
    repo = Repo(open_db(tmp_path / "t.db"))
    [card] = parse_email(_load("indeed_match.eml")).cards
    out = ingest(repo, card_to_raw(card))
    job = repo.get_job(out.job_id)
    assert job["job_url"] == "https://de.indeed.com/viewjob?jk=5b574860bd63a20c"
    rows = repo.conn.execute("SELECT url, raw FROM job_sources").fetchall()
    assert not any("cts.indeed.com" in (r["url"] or "") + (r["raw"] or "") for r in rows)


def test_match_email_with_work_location_line_keeps_title_company_and_city():
    # Layout seen from 2026-10: an "Arbeitsort" line follows the location.
    text = (
        "Guten Tag,\n\naufgrund Ihres Profils könnte diese Stelle als JUNIOR DATA ENGINEER (M/W/D) für Sie passend sein.\n\n"
        "JUNIOR DATA ENGINEER (M/W/D)\nReply\n40549 Düsseldorf\nArbeitsort: Hybrides Arbeiten\n\n"
        "Leistungen:\n- Weiterbildungsprogramme\n\n"
        f"Job anzeigen: {_cts({'u': 'https://de.indeed.com/rc/clk?jk=db6a180f94b437a9'})}\n"
    )
    msg = EmailMessage()
    msg["From"] = "Indeed <donotreply@match.indeed.com>"
    msg.set_content(text)
    [card] = IndeedParser().parse(msg)
    assert (card.title, card.company, card.locations) == ("JUNIOR DATA ENGINEER (M/W/D)", "Reply", ["Düsseldorf"])
    assert card.work_mode == "hybrid" and card.source_job_id == "db6a180f94b437a9"


def test_current_job_alert_reads_all_fourteen_jobs_without_fallback():
    from autoapp.sources.alerts.llm_extract import card_needs_fallback

    msg = _load("indeed_alert.eml")
    result = parse_email(msg)
    assert IndeedParser().is_job_alert(msg)
    assert result.warnings == [] and len(result.cards) == 14
    cards = result.cards
    assert [(c.title, c.company, c.locations) for c in cards[:3]] == [
        ("AI Systems Engineer / Software Engineer (m/w/d)", "Ai System Engineers", ["Bad Hersfeld"]),
        ("Backend Developer (m/w/x) – Java & AI-first Software Development", "BIG PICTURE GmbH", ["Berlin-Kreuzberg"]),
        ("Software Developer - Modeling & Simulation (d/m/w)", "E Airbus Defence and Space GmbH", ["Immenstaad am Bodensee"]),
    ]
    assert cards[-1].company == "Instaffo GmbH" and cards[-1].locations == ["Leipzig"]
    assert all(c.source == "indeed-alert" and not card_needs_fallback(c) for c in cards)
    assert all(c.source_job_id is None and c.url is None for c in cards)
    assert all(c.salary_estimate_min is None and c.salary_estimate_max is None for c in cards)
    assert not IndeedParser().is_job_alert(_load("indeed_zero.eml"))


def test_bare_job_links_keep_readable_keys_and_distinct_employers():
    msg = EmailMessage()
    msg["From"] = "Indeed <donotreply@jobalert.indeed.com>"
    msg.set_content(
        "Developer\nMuster - Software GmbH - Berlin\nhttps://de.indeed.com/viewjob?jk=aaaaaaaaaaaaaaaa&tk=SECRET\n\n"
        "Developer\nOther GmbH - Hamburg\nhttps://de.indeed.com/pagead/clk/dl?ad=SECRET\n\n"
        "Developer\nOther GmbH - München\nhttps://de.indeed.com/pagead/clk/dl?ad=SECOND_SECRET\n\n"
        "Developer\nOther GmbH - Hamburg\nhttps://de.indeed.com/pagead/clk/dl?ad=SECRET\n"
    )
    cards = IndeedParser().parse(msg)
    assert len(cards) == 3
    assert cards[0].company == "Muster - Software GmbH"
    assert cards[0].source_job_id == "aaaaaaaaaaaaaaaa"
    assert cards[0].url == canonical_job_url("aaaaaaaaaaaaaaaa")
    assert cards[1].url is None and cards[2].url is None


def test_current_alert_stores_no_click_tokens_and_calls_no_extraction_llm(tmp_path):
    from autoapp.llm.fake import FakeLLM
    from autoapp.sources.alerts import FakeMailbox
    from autoapp.sources.alerts.excerpt import Redactor, card_excerpts
    from autoapp.steps.context import RunContext
    from autoapp.steps.search import run_search
    from tests.helpers import make_workspace

    msg = _load("indeed_alert.eml")
    text = msg.get_body(("plain",)).get_content().replace("ad=DUMMY", "ad=SECRET_CLICK_TOKEN&alid=SECRET_ACCOUNT")
    msg.set_content(text)
    cards = IndeedParser().parse(msg)
    excerpts = card_excerpts(msg, cards, Redactor())
    assert len(excerpts) == 14
    assert cards[0].title in excerpts[0] and cards[1].title not in excerpts[0]
    assert all("SECRET" not in e and "alid=" not in e for e in excerpts)
    ctx = RunContext.open(make_workspace(tmp_path, alert_mode="search_first"))
    ctx.config.sources.alerts.llm_fallback = True
    client = FakeLLM()
    ctx.clients["alert_extract"] = client
    summary = run_search(ctx, api_sources=[], mailbox=FakeMailbox([("<indeed-alert@x>", msg)]))
    assert summary.alert_cards == 14 and summary.alert_llm_extracted == 0
    assert client.calls == []
    rows = ctx.conn.execute("SELECT raw, url FROM job_sources WHERE source = 'indeed-alert'").fetchall()
    assert len(rows) == 14
    for row in rows:
        blob = f"{row['raw']} {row['url']}"
        assert "SECRET" not in blob and "alid=" not in blob and "ad=" not in blob
        assert json.loads(row["raw"])["email_excerpt"]
    ctx.close()
