"""Section behaviour with scripted answers and fake checks (no terminal, no network)."""

from __future__ import annotations

from typing import Any

from autoapp.config.io import load_yaml
from autoapp.config.models import load_config, load_profile
from autoapp.llm.cost import PriceTable
from autoapp.setup.checks import CheckResult, FakeChecks
from autoapp.setup.prompts import ENTER, ScriptedPrompter
from autoapp.setup.sections import cost_rows
from autoapp.setup.wizard import SetupContext, run_section
from tests.setup.conftest import (
    ANTHROPIC_KEY,
    documents_answers,
    email_enter_answers,
    llm_answers,
    profile_answers,
    search_answers,
    sources_answers,
)


def run(ws, name: str, answers: list[Any], checks: FakeChecks | None = None):
    prompter = ScriptedPrompter(answers)
    ctx = SetupContext(ws=ws, prompter=prompter, checks=checks or FakeChecks())
    assert run_section(ctx, name)
    assert prompter.remaining == 0, f"unused answers: {list(prompter._queue)}"
    return prompter


# ------------------------------------------------------------------------ documents


def test_documents_proposal_can_be_edited(ws):
    answers = [
        ("Use this mapping", False),
        ("Role of 'Anschreiben_alt.pdf'", "other"),
        ("Role of 'Arbeitszeugnis.pdf'", "ignore"),
        ("Role of 'Foto.jpg'", ENTER),  # proposal: photo
        ("Role of 'Lebenslauf_de.pdf'", "cv"),
        ("Language of 'Lebenslauf_de.pdf'", "en"),
    ]
    run(ws, "documents", answers)
    docs = {d["file"]: d for d in load_yaml(ws.profile_path)["documents"]}
    assert set(docs) == {"Anschreiben_alt.pdf", "Foto.jpg", "Lebenslauf_de.pdf"}
    assert docs["Foto.jpg"]["role"] == "photo"
    assert docs["Lebenslauf_de.pdf"]["language"] == "en"


def test_documents_requires_a_cv(ws):
    (ws.documents_dir / "Lebenslauf_de.pdf").rename(ws.documents_dir / "notes.pdf")
    answers = [
        ("Role of 'Anschreiben_alt.pdf'", "other"),
        ("Role of 'Arbeitszeugnis.pdf'", "certificate"),
        ("Language of 'Arbeitszeugnis.pdf'", ENTER),
        ("Role of 'Foto.jpg'", "photo"),
        ("Role of 'notes.pdf'", "other"),  # still no CV: asked again
        ("Role of 'Anschreiben_alt.pdf'", "other"),
        ("Role of 'Arbeitszeugnis.pdf'", "certificate"),
        ("Language of 'Arbeitszeugnis.pdf'", ENTER),
        ("Role of 'Foto.jpg'", "photo"),
        ("Role of 'notes.pdf'", "cv"),
        ("Language of 'notes.pdf'", ENTER),
    ]
    p = run(ws, "documents", answers)
    assert any("role 'cv'" in line for line in p.output)


def test_documents_empty_folder_can_be_left(ws):
    for f in ws.documents_dir.iterdir():
        f.unlink()
    prompter = ScriptedPrompter([("Check the folder again", False)])
    ctx = SetupContext(ws=ws, prompter=prompter, checks=FakeChecks())
    assert run_section(ctx, "documents") is False
    assert not ws.profile_path.exists()
    assert "documents" not in (load_yaml(ws.config_path).get("setup") or {}).get("completed_sections", [])


# ----------------------------------------------------------------------- profile/salary


def test_salary_skipped_stores_nothing(ws):
    run(ws, "documents", documents_answers())
    run(ws, "profile", profile_answers())
    assert "salary" not in load_yaml(ws.profile_path)
    run(ws, "salary", [("store salary", False)])
    prof = load_profile(ws)
    assert prof.salary is None
    assert "salary" not in load_yaml(ws.profile_path)
    assert "salary" in load_yaml(ws.config_path)["setup"]["completed_sections"]


