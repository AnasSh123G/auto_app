from __future__ import annotations

import re
from pathlib import Path

from autoapp.config.models import Personal
from autoapp.evaluation.schema import EvaluationResult, FinalEvaluation, Requirement
from autoapp.package.render import (
    AnswerItem,
    ApplyData,
    EvaluationExtra,
    FileItem,
    FormAnswers,
    JobInfo,
    ReadmeData,
    render_apply_html,
    render_cover_letter_html,
    render_evaluation_md,
    render_form_answers_md,
    render_job_md,
    render_readme_md,
)
from autoapp.writing.letter_format import date_line
from autoapp.writing.schema import Claim, CoverLetter

PERSONAL = Personal(
    first_name="Max",
    last_name="Mustermann",
    email="max@example.de",
    phone="+49 170 1234567",
    street="Hauptstraße 1",
    postal_code="10115",
    city="Berlin",
    linkedin_url="https://linkedin.com/in/max",
)


def make_letter(**kw) -> CoverLetter:
    base = dict(
        language="de",
        subject="Bewerbung als Softwareentwickler, Kennziffer 2026-17",
        salutation="Sehr geehrte Damen und Herren,",
        paragraphs=["Erster Absatz mit Müller & Söhne <b>.", "Zweiter Absatz."],
        closing="Mit freundlichen Grüßen",
        signature="Max Mustermann",
        motivation_short="Kurz.",
        email_body="Anbei meine Unterlagen.",
    )
    base.update(kw)
    return CoverLetter(**base)


def test_german_letter_content():
    from datetime import date

    dl = date_line("Berlin", date(2026, 10, 7), "de")
    html = render_cover_letter_html(make_letter(), PERSONAL, ["Müller & Söhne GmbH", "Weg 2", "80331 München"], dl)
    assert "Sehr geehrte Damen und Herren," in html
    assert "Bewerbung als Softwareentwickler, Kennziffer 2026-17" in html
    assert "Berlin, 7. Oktober 2026" in html
    assert "Hauptstraße 1" in html and "10115 Berlin" in html and "max@example.de" in html
    assert "Müller &amp; Söhne GmbH" in html
    assert "Mit freundlichen Grüßen" in html
    assert 'lang="de"' in html


def test_letter_escapes_html():
    html = render_cover_letter_html(make_letter(), PERSONAL, [], "Berlin, 1. Januar 2026")
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


def test_letter_is_self_contained():
    html = render_cover_letter_html(make_letter(), PERSONAL, ["X"], "d")
    assert not re.search(r"(src|href)=", html)
    assert "@import" not in html and "<link" not in html and "<script" not in html


def test_arbeitnow_attribution_is_safe_optional_and_local_only():
    url = 'https://www.arbeitnow.com/jobs/role-"quoted"'
    job = render_job_md(JobInfo("Dev", "A", "Description", arbeitnow_url=url))
    readme = render_readme_md(ReadmeData("Dev", "A", "email", "id", arbeitnow_url=url))
    helper = render_apply_html(ApplyData("Dev", "A", [], arbeitnow_url=url))
    for text in (job, readme, helper):
        assert "Arbeitnow" in text and "%22quoted%22" in text
    for bad in ("javascript:alert(1)", "https://evil.test/jobs/role"):
        assert "Arbeitnow" not in render_job_md(JobInfo("Dev", "A", "", arbeitnow_url=bad))
        assert "Source: Arbeitnow" not in render_apply_html(ApplyData("Dev", "A", [], arbeitnow_url=bad))
    assert "Arbeitnow" not in render_readme_md(ReadmeData("Dev", "A", "email", "id"))
    assert "Arbeitnow" not in render_cover_letter_html(make_letter(), PERSONAL, [], "today")


def test_job_md_labels_estimate():
    job = JobInfo(
        title="Dev",
        company="ACME",
        description="Full text <here>.",
        locations=["Berlin", "Remote"],
        job_url="https://example.com/job",
        application_url="https://example.com/apply",
        sources=["stepstone", "linkedin"],
        salary_estimate_min=55000,
        salary_estimate_max=75000,
    )
    md = render_job_md(job)
    assert "# Dev" in md and "ACME" in md
    assert "Berlin; Remote" in md
    assert "StepStone estimate" in md and "55.000 - 75.000 EUR per year" in md
    assert "stated in the ad" not in md
    assert "https://example.com/apply" in md and "stepstone, linkedin" in md
    assert "Full text <here>." in md  # markdown is not HTML-escaped


def test_job_md_ad_salary():
    md = render_job_md(JobInfo(title="T", company="C", description="d", ad_salary_min=60000, ad_salary_max=70000))
    assert "Salary (stated in the ad):** 60.000 - 70.000 EUR per year" in md
    assert "estimate" not in md


def make_eval(**kw) -> FinalEvaluation:
    reqs = [
        Requirement(text="Python", proficiency="advanced", core=True, status="matched", evidence="5 Jahre Python"),
        Requirement(text="Docker", proficiency="basic", core=False, status="bridgeable"),
        Requirement(text="Rust", proficiency="advanced", core=True, status="missing"),
    ]
    res = dict(
        fit_score=80,
        rationale="Good fit.",
        requirements=reqs,
        dealbreakers=[],
        seniority="mid",
        german_level_required="B2",
        work_mode="hybrid",
        channel="email",
        requested_documents=["cv"],
        language="de",
    )
    res.update(kw)
    return FinalEvaluation(result=EvaluationResult(**res), decision="apply", fit_score=80, notes=["Rule: kept"])


