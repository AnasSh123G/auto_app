# Proposal

## Why

Applying to jobs in Germany means repeating the same work for every listing on LinkedIn, Indeed and StepStone: searching with the same filters, reading the ad against your CV, writing a cover letter, assembling attachments, filling in forms, and then keeping track of what was sent. This change builds a local tool that automates as much of that as is technically and legally possible, and prepares everything else so that only the final click is left to you.

**Research summary:**
- LinkedIn's User Agreement and StepStone's terms both prohibit bots, scripts and scraping.
- Indeed shut down its public Publisher API in 2023. The Indeed connector for Claude only works inside the Claude app, and the Indeed and StepStone APIs that can submit applications are only available to approved recruiting-software (ATS) partners.
- Greenhouse and Lever, two recruiting systems that employers run, can accept applications by API, but only with each employer's private key.

A bot that clicks "Easy Apply" therefore risks a ban on the user's LinkedIn or Indeed account and is out of scope. There are two legitimate ways to send automatically:
1. **Email applications** ("Bewerbung per E-Mail an …"), which are still common in Germany. The tool sends these from the user's own mailbox.
2. **Assisted submission** for everything else. The tool opens the live form in a real browser, fills it in and attaches the files, and the user clicks Submit.

Pre-filling a *saved* copy of the page cannot work, for three reasons:
- Browsers do not let a page pre-attach files.
- The saved page's security token and session expire.
- Forms rendered by JavaScript do not work offline.

The tool therefore pre-fills the live page instead and adds a local "copy-paste" helper page to each application folder.

## What Changes

- **Onboarding wizard.** It starts on first launch and walks through documents, profile, salary, search, sources, AI model, email and writing instructions. Afterwards, `autoapp setup` can redo everything, redo or reset a single section, or start over. The previous settings are backed up first.
- **Documents folder.** One folder holds your CV variants (German and English), certificates (Zeugnisse), photo, optional writing samples and other attachments. A profile file holds your personal data, standard form answers, optional salary preferences, notice period, and extra skills that aren't on your CV.
- **Job search** with filters you set in the wizard or in YAML. Only keywords are required. Every other filter is optional and means "no restriction" when left empty: exclusions, location and radius, on-site/hybrid/remote, full-time or part-time, permanent or fixed-term, experience level, how recently the job was posted, minimum salary, and whether to exclude agencies (Zeitarbeit/Personalvermittlung). The sources are:
  - the Arbeitsagentur Jobsuche API
  - the Adzuna API (Germany)
  - parsing of the job-alert and job-match emails that LinkedIn, StepStone and Indeed send to your inbox
  - manual URL import

  The tool does not scrape the platforms' search pages.
- **Duplicate detection** across sources: the same job is recognised whether it arrives by API, by alert email or as a manual URL. The same role posted in several cities counts as one job, so you apply once.
- **AI fit evaluation** of each job against your CV and profile. It produces a score, an apply/maybe/skip decision, dealbreakers, how to apply (email, employer form or job-site form), the contact person, the documents the ad asks for, and the ad's language. Each requirement is classified as matched, *bridgeable* (basic-level and close to your skills) or missing, so a close match isn't rejected over a few basic tools.
- **Cover letters that read like a person wrote them:**
  - under one page (300 words by default)
  - no em/en dashes used as punctuation, and no stock AI phrases (German and English lists you can extend)
  - your own writing instructions, entered before automation starts, plus optional writing samples for your voice
  - CV facts presented in their strongest truthful form, and basic familiarity claimed with a few bridgeable requirements (capped, and listed so you can brush up before an interview)
  - never invented employers, titles, dates, degrees, certifications or numbers
- **An application folder for each suitable job**, `application_<company>_<title>_<id>/`, containing:
  - a snapshot of the job ad and the fit evaluation
  - the cover letter in the ad's language (editable Markdown and PDF)
  - the selected CV and attachments
  - a combined `Bewerbungsmappe.pdf` (cover letter, CV and certificates in one file)
  - prepared form answers (salary expectation, start date, motivation text)
  - an email draft (`.eml`)
  - `apply.html`, a local helper page with the application link and copy-to-clipboard buttons for each answer
- **Mail connections** through standard IMAP/SMTP logins: Gmail with an app password (the planned sending account), Proton Mail through Proton Mail Bridge (paid plan), or any other IMAP/SMTP server. The mailbox that receives job alerts and the one that sends applications can be different accounts. Outlook.com is not supported in this change, because Microsoft no longer allows password logins for third-party apps and requires its own sign-in.
- **Automatic email sending** (opt-in) for ads that accept applications by email. Safeguards:
  - a daily cap and a minimum fit score
  - no duplicate sends
  - the recipient address must appear verbatim in the ad
  - free-mail recipient addresses (e.g. Gmail) are not auto-sent
  - a dry-run mode
