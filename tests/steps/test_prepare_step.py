import csv
import email
import json
from contextlib import contextmanager
from datetime import date
from email import policy

import pytest

from autoapp.db.status import Status
from autoapp.evaluation.rules import finalize
from autoapp.evaluation.schema import EvaluationResult
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext
from autoapp.steps.prepare import ad_asks_salary, prepare_jobs
from autoapp.tracking.csv_export import export_csv
from tests.helpers import _pdf, make_workspace

AD = (
    "Wir suchen einen Backend Developer (m/w/d) in Berlin. Python, Docker, Grundkenntnisse in Kubernetes. "
    "Bitte senden Sie Ihre Bewerbung mit Gehaltsvorstellung an jobs@muster-gmbh.de, "
    "Ansprechpartnerin Frau Weber, Kennziffer 2026-17. Wir bieten 60.000 bis 70.000 EUR."
)

LETTER = {
    "paragraphs": [
        "Bei der Muster GmbH entwickle ich seit 2022 Python-Services mit FastAPI.",
        "Ich verantworte die CI-Pipelines von 4 Teams, nutze Docker privat und kenne die Grundlagen von Kubernetes.",
        "Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr. Ich freue mich auf ein Gespräch.",
    ],
    "motivation_short": "Ihre Plattform passt zu meiner Arbeit mit Python-Services.",
    "email_body": "Guten Tag Frau Weber,\n\nanbei meine Bewerbung.\n\nViele Grüße\nMax Mustermann",
    "claims": [
        {"text": "Python seit 2022", "type": "cv", "evidence": "Entwicklung von Python-Services mit FastAPI"},
        {"text": "CI 4 Teams", "type": "cv", "evidence": "Maintained CI pipelines used by 4 teams"},
        {"text": "Docker privat", "type": "profile", "evidence": "Docker: basic (private Projekte)"},
        {"text": "Kubernetes-Grundlagen", "type": "assumed_basic", "requirement": "Grundkenntnisse in Kubernetes"},
    ],
}


def evaluation_payload(ctx):
    result = EvaluationResult(
        fit_score=88, rationale="passt", dealbreakers=[], seniority="mid", german_level_required="B2",
        work_mode="hybrid", channel="email", apply_email="jobs@muster-gmbh.de", contact_person="Frau Weber",
        reference="2026-17", requested_documents=["cv", "cover_letter", "certificates"], language="de",
        requirements=[
            {"text": "Python", "proficiency": "intermediate", "core": True, "status": "matched", "evidence": "Python"},
            {"text": "Docker", "proficiency": "unspecified", "core": False, "status": "bridgeable"},
            {"text": "Grundkenntnisse in Kubernetes", "proficiency": "basic", "core": False, "status": "bridgeable"},
        ],
    )
    return finalize(result, profile=ctx.profile, apply_threshold=75, maybe_threshold=60, source_texts=[AD])


def approved_job(ctx, title="Backend Developer (m/w/d)", company="Muster GmbH"):
    jid = ctx.repo.insert_job(title=title, company=company, location="Berlin", description=AD,
                              search_profile="backend", ad_salary_min=60000, ad_salary_max=70000,
                              job_url="https://www.stepstone.de/stellenangebote--1-inline.html")
    ctx.repo.add_source(jid, "stepstone-alert", f"src-{jid}")
    final = evaluation_payload(ctx)
    ctx.repo.add_evaluation(jid, fit_score=final.fit_score, decision=final.decision,
                            payload=final.model_dump(mode="json"))
    ctx.repo.transition(jid, Status.APPROVED)
    return jid


class FakeRenderer:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def html_to_pdf(self, html, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        _pdf(out, "Anschreiben")
        self.calls += 1
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]


def factory(renderer):
    @contextmanager
    def make():
        yield renderer
    return make


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def test_ad_asks_salary():
    assert ad_asks_salary(AD) and not ad_asks_salary("Python Entwickler gesucht")