def test_salary_can_be_added_then_removed_and_overrides(ws):
    run(ws, "profile", profile_answers())
    answers = [
        ("store salary", True), ("Target salary", "abc"), ("Target salary", "70000"),
        ("Minimum acceptable", "80000"), ("Minimum acceptable", "60000"),
        ("per seniority", True),
        ("Target for junior", "55000"), ("Minimum for junior", ENTER),
        ("Target for mid", ENTER), ("Target for senior", "85000"), ("Minimum for senior", "75000"),
        ("Target for lead", ENTER),
        ("different amount for jobs", True), ("Federal state (", "bayern"), ("Target for jobs in bayern", "56000"),
        ("Minimum for jobs in bayern", ENTER), ("Federal state (", "done"),
    ]
    run(ws, "salary", answers)
    prof = load_profile(ws)
    assert prof.salary is not None
    assert prof.salary.region_overrides["bayern"].target_eur == 56000 and prof.salary.target_eur == 70000
    assert prof.salary.overrides["senior"].min_eur == 75000
    assert prof.salary.overrides["junior"].target_eur == 55000
    run(ws, "salary", [("store salary", ENTER), ("Target salary", ENTER), ("Minimum", ENTER), ("per seniority", ENTER),
                      ("Target for junior", ENTER), ("Minimum for junior", ENTER), ("Target for mid", ENTER),
                      ("Target for senior", ENTER), ("Minimum for senior", ENTER), ("Target for lead", ENTER),
                      ("different amount for jobs", ENTER), ("Federal state (", ENTER),
                      ("Target for jobs in", ENTER), ("Minimum for jobs in", ENTER), ("Federal state (", ENTER)])
    assert load_profile(ws).salary.target_eur == 70000  # Enter keeps the current values
    assert load_profile(ws).salary.region_overrides["bayern"].target_eur == 56000


def test_profile_validation_reasons_and_extra_languages(ws):
    answers = profile_answers()
    i = next(n for n, a in enumerate(answers) if a[0] == "Add another language")
    answers[i : i + 1] = [("Add another language", True), ("Language code", "fr"), ("Level in", "A2"),
                          ("Add another language", False)]
    p = run(ws, "profile", answers)
    assert "! 'not-an-email' is not a valid email address" in p.output
    assert load_profile(ws).language_levels == {"de": "C1", "en": "B2", "fr": "A2"}


# --------------------------------------------------------------------------- search


def test_invalid_radius_is_explained_and_reasked(ws):
    p = run(ws, "search", search_answers())
    reasons = [line for line in p.output if line.startswith("!")]
    assert reasons == ["! The radius must be a whole number of kilometres, or empty; 'abc' is not."]
    [sp] = load_config(ws).search_profiles
    assert sp.locations[0].radius_km == 30
    assert sp.keywords == ["Machine Learning Engineer", "Data Scientist"]  # earlier answers were not lost
    radius_questions = [m for k, m in p.asked if k == "text" and "radius" in m.lower()]
    assert len(radius_questions) == 2


def test_search_needs_only_keywords_and_skips_filters(ws):
    answers = [("keywords", ""), ("keywords", "Python"), ("Name for this search", ENTER), ("optional filters", False),
               ("Add another search profile", False)]
    p = run(ws, "search", answers)
    assert any("at least one keyword" in line for line in p.output)
    assert load_yaml(ws.config_path)["search_profiles"] == [{"name": "python", "keywords": ["Python"]}]
    load_config(ws)


def test_search_edit_keep_delete_and_add(ws):
    ws.config_path.write_text(
        "search_profiles:\n"
        "  - name: first\n    keywords: [A]\n"
        "  - name: second  # my favourite\n    keywords: [B]\n    work_mode: remote\n"
        "  - name: third\n    keywords: [C]\n"
    )
    answers = [
        ("'first'", "keep"),
        ("'second'", "edit"), ("keywords", ENTER), ("Name for this search", ENTER), ("optional filters", ENTER),
        ("Exclude titles", "Intern"), ("Exclude companies", ENTER), ("Locations", ENTER),
        ("Work mode", ENTER), ("Working time", ENTER), ("Contract type", ENTER), ("Exclude position types", ENTER),
        ("Experience levels", ENTER), ("within this many days", ENTER), ("Minimum yearly", ENTER),
        ("recruitment agencies", ENTER),
        ("'third'", "delete"),
        ("Add another search profile", True),
        ("keywords", "First"),  # name 'first' is taken, the default slug collides
        ("Name for this search", "first"), ("Name for this search", "fourth"),
        ("optional filters", False),
        ("Add another search profile", False),
    ]
    run(ws, "search", answers)
    profs = {p.name: p for p in load_config(ws).search_profiles}
    assert list(profs) == ["first", "second", "fourth"]
    assert profs["second"].work_mode == "remote" and profs["second"].exclude_keywords == ["Intern"]


