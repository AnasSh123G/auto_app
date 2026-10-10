# Tasks

**Delegation:** every task ends with the model tier it suits. The main session works on `[opus]` tasks and reviews delegated output before checking a task off. It hands the rest to subagents, in parallel where tasks don't depend on each other:
- `[haiku]`: mechanical work (ignore files, example files, fixtures, word lists, unit files, README sections, simple golden tests).
- `[sonnet]`: standard implementation (adapters, CLI commands, storage, rendering, mail, wizard sections, and their tests).
- `[opus]`: work where judgment matters (LLM prompts and schemas, the claim and style policy, structured output and caching, assisted-fill safety, email eligibility and idempotency, end-to-end integration).

## 1. Project scaffolding

- [x] 1.1 Create the `uv` project (`pyproject.toml`, Python ≥3.12, `src/autoapp/` package, `autoapp` console script backed by a Typer app with placeholder subcommands). Verify: `uv run autoapp --help` lists the commands. `[sonnet]`
- [x] 1.2 Add runtime dependencies:
  - LLM SDKs: `anthropic`, `google-genai`, `openai`
  - CLI and wizard: `typer`, `questionary`, `rich`
  - Config and data: `pydantic`, `ruamel.yaml`, `httpx`, `jinja2`, `python-dotenv`
  - Browser and PDF: `playwright`, `pypdf`, `python-docx`, `beautifulsoup4`

  Add dev dependencies: `pytest`, `ruff`, `aiosmtpd`, `freezegun`. Verify: `uv sync` succeeds and `uv run playwright install chromium` completes. `[haiku]`