def test_full_folder_and_records(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])), today=date(2026, 10, 7))
    assert s.prepared == 1 and s.flagged == 0
    app = ctx.repo.get_application(jid)
    folder = ctx.ws.applications_dir / app["folder"].split("/")[-1]
    assert folder.name == f"application_muster-gmbh_backend-developer_{jid}"
    names = {p.name for p in folder.iterdir()}
    for f in ("job.md", "evaluation.md", "cover_letter.md", "cover_letter.pdf", "Lebenslauf_de.pdf",
              "Zeugnis_Muster.pdf", "Bewerbungsmappe.pdf", "form_answers.md", "apply.html", "README.md", "email.eml"):
        assert f in names, f
    assert ctx.repo.get_job(jid)["status"] == "prepared"
    assert app["salary_expectation"] == "65000"  # target 65000 within the 60-70k ad range
    assert app["contact_email"] == "jobs@muster-gmbh.de" and app["channel"] == "email"
    md = (folder / "cover_letter.md").read_text()
    assert md.startswith("# Bewerbung als Backend Developer, Kennziffer 2026-17")
    assert "Sehr geehrte Frau Weber," in md
    readme = (folder / "README.md").read_text()
    assert "Kubernetes" in readme and "brush up" in readme.lower()
    msg = email.message_from_bytes((folder / "email.eml").read_bytes(), policy=policy.default)
    assert msg["To"] == "jobs@muster-gmbh.de"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["Bewerbungsmappe.pdf"]
    # salary sentence offered to the writer because the ad asks for it
    assert "Gehaltsvorstellung liegt bei 65.000" in ctx.clients["write"].calls[0][2]
    # 7.7: assumed skills reach the CSV
    export_csv(ctx.conn, ctx.ws.csv_path)
    rows = list(csv.DictReader(ctx.ws.csv_path.open(encoding="utf-8-sig"), delimiter=";"))
    assert "Kubernetes" in rows[0]["assumed_skills"] and "Docker" not in rows[0]["assumed_skills"]


def test_letter_over_one_page_is_shortened_once(ctx):
    approved_job(ctx)
    llm = FakeLLM(script={"write": [LETTER, LETTER]})
    ctx.clients["write"] = llm
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([2, 1])))
    assert s.flagged == 0 and len(llm.calls) == 2 and "255 words" in llm.calls[1][2]


def test_letter_still_too_long_is_flagged(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER, LETTER]})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([2, 2])))
    assert s.flagged == 1
    assert "2 pages" in json.loads(ctx.repo.get_job(jid)["needs_review_reasons"])[0]


def test_existing_folder_needs_force_and_backs_up(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER, LETTER]})
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    folder = ctx.ws.applications_dir / ctx.repo.get_application(jid)["folder"].split("/")[-1]
    ctx.repo.transition(jid, Status.APPROVED)
    s = prepare_jobs(ctx, [jid], renderer_factory=factory(FakeRenderer([1])))
    assert s.skipped_existing == [jid] and s.prepared == 0
    s = prepare_jobs(ctx, [jid], force=True, renderer_factory=factory(FakeRenderer([1])))
    assert s.prepared == 1
    assert len(list(folder.glob("cover_letter.*.md"))) == 1


def test_folder_left_by_an_interrupted_run_is_rebuilt_not_skipped(ctx):
    from autoapp.package.slug import folder_name

    jid = approved_job(ctx)
    job = ctx.repo.get_job(jid)
    partial = ctx.ws.applications_dir / folder_name(job["company"], job["title"], jid)
    partial.mkdir(parents=True)
    (partial / "cover_letter.pdf").write_bytes(b"half written")  # Ctrl-C hit during the letter
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert s.prepared == 1 and s.skipped_existing == []
    assert (partial / "README.md").exists() and ctx.repo.get_job(jid)["status"] == "prepared"
    assert list(partial.glob("cover_letter.*.md")) == []  # a rebuild of a partial folder makes no backup


def test_limit(ctx):
    ctx.config.limits.max_prepare_per_run = 2
    for i in range(3):
        approved_job(ctx, company=f"Firma {i}")
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER] * 2})
    s = prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    assert s.prepared == 2 and s.waiting == 1
    assert len(ctx.repo.jobs_by_status("approved")) == 1


@pytest.mark.browser
def test_real_pdf_rendering(ctx):
    jid = approved_job(ctx)
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    s = prepare_jobs(ctx)
    assert s.prepared == 1 and s.flagged == 0
    from pypdf import PdfReader

    folder = ctx.ws.applications_dir / ctx.repo.get_application(jid)["folder"].split("/")[-1]
    reader = PdfReader(folder / "Bewerbungsmappe.pdf")
    assert len(reader.pages) == 3  # letter, CV, certificate
    assert "Frau Weber" in reader.pages[0].extract_text()
