"""Pydantic models for ``config.yaml`` and ``profile.yaml`` plus their loaders.

Every config section has defaults, so a file with a single search profile is valid.
Loaders collect *all* validation problems into one ``ConfigError``. Nothing here
touches the network.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAMLError

from autoapp.config.io import load_yaml, to_plain
from autoapp.config.workspace import Workspace
from autoapp.llm.settings import LLMSettings

CEFR = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
CEFR_ORDER: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
_UNSUPPORTED_MAIL_DOMAINS = ("outlook.com", "hotmail.com", "live.com")

# Domains whose addresses go to manual review (design decision 11). ``*`` matches any TLD.
DEFAULT_FREE_MAIL_DOMAINS: list[str] = [
    "gmail.com",
    "googlemail.com",
    "gmx.de",
    "gmx.net",
    "web.de",
    "t-online.de",
    "yahoo.*",
    "outlook.*",
    "hotmail.*",
    "icloud.com",
    "freenet.de",
    "mail.de",
    "posteo.de",
]

DEFAULT_RESTRICTED_DOMAINS: list[str] = ["linkedin.com", "indeed.com", "indeed.de", "stepstone.de"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_RE.match(value):
        raise ValueError(f"'{value}' is not a valid email address")
    return value


# --------------------------------------------------------------------------- config


class Location(_Model):
    name: str
    radius_km: int | None = Field(default=None, ge=0)


class SearchProfile(_Model):
    """One saved search. Only ``name`` and ``keywords`` are required; ``None`` = no restriction."""

    name: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list, validate_default=True)
    exclude_keywords: list[str] | None = None
    exclude_companies: list[str] | None = None
    locations: list[Location] | None = None
    work_mode: Literal["onsite", "hybrid", "remote", "any"] | None = None
    working_time: Literal["full_time", "part_time", "any"] | None = None
    contract_type: Literal["permanent", "fixed_term", "any"] | None = None
    exclude_position_types: list[str] | None = None
    experience_levels: list[str] | None = None
    max_posting_age_days: int | None = Field(default=None, ge=1)
    min_salary: int | None = Field(default=None, ge=0)
    exclude_agencies: bool | None = None
    writing_instructions: str | None = None

    @field_validator("keywords")
    @classmethod
    def _needs_keyword(cls, v: list[str]) -> list[str]:
        v = [k.strip() for k in v if k and k.strip()]
        if not v:
            raise ValueError("a search profile needs at least one keyword")
        return v


class ToggleSource(_Model):
    enabled: bool = True


class AdzunaConfig(_Model):
    """Request caps keep the free tier (reported: 25/min, 250/day, 1000/month) from being exceeded."""

    enabled: bool = True
    max_requests_per_run: int = Field(default=40, ge=1)
    max_requests_per_day: int = Field(default=150, ge=1)
    max_requests_per_month: int = Field(default=900, ge=1)


AlertMode = Literal["search_first", "platform"]


class AlertsSource(_Model):
    """``mode`` decides how an alert job gets its ad: ``search_first`` (default) searches for the employer's own
    ad and careers page and never opens the job board's page; ``platform`` reads the job board's page as before."""

    enabled: bool = True
    folder: str = "Jobalerts"
    mode: AlertMode = "search_first"


class ArbeitnowConfig(_Model):
    enabled: bool = False
    max_pages_per_run: int = Field(default=40, ge=1)
    max_requests_per_run: int = Field(default=40, ge=1)
    max_requests_per_day: int = Field(default=200, ge=1)
    max_requests_per_month: int = Field(default=1000, ge=1)
    min_delay_s: float = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _caps_consistent(self) -> ArbeitnowConfig:
        if not self.max_requests_per_run <= self.max_requests_per_day <= self.max_requests_per_month:
            raise ValueError("request caps must be ordered per-run <= daily <= monthly")
        return self


class EmployerCareersConfig(_Model):
    """Opt-in employer careers discovery (employer-career-discovery spec). Disabled by default."""

    enabled: bool = False
    max_employers_per_run: int = Field(default=10, ge=1)
    max_search_requests_per_run: int = Field(default=300, ge=1)
    max_search_requests_per_day: int = Field(default=1000, ge=1)
    max_search_requests_per_month: int = Field(default=5000, ge=1)
    # Searches employer discovery leaves unused each run and day, so the own-ad search (company + title) still has some.
    reserved_for_own_ad_search: int = Field(default=25, ge=0)
    max_pages_per_employer: int = Field(default=20, ge=1)
    max_pages_per_run: int = Field(default=100, ge=1)
    max_openings_per_employer: int = Field(default=50, ge=1)
    max_openings_per_run: int = Field(default=100, ge=1)
    scan_ttl_hours: int = Field(default=24, ge=0)
    failure_ttl_hours: int = Field(default=24, ge=0)
    search_cost_per_1000_usd: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def _caps_consistent(self) -> EmployerCareersConfig:
        if self.max_search_requests_per_run > self.max_search_requests_per_day:
            raise ValueError("max_search_requests_per_run must be <= max_search_requests_per_day")
        if self.max_search_requests_per_day > self.max_search_requests_per_month:
            raise ValueError("max_search_requests_per_day must be <= max_search_requests_per_month")
        if self.max_pages_per_employer > self.max_pages_per_run:
            raise ValueError("max_pages_per_employer must be <= max_pages_per_run")
        if self.max_openings_per_employer > self.max_openings_per_run:
            raise ValueError("max_openings_per_employer must be <= max_openings_per_run")
        return self


class WorkwiseConfig(_Model):
    """Workwise's public job search (the same requests its website makes, no login). Matched to your profiles
    locally: Workwise searches by keyword, and the distance to your cities is checked here."""

    enabled: bool = True
    max_pages_per_keyword: int = Field(default=2, ge=1, le=10)
    page_size: int = Field(default=50, ge=10, le=100)
    max_requests_per_run: int = Field(default=30, ge=1)
    max_requests_per_day: int = Field(default=100, ge=1)
    max_requests_per_month: int = Field(default=1500, ge=1)
    min_delay_s: float = Field(default=2, ge=1)

    @model_validator(mode="after")
    def _caps_consistent(self) -> WorkwiseConfig:
        if not self.max_requests_per_run <= self.max_requests_per_day <= self.max_requests_per_month:
            raise ValueError("request caps must be ordered per-run <= daily <= monthly")
        return self


class ApplyLinksWithLogin(_Model):
    """Read the employer's apply address from a platform's job page with your login (off by default).

    The default way past a LinkedIn/Indeed/StepStone link is the employer's own careers site
    (``employer_careers``). Switched on here, a platform's job page is also opened in the app's browser profile
    (log in once with ``autoapp login <site>``) to read where "apply on the company website" leads, for the jobs
    the employer lookup could not match. Against the platforms' terms; a low but real risk for the account.
    """

    linkedin: bool = False
    indeed: bool = False
    stepstone: bool = False
    max_per_run: int = Field(default=20, ge=1, le=100)
    min_delay_s: float = Field(default=20, ge=5)
    max_delay_s: float = Field(default=40, ge=5)

    @model_validator(mode="after")
    def _delays_ordered(self) -> ApplyLinksWithLogin:
        if self.max_delay_s < self.min_delay_s:
            raise ValueError("max_delay_s must be >= min_delay_s")
        return self

    def enabled_for(self, site: str | None) -> bool:
        return bool(site) and bool(getattr(self, site, False))


class Sources(_Model):
    arbeitsagentur: ToggleSource = Field(default_factory=ToggleSource)
    adzuna: AdzunaConfig = Field(default_factory=AdzunaConfig)
    arbeitnow: ArbeitnowConfig = Field(default_factory=ArbeitnowConfig)
    workwise: WorkwiseConfig = Field(default_factory=WorkwiseConfig)
    alerts: AlertsSource = Field(default_factory=AlertsSource)
    employer_careers: EmployerCareersConfig = Field(default_factory=EmployerCareersConfig)
    apply_links_with_login: ApplyLinksWithLogin = Field(default_factory=ApplyLinksWithLogin)


class DetailFetch(_Model):
    linkedin: bool = True
    indeed: bool = True
    stepstone: bool = True
    max_per_site: int = Field(default=20, ge=0)
    min_delay_s: float = Field(default=20, ge=0)
    max_delay_s: float = Field(default=40, ge=0)

    @model_validator(mode="after")
    def _delays_ordered(self) -> DetailFetch:
        if self.max_delay_s < self.min_delay_s:
            raise ValueError("max_delay_s must be >= min_delay_s")
        return self


class Evaluation(_Model):
    apply_threshold: int = Field(default=75, ge=0, le=100)
    maybe_threshold: int = Field(default=60, ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> Evaluation:
        if self.apply_threshold < self.maybe_threshold:
            raise ValueError("apply_threshold must be >= maybe_threshold")
        return self


class Limits(_Model):
    max_llm_calls_per_run: int = Field(default=500, ge=0)
    max_cost_per_run_usd: float = Field(default=3.0, ge=0)  # estimated AI spend; the run stops asking the AI at this amount
    max_evaluate_per_run: int = Field(default=200, ge=0)
    max_prepare_per_run: int = Field(default=50, ge=0)


class MailAccount(_Model):
    """One mailbox. Secrets are never stored here, only the env var holding the password."""

    preset: Literal["gmail", "proton_bridge", "custom"]
    address: str
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_tls: Literal["ssl", "starttls", "none"] | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_tls: Literal["ssl", "starttls", "none"] | None = None
    username: str | None = None
    password_env: str | None = None
    sent_folder: str | None = None
    drafts_folder: str | None = None  # found automatically when empty (special-use attribute)
    bridge_cert_sha256: str | None = None

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        v = _check_email(v)
        domain = v.rsplit("@", 1)[1].lower()
        if domain in _UNSUPPORTED_MAIL_DOMAINS:
            raise ValueError(
                "Outlook.com is not supported yet because Microsoft disabled password logins "
                "for third-party apps; use Gmail, Proton Mail Bridge or a custom IMAP/SMTP server"
            )
        return v

    @model_validator(mode="after")
    def _custom_needs_hosts(self) -> MailAccount:
        if self.preset == "custom" and not (self.imap_host or self.smtp_host):
            raise ValueError("preset 'custom' needs imap_host and/or smtp_host")
        return self


class MailConfig(_Model):
    alerts: MailAccount | None = None
    sending: MailAccount | None = None

    @model_validator(mode="after")
    def _default_password_env(self) -> MailConfig:
        if self.alerts and not self.alerts.password_env:
            self.alerts.password_env = "AUTOAPP_ALERTS_PASSWORD"
        if self.sending and not self.sending.password_env:
            self.sending.password_env = "AUTOAPP_SMTP_PASSWORD"
        return self


RunMode = Literal["automatic", "review", "test"]
RUN_MODES: tuple[str, ...] = ("automatic", "review", "test")


class RunSettings(_Model):
    """``default_mode`` is used when a run does not ask (scheduled runs). Never ``test``."""

    default_mode: Literal["automatic", "review"] = "review"


class EmailPolicy(_Model):
    daily_cap: int = Field(default=100, ge=0)
    min_score: int = Field(default=80, ge=0, le=100)
    company_cooldown_days: int = Field(default=14, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    max_bundle_mb: float = Field(default=10, gt=0)
    free_mail_domains: list[str] = Field(default_factory=lambda: list(DEFAULT_FREE_MAIL_DOMAINS))


class Writing(_Model):
    max_words: int = Field(default=300, ge=50)
    max_assumed_skills: int = Field(default=5, ge=0)
    banned_phrases: list[str] = Field(default_factory=list)
    allowed_phrases: list[str] = Field(default_factory=list)
    language: Literal["auto", "de", "en"] = "auto"
    always_attach_certificates: bool = False


class Tracking(_Model):
    delimiter: str = Field(default=";", min_length=1, max_length=1)
    include_skipped: bool = False


class Assist(_Model):
    restricted_domains: list[str] = Field(default_factory=lambda: list(DEFAULT_RESTRICTED_DOMAINS))
    llm_answers: bool = True
    # model: the model reads the form's fields and fills them from your CV, profile and saved answers (the fixed
    # answers for how-found, referral and prior-employment questions and the uploads stay rule-based).
    # rules: only the built-in rules fill facts; the model is asked about fields they cannot place.
    fill_mode: Literal["model", "rules"] = "model"
    # model: the form_find model chooses each click on the way to the application form (the safety rules stay in
    # code; it falls back to the rules when the model cannot be used). rules: only the built-in link rules.
    navigate_mode: Literal["model", "rules"] = "model"
    navigate_max_steps: int = Field(default=8, ge=1, le=15)


class Setup(_Model):
    completed_sections: list[str] = Field(default_factory=list)
    version: int = 1


class Config(_Model):
    search_profiles: list[SearchProfile] = Field(default_factory=list)
    sources: Sources = Field(default_factory=Sources)
    detail_fetch: DetailFetch = Field(default_factory=DetailFetch)
    evaluation: Evaluation = Field(default_factory=Evaluation)
    limits: Limits = Field(default_factory=Limits)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    mail: MailConfig = Field(default_factory=MailConfig)
    email: EmailPolicy = Field(default_factory=EmailPolicy)
    writing: Writing = Field(default_factory=Writing)
    tracking: Tracking = Field(default_factory=Tracking)
    assist: Assist = Field(default_factory=Assist)
    setup: Setup = Field(default_factory=Setup)
    run: RunSettings = Field(default_factory=RunSettings)
    _notices: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_auto_send(cls, data: Any) -> Any:
        """``email.auto_send`` (replaced by ``run.default_mode``): true -> automatic, false -> review."""
        if not isinstance(data, dict) or not isinstance(data.get("email"), dict) or "auto_send" not in data["email"]:
            return data
        data = dict(data)
        email = dict(data["email"])
        legacy = email.pop("auto_send")
        data["email"] = email
        run = data.get("run") if data.get("run") is not None else {}
        if isinstance(run, dict) and "default_mode" not in run:
            run = dict(run)
            run["default_mode"] = "automatic" if legacy else "review"
            data["run"] = run
        return data

    @property
    def notices(self) -> list[str]:
        return self._notices

    @model_validator(mode="after")
    def _unique_profile_names(self) -> Config:
        seen: set[str] = set()
        for p in self.search_profiles:
            key = p.name.strip().lower()
            if key in seen:
                raise ValueError(f"search_profiles: duplicate profile name '{p.name}'")
            seen.add(key)
        return self


# -------------------------------------------------------------------------- profile


class Personal(_Model):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    email: str
    phone: str
    street: str
    postal_code: str
    city: str
    country: str = "Deutschland"
    linkedin_url: str | None = None
    xing_url: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _check_email(v)


class Availability(_Model):
    earliest_start: str | None = None
    notice_period: str | None = None


# certificate: degrees, diplomas, courses. work_reference: an Arbeitszeugnis from an employer.
# other: anything else you may want to attach to a form that has a field for it.
DocumentRole = Literal["cv", "certificate", "work_reference", "photo", "writing_sample", "other"]


class DocumentEntry(_Model):
    """A file in ``documents/`` (path relative to that folder) and its role."""

    file: str = Field(min_length=1)
    role: DocumentRole = "other"
    # "any" marks a document that is sent whatever the language of the ad; "de"/"en" ones only with a
    # letter in that language; unset behaves like "any".
    language: Literal["de", "en", "any"] | None = None
    label: str | None = None


class SalaryOverride(_Model):
    target_eur: int = Field(gt=0)
    min_eur: int | None = Field(default=None, gt=0)


Seniority = Literal["junior", "mid", "senior", "lead"]


class SalaryPrefs(_Model):
    target_eur: int = Field(gt=0)
    min_eur: int | None = Field(default=None, gt=0)
    overrides: dict[Seniority, SalaryOverride] = Field(default_factory=dict)
    # Another amount for jobs in a federal state, e.g. ``bayern: {target_eur: 56000}``; wins over ``overrides``.
    region_overrides: dict[str, SalaryOverride] = Field(default_factory=dict)

    @field_validator("region_overrides")
    @classmethod
    def _known_states(cls, value: dict[str, SalaryOverride]) -> dict[str, SalaryOverride]:
        from autoapp.geo import STATES

        unknown = sorted(k for k in value if k not in STATES)
        if unknown:
            raise ValueError(f"unknown federal state {unknown}; use one of: {', '.join(STATES)}")
        return value


class ExtraSkill(_Model):
    name: str = Field(min_length=1)
    level: Literal["basic", "good", "strong"]
    context: str | None = None


class Dealbreakers(_Model):
    max_german_level_required: CEFR | None = None
    no_relocation: bool = False
    excluded_industries: list[str] = Field(default_factory=list)


class Profile(_Model):
    personal: Personal
    work_authorization: str | None = None
    availability: Availability = Field(default_factory=Availability)
    language_levels: dict[str, str] = Field(default_factory=dict)
    documents: list[DocumentEntry] = Field(default_factory=list)
    standard_answers: dict[str, str] = Field(default_factory=dict)
    salary: SalaryPrefs | None = None
    extra_skills: list[ExtraSkill] = Field(default_factory=list)
    dealbreakers: Dealbreakers = Field(default_factory=Dealbreakers)

    @field_validator("language_levels")
    @classmethod
    def _levels(cls, v: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for lang, level in v.items():
            norm = str(level).strip()
            if norm.lower() == "native":
                norm = "native"
            elif norm.upper() in CEFR_ORDER:
                norm = norm.upper()
            else:
                raise ValueError(f"language '{lang}': level '{level}' must be A1-C2 or 'native'")
            out[lang] = norm
        return out


# ---------------------------------------------------------------------------- loading


class ConfigError(Exception):
    """One or more problems in a user file; ``errors`` holds one readable line each."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def _dotted(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(root)"


