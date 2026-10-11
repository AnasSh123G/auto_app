# Design

## Context

The search-first flow from the archived change `2026-10-11-search-first-alert-handling` is the default. For an alert job it runs `find_alert_ads` (direct "company" "title" search), then `run_discovery` (employer website, careers pages), then fetch, evaluate and prepare. Gaps this change closes:

- `ingest_alerts` discards the email after parsing; only the card fields stay in `job_sources.raw`.
- A known sender whose email gives zero cards was only a "parser may be outdated" warning, and the email was marked processed. Every job in it was lost silently.
- A card with an unknown company (Workwise) was skipped by the search for good.
- For Arbeitsagentur jobs the form finder started from `job_url`, `arbeitsagentur.de` was not in `assist.restricted_domains`, partner boards (jobvector.de) were not in `DIRECTORY_HOSTS`, the own-ad search was skipped when `application_url` already held the partner's `externeURL`, nothing looked at the employer's careers pages when the own-ad search missed, and the detail call's `contact_fields` were dropped by the second `ingest`.
- `autoapp search` and `autoapp run` decided differently whether to run the own-ad pass.

## Goals / Non-Goals

**Goals:**
- Every alert job and every Arbeitsagentur job gets the employer's own ad through one path: direct search, then careers pages. A fallback to a job-board page is rare, explained and visible.
- No alert card and no alert email is dropped without a visible warning or an LLM attempt.
- Nothing from the email that identifies the user is stored or sent to a model or a search.

**Non-Goals:**
- No new search provider and no replacement of the parsers.
- No change to how forms are found (Phase 3) and no pre-filled offline forms (Phase 4).
- No change to the "the app never submits" guarantee.

## Decisions

1. **Excerpts are redacted before they are stored.** `sources/alerts/excerpt.py` builds a `Redactor` from the profile's `personal` block and the alert mailbox address. It removes the name, street, email and phone (any common spacing), every email address, unsubscribe and tracking links, and the query string of every other link. Job links stay, without query. A card's excerpt is its block text, cut at the email footer, at most 1,500 characters (`EXCERPT_CHARS`); an email with no card keeps up to 6,000 (`BODY_CHARS`). `job_sources.raw` gets `email_excerpt`, `message_id` and, for model-extracted cards, `llm_extracted`.
   *Alternative:* store the raw email and redact on use. Rejected because a stored raw email would hold personal data at rest.

2. **Zero-card handling in `ingest_alerts`.** A known sender with zero cards, or a parser that raises, becomes an `UnparsedEmail` (redacted body). It goes to the fallback hook. If the fallback returns cards they are ingested and the email is marked processed. If the fallback ran and found nothing, the email is marked processed with a warning (so it is not paid for in every run). If there is no fallback, it was skipped (`FallbackSkipped`, cap reached) or it failed, the email stays unprocessed with a warning naming sender and subject and is read again next run.
   *Alternative:* never mark a zero-card email processed. Rejected because a layout the model cannot read would cost one call per run forever.

3. **`alert_extract` is one more LLM task.** It follows the evaluate model unless `llm.tasks.alert_extract` is set, is not asked in the setup wizard (the wizard only asks whether the fallback is allowed), and counts on the shared meter. `AlertExtractor` stops for the rest of the run when there is no model or an auth error, and at `max_llm_extractions_per_run`. Every returned `title`, `company`, `city` must appear in the text given to the model; others are dropped. Triggers (`card_needs_fallback`): `company_unknown`, an empty or "(company…" placeholder company, an empty title, or a truncated title with fewer than 2 words. Unknown-sender emails go to the model only with `llm_unknown_senders` and only when `looks_like_alert` says the subject or body reads like a job alert.

4. **Search by the alert's text.** When extraction still names no company, `_excerpt_postings` calls `find_posting_from_excerpt`, which puts the redacted excerpt into Parallel's `objective` (introduced by a sentence that marks the text as data, not instructions, and tells the search to ignore job boards) and sends queries made from the title and city. `judge()` takes an `excerpt` and relaxes only the company check: the company comes from the page (the ad's company, else the site's own name, never a shared host or directory) and must also appear in the excerpt as a whole word of at least 3 characters. All other `judge()` rules stay, including title in the heading and the open-ad check. A hit is marked `confidence: from_email_text` and the job is flagged `needs_review`, so automatic mode never sends it unseen. One search request per job, within the existing caps and memo (`failure_ttl_hours`).

5. **Arbeitsagentur stays after evaluation.** Alerts search before evaluation because they have no description. The Arbeitsagentur detail call gives the full text, so a search per Arbeitsagentur job before evaluation would multiply search cost, and searching after evaluation lets `judge()` compare the ad text (`MIN_OVERLAP`). `seed_arbeitsagentur_misses` runs `run_discovery(job_ids=missed, sources=("arbeitsagentur",), force=True, import_related=employer_careers.enabled)` right after `find_own_ads`, both in `run_pipeline` and in `autoapp search`. The own-ad pass condition is `own_ads_wanted(config)`: `employer_careers.enabled` or `arbeitsagentur.enabled`.

6. **Partner boards.** `scope.is_partner_board(partner, company)` is true when `allianzpartnerName` is set, is not the Arbeitsagentur itself, and is not a name contained in the company. `jobvector.de` is added to `DIRECTORY_HOSTS`; the list grows from recorded fixtures. `_direct_postings` keeps the "already has a non-board address" skip but no longer skips a job whose address is a partner board's.