# -------------------------------------------------------------------------- sources


def test_arbeitnow_opt_in_and_redo_preserve_other_sources(ws):
    from autoapp.setup.wizard import write_key

    write_key(ws.config_path, "sources", {
        "arbeitnow": {"enabled": False, "max_pages_per_run": 2},
        "employer_careers": {"enabled": False, "scan_ttl_hours": 12},
        "adzuna": {"enabled": False, "max_requests_per_run": 5},
    })
    answers = [("Use Adzuna", False), ("job-alert emails", False), ("LinkedIn", ENTER),
               ("Indeed", ENTER), ("StepStone", ENTER), ("careers pages", ENTER),
               ("company apply links", ENTER), ("Use Arbeitnow", True), ("Use Workwise", ENTER),
               ("Test the connections", False)]
    p = run(ws, "sources", answers)
    cfg = load_config(ws)
    assert cfg.sources.arbeitnow.enabled and cfg.sources.arbeitnow.max_pages_per_run == 2
    assert cfg.sources.adzuna.max_requests_per_run == 5
    assert cfg.sources.employer_careers.scan_ttl_hours == 12
    assert not any(kind == "password" for kind, _ in p.asked)
    answers[-3] = ("Use Arbeitnow", ENTER)
    run(ws, "sources", answers)
    assert load_config(ws).sources.arbeitnow.enabled
    answers[-3] = ("Use Arbeitnow", False)
    run(ws, "sources", answers)
    assert not load_config(ws).sources.arbeitnow.enabled


def test_outlook_address_is_not_supported_and_reasked(ws):
    answers = sources_answers(test=False)
    answers[5:6] = [("Email address", "me@outlook.com"), ("Email address", "me@gmail.com")]
    p = run(ws, "sources", answers)
    assert any("not supported yet" in line for line in p.output)
    assert load_config(ws).mail.alerts.address == "me@gmail.com"


def test_gmail_guidance_precedes_hidden_password_prompt(ws):
    p = run(ws, "sources", sources_answers(test=False))
    text = "\n".join(p.output)
    assert "2-Step Verification" in text and "app password" in text
    kinds = [k for k, _ in p.asked]
    assert "password" in kinds
    assert ANTHROPIC_KEY not in p.shown


def test_employer_careers_opt_in_stores_key_in_env_only(ws):
    key = "pk-test-0123456789abcdef"
    answers = [("Use Adzuna", False), ("job-alert emails", False), ("LinkedIn job pages", ENTER),
               ("Indeed job pages", ENTER), ("StepStone job pages", ENTER), ("careers pages", True),
               ("Parallel Search API key", key),
               ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER), ("Test the connections", False)]
    p = run(ws, "sources", answers)
    cfg = load_config(ws)
    assert cfg.sources.employer_careers.enabled is True
    assert f"PARALLEL_API_KEY='{key}'" in ws.env_path.read_text()
    assert key not in ws.config_path.read_text() and key not in p.shown


def test_employer_careers_stays_off_without_opt_in(ws):
    answers = [("Use Adzuna", False), ("job-alert emails", False), ("LinkedIn job pages", ENTER),
               ("Indeed job pages", ENTER), ("StepStone job pages", ENTER), ("careers pages", False),
               ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER), ("Test the connections", False)]
    run(ws, "sources", answers)
    assert load_config(ws).sources.employer_careers.enabled is False


def test_adzuna_off_and_alerts_off(ws):
    answers = [("Use Adzuna", False), ("job-alert emails", False), ("LinkedIn job pages", False),
               ("Indeed job pages", ENTER), ("StepStone job pages", ENTER), ("careers pages", ENTER),
               ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER), ("Test the connections", False)]
    run(ws, "sources", answers)
    cfg = load_config(ws)
    assert not cfg.sources.adzuna.enabled and not cfg.sources.alerts.enabled
    assert cfg.detail_fetch.linkedin is False and cfg.mail.alerts is None


