# Proposal

## Why

The goal is that every application points to the employer's own form. Two groups of jobs still miss it:

- Alert-email jobs. The search-first path already exists, but it throws the email text away after parsing, loses every job of an email when the parser finds zero cards, and gives up when the parser cannot name the company or title (for example Workwise cards that never name the employer).
- Arbeitsagentur jobs. They usually end up with an `arbeitsagentur.de` link as the "application page". The form finder starts from `job_url`, `arbeitsagentur.de` is not a restricted domain, the own-ad search is skipped when `externeURL` points to a partner job board such as jobvector.de, nothing tries the employer's careers pages when the own-ad search misses, and the employer's application email (`contact_fields`) from the detail call is never saved, so jobs that could be email applications are treated as form jobs.

This is Phase 1 of the employer-first applying work (requests 1, 2 and 3). It builds on the archived change `2026-10-11-search-first-alert-handling`.

## What Changes

- Alert emails keep their text: each card stores a redacted excerpt of its block (about 1,500 characters) and the `message_id` in `job_sources.raw`. Redaction removes the profile's name, address, email and phone, every email address, unsubscribe and tracking links, and the query string of every link.
- No more silent zero-card emails: an email from a known sender with zero cards (or a parser that raises) is not marked processed unless the LLM fallback has read it. The run warns with sender and subject.
- New LLM task `alert_extract`. It reads the redacted card text (or the whole redacted email) when the parser cannot name a card's company or title, or finds no card. Every value it returns must appear in the text it was given, otherwise it is dropped. Controlled by `sources.alerts.llm_fallback` (default `true`), `sources.alerts.max_llm_extractions_per_run` (default 20) and `sources.alerts.llm_unknown_senders` (default `false`, opt-in for alert-like emails from senders without a parser).
- When extraction still names no company, the redacted excerpt goes into the search itself (Parallel's `objective`). `judge()` relaxes only the company check: the company is taken from the page and must also be named in the excerpt. Such results carry `confidence: "from_email_text"` and are flagged `needs_review`, so automatic mode never sends them unseen.
- The run summary and quick test gain an `alerts:` line: cards, searched, found directly, via careers page, waiting (with reasons), and how many needed LLM extraction.
- Arbeitsagentur jobs resolve to the employer like alert jobs:
  - `arbeitsagentur.de` joins the default `assist.restricted_domains`, and an existing config that lists the domains explicitly gets it added in memory with a notice.
  - Partner boards named in `allianzpartnerName` (starting with jobvector.de) count as job boards, so those jobs are searched instead of skipped.
  - Arbeitsagentur jobs the own-ad search missed go to employer discovery (careers pages) after evaluation. Arbeitsagentur stays after evaluation, unlike alerts.
  - The detail call's `contact_fields` and `detail` are merged into the existing source row, so evaluation sees the employer's email and picks the `email` channel.
- New form status `no_employer_link` (label "employer ad not found") and folder marker `_NOEMPLOYER`. When the own-ad search and the careers lookup both ran and missed, the letter is still prepared, README, `job.md` and `apply.html` show the Arbeitsagentur page only as the job ad and never as the application page, and the job is retried within `failure_ttl_hours`. A job posted directly on the Arbeitsagentur keeps its Arbeitsagentur link, labelled "only on the Arbeitsagentur". A job whose lookups have not run yet (no key, cap, no company name) is never `no_employer_link`.
- `autoapp search` and `autoapp run` run the same own-ad pass (and the same Arbeitsagentur careers pass) for the same config.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `alert-resolution`: email excerpts, zero-card safety, LLM fallback, search by the alert's text, the `alerts:` coverage line, and `search`/`run` parity.
- `job-discovery`: the alert source no longer marks known-sender emails with no cards as processed silently; Arbeitsagentur contact details from the detail call are kept.
- `employer-career-discovery`: Arbeitsagentur jobs and partner boards; careers-page lookup for Arbeitsagentur jobs the own-ad search missed.
- `application-form-finding`: Arbeitsagentur is a restricted job board by default, the new status `no_employer_link`, and the rules for Arbeitsagentur entries and links.
- `application-package`: the `_NOEMPLOYER` folder marker and the README, `job.md` and `apply.html` wording for jobs without an employer link.
- `llm-providers`: the fifth task `alert_extract`.

## Impact

- Config: `src/autoapp/config/models.py` (`AlertsSource.llm_fallback`, `max_llm_extractions_per_run`, `llm_unknown_senders`; `DEFAULT_RESTRICTED_DOMAINS`; load-time notice), `src/autoapp/llm/settings.py` (task `alert_extract`), setup wizard (`src/autoapp/setup/sections.py`: asks whether the AI may read alert cards), `examples/config.example.yaml`, `docs/job-sources.md`, `docs/llm-providers.md`, `docs/usage-guide.md`.
- Alerts: `src/autoapp/sources/alerts/excerpt.py` (redaction, per-card excerpt), `imap_reader.py` (fallback hook, unprocessed list), `llm_extract.py` (`AlertExtractor`), `models.py`, `steps/search.py`, `discovery/ingest.py`.
- Employers: `employers/posting.py` (excerpt search, `judge` relaxation, `from_email_text`), `employers/scope.py` (`jobvector.de`, `is_partner_board`), `employers/service.py` (`own_ads_wanted`, `seed_arbeitsagentur_misses`, `_excerpt_postings`, summary line).
- Forms and package: `formfinder/employer_link.py` (new), `formfinder/store.py`, `formfinder/finder.py`, `formfinder/explain.py`, `package/folders.py`, `package/render.py`, templates, `steps/prepare.py`, `menu/main.py`, `commands.py`, `steps/pipeline.py`.
- Search and AI usage: `alert_extract` calls count on the shared meter, at most 20 per run by default. The excerpt search uses one search request per job within the existing caps.
- No database migration.
