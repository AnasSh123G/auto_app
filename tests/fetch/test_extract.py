from __future__ import annotations

from datetime import date
from pathlib import Path

from autoapp.fetch.extract import extract_job, html_to_text

PAGES = Path(__file__).resolve().parent.parent / "fixtures" / "pages"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def test_json_ld_graph_with_escaped_html_description() -> None:
    job = extract_job(page("jsonld_stepstone.html"), "https://www.stepstone.de/stellenangebote--x-13850318-inline.html")
    assert job.method == "json-ld"
    assert job.title == "Senior Data Scientist (m/w/d)"
    assert job.company == "Acme Analytics GmbH"
    assert job.location == "Berlin" and job.locations == ["Berlin", "Hamburg"]
    assert job.posted_at == date(2026, 10, 3)
    assert (job.ad_salary_min, job.ad_salary_max) == (54000, 66000)  # monthly x 12
    assert job.work_mode == "remote"
    assert job.working_time == "full_time" and job.contract_type == "permanent"
    assert job.application_url == "https://www.stepstone.de/apply/13850318"
    assert job.description is not None
    assert "Wir bauen Prognosemodelle." in job.description
    assert "- Python und SQL" in job.description
    assert "<" not in job.description


def test_json_ld_in_list_with_annual_salary_and_html_description() -> None:
    job = extract_job(page("jsonld_list_annual.html"), "https://example.com/job/1")
    assert job.method == "json-ld"
    assert job.title == "Backend Developer" and job.company == "Beta AG"
    assert job.location == "Köln"
    assert (job.ad_salary_min, job.ad_salary_max) == (65000, 65000)
    assert job.working_time == "part_time"
    assert job.application_url is None
    assert job.description == "Build APIs.\nWork with Kotlin.\nRemote friendly."


def test_hourly_salary_is_not_converted() -> None:
    job = extract_job(page("jsonld_hourly.html"), "https://example.com/job/2")
    assert job.ad_salary_min is None and job.ad_salary_max is None
    assert job.description == "Make coffee."


def test_json_ld_without_description_takes_text_from_selectors() -> None:
    job = extract_job(page("jsonld_no_description_stepstone.html"), "https://www.stepstone.de/stellenangebote--1-inline.html")
    assert job.method == "json-ld"
    assert job.title == "Data Engineer" and job.company == "Delta GmbH"
    assert job.description and "Datenpipelines mit Airflow" in job.description


def test_selectors_stepstone() -> None:
    job = extract_job(page("selectors_stepstone.html"), "https://www.stepstone.de/stellenangebote--1-inline.html")
    assert job.method == "selectors"
    assert job.title == "Data Analyst (m/w/d)"
    assert job.company == "Gamma SE" and job.location == "München"
    assert job.description is not None
    assert "Ihre Aufgaben" in job.description and "- Reports erstellen" in job.description
    assert "Impressum" not in job.description and "Karriere" not in job.description


def test_selectors_linkedin_guest_view() -> None:
    job = extract_job(page("selectors_linkedin.html"), "https://www.linkedin.com/jobs/view/3912345678/")
    assert job.method == "selectors"
    assert job.title == "ML Engineer" and job.company == "Epsilon Labs"
    assert job.location == "Düsseldorf, Nordrhein-Westfalen"
    assert job.description is not None and "- PyTorch" in job.description


def test_selectors_indeed() -> None:
    job = extract_job(page("selectors_indeed.html"), "https://de.indeed.com/viewjob?jk=abc")
    assert job.method == "selectors"
    assert job.title == "Systemadministrator" and job.company == "Zeta GmbH" and job.location == "Leipzig"
    assert job.description == "Betreuung der Serverinfrastruktur.\nLinux, Ansible, Monitoring."


def test_fallback_largest_text_block() -> None:
    job = extract_job(page("fallback_employer.html"), "https://karriere.omega.example/jobs/pm")
    assert job.method == "fallback"
    assert job.title == "Product Manager Data Platform"
    assert job.company == "Omega Software GmbH"
    assert job.description is not None
    assert "Roadmap unserer Datenplattform" in job.description
    assert "- Stakeholder-Management" in job.description
    assert "Impressum" not in job.description and "ignore me" not in job.description


def test_known_site_without_matching_selectors_falls_back() -> None:
    job = extract_job(page("fallback_employer.html"), "https://www.stepstone.de/stellenangebote--1-inline.html")
    assert job.method == "fallback"
    assert job.description and "Roadmap" in job.description


def test_empty_page() -> None:
    job = extract_job(page("empty.html"), "https://example.com/x")
    assert job.description in (None, "")


def test_invalid_json_ld_is_ignored() -> None:
    html = '<html><head><script type="application/ld+json">{not json</script></head><body><main><p>' + "Text " * 60 + "</p></main></body></html>"
    job = extract_job(html, "https://example.com/x")
    assert job.method == "fallback" and job.description


def test_html_to_text_lists_and_breaks() -> None:
    assert html_to_text("<p>A</p><ul><li>b</li><li>c</li></ul>x<br>y") == "A\n\n- b\n- c\nx\ny"
