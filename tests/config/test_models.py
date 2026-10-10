from __future__ import annotations

from pathlib import Path

import pytest

from autoapp.config.models import ConfigError, load_config, load_profile
from autoapp.config.workspace import Workspace
from autoapp.salary import resolve_salary_target


def ws_with(tmp_path: Path, config: str | None = None, profile: str | None = None) -> Workspace:
    ws = Workspace(root=tmp_path)
    if config is not None:
        ws.config_path.write_text(config, encoding="utf-8")
    if profile is not None:
        ws.profile_path.write_text(profile, encoding="utf-8")
    return ws


MIN_CONFIG = "search_profiles:\n  - name: dev\n    keywords: [python]\n"

PERSONAL = """\
personal:
  first_name: Max
  last_name: Mustermann
  email: max.mustermann@example.com
  phone: "+49 170 1234567"
  street: Musterstr. 1
  postal_code: "10115"
  city: Berlin
"""


def test_minimal_config_gets_defaults(tmp_path: Path) -> None:
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG))
    p = cfg.search_profiles[0]
    assert p.keywords == ["python"]
    assert p.locations is None and p.work_mode is None and p.min_salary is None
    assert cfg.limits.max_llm_calls_per_run == 500
    assert cfg.limits.max_cost_per_run_usd == 3.0
    assert cfg.evaluation.apply_threshold == 75
    assert cfg.run.default_mode == "review" and cfg.email.daily_cap == 100 and cfg.email.min_score == 80
    assert cfg.writing.max_words == 300 and cfg.writing.language == "auto"
    assert cfg.tracking.delimiter == ";"
    assert "linkedin.com" in cfg.assist.restricted_domains
    assert cfg.sources.alerts.folder == "Jobalerts"
    assert cfg.detail_fetch.max_per_site == 20
    assert cfg.setup.version == 1 and cfg.setup.completed_sections == []
    assert {cfg.llm.resolve(t)[0] for t in ("evaluate", "write", "form_fill", "form_find")} == {"anthropic"}


def test_empty_config_is_valid(tmp_path: Path) -> None:
    assert load_config(ws_with(tmp_path, "")).search_profiles == []


def test_arbeitnow_defaults_and_enablement(tmp_path: Path) -> None:
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG))
    assert not cfg.sources.arbeitnow.enabled
    ar = load_config(ws_with(tmp_path, MIN_CONFIG + "sources:\n  arbeitnow:\n    enabled: true\n")).sources.arbeitnow
    assert ar.enabled and ar.max_pages_per_run == ar.max_requests_per_run == 40
    assert ar.max_requests_per_day == 200 and ar.max_requests_per_month == 1000 and ar.min_delay_s == 3


@pytest.mark.parametrize("setting", [
    "max_pages_per_run: 0", "max_requests_per_run: 0", "min_delay_s: 0.5",
    "max_requests_per_run: 201", "max_requests_per_month: 29",
])
def test_invalid_arbeitnow_config(tmp_path: Path, setting: str) -> None:
    with pytest.raises(ConfigError, match="arbeitnow"):
        load_config(ws_with(tmp_path, MIN_CONFIG + "sources:\n  arbeitnow:\n    " + setting + "\n"))


def test_profile_without_keywords(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as ei:
        load_config(ws_with(tmp_path, "search_profiles:\n  - name: dev\n"))
    assert len(ei.value.errors) == 1
    line = ei.value.errors[0]
    assert line.startswith("config.yaml: search_profiles[0].keywords:")
    assert "at least one keyword" in line


def test_profile_with_empty_keywords(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as ei:
        load_config(ws_with(tmp_path, "search_profiles:\n  - name: dev\n    keywords: []\n"))
    assert "at least one keyword" in ei.value.errors[0]


def test_multiple_errors_reported_at_once(tmp_path: Path) -> None:
    text = """\
search_profiles:
  - name: a
  - name: b
    keywords: [x]
    work_mode: sometimes
limits:
  max_llm_calls_per_run: -1
evaluation:
  apply_threshold: 50
  maybe_threshold: 60
bogus: 1
"""
    with pytest.raises(ConfigError) as ei:
        load_config(ws_with(tmp_path, text))
    joined = "\n".join(ei.value.errors)
    assert len(ei.value.errors) >= 5
    assert "search_profiles[0].keywords" in joined
    assert "search_profiles[1].work_mode" in joined
    assert "limits.max_llm_calls_per_run" in joined
    assert "apply_threshold must be >= maybe_threshold" in joined
    assert "config.yaml: bogus: Extra inputs are not permitted" in joined


def test_duplicate_profile_names(tmp_path: Path) -> None:
    text = "search_profiles:\n  - {name: a, keywords: [x]}\n  - {name: A, keywords: [y]}\n"
    with pytest.raises(ConfigError, match="duplicate profile name"):
        load_config(ws_with(tmp_path, text))


def test_outlook_rejected(tmp_path: Path) -> None:
    text = MIN_CONFIG + "mail:\n  sending:\n    preset: gmail\n    address: me@outlook.com\n"
    with pytest.raises(ConfigError, match="Outlook.com is not supported yet"):
        load_config(ws_with(tmp_path, text))


def test_mail_defaults_and_presets(tmp_path: Path) -> None:
    text = (
        MIN_CONFIG
        + "mail:\n  alerts: {preset: gmail, address: me@gmail.com}\n"
        + "  sending: {preset: proton_bridge, address: me@proton.me, imap_port: 2143}\n"
    )
    cfg = load_config(ws_with(tmp_path, text))
    assert cfg.mail.alerts and cfg.mail.alerts.password_env == "AUTOAPP_ALERTS_PASSWORD"
    assert cfg.mail.sending and cfg.mail.sending.password_env == "AUTOAPP_SMTP_PASSWORD"
    assert cfg.mail.sending.imap_port == 2143


def test_llm_section_uses_llm_settings(tmp_path: Path) -> None:
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG + "llm:\n  provider: google\n  model: gemini-x\n"))
    assert cfg.llm.resolve("evaluate")[:2] == ("google", "gemini-x")
    assert any("every task has its own model" in n and "llm.tasks" in n for n in cfg.notices)  # old single default