- **Assisted form filling.** `autoapp assist <id>` opens the application page in a browser that keeps your logins. When you ask, it fills the form fields and attaches the files, then leaves the Submit click to you. It is disabled on the LinkedIn, Indeed and StepStone domains.
- **A pluggable LLM layer** that works with Anthropic Claude, Google Gemini and OpenRouter (which also gives access to OpenAI, DeepSeek and Mistral models), with structured (JSON-schema) output, volume limits, and a cost estimate in every run summary.
- **Application tracking.** A SQLite database is the source of truth, exported to `applications.csv` with columns for company, job title, location, remote type, source, job link, application link, application channel, contact, the ad's salary range, your salary expectation, fit score, status and dates. The `status` and `notes` columns sync back into the database if you edit them in a spreadsheet.
- **Statistics page.** (The main menu that opens it is specified in the separate `add-menu-and-run-modes` change.) The statistics page shows the models in use, ads read, approved (high match score), emails sent and held back, forms saved, review queue, sources, estimated cost and the requirements you most often miss, for the latest run, today, 7 days, 30 days or all time. During `run` the same screen goes live: statistics about the run at the top and, at the bottom, a feed with one line for every email and form saved or sent. It can also be opened from another terminal while a scheduled run is active.
- **Test email.** `autoapp send --test` delivers a note ("this is what would have been sent to <address> in a real run") plus the exact application email and attachment to your own mailbox, so you see what an employer would see. Nothing is recorded and the employer is never contacted.
- **A command-line interface** (`setup`, `search`, `evaluate`, `prepare`, `render`, `send`, `assist`, `run`, `stats`, `status`, `list`, `mark`, `add`, `describe`, `export`) plus an example systemd user timer for unattended daily runs.

## Capabilities

### New Capabilities
- `setup-onboarding`: the first-run wizard, setup sections, redoing or resetting a section, full reset with backup, connection checks, and secret entry.
- `candidate-profile`: the documents folder, the classification of documents by role and language, CV text extraction, and the profile with personal data, standard answers, optional salary preferences and extra skills.
- `job-discovery`: search profiles with optional filters, the job sources (Arbeitsagentur, Adzuna, alert emails, manual URLs), fetching full job details, normalisation, duplicate detection and rule-based filtering.
- `llm-providers`: a provider-agnostic interface for structured and free-text generation, with Anthropic, Gemini and OpenRouter implementations, per-task model selection and usage limits.
- `job-evaluation`: the AI fit decision for each job, the classification of each requirement (matched, bridgeable, missing), and the extraction of application metadata (channel, contact, required documents, language, salary).
- `cover-letter-writing`: how letters and other generated text are written. Covers persuasive but truthful claims, assumed basic skills, the claim check, human style rules, the length limit, custom instructions, writing samples and German letter format.
- `application-package`: the per-job application folder, PDF rendering, the combined PDF, form answers, the salary expectation rule, the email draft and the `apply.html` helper.
- `email-submission`: automatic sending of email applications through the user's own SMTP account, with safeguards, a copy in the Sent folder and a send log.
- `assisted-form-fill`: browser automation that pre-fills the live application form and uploads documents, without ever clicking Submit.
- `application-tracking`: the persistent job and application state, the status lifecycle, CSV export with two-way sync of `status` and `notes`, and the status commands.
- `run-dashboard`: the statistics page, the live run view with its event feed, stored run events for watching a run from another terminal, and plain output without a terminal.
- `automation-pipeline`: the end-to-end `run` command, per-run limits, dry-run, logging and unattended scheduling.

### Modified Capabilities
None. This is a new project with no existing specs.

## Impact

- **New code.** A Python 3.12+ package (`autoapp`) managed with `uv`, plus a `tests/` suite and example configuration (`config.example.yaml`, `.env.example`).
- **Dependencies:**
  - LLM SDKs: `anthropic`, `google-genai`, and `openai` (used only as the OpenRouter client)
  - Command line and data: `typer`, `questionary` (wizard prompts), `pydantic`, `ruamel.yaml` (keeps comments when the wizard rewrites YAML), `httpx`, `jinja2`
  - Browser automation and PDF rendering: `playwright` (Chromium)
  - PDF and Word handling: `pypdf`, `python-docx`
  - Email HTML parsing: `beautifulsoup4`
  - CLI output: `rich`
  - Credentials loading: `python-dotenv`
- **External services:** the Arbeitsagentur REST API (public client ID, no account needed), the Adzuna API (free app ID and key), the user's Gmail account (app password) or another IMAP/SMTP mailbox, optionally Proton Mail Bridge running locally, and one or more LLM provider APIs (paid per use).
- **Privacy:** CVs, certificates, the browser profile, the database and generated applications contain personal data. They live in a workspace directory that is git-ignored, and all credentials are read from `.env` or environment variables.
- **Platform terms:** the tool does not automate search or submission on LinkedIn, Indeed or StepStone, and it never solves CAPTCHAs or tries to hide that it is automated. The remaining risk is fetching individual job pages linked from alert emails. This happens rarely, through the user's own browser profile, and stops the moment a site shows a bot challenge. It can be turned off in the config.
