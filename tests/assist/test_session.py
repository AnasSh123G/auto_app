from pathlib import Path
from types import SimpleNamespace

import pytest

from autoapp.assist.fields import FieldInfo
from autoapp.assist.filler import FillReport
from autoapp.assist.session import AssistSession, NotPrepared
from autoapp.llm.base import LLMError
from autoapp.llm.fake import FakeLLM
from autoapp.steps.context import RunContext
from autoapp.steps.prepare import prepare_jobs
from tests.helpers import make_workspace
from tests.steps.test_prepare_step import LETTER, FakeRenderer, approved_job, factory


class FakePage:
    def bring_to_front(self):
        pass

    def is_closed(self):
        return False

    def __init__(self, url="https://jobs.muster-gmbh.de/apply/1"):
        self.url = url
        self.visited = []
        self.frames = [self]

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = url

    def evaluate(self, *args):
        return None

    def content(self):
        return '<html><body><form>Application<input name="cv" type="file"></form></body></html>'


class FakeCDP:
    def send(self, *args):
        pass

    def on(self, *args):
        pass

    def detach(self):
        pass


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]

    def new_page(self):
        p = FakePage("about:blank")
        self.pages.append(p)
        return p

    def route(self, *args):
        pass

    def on(self, *args):
        pass

    def remove_listener(self, *args):
        pass

    def unroute(self, *args):
        pass

    def new_cdp_session(self, page):
        return FakeCDP()


@pytest.fixture
def ctx(tmp_path):
    c = RunContext.open(make_workspace(tmp_path))
    yield c
    c.close()


def prepared(ctx):
    jid = approved_job(ctx)
    ctx.repo.update_job(jid, application_url="https://jobs.muster-gmbh.de/apply/1")
    ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
    ctx.mode = "automatic"  # assist fills the form in a folder
    prepare_jobs(ctx, renderer_factory=factory(FakeRenderer([1])))
    return jid


def test_refuses_unprepared(ctx):
    jid = ctx.repo.insert_job(title="Dev", company="A")
    with pytest.raises(NotPrepared, match="autoapp prepare"):
        AssistSession(ctx, jid)


def test_legacy_arbeitsagentur_board_urls_are_not_used_as_assist_entry(ctx):
    jid = prepared(ctx)
    board = "https://www.arbeitsagentur.de/jobsuche/jobdetail/123"
    ctx.repo.update_job(jid, job_url=board, application_url=board)
    ctx.repo.upsert_application(jid, form_entry_url=board)
    sess = AssistSession(ctx, jid, say=lambda _: None, use_llm=False)
    assert sess.url is None

    employer = "https://jobs.muster-gmbh.de/apply/1"
    ctx.repo.upsert_application(jid, form_entry_url=employer)
    sess = AssistSession(ctx, jid, say=lambda _: None, use_llm=False)
    assert sess.url == employer


def test_workplace_fallback_does_not_ask_when_form_fill_configuration_fails(ctx, monkeypatch):
    jid = prepared(ctx)
    sess = AssistSession(ctx, jid, say=lambda _: None, use_llm=True, ask_questions=True)
    field = FieldInfo(field_id="work", frame_index=0, selector="#work", tag="select", type="select",
                      label="Preferred work location",
                      options=[("unknown", "Unknown City"), ("ber", "Berlin"), ("ess", "Essen")])
    monkeypatch.setattr("autoapp.assist.session.collect_fields", lambda *args, **kwargs: [field])

    def unavailable(*args, **kwargs):
        raise LLMError("form_fill is not configured")

    monkeypatch.setattr(sess, "_extra_assignments", unavailable)
    assignments, review, open_fields = sess._plan(FakePage())
    assert [(item.field_id, item.value) for item in assignments] == [("work", "ess")]
    assert review == ["Preferred work location: Essen (nearest locally recognized option; please review)"]
    assert open_fields == []