def test_llm_with_one_model_per_task_has_no_notice(tmp_path: Path) -> None:
    tasks = "".join(f"    {t}: {{provider: openrouter, model: m/{t}}}\n" for t in ("evaluate", "write", "form_fill", "form_find"))
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG + "llm:\n  tasks:\n" + tasks))
    assert cfg.llm.resolve("form_fill")[:2] == ("openrouter", "m/form_fill")
    assert not any("every task has its own model" in n for n in cfg.notices)


def test_missing_and_invalid_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file not found"):
        load_config(ws_with(tmp_path))
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(ws_with(tmp_path, "a: [unclosed\n"))


# ------------------------------------------------------------------ profile


def test_invalid_email(tmp_path: Path) -> None:
    text = PERSONAL.replace("max.mustermann@example.com", "max.mustermann@")
    with pytest.raises(ConfigError) as ei:
        load_profile(ws_with(tmp_path, profile=text))
    assert ei.value.errors[0].startswith("profile.yaml: personal.email:")
    assert "valid email" in ei.value.errors[0]


def test_profile_without_salary(tmp_path: Path) -> None:
    p = load_profile(ws_with(tmp_path, profile=PERSONAL))
    assert p.salary is None
    assert p.personal.country == "Deutschland"
    assert p.dealbreakers.no_relocation is False
    assert resolve_salary_target(p, "senior") is None


def test_extra_skills_and_rest(tmp_path: Path) -> None:
    text = (
        PERSONAL
        + """\
extra_skills:
  - {name: Kubernetes, level: good, context: "side project"}
  - {name: Rust, level: basic}
language_levels: {Deutsch: native, English: c1}
dealbreakers:
  max_german_level_required: B2
  excluded_industries: [gambling]
documents:
  - {file: Lebenslauf_de.pdf, role: cv, language: de}
salary:
  target_eur: 60000
  overrides:
    senior: {target_eur: 75000}
"""
    )
    p = load_profile(ws_with(tmp_path, profile=text))
    assert [(s.name, s.level, s.context) for s in p.extra_skills] == [
        ("Kubernetes", "good", "side project"),
        ("Rust", "basic", None),
    ]
    assert p.language_levels == {"Deutsch": "native", "English": "C1"}
    assert p.dealbreakers.max_german_level_required == "B2"
    assert p.documents[0].role == "cv"
    t = resolve_salary_target(p, "senior")
    assert t is not None and t.target_eur == 75000


def test_invalid_skill_level_and_language_level(tmp_path: Path) -> None:
    text = PERSONAL + "extra_skills:\n  - {name: X, level: expert}\nlanguage_levels: {Deutsch: fluent}\n"
    with pytest.raises(ConfigError) as ei:
        load_profile(ws_with(tmp_path, profile=text))
    joined = "\n".join(ei.value.errors)
    assert "extra_skills[0].level" in joined
    assert "language_levels" in joined


def test_salary_resolution(tmp_path: Path) -> None:
    text = (
        PERSONAL
        + """\
salary:
  target_eur: 60000
  min_eur: 55000
  overrides:
    senior: {target_eur: 75000}
    lead: {target_eur: 90000, min_eur: 85000}
"""
    )
    p = load_profile(ws_with(tmp_path, profile=text))
    base = resolve_salary_target(p, None)
    assert base is not None and (base.target_eur, base.min_eur) == (60000, 55000)
    assert resolve_salary_target(p, "mid") == base
    assert resolve_salary_target(p, "unspecified") == base
    senior = resolve_salary_target(p, "Senior")
    assert senior is not None and (senior.target_eur, senior.min_eur) == (75000, 55000)
    lead = resolve_salary_target(p, "lead")
    assert lead is not None and (lead.target_eur, lead.min_eur) == (90000, 85000)


