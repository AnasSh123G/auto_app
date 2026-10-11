# Tasks

## 1. Arbeitsagentur resolves to the employer (T1)

- [x] 1.1 Add `arbeitsagentur.de` to `DEFAULT_RESTRICTED_DOMAINS` in `src/autoapp/config/models.py`, add it in memory with a notice when an existing config lists `assist.restricted_domains` without it, and write it in the setup wizard
- [x] 1.2 Add `jobvector.de` to `employers/scope.DIRECTORY_HOSTS` and `is_partner_board(partner, company)`; make `_direct_postings` search Arbeitsagentur jobs whose `externeURL` leads to a partner board instead of skipping them
- [x] 1.3 Seed Arbeitsagentur jobs the own-ad search missed into `run_discovery` after evaluation (`seed_arbeitsagentur_misses`), whether or not `employer_careers.enabled` is set; related openings are imported only when it is
- [x] 1.4 Merge the detail call's `contact_fields` and `detail` into the existing source row (`discovery/ingest.py`, `steps/search.py`) so evaluation picks the `email` channel
- [x] 1.5 Make `autoapp search` and `run_pipeline` decide the own-ad pass with the same `own_ads_wanted(config)` helper
- [x] 1.6 Tests next to `tests/sources/test_arbeitsagentur.py`, `tests/employers/test_posting.py` and `tests/discovery/test_ingest.py`

## 2. no_employer_link status and _NOEMPLOYER marker (T2)

- [x] 2.1 Add `no_employer_link` to `formfinder/finder.py` `LABELS`, `formfinder/explain.py` and `MARKERS` (`NOEMPLOYER`) in `package/folders.py`
- [x] 2.2 Add `formfinder/employer_link.py`: employer address of an Arbeitsagentur job, or `direct` / `missing` / `deferred`; `store.entry_url` and `store.resolve` never crawl or save the `arbeitsagentur.de` page for these jobs
- [x] 2.3 Show the Arbeitsagentur page only as Job ad (README, `job.md`, `apply.html`) with "employer ad not found yet: <reason>"; keep the application link for directly posted jobs with the label "only on the Arbeitsagentur"
- [x] 2.4 Refresh folder, marker and links when the ad turns up (`_recheck_form`, `_recheck_no_employer_forms`); CSV, run summary, dashboard and the Finish-forms menu know the new status
- [x] 2.5 Tests: search and discovery miss gives `_NOEMPLOYER`; a later hit refreshes it; a directly posted job keeps its link; deferred searches never give `no_employer_link`

## 3. Alert email excerpts and zero-card safety (T3)

- [x] 3.1 Add `sources/alerts/excerpt.py` (`Redactor`, `card_excerpts`, `redacted_body`) and store `email_excerpt` and `message_id` in `job_sources.raw` for every card
- [x] 3.2 Do not mark a known-sender email with zero cards or a parser error as processed; emit a warning naming sender and subject; return those emails in `IngestResult.unprocessed`
- [x] 3.3 Extend `AlertResolutionSummary` with `coverage_line()` and print the `alerts:` line in the run summary
- [x] 3.4 Tests: privacy test that no stored excerpt contains the profile's email, phone or name; broken-layout email gives a warning and stays unprocessed; LinkedIn card makes one `posting:<id>` search whose first query is `"<short company>" "<full title>"`; StepStone truncated title; no job-board request in `search_first`

## 4. LLM fallback for alert cards (T4)

- [x] 4.1 Add the task `alert_extract` to `llm/settings.py` (follows the evaluate model unless set) and the config keys `llm_fallback`, `max_llm_extractions_per_run`, `llm_unknown_senders`
- [x] 4.2 Add `sources/alerts/llm_extract.py` (`AlertExtractor`, `card_needs_fallback`): validate each returned value against the given text, cap calls per run, count on the shared meter; wire it into `ingest_alerts` for cards and for zero-card emails
- [x] 4.3 Mark an email processed only after the fallback has run; when it found nothing keep the warning and mark it processed so it is not paid for again
- [x] 4.4 Add `find_posting_from_excerpt`, `judge(..., excerpt=...)` relaxed on the company check only, `_excerpt_postings`, `mark_from_email_text` (`confidence: from_email_text`, `needs_review`)
- [x] 4.5 Setup wizard question, `examples/config.example.yaml`, `docs/job-sources.md`, `docs/llm-providers.md`, `docs/usage-guide.md`
- [x] 4.6 Tests: broken LinkedIn email yields its jobs through a fake LLM and is processed once; Workwise card without a company resolves through the excerpt search and is `needs_review`; output not present in the text is rejected; `llm_fallback: false` behaves like 3

## 5. Verification

- [x] 5.1 Tests for sections 1 to 4 are in place; the commits for T1 to T4 each carry their tests
- [ ] 5.2 Confirm Parallel's current limit for `objective` (see design.md, Open Questions) and replace `OBJECTIVE_MAX_CHARS` if it differs