7. **`employer_link.py` decides an Arbeitsagentur job's link.** `employer_link(ctx, job, app)` returns one of:
   - `employer`: a matched careers posting (`employer_seeds.outcome = 'matched'`), else the first of the form entry, the job's `application_url` or the evaluated application URL that is employer-side (not an Arbeitsagentur page, restricted, directory, or the partner board);
   - `direct`: both lookups ran and missed, the ad has no `externeURL`, and `allianzpartnerName` is the Arbeitsagentur: the Arbeitsagentur link is kept, labelled "only on the Arbeitsagentur", status `restricted`;
   - `missing`: both lookups ran and missed otherwise: status `no_employer_link`;
   - `deferred`: a lookup has not run yet (no `PARALLEL_API_KEY`, cap, no company name, careers pages not looked at): status `restricted` with the reason "employer ad not searched yet".
   "Ran and missed" is read from `posting_searches` (`found = 0`) and `employer_seeds` (outcome other than `matched`), so a deferred search is never a miss. `store.entry_url` and `store.resolve` use this for every Arbeitsagentur job (its page is on `arbeitsagentur.de` and one of its sources is `arbeitsagentur`), so the finder never crawls or saves the Arbeitsagentur page as the application link.
   *Alternative:* hold `no_employer_link` jobs back as `approved`. Rejected (settled in the spec): the letter is ready and the job is retried.

8. **Presentation of `no_employer_link`.** `MARKERS["no_employer_link"] = "NOEMPLOYER"`. `prepare` passes `no_employer=True` to the renderers: README shows "Application link: none yet" plus an explanation, `job.md` shows "Application page: none yet", `apply.html` shows only the status text and no application link. `explain.py` has texts for the three Arbeitsagentur outcomes. The Finish-forms menu lists no browser action for `no_employer_link`. `_recheck_form` and `_recheck_no_employer_forms` refresh the folder when the ad turns up or when both lookups have now run for a job prepared earlier.

9. **Detail data is merged, not replaced.** The detail re-ingest hits the existing-by-source branch; it now calls `repo.merge_source_raw(source, source_job_id, {contact_fields, detail})`, so the employer's application email reaches `evaluate._contact_fields`.

10. **Reporting.** `AlertResolutionSummary.coverage_line()` prints `alerts: N cards → X searched, Y found directly, Z via careers page, W waiting (reasons…), V needed LLM extraction`, with at most three reasons, each cut at 60 characters. `RunSummary.lines()` prints it first in the alert section. Cards and LLM counts come from the search step's summary.

## Risks / Trade-offs

- [A wrong ad is accepted because the company came from the page, not from the email] → Only the company check is relaxed; the page's employer must also be named in the excerpt; the job is `needs_review` and flagged `from_email_text`, so it is never auto-sent.
- [Personal data leaks into the model or the search] → Redaction runs before storage and before every model or search call; a privacy test (same style as `tests/test_privacy.py`) asserts no stored excerpt contains the profile's email, phone or name. The excerpt-search objective states that the text is data.
- [LLM cost] → At most `max_llm_extractions_per_run` calls (default 20), counted on the shared meter; none when `llm_fallback` is false.
- [An email nobody can read is retried every run] → Once the fallback has run and found nothing, the email is marked processed with a warning.
- [Existing configs with an explicit `assist.restricted_domains` miss the new domain] → Load adds it in memory and prints a notice; no file is rewritten.
- [An Arbeitsagentur job prepared before both lookups ran shows a stale status] → `_recheck_no_employer_forms` updates it when the lookups complete.

## Migration Plan

No database migration. New config keys have defaults (`llm_fallback: true`, `max_llm_extractions_per_run: 20`, `llm_unknown_senders: false`). `arbeitsagentur.de` is added to `assist.restricted_domains` on load for configs that list it explicitly. To turn the AI fallback off, set `sources.alerts.llm_fallback: false`; behavior then equals zero-card safety without extraction. Existing `_NOFORM` or `_RESTRICTED` Arbeitsagentur folders are renamed by the normal form re-check.

## Open Questions

- Parallel's documented size limit for `objective` could not be confirmed (docs.parallel.ai was not reachable when this was written). The code caps the objective at `OBJECTIVE_MAX_CHARS = 4000` characters (intro sentence plus excerpt). The spec asked to confirm the limit at implementation time; this stays open (task 5.2).
- The spec's target for jobs posted directly on the Arbeitsagentur says the link is "allowed and labelled". The code keeps the form status `restricted` with the note "only on the Arbeitsagentur: …" (there is no separate status), and the link is shown as the application page with that label. The same status `restricted` is used for a job whose lookups have not run yet, with the note "employer ad not searched yet: <reason>" and no link. Decide whether these two deserve statuses of their own in Phase 3.
- The spec said the folder and README should say "employer ad not found yet: <reason>" and show the Arbeitsagentur page "only as Job ad". For `no_employer_link` the code shows "Application link: none yet" and the status text; the Job-ad line is the existing one. Confirm this wording is what is wanted.
- `partner boards`: only `jobvector.de` is listed. The list should grow from recorded fixtures of real `allianzpartnerName` values.