def test_evaluation_md_groups():
    md = render_evaluation_md(make_eval(), EvaluationExtra(title="Dev", company="ACME", notes=["Extra note"]))
    assert "80/100" in md and "apply" in md and "Good fit." in md
    i_m, i_b, i_x = md.index("### Matched"), md.index("### Bridgeable"), md.index("### Missing")
    assert i_m < i_b < i_x
    assert "Python" in md[i_m:i_b] and "5 Jahre Python" in md[i_m:i_b]
    assert "Docker" in md[i_b:i_x]
    assert "Rust" in md[i_x:]
    assert "Rule: kept" in md and "Extra note" in md
    assert "Dev at ACME" in md


def test_evaluation_md_dealbreakers():
    md = render_evaluation_md(make_eval(dealbreakers=["Needs security clearance"]))
    assert "Needs security clearance" in md


def make_answers(**kw) -> FormAnswers:
    base = dict(
        sender=PERSONAL,
        salary_expectation=65000,
        earliest_start="01.12.2026",
        notice_period="3 Monate",
        work_authorization="EU citizen",
        language_levels={"Deutsch": "C1", "Englisch": "native"},
        linkedin_url="https://linkedin.com/in/max",
        motivation="Ich mag Ihr Produkt.",
    )
    base.update(kw)
    return FormAnswers(**base)


def test_form_answers_with_salary():
    md = render_form_answers_md(make_answers())
    assert "## Salary expectation\n\n65000" in md
    assert "annual gross" in md
    assert "## First name\n\nMax" in md
    assert "Deutsch: C1" in md and "3 Monate" in md and "EU citizen" in md
    assert "https://linkedin.com/in/max" in md and "Ich mag Ihr Produkt." in md


def test_form_answers_salary_not_configured():
    md = render_form_answers_md(make_answers(salary_expectation=None))
    assert "## Salary expectation\n\nnot configured" in md


def make_readme(**kw) -> ReadmeData:
    base = dict(
        title="Dev",
        company="ACME",
        channel="email",
        job_id="a1b2c3",
        apply_email="jobs@acme.de",
        files=["cover_letter.pdf", "Bewerbungsmappe.pdf"],
    )
    base.update(kw)
    return ReadmeData(**base)


def test_readme_channels():
    email = render_readme_md(make_readme())
    assert "jobs@acme.de" in email and "autoapp send a1b2c3" in email and "auto-send" in email
    form = render_readme_md(
        make_readme(channel="employer_form", application_url="https://acme.de/apply")
    )
    assert "https://acme.de/apply" in form and "autoapp assist a1b2c3" in form and "apply.html" in form
    plat = render_readme_md(make_readme(channel="platform", job_url="https://stepstone.de/j/1"))
    assert "apply on the site" in plat and "https://stepstone.de/j/1" in plat
    assert "`Bewerbungsmappe.pdf`" in plat


def test_readme_conditional_sections():
    plain = render_readme_md(make_readme())
    for text in ("NEEDS REVIEW", "brush up", "Missing documents", "salary expectation", "Warnings"):
        assert text not in plain
    full = render_readme_md(
        make_readme(
            needs_review_reasons=["Phrase 'dynamisches Team' remains"],
            assumed_skills=["Docker", "Terraform"],
            missing_documents=["portfolio"],
            salary_note=True,
            bundle_too_large=True,
            bundle_size_mb=6.2,
            bundle_max_mb=5,
            warnings=["CV cv.docx is not a PDF"],
        )
    )
    assert full.index("NEEDS REVIEW") < full.index("## Apply")
    assert "dynamisches Team" in full
    assert "Before an interview, brush up on" in full and "- Docker" in full and "- Terraform" in full
    assert "- portfolio" in full
    assert "asks for a salary expectation" in full
    assert "6.2 MB" in full and "5 MB" in full and "cv.docx" in full


def make_apply(tmp_path: Path) -> ApplyData:
    f = tmp_path / "Bewerbungsmappe.pdf"
    return ApplyData(
        title="Dev <script>",
        company="ACME",
        job_url="https://example.com/job?a=1&b=2",
        application_url="https://acme.de/apply",
        answers=[*make_answers().items()[:2], AnswerItem("Salary expectation", "not configured", copyable=False)],
        files=[FileItem("Application bundle", f)],
    )


def test_apply_html_is_offline(tmp_path):
    html = render_apply_html(make_apply(tmp_path))
    assert 'src="http' not in html
    assert "<link" not in html and "@import" not in html and "<script src" not in html
    assert "url(" not in html and "<img" not in html
    hrefs = re.findall(r'href="([^"]*)"', html)
    external = [h for h in hrefs if h.startswith("http")]
    assert sorted(external) == ["https://acme.de/apply", "https://example.com/job?a=1&amp;b=2"]
    assert "navigator.clipboard.writeText" in html
    assert "<script>" in html and "<script>" not in html.split("</script>")[-1]


def test_apply_html_content(tmp_path):
    html = render_apply_html(make_apply(tmp_path))
    assert f"file://{tmp_path}/Bewerbungsmappe.pdf" in html
    assert f">{tmp_path}/Bewerbungsmappe.pdf<" in html
    assert html.count("data-target=") == 2  # the uncopyable item has no button
    assert ">Max</pre>" in html and "not configured" in html
    assert "Dev &lt;script&gt;" in html  # escaped
    assert "prefers-color-scheme: dark" in html


def test_assumed_skills_property_feeds_readme():
    letter = make_letter(claims=[Claim(text="Docker", type="assumed_basic", requirement="Docker basics")])
    assert render_readme_md(make_readme(assumed_skills=letter.assumed_skills)).count("Docker basics") == 1
