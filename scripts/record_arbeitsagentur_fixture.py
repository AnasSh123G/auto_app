"""One-off: record a real Arbeitsagentur search and detail response as test fixtures.

Makes exactly two requests (search for "Data Scientist" in Berlin, size 5, then the detail of
the first hit) and writes them to ``tests/fixtures/arbeitsagentur/``. Not used by the tests.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
HEADERS = {"X-API-Key": "jobboerse-jobsuche", "User-Agent": "autoapp/0.1 (personal job search tool)"}
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "arbeitsagentur"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        search = client.get(f"{BASE}/pc/v6/jobs", params={"was": "Data Scientist", "wo": "Berlin", "size": 5})
        search.raise_for_status()
        data = search.json()
        (OUT / "search_data_scientist_berlin.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        jobs = data.get("ergebnisliste") or data.get("stellenangebote") or []
        if not jobs:
            print("no results; no detail recorded")
            return
        first = jobs[0]
        refnr = first.get("referenznummer") or first.get("refnr")
        encoded = base64.b64encode(refnr.encode()).decode()
        detail = client.get(f"{BASE}/pc/v4/jobdetails/{encoded}")
        detail.raise_for_status()
        (OUT / "detail.json").write_text(
            json.dumps(detail.json(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"recorded search ({len(jobs)} hits) and detail for {refnr}")


if __name__ == "__main__":
    main()