def test_proton_fingerprint_is_stored_only_after_confirmation(ws):
    checks = FakeChecks(fingerprint="AB:CD" + ":EF" * 30)
    answers = [("Use Adzuna", False), ("job-alert emails", True), ("Mail provider", "proton_bridge"),
               ("Email address", "me@proton.me"), ("Bridge username", ENTER), ("Bridge password", "bridge-pw"),
               ("Pin this certificate", False), ("Folder or label", "INBOX"), ("alert jobs get their full ad", ENTER), ("AI read", ENTER), ("LinkedIn", ENTER), ("Indeed", ENTER),
               ("StepStone", ENTER), ("careers pages", ENTER), ("Parallel Search API key", ENTER),
               ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER), ("Test the connections", False)]
    p = run(ws, "sources", answers, checks)
    assert load_config(ws).mail.alerts.bridge_cert_sha256 is None
    assert any("paid Proton plan" in line for line in p.output)
    assert any("AB:CD:EF" in line for line in p.output)  # fingerprint shown for confirmation
    answers[6] = ("Pin this certificate", True)
    run(ws, "sources", answers, checks)
    assert load_config(ws).mail.alerts.bridge_cert_sha256 == ("abcd" + "ef" * 30)
    assert ("bridge_fingerprint", "127.0.0.1", 1143) in checks.calls


def test_proton_bridge_not_running_can_be_skipped(ws):
    checks = FakeChecks(fingerprint=OSError("connection refused"))
    answers = [("Use Adzuna", False), ("job-alert emails", True), ("Mail provider", "proton_bridge"),
               ("Email address", "me@proton.me"), ("Bridge username", ENTER), ("Bridge password", "pw"),
               ("Try again", False), ("Folder or label", ENTER), ("alert jobs get their full ad", ENTER), ("AI read", ENTER), ("LinkedIn", ENTER), ("Indeed", ENTER),
               ("StepStone", ENTER), ("careers pages", ENTER), ("Parallel Search API key", ENTER),
               ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER), ("Test the connections", False)]
    p = run(ws, "sources", answers, checks)
    assert any("Could not read the Bridge certificate" in line for line in p.output)
    assert load_config(ws).mail.alerts.bridge_cert_sha256 is None


def test_custom_preset_asks_for_hosts(ws):
    answers = [("Use Adzuna", False), ("job-alert emails", True), ("Mail provider", "custom"),
               ("Email address", "me@example.org"), ("IMAP host", "imap.example.org"), ("IMAP encryption", "starttls"),
               ("IMAP port", ENTER), ("Login name", "me"), ("Mail password", "pw"), ("Folder or label", ENTER),
               ("alert jobs get their full ad", ENTER), ("AI read", ENTER), ("LinkedIn", ENTER), ("Indeed", ENTER), ("StepStone", ENTER), ("careers pages", ENTER), ("Parallel Search API key", ENTER),
               ("company apply links", ENTER), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER), ("Test the connections", False)]
    run(ws, "sources", answers)
    acct = load_config(ws).mail.alerts
    assert (acct.imap_host, acct.imap_port, acct.imap_tls, acct.username) == ("imap.example.org", 143, "starttls", "me")


def test_imap_auth_failure_offers_reentering_or_continuing(ws):
    auth_fail = CheckResult(False, "IMAP login failed: [AUTHENTICATIONFAILED] Invalid credentials")
    checks = FakeChecks(imap=[auth_fail, CheckResult(True, "ok")])
    answers = sources_answers()
    answers += [
        ("What now", "retry"),
        ("Mail provider", ENTER), ("Email address", ENTER), ("app password", "new-app-password-1234"),
    ]
    p = run(ws, "sources", answers, checks)
    assert any("IMAP login failed" in line for line in p.output)
    imap_calls = [c for c in checks.calls if c[0] == "imap"]
    assert len(imap_calls) == 2
    assert imap_calls[0][3] == "abcdefghijklmnop" and imap_calls[1][3] == "new-app-password-1234"
    assert "new-app-password-1234" in ws.env_path.read_text()
    assert "new-app-password-1234" not in p.shown


def test_imap_failure_can_be_ignored(ws):
    checks = FakeChecks(imap=CheckResult(False, "IMAP login failed: nope"), arbeitsagentur=CheckResult(False, "offline"))
    answers = sources_answers() + []
    answers[-1:] = [("Test the connections", True), ("What now", "continue"), ("What now", "continue")]
    p = run(ws, "sources", answers, checks)
    assert any("FAILED: Arbeitsagentur" in line for line in p.output)
    assert load_config(ws).mail.alerts is not None  # the answers are kept despite the failed checks


