"""The eight wizard sections.

Each section reads its current values from the files (so reruns show hand edits), asks
its questions with immediate validation and writes only the keys it owns:

=========  =====================================================================
documents  profile.yaml: ``documents``
profile    profile.yaml: ``personal``, ``work_authorization``, ``availability``,
           ``language_levels``, ``extra_skills``
salary     profile.yaml: ``salary``
search     config.yaml: ``search_profiles``
sources    config.yaml: ``sources`` (incl. ``workwise``, ``apply_links_with_login``), ``detail_fetch``, ``mail.alerts``;
           .env: ``ADZUNA_*``, ``PARALLEL_API_KEY``, the alert mailbox password
llm        config.yaml: ``llm.tasks.evaluate|write|form_fill|form_find`` (a provider and model each)
           (provider/model only); .env: provider API keys
email      config.yaml: ``mail.sending``, ``run.default_mode`` and the three limits;
           .env: the sending password
writing    writing_instructions.md; config.yaml: ``writing.language``;
           profile.yaml: the ``writing_sample`` role of ``documents`` entries
=========  =====================================================================
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from autoapp.config.models import (
    DetailFetch,
    EmailPolicy,
    MailAccount,
    Personal,
    SearchProfile,
    Sources,
)
from autoapp.documents import propose_roles
from autoapp.llm.cost import PriceTable
from autoapp.llm.settings import API_KEY_ENV, DEFAULT_MODEL, DEFAULT_PROVIDER, TASK_NAMES, LLMSettings, Price
from autoapp.mail.connection import normalize_fingerprint
from autoapp.setup.checks import CheckResult
from autoapp.setup.wizard import (
    Section,
    SetupContext,
    ask_secret,
    drop_key,
    env_values,
    has_secret,
    integer,
    read_yaml,
    register,
    required,
    slugify,
    split_list,
    write_key,
    write_or_drop,
)

CEFR_CHOICES = [("none", "not specified"), ("A1", "A1"), ("A2", "A2"), ("B1", "B1"), ("B2", "B2"), ("C1", "C1"),
                ("C2", "C2"), ("native", "native")]
ROLE_CHOICES = [
    ("cv", "CV / Lebenslauf"),
    ("certificate", "Certificate (degree, diploma, course)"),
    ("work_reference", "Arbeitszeugnis (reference from an employer)"),
    ("photo", "Photo"),
    ("writing_sample", "Writing sample (e.g. an old cover letter)"),
    ("other", "Other"),
    ("ignore", "Do not declare this file"),
]
LANG_CHOICES = [("none", "unspecified (treated as any)"), ("any", "any language (always sent)"),
                ("de", "German (only with a German letter)"), ("en", "English (only with an English letter)")]
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "google": "gemini-3.8-flash",
    "openrouter": "deepseek/deepseek-v4.1-flash",
}
PROVIDER_LABELS = {"anthropic": "Anthropic", "google": "Google (Gemini)", "openrouter": "OpenRouter"}

# Approximate tokens per job (design decision 17): (cached input, uncached input, output).
EVAL_TOKENS = (3500, 1500, 1500)
WRITE_TOKENS = (7280, 2920, 3360)
TYPICAL_DAY = (40, 5)  # evaluated jobs, prepared applications

MAIL_PRESETS = [
    ("gmail", "Gmail"),
    ("proton_bridge", "Proton Mail (through Proton Mail Bridge)"),
    ("custom", "Other (your own IMAP/SMTP server)"),
]
PREREQUISITES = {
    "gmail": (
        "Gmail needs 2-Step Verification and an app password (your normal password does not work):\n"
        "  1. Turn on 2-Step Verification: https://myaccount.google.com/security\n"
        "  2. Create an app password named 'autoapp': https://myaccount.google.com/apppasswords\n"
        "  3. Enter that 16-character app password below."
    ),
    "proton_bridge": (
        "Proton Mail needs a paid Proton plan and Proton Mail Bridge running on this computer\n"
        "(for example `protonmail-bridge --cli`). Use the Bridge username and the Bridge password\n"
        "shown in Bridge, not your Proton login password."
    ),
    "custom": (
        "Other providers: you need the IMAP and/or SMTP host, port and TLS mode from your provider's\n"
        "help pages. Use an app password if the provider offers one."
    ),
}
PASSWORD_LABELS = {"gmail": "Gmail app password", "proton_bridge": "Bridge password", "custom": "Mail password"}
TLS_CHOICES = [("ssl", "SSL/TLS (implicit)"), ("starttls", "STARTTLS"), ("none", "none (not recommended)")]

WRITING_TEMPLATE = """# Writing instructions

Tone, emphasis, facts to mention and things to avoid in my cover letters.

