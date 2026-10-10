# Tasks

## 1. Config switch and baseline safety

- [x] 1.1 Add `mode: Literal["search_first", "platform"] = "search_first"` to `AlertsSource` in `src/autoapp/config/models.py`; add tests in `tests/config/test_models.py` for default, explicit `platform`, and a misspelled value whose error lists both allowed values; verify with `uv run pytest tests/config`
- [x] 1.2 Add `alerts_search_first(config)` and `alert_sources(config)` (search-first includes `workwise-alert`) in `src/autoapp/employers/service.py`, keeping `ALERT_SOURCES` unchanged for platform mode; unit-test both helpers in `tests/employers/test_service.py`
- [x] 1.3 Pin `sources.alerts.mode: platform` in existing test configs/fixtures that rely on platform detail fetching or today's own-ad order (e.g. `tests/steps/test_pipeline.py`, `tests/steps/test_other_steps.py`, `tests/employers/test_pipeline.py`, `tests/employers/test_e2e.py`); verify the full suite `uv run pytest` is green before any behavior change

## 2. Workwise alerts and job-board scope

- [x] 2.1 Add `workwise.io` to `DIRECTORY_HOSTS` in `src/autoapp/employers/scope.py`; add a test in `tests/employers/test_posting.py` that a workwise.io search result is never accepted as an own ad or employer site
- [x] 2.2 Extend `src/autoapp/sources/alerts/workwise.py` to take the company from the card text near the job link (not a button text, location or badge) and clear `company_unknown` when found; add `tests/alerts/test_workwise.py` with an HTML fixture covering a card with a company and one without; verify with `uv run pytest tests/alerts`

## 3. Search-first own-ad pre-pass

- [x] 3.1 Implement `find_alert_ads(ctx, …)` in `src/autoapp/employers/service.py` reusing `_direct_postings` with statuses `NEW`/`DESCRIPTION_UNAVAILABLE`, `alert_sources(config)`, jobs without description only, an unreserved `SearchGateway`, and skipping company-unknown jobs; verify with tests in `tests/employers/test_posting.py` that a LinkedIn and a Workwise alert job get description + application URL from a fake employer page and stay/return to `new`
- [x] 3.2 Handle missing `PARALLEL_API_KEY` and capped/deferred searches in the pre-pass: one warning with the waiting count, no miss memoized for deferred results; test both cases (no request sent, warning text, job retried on next call)
- [x] 3.3 Add an `AlertResolutionSummary` (mode, found directly, found via careers page, not found, deferred) with `lines()`; unit-test the summary text against the scenario in the `alert-resolution` spec

## 4. Discovery fallback independent of `employer_careers.enabled`

- [x] 4.1 Add an `import_related: bool | None` parameter to `run_discovery` (default `cfg.enabled`) gating `reconciler.import_related`, and make it accept `alert_sources(config)` as seed sources; test that with `force=True, employer_careers.enabled=False` a StepStone seed is matched on a cached careers site while related openings are not imported (`tests/employers/test_service.py`)
- [x] 4.2 Test that a Workwise alert job with known company not resolved by the pre-pass is grouped and scanned like other alert employers, and that its saved website is reused without a new search on a second run

## 5. Detail step without platform pages

- [x] 5.1 In `src/autoapp/steps/fetch.py`, in search-first mode, skip any request for alert-sourced jobs without a matched employer posting and transition them to `description_unavailable` with the recorded employer-search reason (fallback "employer ad not found"; "alert does not name the employer" for company-unknown cards); platform mode code path unchanged; verify with tests in `tests/steps/test_other_steps.py` using a browser/http fake that asserts zero calls for linkedin/indeed/stepstone/workwise URLs
- [x] 5.2 Test that a search-first alert job with a matched employer posting still has that posting read by the detail step, and that non-alert jobs (manual, Adzuna) are fetched as before

## 6. Pipeline, command and menu wiring