- [x] 1.3 Add `.gitignore` covering `workspace/`, `samples/` (raw alert emails contain personal data and StepStone login tokens), `.env`, `*.db`, `browser-profile/`, `.venv/`, caches and `logs/`. Verify: `git check-ignore workspace/x samples/x.eml .env` reports all three as ignored. `[haiku]`
- [x] 1.4 Configure pytest (markers `browser` and `live`; `live` skipped unless the provider's key env var is set, `browser` skipped if Chromium is missing) and ruff. Verify: `uv run pytest` runs a smoke test and `uv run ruff check` passes. `[haiku]`
- [x] 1.5 Extend `.github/workflows/copilot-setup-steps.yml` to install `uv`, run `uv sync` and `uv run playwright install --with-deps chromium`. Verify: the workflow YAML parses, and the steps run on a manual `workflow_dispatch`. `[haiku]`

## 2. Configuration and candidate profile

- [x] 2.1 Write Pydantic models for `config.yaml`:
  - search profiles: keywords required, every other filter optional with `None` meaning no restriction, optional extra writing instructions
  - `limits`, `llm` (with per-task overrides and price overrides), `email`, `detail_fetch`, `tracking`, `assist.restricted_domains`
  - `writing`: `max_words` (default 300), `max_assumed_skills` (default 5), `banned_phrases`, `allowed_phrases`, `language`
  - `setup` state

  Make startup validation report all errors at once. Verify: unit tests for a minimal config (only one profile with keywords), a profile without keywords, and multiple simultaneous errors. `[sonnet]`
- [x] 2.2 Write Pydantic models for `profile.yaml`: personal data, documents map (roles `cv`, `certificate`, `photo`, `writing_sample`, `other`, with language), standard answers, optional salary (target, optional minimum, seniority overrides), language levels, extra skills (name, level `basic|good|strong`, context), dealbreakers. Verify: unit tests for an invalid email (`max.mustermann@`), a profile with no salary, and extra skills parsing. `[sonnet]`
- [x] 2.3 Implement round-trip config IO with `ruamel.yaml`, which keeps the user's comments and hand edits when the program rewrites a section. Verify: a test edits one section of a commented YAML file, and every other line, comments included, is unchanged. `[sonnet]`
- [x] 2.4 Implement workspace resolution (`--workspace`, then `$AUTOAPP_WORKSPACE`, then `./workspace`), directory layout creation (including `backups/`), and `.env` loading from the workspace. Verify: tests using `tmp_path` cover each resolution path. `[sonnet]`
- [x] 2.5 Implement document loading:
  - stop with an error if the documents folder is missing
  - report unmapped files
  - require at least one CV
  - extract text from PDFs and DOCX files, including writing samples
  - give a clear error for an image-only CV

  Verify: tests with generated PDF, DOCX and image-only fixtures. `[sonnet]`
- [x] 2.6 Implement the file-name based role and language proposal used by onboarding (`Lebenslauf_de.pdf` → cv/de, `Zeugnis_2023.pdf` → certificate, names containing `anschreiben`/`cover` → writing_sample). Verify: golden tests over sample names. `[haiku]`
- [x] 2.7 Implement salary preference resolution (optional; seniority overrides). Verify: unit tests where target 60000 with a senior override of 75000 gives 75000, and where no salary configured resolves to "none". `[haiku]`
- [x] 2.8 Write `examples/config.example.yaml`, `examples/profile.example.yaml`, `examples/writing_instructions.example.md` and `examples/.env.example` with explanatory comments that mark the optional filters. Verify: the example files pass validation once placeholders are filled, in a test. `[haiku]`

## 3. Persistence and application tracking

- [x] 3.1 Create the SQLite schema (`jobs`, `job_sources`, `evaluations`, `applications`, `send_attempts`, `status_events`, `processed_emails`, `llm_usage`, `runs`) with migrations keyed on `PRAGMA user_version`. Verify: tests create a fresh database, re-open it idempotently, and migrate from version 0. `[sonnet]`
- [x] 3.2 Implement the status enum (the full lifecycle from the tracking spec), the `needs_review` flag with reasons, and repository functions: insert/upsert a job, attach a source, transition status with a `status_events` row, and query by status. Verify: tests show that the `approved → prepared → sent` history has three timestamped events, and that unknown statuses are rejected. `[sonnet]`
- [x] 3.3 Implement the workspace lock (`fcntl.flock` on `workspace/.lock`). Verify: a test shows a second acquisition fails immediately with a clear message. `[haiku]`
- [x] 3.4 Implement per-run log files in `workspace/logs/`, with a redaction filter that masks configured secret values, key-like tokens and any `magicLink=` value as `***`. Verify: tests log an error containing an API key and a URL containing `magicLink=eyJ…`, and assert both are masked. `[sonnet]`
- [x] 3.5 Implement CSV export:
  - all columns from the tracking spec, including `assumed_skills` and `needs_review`
  - UTF-8 with BOM and the configured delimiter (default `;`)
  - atomic write via a temp file and `os.replace`
  - rows only for jobs at `review` or later, unless `tracking.include_skipped` is set

  Verify: tests check the column order, that `Müller & Söhne GmbH` round-trips, and that the delimiter and the inclusion rule are applied. `[sonnet]`
- [x] 3.6 Implement two-way sync of `status` and `notes`, using the last exported values to detect user edits. Invalid statuses are ignored with a warning, and notes are still imported. Verify: tests cover a user edit (`sent → interview`), an invalid status (`maybe later`), and a stale CSV that does not overwrite a newer DB status. `[sonnet]`
- [x] 3.7 Implement `status` (counts per status, sent today, review queue including `needs_review`, estimated LLM cost of today and of the last run), `list --status <s>` and `mark <id> <status> [--note]`. Verify: Typer `CliRunner` tests, including `mark <id> rejected --note "Absage per Mail"` being reflected in the CSV. `[sonnet]`

## 4. LLM provider layer

- [x] 4.1 Define the `LLMClient` protocol (`structured`, `text`), the `Block` type (with a stable/cacheable flag), `Usage`, and the typed errors (auth, invalid request, transient). Add `UsageMeter`, which enforces `limits.max_llm_calls_per_run` and writes `llm_usage` rows. Verify: unit tests show the limit stops the 41st call when the limit is 40. `[opus]`
- [x] 4.2 Implement `FakeLLM`, which returns scripted objects or errors per task and counts calls. Verify: it is used by a self-test and is importable from the test helpers. `[sonnet]`
- [x] 4.3 Implement the shared validation-and-retry wrapper: Pydantic validation, one repair retry that includes the validation error, exponential backoff for 429/5xx honouring retry-after, and no retry for 401/400. Verify: FakeLLM tests cover a missing `fit_score` that is repaired, two invalid responses that fail the item, a 429 then success, and a 401 that stops immediately. `[sonnet]`
- [x] 4.4 Implement `AnthropicClient` with `messages.parse()`, `cache_control` on the stable system block, and configurable `effort`. Verify: a unit test with a stubbed SDK client asserts the request shape (cache marker, schema), plus a `live` test that evaluates a tiny sample. `[opus]`
- [x] 4.5 Implement `GeminiClient` using `google-genai` with `response_mime_type="application/json"` and `response_schema`. Verify: a stubbed request-shape test and a `live` test. `[sonnet]`
- [x] 4.6 Implement `OpenRouterClient` using the `openai` SDK with the OpenRouter `base_url`, `json_schema` response format, a `json_object` fallback, and a reasoning-effort pass-through for models that bill reasoning tokens. Verify: a stubbed test covers both modes, plus a `live` test. `[sonnet]`
- [x] 4.7 Implement the provider factory, which resolves the provider and model per task (`evaluate`, `write`, `form_fill`) with fallback to the default. A missing API key env var stops startup with an error naming that variable. Verify: tests cover the per-task override and a missing `GOOGLE_API_KEY`. `[sonnet]`
- [x] 4.8 Implement cost estimation: a shipped price table (`llm/prices.yaml`, per 1M input, output and cached-input tokens, with the date it was checked) that config can override; cost per call, per task and per run; "unknown (no price configured)" for unpriced models. Verify: unit tests for a priced run total and an unpriced model. `[sonnet]`
- [x] 4.9 Write a README "LLM providers and cost" section: env vars, per-task model config, the per-job cost table from design.md, and the privacy note (use paid tiers that do not train on inputs). Verify: the documented config snippets pass validation in a test. `[haiku]`

## 5. Job discovery

- [x] 5.1 Implement the normalised job model, URL canonicalisation (strip `utm_*`, `trk`, `refId`, `trackingId`, …) and the fingerprint (umlauts, gender tags such as `(m/w/d)`/`(w/m/d)`/`*in`, legal forms, whitespace). Verify: golden tests, including two titles that differ only in gender tags producing the same fingerprint. `[sonnet]`
- [x] 5.2 Implement the Arbeitsagentur adapter:
  - mapping of only the configured filters (`was` always; `wo`, `umkreis`, `veroeffentlichtseit`, `arbeitszeit`, `befristung`, `angebotsart`, `zeitarbeit`/`pav` only when set)
  - pagination
  - v4 detail fetch and mapping of description, location and any contact or `externeUrl` fields
  - storage of the raw payload

  Record real response fixtures once with a small script. Verify: `httpx.MockTransport` tests assert the parameters for a fully filtered profile and for a keywords-only profile (no location parameter, so all of Germany). `[sonnet]`
- [x] 5.3 Implement the Adzuna adapter (Germany endpoint, mapping of only the configured filters, salary range capture, `redirect_url` queued for detail fetch). It is skipped with `adzuna: skipped (not configured)` when there are no credentials. Verify: MockTransport tests for a 55000–70000 salary range, a profile without a salary filter (no `salary_min` sent), and the unconfigured case. `[sonnet]`
- [x] 5.3a Keep Adzuna inside the free tier: at least 2.6 s between requests, per-run/day/month request caps (config `sources.adzuna.max_requests_per_*`, counted in the `api_usage` table), a clean stop on HTTP 429 that keeps the jobs already found, and the reason shown in the run summary. Verify: tests for pacing, each cap, caps holding across runs, month rollover, and 429. `[sonnet]`
- [x] 5.4 Implement the IMAP alert reader:
  - connects with the `mail.alerts` account settings (same connection code as 9.1)
  - `EXAMINE` (read-only) on the configured folder
  - processed Message-IDs stored in `processed_emails`
  - unsupported senders skipped and counted as `unrecognised`

  Put it behind an interface with a fake mailbox. Verify: tests show an email is ingested once across two runs and that the mailbox is never modified. `[sonnet]`
- [x] 5.5 Build alert email fixtures from the real samples in `samples/`. Anonymise them: name, recipient address, LinkedIn headline, greeting, and every `click.stepstone.de`/tracking link replaced by deterministic dummies. Keep the card structure exactly. Add one synthetic zero-card fixture per parser (the real StepStone sample already covers truncated titles and a monthly salary). Verify: a test asserts the fixtures contain no `@gmail.com`/`@outlook.com` address, no `magicLink` and no original tracking path, and that each parses with the stdlib `email` module. `[sonnet]`
- [x] 5.6 Implement the parser registry (From-address matching, `AlertCard` model, unrecognised senders left unprocessed) and the LinkedIn parser (plain-text cards; title, company, city, badges, job ID). Verify: the LinkedIn fixture yields 6 cards with IDs `4476569290`, `4476973777`, `4475475300`, `4440002570`, `4440012224` and `4475402162`, an email from an unregistered sender is counted `unrecognised`, and the zero-card fixture triggers the "parser may be outdated" warning. `[sonnet]`
- [x] 5.7 Implement the StepStone parser: plain-text cards with markers, title, company, locations, size, contract type, work mode and working time, `salary_estimate` (€/Jahr or €/Monat × 12), and `section` (`match` before the "Andere Bewerber interessierten sich auch für diese Jobs" line, `recommended` after it). Footer links ("Weitere passende Jobs anzeigen", e-mail settings, unsubscribe) are not cards. Verify: the fixture yields 8 `stepstone-alert` cards (inovex first, estimate 75000–90000; "Duales Studium …" with contract type `Ausbildung, Studium`) and 4 `stepstone-alert-recommended` cards (Bavaria Systems with a monthly estimate converted to 54000–66000), and no card from footer links. `[sonnet]`
- [x] 5.8 Implement the StepStone link resolver: one `GET` without following redirects, `returnUrl` parsed for `-(\d+)-inline.html`, canonical URL built, `Location` header discarded and never logged, the login URL never requested, failures marked `description_unavailable`. Verify: MockTransport tests for a normal 302, a missing `returnUrl`, and a 404, plus an assertion that no request goes to `/v2/magiclink/` and that captured logs contain no `magicLink` value. `[opus]`
- [x] 5.9 Implement `add <url>` (creates a `manual` job queued for detail fetch) and `describe <id>` (stores pasted text and makes the job eligible for evaluation). Verify: CLI tests. `[haiku]`
- [x] 5.10 Implement the detail extractor with JSON-LD `JobPosting` first, then per-site selectors, then the largest-text-block fallback, mapping `baseSalary`, `jobLocationType` and `datePosted`. Verify: HTML fixture tests for JSON-LD, selector-only and fallback pages. `[sonnet]`
- [x] 5.11 Implement `HttpFetcher`: honest User-Agent, per-host delay, stop on 403/429. Verify: MockTransport tests for normal fetch and blocking. `[sonnet]`
- [x] 5.12 Implement `BrowserFetcher`:
  - persistent profile, headless
  - sequential, with randomised delays (configurable; 0 in tests)
  - `detail_fetch.max_per_site` and per-site enable flags
  - challenge detection (403/429/999, CAPTCHA and "Just a moment" markers), which marks the job `description_unavailable` and disables the site for the rest of the run

  Verify: unit tests for the challenge detector, plus `browser`-marked tests against local fixture pages. `[sonnet]`
- [x] 5.13 Wire deduplication into ingestion (source ID, then canonical URL, then fingerprint), linking extra sources to the existing job. Add multi-city grouping (company + title within 30 days; primary city closest to the search location, else first seen; others stored in `job_locations`). Verify: a test where the same job arrives from Arbeitsagentur and a StepStone alert yields one job with two sources and one evaluation, and the LinkedIn fixture's two 4flow postings (Düsseldorf, Rüsselsheim) become one job with two locations. `[opus]`
- [x] 5.14 Implement the rule-based pre-filter (excluded title keywords, company blocklist, posting age, employer-stated salary maximum below the minimum salary, impossible work mode, excluded contract or position type, including data from alert cards before any page fetch), applying only configured rules and recording the reason. Platform salary estimates never filter. Verify: tests for `Werkstudent` → `filtered` with reason `excluded keyword: Werkstudent`, the company blocklist, and a profile without salary or location filters that drops nothing for those reasons. `[sonnet]`
- [x] 5.15 Implement the `search [--profile <name>]` command: loops over profiles and sources, tags jobs with the profile, isolates source failures, prints a per-source summary. Verify: a CLI test where Adzuna returns 503 while the other sources succeed, and a `--profile` filter test. `[sonnet]`
- [x] 5.16 Write a README "Job sources" section: Adzuna key setup, creating LinkedIn and StepStone job alerts, a Gmail filter that labels mail from `jobalerts-noreply@linkedin.com` and `*@jobagent.stepstone.de` as "Jobalerts", how to add a parser for another sender (e.g. Indeed: one module, one anonymised fixture, one registry entry), detail-fetch settings, and the residual-risk statement. Verify: the documented config passes validation in a test. `[haiku]`

- [x] 5.17 Add the Indeed parser from the real sample (offline `jk` decoding from `cts.indeed.com` payloads, footer profile lines ignored, postcode stripped), its anonymised fixtures and tests. Verify: `tests/alerts/test_indeed.py` passes and the fixtures contain no personal data. `[opus]`
## 6. Job evaluation

- [x] 6.1 Define the `Evaluation` schema and the prompt builder:
  - schema: per-requirement proficiency (`basic|intermediate|advanced|unspecified`) and status (`matched|bridgeable|missing`) with a core flag, dealbreakers, plus the application metadata fields
  - prompt: the stable cached system prefix (rules, profile with extra skills, CV), then the search-profile summary, then the delimited `<job_ad>` data with a treat-as-data instruction

  Verify: tests show that injection text in the ad appears only inside the delimiters and that the stable prefix is byte-identical across jobs. `[opus]`
- [x] 6.2 Enforce the bridgeable rules in code: `advanced`, `intermediate` or core requirements are never `bridgeable`; bridgeable items are never dealbreakers; extra skills from the profile count as matched. Verify: tests for basic Docker → bridgeable, "mehrjährige Erfahrung als Kubernetes-Administrator" → missing, and Java in a Java role → missing. `[opus]`
- [x] 6.3 Implement decision thresholds and the code-side dealbreaker re-check (e.g. a required German level above the profile's). Verify: tests show 68 with no dealbreakers gives `maybe` (thresholds 75/60), 90 with one dealbreaker gives `maybe`, and a C2 requirement against a B2 profile gives `skip`. `[sonnet]`
- [x] 6.4 Implement email grounding: keep `apply_email` only if it occurs verbatim (case-insensitive) in the description or the structured contact fields; otherwise clear it and set the channel away from `email`. Verify: tests for a grounded address (`jobs@muster-gmbh.de`) and a hallucinated one. `[sonnet]`
- [x] 6.5 Implement the `evaluate [--job <id>] [--force]` command:
  - status transitions `new` → `skipped` / `review` / `approved`
  - `evaluation_failed` after a failed repair retry
  - `--force` re-evaluates while keeping the previous model and score in history
  - per-run limit respected

  Verify: FakeLLM tests for each path. `[sonnet]`
- [x] 6.6 Support the review queue: `list --status review` and `mark <id> apply|skip`, so that `apply` moves the job to `approved`. Verify: a CLI test where a promoted job is picked up by the next `prepare`. `[haiku]`

## 7. Cover letter writing

- [x] 7.1 Create the phrase lists `writing/phrases_de.txt` and `writing/phrases_en.txt`. Seed them from design.md decision 9: AI tells and worn template openers only, never standard closings ("Mit freundlichen Grüßen", "Kind regards") or neutral words. Add a list of connector words that are checked for overuse rather than banned. Verify: a test asserts the lists contain no standard closing, and that each entry is lowercase and unique. `[haiku]`
- [x] 7.2 Implement the style linter:
  - em/en dash anywhere, and a spaced hyphen as punctuation; hyphens inside words allowed
  - listed phrases (built-in + `writing.banned_phrases` − `writing.allowed_phrases`), case-insensitive
  - exclamation marks and question marks in the body
  - more than 2 connector words per letter
  - body word count > `writing.max_words`

  Implement the deterministic dash fixer. Verify: tests for "Erfahrung mit — vor allem", "Backend-Entwicklung" (not flagged), "I am writing to express my interest", a user-added phrase, and the word count. `[sonnet]`
- [x] 7.3 Define the `CoverLetter` schema and the writer prompt:
  - schema: subject, salutation, paragraphs, closing, `motivation_short` ≤1000, `email_body`, and claims typed `cv|profile|instructions|assumed_basic` with evidence or a requirement reference
  - stable cached prefix: truth policy, persuasive-framing rules, assumed-skill policy with cap, style guide, letter format, profile, CV, writing samples (voice only, no copying), global writing instructions
  - variable part: job ad, the evaluation's requirements, per-search-profile instructions, language, `max_words`, salary when asked

  Verify: FakeLLM tests check that every part is present, and that the instructions come after the defaults but before the truth rules' final reminder. `[opus]`
- [x] 7.4 Implement the claim check:
  - `cv`, `profile` and `instructions` claims need evidence found in that source (whitespace and case normalised)
  - `assumed_basic` claims must reference a `bridgeable` requirement, the job's decision must be `apply`, and the count must be ≤ `max_assumed_skills`

  Verify: tests for the "5 Jahre Erfahrung mit AWS" case, basic Docker allowed, Kubernetes 5+ years rejected, the cap of 5 with 7 bridgeable requirements, and a Terraform fact taken from the instructions. `[opus]`
- [x] 7.5 Implement the writing loop: generate, then claim check plus style lint, then one combined rewrite listing all violations, then the dash fixer, with any remaining phrase or claim violations flagging `needs_review` with reasons. After the PDF render (group 8), one shorter rewrite (`max_words` × 0.85) if the letter exceeds one page. Verify: FakeLLM tests for a clean draft (1 call), a violating draft fixed by the rewrite (2 calls), and a persistent violation (flagged). `[opus]`
- [x] 7.6 Apply the same writer, rules and linter to `motivation_short`, `email_body` and assisted-fill free-text answers. Verify: tests show the linter runs on each, and a dash in the motivation text is fixed. `[sonnet]`
- [x] 7.7 Record assumed skills on the application: they feed the README "Before an interview, brush up on" list and the CSV `assumed_skills` column. Verify: a test where a Docker assumed skill appears in both. `[sonnet]`
- [x] 7.8 Write a README "Cover letters" section: what the writer may and may not claim, the bridgeable-skill policy and cap, the style rules, how to write effective instructions, how to add writing samples, and how to extend the phrase lists. Verify: the documented settings pass validation in a test. `[haiku]`

## 8. Application package

- [x] 8.1 Implement the slug and folder name builder (umlaut transliteration, length cap, `application_<company>_<title>_<shortid>`). Verify: a golden test where "Müller & Söhne GmbH" / "Software-Entwickler (m/w/d)" gives `application_mueller-soehne-gmbh_software-entwickler_a1b2c3`. `[haiku]`
- [x] 8.2 Implement the salary expectation rule (clamp into the ad range, raise to the minimum if set, round to 1,000; use the target when there is no range; no expectation when salary is not configured, with a README note if the ad asks). Verify: tests where target 72000, minimum 58000 and range 55000–65000 give 65000, plus the no-range case and the unconfigured case where the ad asks. `[sonnet]`
- [x] 8.3 Implement document selection: CV by letter language with fallback; certificates when requested or when `always_attach_certificates` is set; missing requested documents listed; writing samples never attached. Verify: tests for a German ad picking the German CV, a missing portfolio being listed, and writing samples excluded. `[sonnet]`
- [x] 8.4 Create Jinja2 templates:
  - `cover_letter.html` (DIN 5008 layout, German date "Berlin, 7. Oktober 2026", salutation rules)
  - `job.md`, `evaluation.md` (requirements by status), `form_answers.md`
  - `README.md` (next step, missing documents, `needs_review` reasons, brush-up list)
  - `apply.html` (offline, copy-to-clipboard buttons, file paths, links)

  Verify: snapshot tests, the "Sehr geehrte Damen und Herren," fallback, and a test that `apply.html` has no external script, style or font references. `[sonnet]`
- [x] 8.5 Implement PDF rendering with Playwright `page.pdf(format="A4")`: a page count fed back to the writing loop's one-page rule; a pypdf merge into `Bewerbungsmappe.pdf` in the order letter, CV, certificates; a size-limit warning; DOCX CVs excluded from the merge with a warning. Verify: `browser` tests check the page order, that the text is present, that the letter is exactly one page, and that the warning is triggered. `[sonnet]`
- [x] 8.6 Implement `email.eml` generation (recipient, subject with reference, the linted body in the ad's language, `Bewerbungsmappe.pdf` attached). Verify: a test parses the file with the stdlib `email` module and asserts all parts. `[haiku]`
- [x] 8.7 Implement the `prepare [--job <id>] [--force]` command: per-run limit, refusal without `--force` when the folder exists, timestamped backup of `cover_letter.md` on `--force`, status `approved → prepared`. Verify: CLI tests for the 14-approved / limit-10 case, the refusal, and the backup. `[sonnet]`
- [x] 8.8 Implement `render <id>`: rebuild `cover_letter.pdf`, `Bewerbungsmappe.pdf` and `email.eml` from an edited `cover_letter.md` with zero LLM calls (the user's edits are not linted or blocked). Verify: a test where an edited sentence appears in the PDF text and FakeLLM records 0 calls. `[sonnet]`
- [x] 8.9 Write a README "Application folders" section describing each file and the manual-apply workflow using `apply.html`. Verify: the documented file list matches a folder produced in a test. `[haiku]`

## 9. Email submission

- [x] 9.1 Implement the mail connection layer:
  - presets `gmail`, `proton_bridge`, `custom`, with overridable host, port and TLS mode
  - separate `mail.alerts` and `mail.sending` accounts
  - credentials from env
  - Bridge certificate fingerprint pinning, allowed only for localhost
  - Sent copy: APPEND only for `custom`
  - a fake mailer for tests

  Verify: an integration test against a local `aiosmtpd` server receives the message with its attachment; tests show no APPEND for `gmail`/`proton_bridge` and one APPEND for `custom`; a fingerprint-mismatch test refuses to connect. `[opus]`
- [x] 9.2 Implement the eligibility rules:
  - `auto_send` enabled
  - decision `apply` and channel `email`
  - grounded address
  - recipient not on the free-mail list (configurable; matches are flagged for review)
  - score at or above `min_score`
  - requested documents attached and size within the limit
  - not flagged `needs_review`

  Verify: table-driven tests covering every rejection reason. `[opus]`
- [x] 9.3 Implement the daily cap (Europe/Berlin day), the duplicate-job guard and the company cooldown. Verify: freezegun tests for the cap of 5, a duplicate send, and a 3-day-old send within a 14-day cooldown. `[sonnet]`
- [x] 9.4 Implement idempotent send attempts: a pre-generated Message-ID, a `pending` row before SMTP, `sent` after success, stale `pending` (>10 min) moved to `send_unconfirmed` and never auto-resent. Verify: a test simulates a crash after the pending row is written. `[opus]`
- [x] 9.5 Implement failure handling: SMTP auth failure stops all sends for the run, other failures keep the job `prepared` with the error recorded, and attempts are capped by `email.max_attempts`. Verify: tests with failing fake mailers. `[sonnet]`
- [x] 9.6 Implement the `send <id> [--yes]` command (shows recipient, subject and attachments, then asks for confirmation), the auto-send step, and the `--dry-run` "would send" reporting with no SMTP or IMAP contact. Verify: CLI tests, including a dry run that leaves statuses `prepared` and makes zero mailer calls. `[sonnet]`
- [x] 9.6a Implement `send [<id>] --test`: the prepared email goes to the sending mailbox's own address as one email, with the subject prefixed `TEST_RUN: ` and the first line "This email is what would be sent to <address> in a real run."; Cc/Bcc/Reply-To dropped; no send attempt, status change, cap or cooldown effect; without an ID the newest prepared email job is used, or a plain test mail when there is none. Verify: tests that only the own address receives it, the content and attachment match, the DB is untouched and the job can still be sent for real. `[sonnet]`
- [x] 9.7 Write a README "Email" section:
  - Gmail: 2-Step Verification and creating an app password
  - Proton: paid plan, installing Bridge, running `protonmail-bridge --cli` as a systemd user service
  - other IMAP/SMTP servers
  - why Outlook.com isn't supported yet
  - separate alert and sending mailboxes
  - safeguards and the recommended rollout (dry-run first, small cap)

  Verify: the documented config passes validation in a test. `[haiku]`

## 10. Onboarding and setup

- [x] 10.1 Implement the wizard framework: a section registry (`documents`, `profile`, `salary`, `search`, `sources`, `llm`, `email`, `writing`), each with its questions, immediate validators, a writer for its config/profile/`.env` keys, and defaults loaded from the current files. The skip option applies to optional sections and questions. Verify: tests drive a section with scripted answers, including an invalid radius `abc` that is re-asked. `[sonnet]`
- [x] 10.2 Implement the sections:
  - `documents`: mapping proposal from 2.6
  - `profile`: including extra skills
  - `salary`: skippable
  - `search`: one or more profiles; only keywords required; every filter skippable
  - `sources`: Adzuna; the alert mailbox (presets Gmail / Proton Bridge / Other, with prerequisites shown, Bridge fingerprint confirmation, and an Outlook.com address answered with "not supported yet") and folder; detail-fetch toggles
  - `llm`: provider and models per task; shows the per-job cost table
  - `email`: the sending mailbox (offer to reuse the alert account; same presets and app-password guidance); auto-send off by default after explaining the safeguards
  - `writing`: instructions inline or in `$EDITOR`, saved to `writing_instructions.md`; writing samples

  Verify: a scripted full run produces valid `config.yaml`, `profile.yaml`, `.env` and `writing_instructions.md`, and pressing Enter through `email` leaves `auto_send: false`. `[sonnet]`
- [x] 10.3 Implement secret entry: hidden input; secrets written only to `.env`; never echoed or written to YAML. Verify: a test asserts that no API key appears in `config.yaml`, `profile.yaml` or captured output. `[sonnet]`
- [x] 10.4 Implement connection checks at the end of `llm`, `sources` and `email`: a minimal LLM call; IMAP login and folder check; SMTP login without sending; Arbeitsagentur reachability. On failure, offer re-enter or continue. Verify: tests with fake clients for success and an IMAP auth failure. `[sonnet]`
- [x] 10.5 Implement `autoapp setup` entry points:
  - first run starts the wizard
  - after setup, a menu: redo all / redo section / reset section / cancel
  - `--section <name>`
  - `--reset <section>` with confirmation
  - `--reset all`, which backs up config, profile, writing instructions and `.env` to `backups/<timestamp>/` and leaves documents, the database and applications untouched

  Verify: tests show a section rerun leaves the other sections (including a hand-added search profile) unchanged, a full reset creates the backup and keeps `applications.csv`, and a declined reset changes nothing. `[sonnet]`
- [x] 10.6 Implement first-run gating for all commands: interactive commands start onboarding when setup is incomplete; non-interactive ones exit non-zero with "run `autoapp setup`". Verify: CLI tests for both modes. `[sonnet]`
- [x] 10.7 Write a README "Getting started" section around `autoapp setup`: the sections, rerunning and resetting, and where files live. Verify: following the section on a fresh directory completes setup. `[haiku]`

## 11. Assisted form fill

- [x] 11.1 Implement the field collector across all frames, with label resolution (`label[for]`, wrapping label, `aria-label`, `aria-labelledby`, placeholder, name, preceding text), type, required, maxlength and options. Verify: `browser` tests on a plain-form fixture and an iframe fixture. `[sonnet]`
- [x] 11.2 Create the HTML form fixtures: plain, iframe, multi-step, consent checkbox, separate CV and cover letter uploads, single generic upload, and a submit listener that records submits. Verify: each fixture opens in Playwright without errors. `[haiku]`
- [x] 11.3 Implement the deterministic German/English synonym mapper for personal data, salary, start date, notice period and profile links, plus the upload mapping (Lebenslauf/CV → CV, Anschreiben → `cover_letter.pdf`, Zeugnisse/weitere Dokumente → certificates, single generic upload → `Bewerbungsmappe.pdf`). Verify: unit tests over the label variants. `[sonnet]`
- [x] 11.4 Implement the LLM fallback mapping (`form_fill` task) for unmatched fields, with generated free text going through the writing rules from 7.6 and `maxlength` enforced. Verify: FakeLLM tests, including a 500-character limit on "Warum möchten Sie bei uns arbeiten?". `[opus]`
- [x] 11.5 Implement the filler:
  - `fill`, `select_option`, `check` for non-consent choices only, `set_input_files`
  - a consent-label guard that never ticks consent boxes
  - no code path that clicks buttons or links
  - green/red highlighting and a terminal summary

  Verify: `browser` tests show the privacy checkbox stays unticked and highlighted, the submit listener records zero submits, and the multi-step fixture fills step 2 after a second trigger. `[opus]`
- [x] 11.6 Implement the restricted-domain guard, checked on every fill trigger (defaults: linkedin.com, indeed.com, indeed.de, stepstone.de). Verify: a `browser` test routes a `linkedin.com` URL to a local fixture and asserts no fields are filled and the message points to `apply.html`. `[sonnet]`
- [x] 11.7 Implement the `assist <id>` command:
  - refuses unprepared jobs
  - opens a persistent headed context at the application URL (or the job URL)
  - terminal loop `[f] fill / [h] apply.html / [s] submitted / [q] quit`
  - asks on exit whether the application was submitted, and sets `submitted` with the date if so

  Verify: command-logic tests with a fake browser driver, plus a documented manual smoke test on a real employer ATS page. `[sonnet]`
- [x] 11.8 Write a README "Assisted applying" section: first-time logins in the persistent browser, what is and isn't filled, and the restricted domains. Verify: the manual smoke test steps work as written. `[haiku]`

## 12. Pipeline orchestration and scheduling

- [x] 12.1 Implement `run [--profile] [--dry-run] [--non-interactive]`:
  - steps: search → fetch → filter → evaluate → prepare → auto-send → export
  - per-step counts and the estimated LLM cost in the summary
  - per-run limits respected
  - no prompts in non-interactive mode or without a TTY
  - non-zero exit on config or auth errors, while the steps that need no email still complete

  Verify: an end-to-end test in a temporary workspace with MockTransport sources, FakeLLM and the fake mailer, including a bad-SMTP-credentials case. `[opus]`
- [x] 12.2 Add the `export` command, and refresh the CSV (with sync) at the end of every state-changing command. Verify: a test runs `mark` and then checks the CSV without an explicit export. `[haiku]`
- [x] 12.3 Integrate the workspace lock into all state-changing commands. Verify: a test where a concurrent second `run` exits immediately with the "another run is active" message. `[haiku]`
- [x] 12.4 Add `deploy/systemd/autoapp.service` (oneshot, `run --non-interactive`) and `autoapp.timer` (daily, `Persistent=true`, randomised delay), plus a README "Scheduling" section with install and enable commands and a cron alternative. Verify: `systemd-analyze --user verify deploy/systemd/autoapp.*` reports no errors. `[haiku]`
- [x] 12.5 Verify workspace privacy: after the end-to-end test writes to a repo-local `./workspace` and a `.env`, `git status --porcelain` shows nothing from them. Implement this as a test or a scripted check. `[haiku]`

## 13. Integration verification

- [x] 13.1 Run `autoapp setup` from scratch in the user's real workspace, then redo one section and reset one section. Confirm the files, the backups and the preserved settings match the onboarding spec. `[opus]`
- [x] 13.2 Run a live dry run in the user's real workspace: `autoapp run --dry-run` with one real search profile, the Arbeitsagentur source, the real alert folder and the configured LLM. Confirm the summary counts and cost estimate, at least one complete `application_*` folder (letter PDF, `Bewerbungsmappe.pdf`, `apply.html`, `email.eml` if applicable), CSV rows, and zero emails sent. `[opus]`
- [x] 13.3 Review 5 generated letters from 13.2 with the user against the writing spec: no dashes or listed phrases, one page, persuasive but truthful, assumed skills within the cap and listed in the README, and the writing instructions followed. Feed the corrections back into the prompt or the phrase lists. `[opus]`
- [x] 13.4 Run `autoapp assist <id>` on one prepared real application on an employer ATS page. Confirm that fields and uploads are filled, consent and submit are untouched, and the `submitted` status is recorded after the user submits manually. `[opus]`
- [x] 13.5 Run `openspec validate add-automated-job-applications --strict` and walk through each spec scenario, confirming it maps to at least one automated test or to the manual checks in 13.1–13.4. `[sonnet]`

## 14. Main menu, statistics page and live run view

- [x] 14.1 Add the `RunEvent` model, the `EventBus` and `RunStats` counters, migration 3 (`run_events`, `runs.progress`), and a database writer and log writer subscribed to the bus. Verify: unit tests that each event kind updates the right counter, is stored with its run, and that progress is written at most once per second. `[sonnet]`
- [x] 14.2 Emit events from the steps (search, fetch, evaluate, prepare, send, assist) with the company, title and a short message; add `--no-dashboard` to `run`. Verify: an end-to-end test with fakes asserts the exact event sequence for one approved email job (saved, then sent) and for a dry run (saved, then would send). `[sonnet]`
- [x] 14.3 Implement the statistics queries for the periods latest run, today, 7 days, 30 days and all time: models in use, funnel counts and approval share, average score, applications by channel, emails sent and held back with reasons, forms filled, review queue, per-source counts, Adzuna request budget, LLM calls, tokens, estimated cost and cost per prepared application, status counts, and the most common missing and bridgeable requirements. Verify: tests on a seeded database for each period, an empty database (all zeros), and a model without a price shown as unknown. `[sonnet]`
- [x] 14.4 Implement the statistics page renderer (`rich`) for a given width, including the compact layout under 100 columns, `NO_COLOR` and the period switch, and the `stats [--period]` command. Verify: snapshot tests render to a string at widths 60, 100 and 140 and check the labels and numbers. `[sonnet]`
- [x] 14.5 Implement the live run view: a top statistics panel and a bottom feed (200 lines kept, "emails and forms only" filter, "would send" wording and a "dry run" label in dry runs, final summary held until a key is pressed, then the plain summary printed). Verify: a test drives scripted events into the view and checks the rendered top counters and the order and wording of the feed lines. `[opus]`
- [x] 14.6 Implement the plain fallback (no TTY, `--non-interactive`, `--no-dashboard`, terminal under 60 columns) and the renderer-failure fallback. Verify: tests that a scheduled-style run prints one plain line per event with no control characters, and that an exception thrown by the renderer leaves the run results unchanged and is logged. `[sonnet]`
- [x] 14.7 Implement viewing a run from another process: read `runs.progress` and the newest events once per second without taking the lock, mark a run stale after 15 minutes. Verify: a test that starts a run in a thread, opens the viewer on the same database, sees counters and feed lines increase, and that closing the viewer does not stop the run. `[opus]`
- [x] 14.8 Expose the statistics page and the live view as plain functions (`show_stats(period)`, `watch_run()`) so a menu can call them; the main menu itself is specified and built by the `add-menu-and-run-modes` change. Verify: `stats` calls the same function that is exported. `[sonnet]`
- [x] 14.9 Add a privacy test that no password, API key or login token from the environment appears in any feed line or statistics output, and a README "Statistics and the main menu" section with a sample screen. Verify: the test plants fake secrets in the environment and a failing login, then greps the rendered output and the log. `[haiku]`
- [x] 14.10 Walk through each scenario of the `run-dashboard` spec and confirm it maps to an automated test, or to a manual check in the real terminal: watch a live `run --dry-run` and open the statistics page from a second terminal during the run. `[opus]`