def test_fill_then_submitted_marks_status(ctx):
    jid = prepared(ctx)
    answers = iter(["f", "h", "q", "y"])
    said, fills = [], []

    def fake_fill(page, values, *, restricted_domains, extra_assignments=(), scope=None):
        fills.append((page.url, values.salary_expectation, values.cv))
        return FillReport(filled=["Vorname"], needs_user=["Datenschutz"], skipped_restricted=False, message="")

    sess = AssistSession(ctx, jid, ask=lambda q: next(answers), say=said.append, fill=fake_fill, use_llm=False)
    context = FakeContext()
    assert sess.run(context) is True
    assert context.pages[0].visited == ["https://jobs.muster-gmbh.de/apply/1"]
    assert fills[0][1] == "65000" and Path(fills[0][2]).name == "Lebenslauf_de.pdf"
    assert context.pages[1].visited[0].endswith("apply.html")
    assert any("Nothing was submitted" in s for s in said)
    assert ctx.repo.get_job(jid)["status"] == "submitted"


def test_quit_without_submitting_keeps_status(ctx):
    jid = prepared(ctx)
    answers = iter(["q", "n"])
    sess = AssistSession(ctx, jid, ask=lambda q: next(answers), say=lambda s: None,
                         fill=lambda *a, **k: None, use_llm=False)
    assert sess.run(FakeContext()) is False
    assert ctx.repo.get_job(jid)["status"] == "prepared"


def test_restricted_domain_points_to_apply_html(ctx):
    jid = prepared(ctx)
    said = []
    report = FillReport(filled=[], needs_user=[], skipped_restricted=True,
                        message="Filling is disabled on linkedin.com; use apply.html")
    sess = AssistSession(ctx, jid, ask=lambda q: "s", say=said.append, fill=lambda *a, **k: report, use_llm=False)
    sess.fill_current(SimpleNamespace(url="https://www.linkedin.com/jobs/view/1/"))
    assert any("apply.html" in s for s in said)


def test_a_renamed_folder_is_followed_before_filling(ctx):
    """The folder is renamed when the form status changes; the files to upload must come from the new place."""
    jid = prepared(ctx)
    sess = AssistSession(ctx, jid, say=lambda s: None, use_llm=False)
    old = sess.folder
    new = old.with_name(old.name + "_moved")
    old.rename(new)
    ctx.repo.upsert_application(jid, folder=str(new))
    sess.fill_current(FakePage("file:///x"))  # a local page: nothing is planned, only the values matter
    assert sess.folder == new and sess.values.cv is not None and sess.values.cv.parent == new


def test_opening_a_linkedin_job_reads_the_company_link_when_switched_on(ctx, monkeypatch):
    import autoapp.fetch.apply_links as apply_links
    from autoapp.fetch.apply_links import ApplyLink

    jid = prepared(ctx)
    ctx.repo.update_job(jid, application_url="https://www.linkedin.com/jobs/view/42/")
    said = []
    read = []

    class Reader:
        def __init__(self, context, **kw):
            pass

        def read(self, url):
            read.append(url)
            return ApplyLink("found", "https://jobs.muster-gmbh.de/apply/9", "apply link read from the LinkedIn page")

    monkeypatch.setattr(apply_links, "ApplyLinkReader", Reader)
    sess = AssistSession(ctx, jid, say=said.append, use_llm=False)
    sess._read_platform_link(FakeContext())
    assert read == []  # off by default: LinkedIn is not opened
    ctx.config.sources.apply_links_with_login.linkedin = True
    sess._read_platform_link(FakeContext())
    assert read == ["https://www.linkedin.com/jobs/view/42/"]
    assert sess.url == "https://jobs.muster-gmbh.de/apply/9"
    assert ctx.repo.get_job(jid)["application_url"] == "https://jobs.muster-gmbh.de/apply/9"
    assert ctx.repo.get_application(jid)["form_entry_url"] == "https://jobs.muster-gmbh.de/apply/9"
    assert any("with your login" in line for line in said)
