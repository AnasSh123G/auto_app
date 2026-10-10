"""Search-first dry run over the real sample alert emails in samples/ (LinkedIn, Indeed, StepStone).

Nothing may be requested from a job board: the job-board fetchers record every call, the employer web and the
search service are fakes, and the run is a dry run.
"""

from __future__ import annotations

import email
from email import policy
from pathlib import Path

from autoapp.llm.fake import FakeLLM
from autoapp.sources.alerts.imap_reader import FakeMailbox
from autoapp.steps.context import RunContext
from autoapp.steps.pipeline import run_pipeline
from tests.employers.identity_world import search_client
from tests.employers.webfake import FakeWeb
from tests.helpers import make_workspace
from tests.steps.test_evaluate_step import verdict
from tests.steps.test_other_steps import FakeResolver
from tests.steps.test_search_first_fetch import Browser, http_seen

SAMPLES = Path(__file__).parent.parent / "samples"


def mailbox() -> FakeMailbox:
    messages = []
    for n, path in enumerate(sorted(SAMPLES.glob("*.eml"))):
        messages.append((f"<sample-{n}>", email.message_from_bytes(path.read_bytes(), policy=policy.default)))
    return FakeMailbox(messages)


def test_the_sample_alerts_are_resolved_by_search_without_touching_a_job_board(tmp_path):
    assert len(list(SAMPLES.glob("*.eml"))) == 3
    ctx = RunContext.open(make_workspace(tmp_path, alert_mode="search_first"), dry_run=True)
    ctx.mode = "review"
    ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [verdict(20)] * 80})
    browser, seen = Browser(), []
    web = FakeWeb({})  # the employer web knows nothing: every ad stays unfound, which must be reported
    api, calls = search_client([])
    summary = run_pipeline(ctx, api_sources=[], mailbox=mailbox(), resolver=FakeResolver(), employer_fetch=web,
                           employer_api=api, http=http_seen(seen), browser=browser)
    assert browser.urls == [] and seen == []  # not one LinkedIn / Indeed / StepStone / Workwise page
    assert not any(host in url for url in web.calls for host in ("linkedin.com", "indeed.", "stepstone.de", "workwise.io"))
    for call in calls:  # only public hints go to the search service
        text = str(call)
        assert "@" not in text and "token" not in text.lower()
    text = "\n".join(summary.lines())
    assert "alert mode: search first" in text
    assert summary.alerts is not None and summary.alerts.searched > 0 and calls
    waiting = {**summary.alerts.not_found, **summary.alerts.deferred}
    assert waiting and all(reason for reason in waiting.values())
    states = {j["status"] for j in ctx.repo.jobs_by_status("description_unavailable", "new", "filtered")}
    assert "description_unavailable" in states
    ctx.close()