def format_validation_error(filename: str, exc: ValidationError) -> list[str]:
    """Turn a pydantic error into ``file: dotted.path: message`` lines."""
    lines: list[str] = []
    for e in exc.errors():
        msg = e["msg"]
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        loc = e["loc"]
        if loc:
            lines.append(f"{filename}: {_dotted(loc)}: {msg}")
        else:
            lines.append(f"{filename}: {msg}")
    return lines


def _load_model[T: BaseModel](path: Path, model: type[T]) -> T:
    if not path.exists():
        raise ConfigError([f"{path.name}: file not found at {path}"])
    try:
        raw = to_plain(load_yaml(path))
    except YAMLError as exc:
        raise ConfigError([f"{path.name}: invalid YAML: {str(exc).strip().splitlines()[0]}"]) from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(path.name, exc)) from exc


def load_config(ws: Workspace) -> Config:
    """Load and validate ``config.yaml``; raises ``ConfigError`` listing every problem."""
    config = _load_model(ws.config_path, Config)
    try:
        raw = to_plain(load_yaml(ws.config_path)) or {}
    except YAMLError:
        raw = {}
    email = raw.get("email") if isinstance(raw, dict) else None
    if isinstance(email, dict) and "auto_send" in email:
        has_new = isinstance(raw.get("run"), dict) and "default_mode" in raw["run"]
        config._notices.append(
            f"email.auto_send is replaced by run.default_mode; using {config.run.default_mode}"
            + (" (run.default_mode is set and wins)" if has_new else "")
            + ". Run `autoapp setup --section email` or edit config.yaml to remove the old key."
        )
    llm = raw.get("llm") if isinstance(raw, dict) else None
    if isinstance(llm, dict):
        from autoapp.llm.settings import TASK_NAMES

        given = llm.get("tasks") if isinstance(llm.get("tasks"), dict) else {}
        old_keys = [k for k in ("provider", "model", "effort") if k in llm]
        unset = [t for t in TASK_NAMES if not (given.get(t) or {}).get("model")]
        if old_keys or unset:
            used = "; ".join(f"{t}: {config.llm.resolve(t)[0]} {config.llm.resolve(t)[1]}" for t in TASK_NAMES)
            config._notices.append(
                "llm: every task has its own model now (llm.tasks.evaluate, write, form_fill, form_find). "
                f"Using {used}. Run `autoapp setup --section llm` to set them and remove the old "
                f"{'/'.join(old_keys) + ' ' if old_keys else ''}keys."
            )
    return config


def load_profile(ws: Workspace) -> Profile:
    """Load and validate ``profile.yaml``; raises ``ConfigError`` listing every problem."""
    return _load_model(ws.profile_path, Profile)
