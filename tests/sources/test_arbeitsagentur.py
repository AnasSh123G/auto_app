from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

import httpx

from autoapp.config.models import Location, SearchProfile
from autoapp.sources.arbeitsagentur import ArbeitsagenturSource

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "arbeitsagentur"
SEARCH = json.loads((FIX / "search_data_scientist_berlin.json").read_text(encoding="utf-8"))
DETAIL = json.loads((FIX / "detail.json").read_text(encoding="utf-8"))
DETAIL_EXT = json.loads((FIX / "detail_external.json").read_text(encoding="utf-8"))


def make_source(requests: list[httpx.Request], **kwargs) -> ArbeitsagenturSource:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/jobdetails/" in request.url.path:
            ref = base64.b64decode(request.url.path.rsplit("/", 1)[1]).decode()
            return httpx.Response(200, json=DETAIL_EXT if ref.startswith("14225") else DETAIL)
        return httpx.Response(200, json=SEARCH)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ArbeitsagenturSource(client, delay_s=0, **kwargs)


def test_keywords_only_sends_no_optional_filters() -> None:
    requests: list[httpx.Request] = []
    jobs = list(make_source(requests).search(SearchProfile(name="p", keywords=["Data Scientist"])))
    assert len(requests) == 1
    params = dict(requests[0].url.params)
    assert params == {"was": "Data Scientist", "angebotsart": "1", "size": "50", "page": "1"}
    assert requests[0].headers["X-API-Key"] == "jobboerse-jobsuche"
    assert len(jobs) == 5
    assert all(j.search_profile == "p" and j.source == "arbeitsagentur" for j in jobs)


def test_fully_filtered_profile_params() -> None:
    requests: list[httpx.Request] = []
    profile = SearchProfile(
        name="full",
        keywords=["Python Entwickler"],
        locations=[Location(name="München", radius_km=30)],
        work_mode="hybrid",
        working_time="full_time",
        contract_type="permanent",
        max_posting_age_days=7,
        exclude_agencies=True,
    )
    list(make_source(requests).search(profile))
    assert dict(requests[0].url.params) == {
        "was": "Python Entwickler",
        "wo": "München",
        "umkreis": "30",
        "veroeffentlichtseit": "7",
        "arbeitszeit": "vz;ho",
        "befristung": "2",
        "angebotsart": "1",
        "zeitarbeit": "false",
        "pav": "false",
        "size": "50",
        "page": "1",
    }


def test_part_time_fixed_term_and_location_without_radius() -> None:
    requests: list[httpx.Request] = []
    profile = SearchProfile(
        name="p",
        keywords=["x"],
        locations=[Location(name="Berlin")],
        working_time="part_time",
        contract_type="fixed_term",
        work_mode="onsite",
        exclude_agencies=False,
    )
    list(make_source(requests).search(profile))
    params = dict(requests[0].url.params)
    assert params["arbeitszeit"] == "tz"
    assert params["befristung"] == "1"
    assert params["wo"] == "Berlin"
    assert "umkreis" not in params and "zeitarbeit" not in params and "pav" not in params


def test_any_values_send_nothing() -> None:
    requests: list[httpx.Request] = []
    profile = SearchProfile(
        name="p", keywords=["x"], working_time="any", contract_type="any", work_mode="any"
    )
    list(make_source(requests).search(profile))
    params = dict(requests[0].url.params)
    assert not {"arbeitszeit", "befristung"} & set(params)


def test_one_request_per_keyword_and_location_merged_by_refnr() -> None:
    requests: list[httpx.Request] = []
    profile = SearchProfile(
        name="p",
        keywords=["a", "b"],
        locations=[Location(name="Berlin", radius_km=10), Location(name="Hamburg")],
    )
    jobs = list(make_source(requests).search(profile))
    assert [(r.url.params["was"], r.url.params["wo"]) for r in requests] == [
        ("a", "Berlin"),
        ("a", "Hamburg"),
        ("b", "Berlin"),
        ("b", "Hamburg"),
    ]
    assert len(jobs) == 5  # identical responses merge by refnr


def test_search_maps_fields() -> None:
    jobs = list(make_source([]).search(SearchProfile(name="p", keywords=["x"])))
    first = jobs[0]
    assert first.source_job_id == "10001-1003259403-S"
    assert first.title == "Data Scientist (f/m/d)"
    assert first.company == "Forto Logistics SE & Co. KG"
    assert first.location == "Berlin"
    assert first.url == "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1003259403-S"
    assert first.posted_at == date(2026, 9, 15)
    assert first.working_time == "full_time"
    assert first.contract_type == "permanent"
    assert first.work_mode == "hybrid"
    assert first.description is None


def test_pagination_stops_on_short_page_and_caps() -> None:
    full_page = {"ergebnisliste": [{"referenznummer": f"r{i}", "stellenangebotsTitel": "t", "firma": "c"} for i in range(50)], "maxErgebnisse": 1000}
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        items = [{**it, "referenznummer": f"p{page}-{it['referenznummer']}"} for it in full_page["ergebnisliste"]]
        return httpx.Response(200, json={"ergebnisliste": items, "maxErgebnisse": 1000})

    src = ArbeitsagenturSource(httpx.Client(transport=httpx.MockTransport(handler)), delay_s=0)
    jobs = list(src.search(SearchProfile(name="p", keywords=["x"])))
    assert calls == [1, 2, 3]
    assert len(jobs) == 150


def test_fetch_detail_maps_description_and_location() -> None:
    requests: list[httpx.Request] = []
    src = make_source(requests)
    raw = list(src.search(SearchProfile(name="p", keywords=["x"])))[0]
    detailed = src.fetch_detail(raw)
    detail_req = requests[-1]
    assert detail_req.url.path.endswith("/pc/v4/jobdetails/MTAwMDEtMTAwMzI1OTQwMy1T")
    assert detailed.description and detailed.description.startswith("# About Us")
    assert detailed.location == "Berlin"
    assert detailed.source_job_id == raw.source_job_id
    assert "stellenangebotsBeschreibung" not in detailed.raw["detail"]


def test_fetch_detail_external_url_and_salary() -> None:
    src = make_source([])
    raw = [j for j in src.search(SearchProfile(name="p", keywords=["x"])) if j.source_job_id.startswith("14225")][0]
    detailed = src.fetch_detail(raw)
    assert detailed.application_url and detailed.application_url.startswith("https://www.jobvector.de/")
    assert (detailed.ad_salary_min, detailed.ad_salary_max) == (80000, 90000)
    assert detailed.salary_estimate_min is None


def test_contact_fields_collected_from_detail() -> None:
    detail = {
        "referenznummer": "1-2-S",
        "stellenangebotsTitel": "T",
        "firma": "C",
        "stellenangebotsBeschreibung": "d",
        "kontakt": {"email": "jobs@example.com", "telefon": "030 123"},
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=detail)))
    src = ArbeitsagenturSource(client, delay_s=0)
    out = src.fetch_detail(src._to_raw(detail, "1-2-S", None))
    assert out.contact_fields and "jobs@example.com" in out.contact_fields


def test_http_error_propagates() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    src = ArbeitsagenturSource(client, delay_s=0)
    try:
        list(src.search(SearchProfile(name="p", keywords=["x"])))
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 503
    else:  # pragma: no cover
        raise AssertionError("expected HTTPStatusError")