# ----------------------------------------------------------------------------- llm


def test_llm_shows_cost_table_with_current_prices(ws):
    p = run(ws, "llm", llm_answers())
    text = "\n".join(p.output)
    assert "Estimated LLM cost" in text
    assert "claude-sonnet-5-5 | 2 / 10 / 0.2 | $0.019 | $0.060 | $0.95" in text
    assert "openai/gpt-6-luna | 0.1 / 0.5 / 0.01 | $0.001 | $0.003 | $0.05" in text


def test_cost_rows_follow_user_price_overrides():
    from autoapp.llm.settings import Price

    table = PriceTable.load({"my-model": Price(input=1.0, output=0.0, cached_input=1.0)})
    row = next(r for r in cost_rows(table.prices) if r[0] == "my-model")
    assert row[2:] == ["$0.005", "$0.015", "$0.25"]  # 5000 and 10200 input tokens; 40 evaluated, 5 prepared


def test_llm_per_task_models_and_key_check(ws):
    answers = [
        ("Provider for evaluate", "openrouter"), ("Model for evaluate", ENTER),
        ("Provider for write", "anthropic"), ("Model for write", ENTER),
        ("Provider for form_fill", ENTER), ("Model for form_fill", ENTER),  # offered from the answer before
        ("Provider for form_find", ENTER), ("Model for form_find", ENTER),
        ("OpenRouter API key", "sk-or-SECRET"), ("Anthropic API key", ANTHROPIC_KEY),
        ("Test the API key", True),
    ]
    checks = FakeChecks()
    p = run(ws, "llm", answers, checks)
    cfg = load_config(ws)
    assert cfg.llm.resolve("evaluate")[:2] == ("openrouter", "deepseek/deepseek-v4.1-flash")
    assert cfg.llm.resolve("write")[:2] == ("anthropic", "claude-sonnet-5-5")
    assert [c[:2] for c in checks.calls] == [("llm", "evaluate"), ("llm", "write")]
    env = ws.env_path.read_text()
    assert "OPENROUTER_API_KEY='sk-or-SECRET'" in env and "sk-or-SECRET" not in p.shown


def test_llm_asks_every_task_and_prefills_from_the_config_file_else_the_previous_answer(ws):
    from autoapp.setup.wizard import write_key

    write_key(ws.config_path, "llm", {"tasks": {"write": {"provider": "openrouter", "model": "anthropic/claude-haiku-5.5"}}})
    answers = [
        ("Provider for evaluate", "openrouter"), ("Model for evaluate", "openai/gpt-6-luna"),
        ("Provider for write", ENTER), ("Model for write", ENTER),  # already in config.yaml: that value is offered
        ("Provider for form_fill", ENTER), ("Model for form_fill", ENTER),  # not saved: the write answer is offered
        ("Provider for form_find", ENTER), ("Model for form_find", "openai/gpt-6-luna"),
        ("OpenRouter API key", "sk-or-X"), ("Test the API key", False),
    ]
    p = run(ws, "llm", answers)
    raw = load_yaml(ws.config_path)["llm"]
    assert set(raw) == {"tasks"} and set(raw["tasks"]) == {"evaluate", "write", "form_fill", "form_find"}  # no single default
    assert {t: raw["tasks"][t]["model"] for t in raw["tasks"]} == {
        "evaluate": "openai/gpt-6-luna", "write": "anthropic/claude-haiku-5.5",
        "form_fill": "anthropic/claude-haiku-5.5", "form_find": "openai/gpt-6-luna"}
    assert not any("Use a different provider" in m for _, m in p.asked)  # there is no "one model for everything" choice


def test_llm_key_failure_offers_reentering_key(ws):
    checks = FakeChecks(llm=[CheckResult(False, "the provider rejected the API key"), CheckResult(True, "ok")])
    answers = llm_answers() + [("What now", "retry"), ("Anthropic API key", "sk-ant-NEW")]
    p = run(ws, "llm", answers, checks)
    assert "ANTHROPIC_API_KEY='sk-ant-NEW'" in ws.env_path.read_text()
    assert any("rejected the API key" in line for line in p.output)