# --- run.default_mode replaces email.auto_send (add-menu-and-run-modes 1.1) -------------------


def _write_cfg(tmp_path, text):
    from tests.helpers import make_workspace

    ws = make_workspace(tmp_path)
    ws.config_path.write_text(ws.config_path.read_text() + text)
    return ws


def test_old_auto_send_true_becomes_automatic_with_notice(tmp_path):
    from autoapp.config.models import load_config

    cfg = load_config(_write_cfg(tmp_path, "email:\n  auto_send: true\n  daily_cap: 3\n"))
    assert cfg.run.default_mode == "automatic" and cfg.email.daily_cap == 3
    assert len(cfg.notices) == 1 and "email.auto_send is replaced by run.default_mode; using automatic" in cfg.notices[0]


def test_old_auto_send_false_becomes_review(tmp_path):
    from autoapp.config.models import load_config

    assert load_config(_write_cfg(tmp_path, "email:\n  auto_send: false\n")).run.default_mode == "review"


def test_new_key_only_has_no_notice(tmp_path):
    from autoapp.config.models import load_config

    cfg = load_config(_write_cfg(tmp_path, "run:\n  default_mode: automatic\n"))
    assert cfg.run.default_mode == "automatic" and cfg.notices == []


def test_both_keys_the_new_one_wins(tmp_path):
    from autoapp.config.models import load_config

    cfg = load_config(_write_cfg(tmp_path, "email:\n  auto_send: true\nrun:\n  default_mode: review\n"))
    assert cfg.run.default_mode == "review" and "wins" in cfg.notices[0]


def test_default_mode_cannot_be_test_or_invalid(tmp_path):
    from autoapp.config.models import ConfigError, load_config

    for bad in ("test", "auto"):
        ws = _write_cfg(tmp_path / bad, f"run:\n  default_mode: {bad}\n")
        with pytest.raises(ConfigError) as exc:
            load_config(ws)
        assert "default_mode" in " ".join(exc.value.errors)


def test_employer_careers_defaults_disabled_and_old_configs_load(tmp_path: Path) -> None:
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG))
    ec = cfg.sources.employer_careers
    assert ec.enabled is False
    assert (ec.max_employers_per_run, ec.max_search_requests_per_run, ec.max_search_requests_per_day,
            ec.max_search_requests_per_month) == (10, 300, 1000, 5000)
    assert (ec.max_pages_per_employer, ec.max_pages_per_run, ec.max_openings_per_employer,
            ec.max_openings_per_run, ec.scan_ttl_hours, ec.failure_ttl_hours) == (20, 100, 50, 100, 24, 24)
    assert ec.search_cost_per_1000_usd == 1


@pytest.mark.parametrize("override", [
    {"max_search_requests_per_run": 0},
    {"max_search_requests_per_run": 1100, "max_search_requests_per_day": 1000},
    {"max_search_requests_per_day": 6000},
    {"max_pages_per_employer": 200},
    {"max_openings_per_employer": 500},
    {"scan_ttl_hours": -1},
])
def test_employer_careers_invalid_limits_fail_clearly(tmp_path: Path, override: dict) -> None:
    lines = "".join(f"    {k}: {v}\n" for k, v in override.items())
    cfg = MIN_CONFIG + "sources:\n  employer_careers:\n    enabled: true\n" + lines
    with pytest.raises(ConfigError) as err:
        load_config(ws_with(tmp_path, cfg))
    assert "employer_careers" in str(err.value)


def test_task_instructions_are_read_and_default_to_empty(tmp_path: Path) -> None:
    tasks = "".join(f"    {t}: {{provider: openrouter, model: m/{t}}}\n" for t in ("evaluate", "write", "form_fill"))
    tasks += "    form_find: {provider: openrouter, model: m/form_find, instructions: \"  Prefer career sites.  \"}\n"
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG + "llm:\n  tasks:\n" + tasks))
    assert cfg.llm.instructions("form_find") == "Prefer career sites."
    assert cfg.llm.instructions("write") == ""


def test_alert_mode_defaults_to_search_first(tmp_path):
    assert load_config(ws_with(tmp_path, MIN_CONFIG)).sources.alerts.mode == "search_first"


def test_alert_mode_platform_can_be_chosen(tmp_path):
    cfg = load_config(ws_with(tmp_path, MIN_CONFIG + "sources:\n  alerts: {mode: platform}\n"))
    assert cfg.sources.alerts.mode == "platform"


def test_alert_mode_typo_lists_the_allowed_values(tmp_path):
    with pytest.raises(ConfigError) as err:
        load_config(ws_with(tmp_path, MIN_CONFIG + "sources:\n  alerts: {mode: serch_first}\n"))
    assert "search_first" in str(err.value) and "platform" in str(err.value)