- [x] 6.1 Rewire `run_pipeline` in `src/autoapp/steps/pipeline.py`: in search-first run `find_alert_ads` then `run_discovery(force=True)` before fetch, keep the post-evaluation `find_own_ads` as is; add the alert summary to `RunSummary` and print `alert mode: …` in `lines()`; platform mode keeps today's order; verify with pipeline tests for both modes in `tests/steps/test_pipeline.py` (search-first: zero platform requests, counts in summary; platform: identical call order to before)
- [x] 6.2 Apply the same order to `autoapp search` in `src/autoapp/commands.py`; verify with a CLI test in `tests/test_cli_commands.py`
- [x] 6.3 Show the alert mode in the menu's source status line in `src/autoapp/menu/main.py`; verify with the existing menu tests updated in `tests/menu`

## 7. Setup, checks and documentation

- [x] 7.1 In `src/autoapp/setup/sections.py`, write `sources.alerts.mode` explicitly, explain that search-first needs `PARALLEL_API_KEY` (and offer `platform` as the alternative), and keep hand edits preserved; verify with setup tests in `tests/setup`
- [x] 7.2 Update `docs/job-sources.md`, `docs/usage-guide.md` and `README.md` to describe both modes, the default, the fallback order, waiting reasons and how to switch back to `platform`; verify with `uv run pytest tests/test_docs.py tests/test_examples.py` and update `examples/` configs if they list `sources.alerts`

## 8. Quick test command

- [x] 8.1 Add a frozen `SampleLimits` dataclass and an optional `RunContext.sample` (default `None`) in `src/autoapp/steps/context.py`; verify that existing tests still pass unchanged and that a unit test confirms normal runs have `sample is None`
- [x] 8.2 Apply the sample limits where steps already limit work: newest-N emails in the IMAP reader, one query and N jobs per source in `steps/search.py`, seed and employer caps in `find_alert_ads`/`run_discovery`, the evaluate and prepare limits, and the test-send email cap; test each limit in its step's test module with fakes
- [x] 8.3 Make prepare honour `sample.ignore_threshold` by ranking evaluated jobs and picking one email and one form application; test in `tests/steps/test_prepare_step.py` that jobs scoring below the threshold are prepared only when sampling and that form finding stays GET-only
- [x] 8.4 Implement `QuickTestReport` (event subscriber producing named OK/FAILED/SKIPPED checks with reasons, "not reached" fallback, exit code); unit-test it against recorded event sequences, including the Adzuna-failure, no-alert-email and disabled-Arbeitnow scenarios from the `run-modes` spec
- [x] 8.5 Add the `autoapp test` command in `src/autoapp/commands.py` (options `--profile`, `--no-dashboard`), wrapping the fresh sandbox test run with `SampleLimits` and printing the checklist plus the test workspace path; test in `tests/test_cli_commands.py` that the real workspace's jobs, CSV and processed-email rows are unchanged, that usage is merged back, and that the exit code is 1 on a FAILED check
- [x] 8.6 Add "Quick test" as the pre-selected first action of the Tests entry in `src/autoapp/menu/main.py`; update `tests/menu` so that pressing Enter in Tests runs the quick test without the mode prompt
- [x] 8.7 Document `autoapp test` and the menu's Quick test in `docs/usage-guide.md` and `README.md` (what it touches, what it costs, how to read the checklist); verify with `uv run pytest tests/test_docs.py`

## 9. Integration check

- [x] 9.1 Run `uv run pytest` and a dry `autoapp run` against a sandbox workspace with the sample alert emails in `samples/` in search-first mode, confirming the summary shows the mode and outcomes and that the feed contains no LinkedIn/Indeed/StepStone/Workwise page reads
- [x] 9.2 Run `autoapp test` against the real workspace configuration and confirm the checklist has a line for each part, the real workspace is unchanged, at most one `TEST_RUN:` email arrived in your own mailbox, and the reported search and AI usage appear in `autoapp stats`