def test_llm_enter_keeps_saved_key(ws):
    run(ws, "llm", llm_answers())
    answers = llm_answers()
    answers[8] = ("Anthropic API key", ENTER)
    run(ws, "llm", answers)
    assert ANTHROPIC_KEY in ws.env_path.read_text()


# ---------------------------------------------------------------------------- email


def _configure_alerts(ws):
    run(ws, "sources", sources_answers(test=False))


def test_enter_through_email_defaults_to_review_and_offers_reuse(ws):
    _configure_alerts(ws)
    p = run(ws, "email", email_enter_answers())
    assert load_config(ws).run.default_mode == "review"
    assert any("same account (me@gmail.com)" in m for k, m in p.asked)
    assert load_config(ws).mail.sending.address == "me@gmail.com"


def test_email_explains_safeguards_and_modes_before_choosing_automatic(ws):
    _configure_alerts(ws)
    answers = [("sending mailbox", True), ("same account", True), ("Adjust these limits", True),
               ("Maximum emails per day", "3"), ("Minimum score", "90"), ("Days before", "21"),
               ("Default run mode", "automatic"), ("Test the mail login", False)]
    p = run(ws, "email", answers)
    cfg = load_config(ws)
    assert cfg.run.default_mode == "automatic" and cfg.email.daily_cap == 3 and cfg.email.min_score == 90
    assert cfg.email.company_cooldown_days == 21
    out = p.output
    joined = "\n".join(out)
    for phrase in ("at most 100 emails per day", "scored 80 or higher", "free-mail", "within 14 days",
                   "at most 3 emails per day", "scored 90 or higher", "within 21 days"):
        assert phrase in joined
    assert any("dry run" in line for line in out)
    assert any("Review first" in line for line in out) and any("Full automatic" in line for line in out)
    assert "auto_send" not in ws.config_path.read_text()


def test_email_separate_gmail_account_shows_app_password_guidance(ws):
    _configure_alerts(ws)
    answers = [("sending mailbox", True), ("same account", False), ("Mail provider", "gmail"),
               ("Email address", "sender@gmail.com"), ("app password", "send-secret-pw"),
               ("Adjust these limits", False), ("Default run mode", ENTER), ("Test the mail login", True)]
    checks = FakeChecks()
    p = run(ws, "email", answers, checks)
    assert any("2-Step Verification" in line for line in p.output)
    acct = load_config(ws).mail.sending
    assert acct.address == "sender@gmail.com" and acct.password_env == "AUTOAPP_SMTP_PASSWORD"
    assert "AUTOAPP_SMTP_PASSWORD='send-secret-pw'" in ws.env_path.read_text()
    assert ("smtp", "sender@gmail.com", "send-secret-pw") in checks.calls
    assert "send-secret-pw" not in p.shown


def test_email_without_mailbox_defaults_to_review(ws):
    p = run(ws, "email", [("sending mailbox", ENTER)])  # nothing configured, so the default is "no"
    assert load_config(ws).run.default_mode == "review"
    assert any("Skipped" in line for line in p.output)


def test_email_smtp_failure_can_continue(ws):
    _configure_alerts(ws)
    checks = FakeChecks(smtp=CheckResult(False, "SMTP login failed: bad password"))
    answers = email_enter_answers() + [("What now", "continue")]
    p = run(ws, "email", answers, checks)
    assert any("SMTP login failed" in line for line in p.output)
    assert load_config(ws).run.default_mode == "review"


# --------------------------------------------------------------------------- writing


def test_writing_inline_editor_and_samples(ws):
    run(ws, "documents", documents_answers())
    answers = [("Writing instructions", "editor"), ("edit", "# My rules\nShort letters.\n"),
               ("Letter language", "de"), ("writing samples", [])]
    run(ws, "writing", answers)
    assert ws.writing_instructions_path.read_text() == "# My rules\nShort letters.\n"
    assert load_config(ws).writing.language == "de"
    roles = {d.file: d.role for d in load_profile_documents(ws)}
    assert roles["Anschreiben_alt.pdf"] == "other" and roles["Lebenslauf_de.pdf"] == "cv"
    run(ws, "writing", [("Writing instructions", ENTER), ("Letter language", ENTER),
                        ("writing samples", ["Anschreiben_alt.pdf"])])
    assert {d.file: d.role for d in load_profile_documents(ws)}["Anschreiben_alt.pdf"] == "writing_sample"
    # Enter keeps the current text
    run(ws, "writing", [("Writing instructions", ENTER), ("Letter language", ENTER), ("writing samples", ENTER)])
    assert ws.writing_instructions_path.read_text() == "# My rules\nShort letters.\n"