- Keep it direct and conversational.
- Mention concrete projects instead of generic claims.
"""


# ---------------------------------------------------------------------------- helpers


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` and empty values from an answers dict."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _model_error(exc: ValidationError) -> str:
    msg = exc.errors()[0]["msg"]
    return msg.removeprefix("Value error, ")


def _list_documents(ws_documents: Path) -> list[str]:
    if not ws_documents.is_dir():
        return []
    return sorted(
        p.relative_to(ws_documents).as_posix()
        for p in ws_documents.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(ws_documents).parts)
    )


def _cfg(ctx: SetupContext) -> dict[str, Any]:
    return read_yaml(ctx.ws.config_path)


def _profile(ctx: SetupContext) -> dict[str, Any]:
    return read_yaml(ctx.ws.profile_path)


def _check_loop(
    ctx: SetupContext,
    label: str,
    check: Callable[[], CheckResult],
    reenter: Callable[[], None] | None,
) -> bool:
    """Run a check; on failure offer to re-enter the credentials (or test again) or continue."""
    p = ctx.prompter
    while True:
        result = check()
        if result.ok:
            p.say(f"OK: {label}")
            return True
        p.say(f"FAILED: {label}: {result.message}")
        retry_label = "Re-enter the credentials and test again" if reenter else "Test again"
        choice = p.select("What now?", [("retry", retry_label), ("continue", "Continue anyway")], default="retry")
        if choice == "continue":
            p.say("Continuing without a successful check. You can fix it later with `autoapp setup`.")
            return False
        if reenter:
            reenter()


# ------------------------------------------------------------------------ documents


def run_documents(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    p.say(f"Put your CV (PDF or DOCX), certificates and photo into: {ws.documents_dir}")
    p.say("Files in that folder are only read, never changed.")
    files = _list_documents(ws.documents_dir)
    while not files:
        p.say(f"No files found in {ws.documents_dir} yet.")
        if not p.confirm("Check the folder again?", default=True):
            p.say("Skipped. A CV is required before jobs can be evaluated.")
            return False
        files = _list_documents(ws.documents_dir)

    current = {str(e.get("file")): dict(e) for e in _profile(ctx).get("documents") or [] if isinstance(e, dict)}
    for gone in sorted(set(current) - set(files)):
        p.say(f"Note: '{gone}' is declared in profile.yaml but is no longer in documents/; it is dropped.")
    proposals = {e.file: e for e in propose_roles(files)}
    entries: dict[str, dict[str, Any]] = {}
    for f in files:
        if f in current:
            entries[f] = current[f]
        else:
            prop = proposals[f]
            entries[f] = _clean({"file": f, "role": prop.role, "language": prop.language})

    def has_cv() -> bool:
        return any(e.get("role") == "cv" for e in entries.values())

    def show() -> None:
        p.table(
            "Document mapping",
            ["File", "Role", "Language"],
            [[f, _s(e.get("role")), _s(e.get("language")) or "-"] for f, e in entries.items()],
        )

    show()
    accept = has_cv() and p.confirm("Use this mapping?", default=True)
    if not has_cv():
        p.say("No file is marked as your CV yet.")
    while not accept:
        for f in files:
            cur = entries.get(f, {"file": f, "role": "other"})
            role = p.select(f"Role of '{f}'", ROLE_CHOICES, default=_s(cur.get("role")) or "other")
            if role == "ignore":
                entries.pop(f, None)
                continue
            entry = dict(cur)
            entry["role"] = role
            if role in ("cv", "certificate", "work_reference", "writing_sample"):
                lang = p.select(
                    f"Language of '{f}'", LANG_CHOICES, default=_s(cur.get("language")) or "none"
                )
                if lang == "none":
                    entry.pop("language", None)
                else:
                    entry["language"] = lang
            entries[f] = entry
        if has_cv():
            accept = True
        else:
            p.say("At least one document must have the role 'cv'. Please assign it.")
    write_key(ws.profile_path, "documents", list(entries.values()))
    return True


def reset_documents(ctx: SetupContext) -> None:
    drop_key(ctx.ws.profile_path, "documents")


# --------------------------------------------------------------------------- profile


def _email_validator(value: str) -> str | None:
    try:
        Personal.model_validate(
            {"first_name": "x", "last_name": "x", "email": value.strip(), "phone": "x", "street": "x",
             "postal_code": "x", "city": "x"}
        )
    except ValidationError as exc:
        return _model_error(exc)
    return None


def run_profile(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    data = _profile(ctx)
    per = dict(data.get("personal") or {})
    p.say("Your personal data goes into application emails and forms. It stays in profile.yaml.")
    out: dict[str, Any] = dict(per)
    for key, label in (("first_name", "First name"), ("last_name", "Last name")):
        out[key] = p.text(label, _s(per.get(key)), required(label)).strip()
    out["email"] = p.text("Email address (for applications)", _s(per.get("email")), _email_validator).strip()
    out["phone"] = p.text("Phone number", _s(per.get("phone")), required("The phone number")).strip()
    out["street"] = p.text("Street and house number", _s(per.get("street")), required("The street")).strip()
    out["postal_code"] = p.text("Postal code", _s(per.get("postal_code")), required("The postal code")).strip()
    out["city"] = p.text("City", _s(per.get("city")), required("The city")).strip()
    out["country"] = p.text("Country", _s(per.get("country")) or "Deutschland", required("The country")).strip()
    for key, label in (("linkedin_url", "LinkedIn URL (optional)"), ("xing_url", "Xing URL (optional)")):
        out[key] = p.text(label, _s(per.get(key))).strip() or None
    write_key(ws.profile_path, "personal", _clean(out))

    auth = p.text("Work authorization, e.g. 'EU citizen' (optional)", _s(data.get("work_authorization"))).strip()
    write_or_drop(ws.profile_path, "work_authorization", auth or None)

    av = dict(data.get("availability") or {})
    start = p.text("Earliest start date or time, e.g. '4 weeks' (optional)", _s(av.get("earliest_start"))).strip()
    notice = p.text("Notice period, e.g. '3 Monate zum Monatsende' (optional)", _s(av.get("notice_period"))).strip()
    write_or_drop(ws.profile_path, "availability", _clean({**av, "earliest_start": start, "notice_period": notice}))

    levels = {str(k): str(v) for k, v in (data.get("language_levels") or {}).items()}
    new_levels: dict[str, str] = {}
    for code, name in (("de", "German"), ("en", "English")):
        lvl = p.select(f"{name} level", CEFR_CHOICES, default=levels.get(code, "none"))
        if lvl != "none":
            new_levels[code] = lvl
    for code, lvl in levels.items():
        if code not in ("de", "en"):
            new_levels[code] = lvl
    while p.confirm("Add another language?", default=False):
        code = p.text("Language code or name, e.g. 'fr'", "", required("The language")).strip()
        lvl = p.select(f"Level in '{code}'", [c for c in CEFR_CHOICES if c[0] != "none"], default="B1")
        new_levels[code] = lvl
    write_or_drop(ws.profile_path, "language_levels", new_levels)

    skills = [dict(s) for s in data.get("extra_skills") or [] if isinstance(s, dict)]
    p.say("Extra skills are things you can do but that are not prominent in your CV. The letter writer may")
    p.say("present them as basic familiarity, never as expertise.")
    if skills:
        p.say("Current extra skills: " + ", ".join(f"{s.get('name')} ({s.get('level')})" for s in skills))
        if not p.confirm("Keep these extra skills?", default=True):
            skills = []
    while p.confirm("Add an extra skill?", default=False):
        name = p.text("Skill name", "", required("The skill name")).strip()
        level = p.select("Your level", [("basic", "basic"), ("good", "good"), ("strong", "strong")], default="basic")
        context = p.text("Where did you use it? (optional)", "").strip()
        skills.append(_clean({"name": name, "level": level, "context": context}))
    write_or_drop(ws.profile_path, "extra_skills", skills)
    return True


def reset_profile(ctx: SetupContext) -> None:
    for key in ("personal", "work_authorization", "availability", "language_levels", "extra_skills"):
        drop_key(ctx.ws.profile_path, key)


# ---------------------------------------------------------------------------- salary


def run_salary(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    cur = dict(_profile(ctx).get("salary") or {})
    p.say("Salary expectations are optional. They are only used when an ad asks for them.")
    if not p.confirm("Do you want to store salary expectations?", default=bool(cur)):
        drop_key(ws.profile_path, "salary")
        p.say("Skipped: no salary preferences are stored.")
        return True
    target = int(
        p.text("Target salary (EUR per year)", _s(cur.get("target_eur")), integer("The target salary", minimum=1)).strip()
    )

    def min_ok(value: str) -> str | None:
        err = integer("The minimum salary", minimum=1, optional=True)(value)
        if err is None and value.strip() and int(value) > target:
            return "The minimum salary cannot be higher than the target salary."
        return err

    minimum = p.text("Minimum acceptable salary (EUR per year, optional)", _s(cur.get("min_eur")), min_ok).strip()
    salary: dict[str, Any] = dict(cur)
    salary["target_eur"] = target
    if minimum:
        salary["min_eur"] = int(minimum)
    else:
        salary.pop("min_eur", None)
    overrides = {str(k): dict(v) for k, v in (cur.get("overrides") or {}).items()}
    if p.confirm("Set different amounts per seniority (junior/mid/senior/lead)?", default=bool(overrides)):
        overrides = {}
        for level in ("junior", "mid", "senior", "lead"):
            old = (cur.get("overrides") or {}).get(level) or {}
            t = p.text(
                f"Target for {level} roles (EUR per year, Enter = use the general target)",
                _s(old.get("target_eur")),
                integer("The target salary", minimum=1, optional=True),
            ).strip()
            if not t:
                continue
            m = p.text(
                f"Minimum for {level} roles (EUR per year, optional)",
                _s(old.get("min_eur")),
                integer("The minimum salary", minimum=1, optional=True),
            ).strip()
            overrides[level] = _clean({"target_eur": int(t), "min_eur": int(m) if m else None})
    if overrides:
        salary["overrides"] = overrides
    else:
        salary.pop("overrides", None)
    regions = {str(k): dict(v) for k, v in (cur.get("region_overrides") or {}).items()}
    if p.confirm("Set a different amount for jobs in a federal state (for example Bayern)?", default=bool(regions)):
        from autoapp.geo import STATES

        regions = {}
        pending = list(cur.get("region_overrides") or {})  # Enter walks through the saved ones, then finishes
        while True:
            state = p.select("Federal state (the job's location line must show it, a postcode or a known city)",
                             [("done", "Done")] + [(k, k.replace("ue", "ü").replace("-", " ").title()) for k in STATES],
                             default=pending[0] if pending else ("done" if regions else "bayern"))
            if state == "done":
                break
            if state in pending:
                pending.remove(state)
            old = (cur.get("region_overrides") or {}).get(state) or {}
            t = p.text(f"Target for jobs in {state} (EUR per year)", _s(old.get("target_eur")),
                       integer("The target salary", minimum=1)).strip()
            m = p.text(f"Minimum for jobs in {state} (EUR per year, optional; Enter = the general minimum)",
                       _s(old.get("min_eur")), integer("The minimum salary", minimum=1, optional=True)).strip()
            regions[state] = _clean({"target_eur": int(t), "min_eur": int(m) if m else None})
    if regions:
        salary["region_overrides"] = regions
    else:
        salary.pop("region_overrides", None)
    write_key(ws.profile_path, "salary", salary)
    return True


def reset_salary(ctx: SetupContext) -> None:
    drop_key(ctx.ws.profile_path, "salary")


# ---------------------------------------------------------------------------- search

_MODES = [("any", "any"), ("onsite", "on-site"), ("hybrid", "hybrid"), ("remote", "remote")]
_TIMES = [("any", "any"), ("full_time", "full time"), ("part_time", "part time")]
_CONTRACTS = [("any", "any"), ("permanent", "permanent"), ("fixed_term", "fixed-term")]


def _ask_profile(ctx: SetupContext, existing: dict[str, Any] | None, taken: set[str]) -> dict[str, Any]:
    """Questions for one search profile; only keywords are required."""
    p = ctx.prompter
    cur = dict(existing or {})
    rest = {k: v for k, v in cur.items() if k not in ("name", "keywords")}
    prof: dict[str, Any] = {"name": cur.get("name", ""), "keywords": cur.get("keywords", []), **rest}

    def kw_ok(value: str) -> str | None:
        return None if split_list(value) else "Enter at least one keyword (separate several with commas)."

    kws = split_list(
        p.text("Job title keywords, comma-separated (required)", ", ".join(cur.get("keywords") or []), kw_ok)
    )
    prof["keywords"] = kws

    def name_ok(value: str) -> str | None:
        v = value.strip()
        if not v:
            return "The profile needs a name."
        if v.lower() in taken:
            return f"There is already a search profile named '{v}'."
        return None

    prof["name"] = p.text("Name for this search profile", _s(cur.get("name")) or slugify(kws[0]), name_ok).strip()

    has_filters = any(cur.get(k) not in (None, [], "") for k in cur if k not in ("name", "keywords", "writing_instructions"))
    if not p.confirm("Set optional filters? (every filter can be left empty = no restriction)", default=has_filters):
        return prof

    def put(key: str, value: Any) -> None:
        if value in (None, "", []):
            prof.pop(key, None)
        else:
            prof[key] = value

    put("exclude_keywords", split_list(p.text("Exclude titles containing (comma-separated, optional)",
                                              ", ".join(cur.get("exclude_keywords") or []))))
    put("exclude_companies", split_list(p.text("Exclude companies (comma-separated, optional)",
                                               ", ".join(cur.get("exclude_companies") or []))))
    cur_locs = [dict(x) for x in cur.get("locations") or [] if isinstance(x, dict)]
    names = split_list(p.text("Locations (comma-separated, empty = anywhere in Germany)",
                              ", ".join(_s(x.get("name")) for x in cur_locs)))
    if names:
        old_radius = _s(cur_locs[0].get("radius_km")) if cur_locs else ""
        radius = p.text(
            "Search radius in km around the locations (empty = no radius)",
            old_radius,
            integer("The radius", minimum=0, optional=True, unit="kilometres"),
        ).strip()
        old = {_s(x.get("name")).lower(): x for x in cur_locs}
        locs = []
        for n in names:
            prior = old.get(n.lower())
            if prior is not None and radius == old_radius:
                locs.append(prior)
            else:
                locs.append(_clean({"name": n, "radius_km": int(radius) if radius else None}))
        put("locations", locs)
    else:
        put("locations", [])

    for key, label, choices in (
        ("work_mode", "Work mode", _MODES),
        ("working_time", "Working time", _TIMES),
        ("contract_type", "Contract type", _CONTRACTS),
    ):
        choice = p.select(label, choices, default=_s(cur.get(key)) or "any")
        if choice == "any" and key not in cur:
            prof.pop(key, None)
        else:
            prof[key] = choice
    put("exclude_position_types", split_list(p.text("Exclude position types, e.g. Trainee (comma-separated, optional)",
                                                    ", ".join(cur.get("exclude_position_types") or []))))
    put("experience_levels", split_list(p.text("Experience levels, e.g. Mid, Senior (comma-separated, optional)",
                                               ", ".join(cur.get("experience_levels") or []))))
    age = p.text("Only jobs posted within this many days (optional)", _s(cur.get("max_posting_age_days")),
                 integer("The number of days", minimum=1, optional=True, unit="days")).strip()
    put("max_posting_age_days", int(age) if age else None)
    sal = p.text("Minimum yearly salary in EUR stated in the ad (optional)", _s(cur.get("min_salary")),
                 integer("The salary", minimum=0, optional=True, unit="euros")).strip()
    put("min_salary", int(sal) if sal else None)
    agencies = p.confirm("Skip recruitment agencies and temp work?", default=bool(cur.get("exclude_agencies", False)))
    if agencies or "exclude_agencies" in cur:
        prof["exclude_agencies"] = agencies
    return prof


def run_search(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    current = [dict(x) for x in _cfg(ctx).get("search_profiles") or [] if isinstance(x, dict)]
    p.say("A search profile is a saved search. Only the keywords are required; every filter is optional.")
    result: list[dict[str, Any]] = []
    names = {_s(x.get("name")).lower() for x in current}
    for prof in current:
        name = _s(prof.get("name"))
        action = p.select(
            f"Search profile '{name}'",
            [("keep", "Keep as it is"), ("edit", "Edit"), ("delete", "Delete")],
            default="keep",
        )
        if action == "keep":
            result.append(prof)
        elif action == "edit":
            names.discard(name.lower())
            edited = _ask_profile(ctx, prof, names)
            names.add(edited["name"].lower())
            result.append(edited)
        else:
            names.discard(name.lower())
    while True:
        if result and not p.confirm("Add another search profile?", default=False):
            break
        new = _ask_profile(ctx, None, names)
        names.add(new["name"].lower())
        result.append(new)
    for item in result:
        SearchProfile.model_validate(item)  # fail loudly on a bug rather than write a bad file
    write_key(ws.config_path, "search_profiles", result)
    return True


def reset_search(ctx: SetupContext) -> None:
    write_key(ctx.ws.config_path, "search_profiles", [])


# ------------------------------------------------------------------------ mail account


def _address_validator(value: str) -> str | None:
    try:
        MailAccount(preset="gmail", address=value.strip())
    except ValidationError as exc:
        return _model_error(exc)
    return None


def _port_default(tls: str, kind: str) -> str:
    if kind == "imap":
        return "993" if tls == "ssl" else "143"
    return {"ssl": "465", "starttls": "587", "none": "25"}[tls]


def _ask_imap_fields(ctx: SetupContext, acct: dict[str, Any], host_default: str = "") -> None:
    p = ctx.prompter
    acct["imap_host"] = p.text("IMAP host", _s(acct.get("imap_host")) or host_default, required("The IMAP host")).strip()
    tls = p.select("IMAP encryption", TLS_CHOICES, default=_s(acct.get("imap_tls")) or "ssl")
    acct["imap_tls"] = tls
    port = p.text("IMAP port", _s(acct.get("imap_port")) or _port_default(tls, "imap"),
                  integer("The port", minimum=1, maximum=65535))
    acct["imap_port"] = int(port.strip())


def _ask_smtp_fields(ctx: SetupContext, acct: dict[str, Any]) -> None:
    p = ctx.prompter
    acct["smtp_host"] = p.text("SMTP host", _s(acct.get("smtp_host")), required("The SMTP host")).strip()
    tls = p.select("SMTP encryption", TLS_CHOICES, default=_s(acct.get("smtp_tls")) or "ssl")
    acct["smtp_tls"] = tls
    port = p.text("SMTP port", _s(acct.get("smtp_port")) or _port_default(tls, "smtp"),
                  integer("The port", minimum=1, maximum=65535))
    acct["smtp_port"] = int(port.strip())
    acct["sent_folder"] = p.text("Folder for copies of sent mail", _s(acct.get("sent_folder")) or "Sent",
                                 required("The folder name")).strip()


def confirm_bridge_fingerprint(ctx: SetupContext, acct: dict[str, Any]) -> None:
    """Show the local Bridge certificate fingerprint and pin it only after the user confirms."""
    p = ctx.prompter
    port = int(acct.get("imap_port") or 1143)
    while True:
        try:
            fp = normalize_fingerprint(ctx.checks.bridge_fingerprint("127.0.0.1", port))
            break
        except Exception as exc:  # noqa: BLE001 - Bridge not running, wrong port, ...
            p.say(f"Could not read the Bridge certificate: {exc}")
            p.say("Is Proton Mail Bridge running and logged in?")
            if not p.confirm("Try again?", default=True):
                p.say("No certificate pinned. Run `autoapp setup` again once Bridge is running.")
                return
    stored = normalize_fingerprint(_s(acct.get("bridge_cert_sha256")))
    p.say("Proton Mail Bridge uses a self-signed certificate. Its SHA-256 fingerprint is:")
    p.say("  " + ":".join(fp[i : i + 2] for i in range(0, len(fp), 2)).upper())
    p.say("Only accept it if Bridge is running on this computer. autoapp will refuse any other certificate.")
    if stored and stored != fp:
        p.say("WARNING: this differs from the fingerprint stored earlier (expected after reinstalling Bridge).")
    if p.confirm("Pin this certificate?", default=not stored or stored == fp):
        acct["bridge_cert_sha256"] = fp
    else:
        p.say("Not pinned" + ("; the stored fingerprint is kept." if stored else "; connections will be refused."))


def ask_mail_account(
    ctx: SetupContext, *, purpose: str, current: dict[str, Any] | None, default_env: str
) -> dict[str, Any]:
    """Preset, address, hosts and password for one mailbox. The password goes to ``.env`` only."""
    p = ctx.prompter
    cur = dict(current or {})
    while True:
        preset = p.select("Mail provider", MAIL_PRESETS, default=_s(cur.get("preset")) or "gmail")
        p.say(PREREQUISITES[preset])
        acct: dict[str, Any] = dict(cur) if cur.get("preset") == preset else {"preset": preset}
        acct["address"] = p.text("Email address", _s(cur.get("address")), _address_validator).strip()
        if preset == "proton_bridge":
            user = p.text("Bridge username (Enter = the email address)", _s(acct.get("username")) or acct["address"],
                          required("The username")).strip()
            if user == acct["address"]:
                acct.pop("username", None)
            else:
                acct["username"] = user
        elif preset == "custom":
            if purpose == "sending":
                _ask_smtp_fields(ctx, acct)
                host = _s(acct.get("smtp_host"))
                _ask_imap_fields(ctx, acct, host.replace("smtp", "imap", 1))
            else:
                _ask_imap_fields(ctx, acct)
            user = p.text("Login name (Enter = the email address)", _s(acct.get("username")) or acct["address"],
                          required("The login name")).strip()
            if user == acct["address"]:
                acct.pop("username", None)
            else:
                acct["username"] = user
        acct["password_env"] = _s(acct.get("password_env")) or default_env
        try:
            MailAccount.model_validate(acct)
        except ValidationError as exc:
            p.say(f"! {_model_error(exc)}")
            continue
        break
    label = f"{PASSWORD_LABELS[acct['preset']]} for {acct['address']}"
    ask_secret(ctx, label, acct["password_env"], strip_spaces=acct["preset"] == "gmail")
    if acct["preset"] == "proton_bridge":
        confirm_bridge_fingerprint(ctx, acct)
    return acct


def _password_of(ctx: SetupContext, acct: dict[str, Any]) -> str:
    return env_values(ctx.ws).get(_s(acct.get("password_env")), "")


# ------------------------------------------------------------------------- sources


def run_sources(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    cfg = _cfg(ctx)
    sources = {k: dict(v) if isinstance(v, dict) else v for k, v in (cfg.get("sources") or {}).items()}
    detail = dict(cfg.get("detail_fetch") or {})
    mail = dict(cfg.get("mail") or {})

    p.say("Arbeitsagentur (the German federal job board) is always searched and needs no key.")
    p.say("Adzuna is optional and needs free API keys from https://developer.adzuna.com/")
    have = has_secret(ws, "ADZUNA_APP_ID") and has_secret(ws, "ADZUNA_APP_KEY")
    if p.confirm("Use Adzuna?", default=have):
        ask_secret(ctx, "Adzuna app ID", "ADZUNA_APP_ID")
        ask_secret(ctx, "Adzuna app key", "ADZUNA_APP_KEY")
        adzuna_on = has_secret(ws, "ADZUNA_APP_ID") and has_secret(ws, "ADZUNA_APP_KEY")
        if not adzuna_on:
            p.say("Adzuna stays off until both keys are saved.")
    else:
        adzuna_on = False
    sources.setdefault("arbeitsagentur", {"enabled": True})
    sources["adzuna"] = {**(sources.get("adzuna") or {}), "enabled": adzuna_on}

    cur_alerts = dict(sources.get("alerts") or {})
    cur_acct = mail.get("alerts") if isinstance(mail.get("alerts"), dict) else None
    p.say("Job alerts: LinkedIn and StepStone can email you new matches. autoapp reads those emails from a")
    p.say("mailbox folder (read-only: nothing is moved, deleted or marked as read).")
    use_alerts = p.confirm(
        "Read job-alert emails from a mailbox?",
        default=bool(cur_acct) and bool(cur_alerts.get("enabled", True)),
    )
    acct: dict[str, Any] = dict(cur_acct or {})
    if use_alerts:
        acct = ask_mail_account(ctx, purpose="alerts", current=cur_acct, default_env="AUTOAPP_ALERTS_PASSWORD")
        folder = p.text("Folder or label that holds the alert emails", _s(cur_alerts.get("folder")) or "Jobalerts",
                        required("The folder")).strip()
        p.say("How should an alert job get its full ad? Search first: the app looks for the employer's own ad (and")
        p.say("its careers page) with a web search and never opens LinkedIn, Indeed, StepStone or Workwise job pages.")
        p.say("Platform: the job board's page is read slowly, as before; the sites often block that.")
        alert_mode = p.select("How should alert jobs get their full ad?", [
            ("search_first", "Search first: find the employer's own ad by search (recommended; needs a Parallel Search key)"),
            ("platform", "Platform: read the job board's page (often blocked)"),
        ], default=_s(cur_alerts.get("mode")) or "search_first")
        p.say("Some alert emails cannot be read by the built-in parsers (a new layout, a card without the employer's")
        p.say("name). The AI can read just that card's text, with your name, address and tracking links removed first.")
        llm_fallback = p.confirm("Let the AI read alert emails and cards the parsers cannot read?",
                                 default=bool(cur_alerts.get("llm_fallback", True)))
        sources["alerts"] = {**cur_alerts, "enabled": True, "folder": folder, "mode": alert_mode,
                             "llm_fallback": llm_fallback}
        mail["alerts"] = acct
    else:
        alert_mode = _s(cur_alerts.get("mode")) or "search_first"
        sources["alerts"] = {**cur_alerts, "enabled": False, "mode": alert_mode}
    search_first_alerts = use_alerts and alert_mode == "search_first"

    p.say("Some job pages (LinkedIn, Indeed, StepStone) are fetched to read the full description. This is done")
    p.say("slowly with random delays; the sites may still block it.")
    for key, site in (("linkedin", "LinkedIn"), ("indeed", "Indeed"), ("stepstone", "StepStone")):
        detail[key] = p.confirm(f"Fetch {site} job pages for full descriptions?", default=bool(detail.get(key, True)))

    cur_emp = dict(sources.get("employer_careers") or {})
    p.say("Employer careers pages (optional): for jobs from alerts the tool can look up the employer's own careers")
    p.say("site (public pages only), read the original vacancy there and find other matching openings. It uses the")
    p.say("Parallel Search API (about $1 per 1,000 searches, at most 2 per new employer; the result is saved and")
    p.say("reused, so each employer is searched once). Sites already saved are scanned without a key.")
    use_emp = p.confirm("Look up employers' own careers pages for alert jobs?", default=bool(cur_emp.get("enabled", False)))
    if use_emp or search_first_alerts:
        needed = "needed for search-first alert jobs" if search_first_alerts else "optional"
        ask_secret(ctx, f"Parallel Search API key ({needed}; without it only saved sites are scanned)",
                   "PARALLEL_API_KEY")
        if not has_secret(ws, "PARALLEL_API_KEY"):
            p.say("No key saved: employers without a saved careers site are skipped"
                  + (", and alert jobs wait until a key is set." if search_first_alerts else "."))
    sources["employer_careers"] = {**cur_emp, "enabled": use_emp}

    cur_links = dict(sources.get("apply_links_with_login") or {})
    platforms = (("linkedin", "LinkedIn"), ("stepstone", "StepStone"), ("indeed", "Indeed"))
    p.say("LinkedIn, StepStone and Indeed do not show where a job is really applied to without a login, so the app")
    p.say("cannot follow their links. The default way past them is the employer's own careers site (above). The app")
    p.say("can also open the job page with YOUR login (in its own browser) to read the 'apply on company website'")
    p.say("address. That is against these sites' terms: the risk for your account is low at the app's slow pace, but")
    p.say("not zero. It never uses Easy Apply, never types and never sends anything there.")
    had = [key for key, _ in platforms if cur_links.get(key)]
    mode = p.select("How should the app find the company apply links for LinkedIn, StepStone and Indeed jobs?", [
        ("employer", "Only through the employer's own careers site (recommended, no account risk)"),
        ("login", "Also read them from the job page with my login (choose the sites next)"),
    ], default="login" if had else "employer")
    chosen = p.checkbox("Read company apply links with your login on", list(platforms),
                        defaults=had or ["linkedin"]) if mode == "login" else []
    sources["apply_links_with_login"] = {**cur_links, **{key: key in chosen for key, _ in platforms}}
    if chosen:
        p.say("Log in once in the app's browser for each of them: `uv run autoapp login linkedin` (or stepstone,")
        p.say("indeed). The login is kept in workspace/browser-profile/.")

    cur_ar = dict(sources.get("arbeitnow") or {})
    p.say("Arbeitnow is a free public jobs feed, with no API key. Searches read a bounded recent window,")
    p.say("exclude foreign editions and match your profiles locally. Local listings link back to Arbeitnow")
    p.say("(https://www.arbeitnow.com/terms). Unclear location/remote eligibility still needs review.")
    sources["arbeitnow"] = {
        **cur_ar, "enabled": p.confirm("Use Arbeitnow?", default=bool(cur_ar.get("enabled", False))),
    }

    cur_ww = dict(sources.get("workwise") or {})
    p.say("Workwise (workwise.io) is searched with its public job search, without a key or login. Applications on")
    p.say("Workwise need a Workwise account: log in once with `uv run autoapp login workwise`. Workwise alert emails")
    p.say("in your alert folder are read too.")
    sources["workwise"] = {**cur_ww, "enabled": p.confirm("Use Workwise?", default=bool(cur_ww.get("enabled", True)))}

    def persist() -> None:
        write_key(ws.config_path, "sources", sources)
        write_key(ws.config_path, "detail_fetch", detail)
        if use_alerts:
            write_key(ws.config_path, "mail", mail)

    persist()

    if p.confirm("Test the connections now?", default=True):
        _check_loop(ctx, "Arbeitsagentur API reachable", ctx.checks.arbeitsagentur, None)
        if search_first_alerts and not has_secret(ws, "PARALLEL_API_KEY"):
            p.say("Note: search-first alert handling needs PARALLEL_API_KEY; without it alert jobs wait for a key.")
        if use_alerts:
            state = {"acct": acct}

            def check() -> CheckResult:
                folder = _s(sources["alerts"].get("folder"))
                return ctx.checks.imap(MailAccount.model_validate(state["acct"]), _password_of(ctx, state["acct"]), folder)

            def reenter() -> None:
                state["acct"] = ask_mail_account(
                    ctx, purpose="alerts", current=state["acct"], default_env="AUTOAPP_ALERTS_PASSWORD"
                )
                mail["alerts"] = state["acct"]
                persist()

            _check_loop(ctx, "IMAP login and alert folder", check, reenter)
    return True


def reset_sources(ctx: SetupContext) -> None:
    ws = ctx.ws
    write_key(ws.config_path, "sources", Sources().model_dump(mode="json"))
    write_key(ws.config_path, "detail_fetch", DetailFetch().model_dump(mode="json"))
    mail = dict(_cfg(ctx).get("mail") or {})
    if mail.pop("alerts", None) is not None:
        write_or_drop(ws.config_path, "mail", mail)


# ----------------------------------------------------------------------------- llm


def _cost(tokens: tuple[int, int, int], price: Price) -> float:
    cached, uncached, out = tokens
    cached_price = price.cached_input if price.cached_input is not None else price.input
    return (uncached * price.input + cached * cached_price + out * price.output) / 1_000_000


def _money(value: float) -> str:
    return "<$0.001" if value < 0.0005 else f"${value:.3f}"


def cost_rows(prices: dict[str, Price]) -> list[list[str]]:
    """Rows ``[model, prices, per evaluated job, per application, typical day]``, cheapest first."""
    evaluated, prepared = TYPICAL_DAY
    rows: list[tuple[float, list[str]]] = []
    for model, price in prices.items():
        ev = _cost(EVAL_TOKENS, price)
        wr = _cost(WRITE_TOKENS, price)
        cached = price.cached_input if price.cached_input is not None else price.input
        day = evaluated * ev + prepared * wr
        rows.append(
            (
                ev + wr,
                [
                    model,
                    f"{price.input:g} / {price.output:g} / {cached:g}",
                    _money(ev),
                    _money(ev + wr),
                    f"${day:.2f}",
                ],
            )
        )
    return [r for _, r in sorted(rows, key=lambda x: x[0])]


def _price_table(llm: dict[str, Any]) -> PriceTable:
    try:
        return PriceTable.load(LLMSettings.model_validate(llm).prices)
    except ValidationError:
        return PriceTable.load()


def show_cost_table(ctx: SetupContext, llm: dict[str, Any]) -> None:
    table = _price_table(llm)
    evaluated, prepared = TYPICAL_DAY
    ctx.prompter.table(
        "Estimated LLM cost in USD per job",
        ["Model", "Price in / out / cached per 1M tokens", "Per evaluated job", "Per prepared application",
         f"Typical day ({evaluated} evaluated, {prepared} prepared)"],
        cost_rows(table.prices),
    )
    ctx.prompter.say(
        f"Assumes about {EVAL_TOKENS[0] + EVAL_TOKENS[1]} input and {EVAL_TOKENS[2]} output tokens per evaluation and "
        f"{WRITE_TOKENS[0] + WRITE_TOKENS[1]} / {WRITE_TOKENS[2]} for the letter (with a cached prefix). Prices checked "
        f"{table.checked or 'n/a'}; override them under llm.prices in config.yaml. Reasoning tokens count as output."
    )


def _llm_providers(settings: LLMSettings) -> list[str]:
    out: list[str] = []
    for task in TASK_NAMES:
        prov = settings.resolve(task)[0]
        if prov not in out:
            out.append(prov)
    return out


TASK_QUESTIONS = (
    ("evaluate", "judging each job against your CV (many calls, so a cheap model works well)"),
    ("write", "writing the cover letters (few calls, so a strong writer is worth it)"),
    ("form_fill", "answering free-text questions when `assist` fills a web form"),
    ("form_find", "finding the real application page behind a job link"),
    ("judge", "auditing every twentieth evaluation against the job and CV (5% of evaluations)"),
)


def run_llm(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    llm = dict(_cfg(ctx).get("llm") or {})
    show_cost_table(ctx, llm)
    p.say("Every task has its own model. The first answer is offered for the next tasks; press Enter to accept it.")

    saved = {k: dict(v) for k, v in (llm.get("tasks") or {}).items() if isinstance(v, dict)}
    tasks: dict[str, dict[str, Any]] = {}
    last_provider, last_model = "", ""
    for task, label in TASK_QUESTIONS:
        current = saved.get(task) or {}
        # What is already in config.yaml wins; otherwise the previous task's answer, otherwise the built-in default.
        provider0 = _s(current.get("provider")) or last_provider or DEFAULT_PROVIDER
        model0 = _s(current.get("model")) or (last_model if provider0 == last_provider else "") or DEFAULT_MODELS[provider0]
        p.say(f"--- {task}: {label}")
        provider = p.select(f"Provider for {task}", [(k, v) for k, v in PROVIDER_LABELS.items()], default=provider0)
        known = [m for m in _price_table(llm).prices if _belongs(m, provider)]
        if known:
            p.say("Models with a known price: " + ", ".join(known))
        if provider != provider0:
            model0 = last_model if provider == last_provider else DEFAULT_MODELS[provider]
        model = p.text(f"Model for {task}", model0, required("The model")).strip()
        entry = {"provider": provider, "model": model}
        for kept in ("effort", "instructions"):  # your own settings survive a re-run
            if current.get(kept):
                entry[kept] = current[kept]
        tasks[task] = entry
        last_provider, last_model = provider, model
    for key in ("provider", "model", "effort"):
        llm.pop(key, None)  # the old single default is replaced by the four tasks
    if saved.get("alert_extract"):  # not asked here: it follows the evaluate model unless set in config.yaml
        tasks["alert_extract"] = saved["alert_extract"]
    llm["tasks"] = tasks
    write_key(ws.config_path, "llm", llm)
    settings = LLMSettings.model_validate(llm)

    providers = _llm_providers(settings)
    for prov in providers:
        env_name = API_KEY_ENV[prov]
        if not ask_secret(ctx, f"{PROVIDER_LABELS[prov]} API key ({env_name})", env_name) and not has_secret(ws, env_name):
            p.say(f"No {PROVIDER_LABELS[prov]} key saved yet. Add it with `autoapp setup --section llm` or in .env.")

    if p.confirm("Test the API key with one small request?", default=True):
        seen: set[tuple[str, str]] = set()
        for task, _label in TASK_QUESTIONS:
            prov, mdl, _ = settings.resolve(task)
            if (prov, mdl) in seen:
                continue
            seen.add((prov, mdl))

            def check(task: str = task) -> CheckResult:
                return ctx.checks.llm(settings, task, env_values(ws))

            def reenter(prov: str = prov) -> None:
                env_name = API_KEY_ENV[prov]
                ask_secret(ctx, f"{PROVIDER_LABELS[prov]} API key ({env_name})", env_name)

            _check_loop(ctx, f"{PROVIDER_LABELS[prov]} model '{mdl}' answers", check, reenter)
    return True


def _belongs(model: str, provider: str) -> bool:
    if provider == "openrouter":
        return "/" in model
    if provider == "anthropic":
        return model.startswith("claude")
    return model.startswith("gemini")


def reset_llm(ctx: SetupContext) -> None:
    llm = dict(_cfg(ctx).get("llm") or {})
    for key in ("provider", "model", "effort"):
        llm.pop(key, None)
    saved = {k: v for k, v in (llm.get("tasks") or {}).items() if isinstance(v, dict)}
    llm["tasks"] = {task: {"provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL,
                           **({"instructions": saved[task]["instructions"]} if saved.get(task, {}).get("instructions") else {})}
                    for task, _ in TASK_QUESTIONS}  # the models reset; your own directions stay
    write_key(ctx.ws.config_path, "llm", llm)


# ---------------------------------------------------------------------------- email


def run_email(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    cfg = _cfg(ctx)
    mail = dict(cfg.get("mail") or {})
    policy = dict(cfg.get("email") or {})
    alerts = mail.get("alerts") if isinstance(mail.get("alerts"), dict) else None
    sending = mail.get("sending") if isinstance(mail.get("sending"), dict) else None
    defaults = EmailPolicy()

    p.say("To email applications yourself or automatically, autoapp needs a sending mailbox.")
    p.say("Without one it still prepares complete application folders for you to send by hand.")
    if not p.confirm("Set up a sending mailbox?", default=bool(sending or alerts)):
        if not sending:
            _write_default_mode(ctx, "review", policy)
        p.say("Skipped. Runs prepare application folders; nothing is sent or saved as a draft.")
        return True

    acct: dict[str, Any]
    reuse = False
    if alerts:
        same = bool(sending) and sending.get("address") == alerts.get("address") and sending.get("preset") == alerts.get("preset")
        reuse = p.confirm(f"Use the same account ({alerts.get('address')}) for sending?", default=same or not sending)
    if reuse and alerts:
        acct = dict(sending) if sending and sending.get("address") == alerts.get("address") else dict(alerts)
        acct["password_env"] = alerts.get("password_env") or "AUTOAPP_ALERTS_PASSWORD"
        if acct.get("preset") == "custom" and not acct.get("smtp_host"):
            p.say("Your alert account is a custom server; I also need its SMTP settings.")
            _ask_smtp_fields(ctx, acct)
        elif acct.get("preset") == "custom":
            acct.setdefault("sent_folder", "Sent")
    else:
        acct = ask_mail_account(ctx, purpose="sending", current=sending, default_env="AUTOAPP_SMTP_PASSWORD")
    mail["sending"] = acct
    write_key(ws.config_path, "mail", mail)

    cap = int(policy.get("daily_cap", defaults.daily_cap))
    score = int(policy.get("min_score", defaults.min_score))
    cooldown = int(policy.get("company_cooldown_days", defaults.company_cooldown_days))
    def explain() -> None:
        p.say("Safeguards for automatic sending (applications that fail them wait for your review):")
        p.say(f"  - at most {cap} emails per day")
        p.say(f"  - only jobs scored {score} or higher")
        p.say("  - addresses on free-mail providers (gmail.com, gmx.de, web.de, ...) go to manual review")
        p.say(f"  - no second email to the same company within {cooldown} days")

    explain()
    p.say("Automatic sending is off by default. Try a dry run first (`autoapp run --dry-run`) and read the")
    p.say("prepared applications before you turn it on.")
    if p.confirm("Adjust these limits?", default=False):
        cap = int(p.text("Maximum emails per day", str(cap), integer("The daily cap", minimum=0)).strip())
        score = int(p.text("Minimum score for automatic sending (0-100)", str(score),
                           integer("The minimum score", minimum=0, maximum=100)).strip())
        cooldown = int(p.text("Days before the same company may be emailed again", str(cooldown),
                              integer("The cooldown", minimum=0, unit="days")).strip())
        policy.update(daily_cap=cap, min_score=score, company_cooldown_days=cooldown)
        explain()
    current = ((cfg.get("run") or {}).get("default_mode") if isinstance(cfg.get("run"), dict) else None) or (
        "automatic" if policy.get("auto_send") else "review")
    p.say("Every run you start yourself asks how to handle emails. Scheduled runs use a default:")
    p.say("  - Review first: each email is saved as a draft in your mailbox; you read it and send it yourself.")
    p.say("  - Full automatic: emails that pass the safeguards above are sent to employers.")
    mode = p.select("Default run mode for scheduled runs", list(RUN_MODE_CHOICES), default=current)
    _write_default_mode(ctx, mode, policy)

    if p.confirm("Test the mail login now? (no email is sent)", default=True):
        state = {"acct": acct}

        def check() -> CheckResult:
            return ctx.checks.smtp(MailAccount.model_validate(state["acct"]), _password_of(ctx, state["acct"]))

        def reenter() -> None:
            state["acct"] = ask_mail_account(
                ctx, purpose="sending", current=state["acct"], default_env="AUTOAPP_SMTP_PASSWORD"
            )
            mail["sending"] = state["acct"]
            write_key(ws.config_path, "mail", mail)

        _check_loop(ctx, "SMTP login", check, reenter)
    return True


RUN_MODE_CHOICES = (
    ("review", "Review first: save emails as drafts (recommended)"),
    ("automatic", "Full automatic: send emails that pass the safeguards"),
)


def _write_default_mode(ctx: SetupContext, mode: str, policy: dict[str, Any]) -> None:
    """Write ``run.default_mode`` and drop the old ``email.auto_send`` key."""
    ws = ctx.ws
    policy.pop("auto_send", None)
    write_or_drop(ws.config_path, "email", policy)
    run = dict(_cfg(ctx).get("run") or {})
    run["default_mode"] = mode
    write_key(ws.config_path, "run", run)


def reset_email(ctx: SetupContext) -> None:
    ws = ctx.ws
    cfg = _cfg(ctx)
    mail = dict(cfg.get("mail") or {})
    if mail.pop("sending", None) is not None:
        write_or_drop(ws.config_path, "mail", mail)
    policy = dict(cfg.get("email") or {})
    for key in ("daily_cap", "min_score", "company_cooldown_days"):
        policy.pop(key, None)
    _write_default_mode(ctx, "review", policy)


# --------------------------------------------------------------------------- writing


def run_writing(ctx: SetupContext) -> bool:
    p, ws = ctx.prompter, ctx.ws
    path = ws.writing_instructions_path
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    p.say("Your writing instructions steer every cover letter: tone, what to emphasise, what to avoid.")
    p.say(f"They are stored in {path.name} and you can edit that file at any time.")
    choices = [("inline", "Type them here"), ("editor", "Open them in $EDITOR"), ("keep" if existing.strip() else "skip",
                                                                                 "Keep what is there" if existing.strip() else "Skip for now")]
    action = p.select("Writing instructions", choices, default=choices[-1][0] if existing.strip() else "inline")
    if action == "inline":
        text = p.multiline("Writing instructions", existing)
        path.write_text(text.rstrip() + "\n" if text.strip() else "", encoding="utf-8")
    elif action == "editor":
        if not existing.strip():
            path.write_text(WRITING_TEMPLATE, encoding="utf-8")
        try:
            p.edit_file(path)
        except Exception as exc:  # noqa: BLE001 - no editor, editor failed, ...
            p.say(f"Could not run the editor ({exc}). Type the instructions here instead.")
            text = p.multiline("Writing instructions", path.read_text(encoding="utf-8"))
            path.write_text(text.rstrip() + "\n" if text.strip() else "", encoding="utf-8")

    cfg = _cfg(ctx)
    writing = dict(cfg.get("writing") or {})
    lang = p.select(
        "Letter language",
        [("auto", "same as the job ad"), ("de", "always German"), ("en", "always English")],
        default=_s(writing.get("language")) or "auto",
    )
    writing["language"] = lang
    write_key(ws.config_path, "writing", writing)

    docs = [dict(e) for e in _profile(ctx).get("documents") or [] if isinstance(e, dict)]
    pickable = [e for e in docs if e.get("role") in ("writing_sample", "other")]
    if not pickable:
        p.say("No document is available as a writing sample (run `autoapp setup --section documents` to declare files).")
    else:
        p.say("Writing samples (for example earlier cover letters) show the writer your own style.")
        picked = p.checkbox(
            "Which documents are writing samples?",
            [(_s(e.get("file")), _s(e.get("file"))) for e in pickable],
            defaults=[_s(e.get("file")) for e in pickable if e.get("role") == "writing_sample"],
        )
        for e in docs:
            if e in pickable:
                e["role"] = "writing_sample" if e.get("file") in picked else "other"
        write_key(ws.profile_path, "documents", docs)
    return True


def reset_writing(ctx: SetupContext) -> None:
    ws = ctx.ws
    ws.writing_instructions_path.unlink(missing_ok=True)
    writing = dict(_cfg(ctx).get("writing") or {})
    if writing.pop("language", None) is not None:
        write_or_drop(ws.config_path, "writing", writing)
    docs = [dict(e) for e in _profile(ctx).get("documents") or [] if isinstance(e, dict)]
    changed = False
    for e in docs:
        if e.get("role") == "writing_sample":
            e["role"] = "other"
            changed = True
    if changed:
        write_key(ws.profile_path, "documents", docs)


# -------------------------------------------------------------------------- registry

register(Section("documents", "Documents", "Map the files in documents/ to roles (CV, certificates, ...).",
                 run_documents, reset_documents, owns="profile.yaml: documents"))
register(Section("profile", "Profile", "Your personal data, availability, language levels and extra skills.",
                 run_profile, reset_profile,
                 owns="profile.yaml: personal, work_authorization, availability, language_levels, extra_skills"))
register(Section("salary", "Salary (optional)", "Salary expectations; you can skip this.",
                 run_salary, reset_salary, optional=True, owns="profile.yaml: salary"))
register(Section("search", "Job searches", "Search profiles: keywords and optional filters.",
                 run_search, reset_search, owns="config.yaml: search_profiles"))
register(Section("sources", "Job sources", "Adzuna, Workwise, the job-alert mailbox, detail pages and company apply links.",
                 run_sources, reset_sources,
                 owns="config.yaml: sources, detail_fetch, mail.alerts; .env keys are kept"))
register(Section("llm", "LLM provider", "Provider, models and API keys, with estimated costs.",
                 run_llm, reset_llm, owns="config.yaml: llm.tasks (evaluate, write, form_fill, form_find)"))
register(Section("email", "Email sending", "The sending mailbox and the safeguards for automatic sending.",
                 run_email, reset_email,
                 owns="config.yaml: mail.sending, run.default_mode, email.daily_cap/min_score/company_cooldown_days"))
register(Section("writing", "Cover letter writing", "Writing instructions and writing samples.",
                 run_writing, reset_writing,
                 owns="writing_instructions.md, config.yaml: writing.language, profile.yaml: writing_sample roles"))