def test_writing_editor_failure_falls_back_to_inline(ws):
    class NoEditor(ScriptedPrompter):
        def edit_file(self, path):
            raise RuntimeError("no editor found")

    prompter = NoEditor([("Writing instructions", "editor"), ("Writing instructions", "typed instead"),
                         ("Letter language", ENTER)])
    ctx = SetupContext(ws=ws, prompter=prompter, checks=FakeChecks())
    assert run_section(ctx, "writing")
    assert ws.writing_instructions_path.read_text() == "typed instead\n"


def load_profile_documents(ws):
    from autoapp.config.models import DocumentEntry

    return [DocumentEntry.model_validate(d) for d in load_yaml(ws.profile_path)["documents"]]


def test_old_auto_send_key_is_replaced_when_the_email_section_is_saved(ws):
    _configure_alerts(ws)
    text = ws.config_path.read_text()
    ws.config_path.write_text(text + "email:\n  auto_send: true\n")
    run(ws, "email", email_enter_answers())
    saved = ws.config_path.read_text()
    assert "auto_send" not in saved and "default_mode: automatic" in saved  # the old value is the default


def test_company_apply_links_with_login_and_workwise_are_chosen_in_settings(ws):
    base = [("Use Adzuna", False), ("job-alert emails", False), ("LinkedIn job pages", ENTER),
            ("Indeed job pages", ENTER), ("StepStone job pages", ENTER), ("careers pages", ENTER)]
    p = run(ws, "sources", [*base, ("company apply links", "login"), ("with your login on", ["linkedin", "indeed"]),
                            ("Use Arbeitnow", ENTER), ("Use Workwise", False), ("Test the connections", False)])
    links = load_config(ws).sources.apply_links_with_login
    assert (links.linkedin, links.indeed, links.stepstone) == (True, True, False)
    assert not load_config(ws).sources.workwise.enabled
    assert any("autoapp login linkedin" in line for line in p.output)
    assert any("against these sites' terms" in line for line in p.output)
    # the default stays the employer's own site, and choosing it again switches the login reading off
    run(ws, "sources", [*base, ("company apply links", "employer"), ("Use Arbeitnow", ENTER), ("Use Workwise", ENTER),
                        ("Test the connections", False)])
    links = load_config(ws).sources.apply_links_with_login
    assert not (links.linkedin or links.indeed or links.stepstone)
    assert load_config(ws).sources.workwise.enabled is False  # Enter keeps the earlier choice


def test_defaults_are_the_employer_site_and_workwise_on():
    from autoapp.config.models import Sources

    s = Sources()
    assert s.workwise.enabled and not (s.apply_links_with_login.linkedin or s.apply_links_with_login.indeed
                                       or s.apply_links_with_login.stepstone)


def test_alert_mode_is_asked_stored_and_search_first_needs_the_key(ws):
    key = "pk-test-0123456789abcdef"
    answers = sources_answers(test=False)
    answers[answers.index(("alert jobs get their full ad", ENTER))] = ("alert jobs get their full ad", "search_first")
    answers[answers.index(("Parallel Search API key", ENTER))] = ("Parallel Search API key", key)
    p = run(ws, "sources", answers)
    assert load_config(ws).sources.alerts.mode == "search_first"
    assert f"PARALLEL_API_KEY='{key}'" in ws.env_path.read_text() and key not in p.shown
    assert any("needed for search-first alert jobs" in q for _, q in p.asked)


def test_platform_alert_mode_is_stored_and_asks_for_no_search_key(ws):
    answers = sources_answers(test=False)
    answers[answers.index(("alert jobs get their full ad", ENTER))] = ("alert jobs get their full ad", "platform")
    answers.remove(("Parallel Search API key", ENTER))
    p = run(ws, "sources", answers)
    assert load_config(ws).sources.alerts.mode == "platform"
    assert not any("Parallel Search API key" in q for _, q in p.asked)
