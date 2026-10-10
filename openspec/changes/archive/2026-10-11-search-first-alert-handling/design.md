# Design

## Context

Most of the needed machinery already exists in `src/autoapp/employers/`:

- `posting.find_posting` searches `"<company>" "<title>"` with Parallel and accepts only a verified employer page. `service.find_own_ads` / `_direct_postings` wrap it with a per-job miss memo (`PostingMemo`, `failure_ttl_hours`) and page budgets.
- `service.run_discovery` resolves and verifies an employer's website (`IdentityResolver`), scans its careers pages (`EmployerScanner`), matches the vacancy (`Reconciler`) and imports related openings.
- `steps/fetch.fetch_details` already prefers a matched employer posting (`_employer_posting`) over the platform URL. If neither exists, it falls back to the browser fetcher for LinkedIn, Indeed and StepStone, and to HTTP for everything else, which includes workwise.io.

How the run is wired today (`steps/pipeline.run_pipeline` and `commands.search`):
`search → [run_discovery if employer_careers.enabled] → fetch → evaluate → [find_own_ads if employer_careers or arbeitsagentur enabled] → prepare`.
`find_own_ads` only takes `OWN_AD_STATUSES` (description_unavailable, review, approved, prepared, drafted). That is why own-ad search happens after evaluation today. `ALERT_SOURCES` leaves out `workwise-alert`.

## Goals / Non-Goals

**Goals:**
- A single switch, `sources.alerts.mode`, chooses between the two flows. `platform` must behave exactly like today's code.
- Reuse the existing own-ad search, discovery, budgets, guards and memo. Write no second search implementation.

**Non-Goals:**
- No new search provider and no title-only search for jobs without a company.
- No change to Arbeitsagentur, Adzuna, Arbeitnow or the Workwise API search source. Their post-evaluation own-ad pass stays as it is.
- No change to ingestion-time link handling: StepStone's single non-following redirect resolution and Indeed's offline decoding are kept. They establish job identity and dedup; they do not read pages.
- `apply_links_with_login` keeps its own explicit per-site opt-in and default-off semantics. It is not part of the alert mode.

## Decisions

1. **The setting lives at `sources.alerts.mode: Literal["search_first", "platform"] = "search_first"`.** It sits on `AlertsSource` because it describes how alert jobs are handled. `employer_careers` is the wrong home: that setting also controls related-opening imports and Arbeitsagentur discovery.
   *Alternative:* overload `employer_careers.enabled`. Rejected because it would bundle related-job import with alert handling and could not express "platform".

2. **One mode helper.** `alerts_search_first(config) -> bool` and an `alert_sources(config)` tuple go in `employers/service.py`. In search-first mode the tuple includes `workwise-alert`. Every place that branches on the mode calls these, so the old-mode paths stay literal copies of today's code.

3. **New pipeline order in search-first:**
   `search → own-ad pre-pass (alert jobs, NEW/DESCRIPTION_UNAVAILABLE without description) → run_discovery(force=True, alert seeds not resolved by the pre-pass) → fetch (no platform pages for alert jobs) → evaluate → find_own_ads (existing post-evaluation pass, unchanged sources: Arbeitsagentur + alerts if employer_careers enabled) → prepare`.
   - The pre-pass is a new `find_alert_ads(ctx, …)` in `service.py`. It reuses `_direct_postings` with `statuses=(NEW, DESCRIPTION_UNAVAILABLE)` and the search-first alert sources, and skips jobs that already have a description. `_attach_direct` already moves recovered `description_unavailable` jobs back to `new`. For `new` jobs it writes the description and leaves the status alone.
   - Discovery runs with `force=True`, so it does not depend on `employer_careers.enabled`. A new `import_related: bool` parameter (defaulting to `cfg.enabled`) gates `reconciler.import_related`.
   - The same order applies to `autoapp search` in `commands.py` and to the menu's run, which goes through `run_pipeline`.
   *Alternative:* fold direct search into `IdentityResolver`. Rejected because it would mix per-job search with the per-employer identity logic and its caps.

4. **The detail step gets a skip rule, not a new fetcher.** In search-first, `fetch_details` treats a job as an alert job when any of its `job_sources` is in the alert source list. If that job has no matched employer posting, the step makes no request. It transitions the job to `description_unavailable` with the reason last recorded by the employer pipeline (`employer_seeds.reason` or the posting memo reason), or with "employer ad not found". A Workwise job with an unknown company gets "alert does not name the employer". Non-alert jobs are unaffected.

5. **Search-key and cap handling.** If `PARALLEL_API_KEY` is missing, the pre-pass emits one warning with the count and does not mark misses. Discovery still scans cached verified sites, which it already does through its cache path. Cap or deferral misses (`PostingMiss.deferred`) are not memoized, so the next run retries them. The reason text is stored on the transition note.

6. **Budgets.** The pre-pass uses a `SearchGateway` with no reserve, so direct search is the first consumer. Discovery keeps `reserved_for_own_ad_search`, so the post-evaluation Arbeitsagentur own-ad pass still has requests left. All three consumers share the persisted run, daily and monthly caps.

7. **Workwise.** `workwise.io` is added to `scope.DIRECTORY_HOSTS`, so a Workwise page can never be accepted as an employer ad or website. The provisional Workwise parser takes a company from the card's text block near the job link: the line after the title, if it is not a generic button text, a location or a badge. With no sample email available, a missing company keeps `company_unknown=True`.

8. **Reporting.** `RunSummary.lines()` starts the alert section with `alert mode: search_first|platform`. A new `AlertResolutionSummary` counts direct hits, careers-page matches, not found and deferred, and reuses the existing `posting_searched`, `employer_matched` and `employer_deferred` events for the feed. The dashboard picks the counts up from the run's stored lines. It needs no schema change.

9. **The quick test is its own command, `autoapp test`, not a run mode.** It wraps the existing fresh-test-run path in `commands.run_with_mode(fresh=True, mode="test")`, so it gets the sandbox, the test-mode send rules and the merge-back of API and AI usage, and adds a `sample` argument. `RUN_MODES`, `--mode` and `run.default_mode` stay untouched.
   *Alternative:* `autoapp run --mode quick`. Rejected: the user wants the command to say "test", and a fourth run mode would make the mode prompt and `default_mode` validation more complicated for no gain.

10. **Sample limits travel through the run context.** A frozen `SampleLimits` dataclass (alert emails 2, jobs per source 2, employer lookups 2, evaluations 4, preparations 2, test emails 1) is set on `RunContext.sample`, which is `None` for every normal run. Each step reads it at the point where it already applies a limit:
    - the IMAP reader takes only the newest N messages;
    - `run_search` passes one profile keyword and one page per source and truncates to N jobs;
    - `find_alert_ads` and `run_discovery` cap seeds and employers;
    - evaluation and prepare lower their per-run limits;
    - test send caps the number of emails.
    Prepare also looks at `sample.ignore_threshold`: it ranks evaluated jobs by score and takes one email and one form application.
    *Alternative:* a separate "smoke pipeline" module that calls the step functions with its own arguments. Rejected because it would drift from the real pipeline, and the point of the test is to exercise the real code.

11. **The checklist is built from the run summary.** `steps/quicktest.build_report(config, summary)` turns what the steps already summarise (`SearchSummary`, `AlertResolutionSummary`, `DiscoverySummary`, `FetchSummary`, `EvaluateSummary`, `PrepareSummary`, `TestRunSummary`, step errors) into a fixed list of named checks, each OK, FAILED or SKIPPED with a reason. Steps do not know about the checklist; `SearchSummary` gained the number of alert emails read and the cards per parser so that alert lines can be reported. A part the run never reached is SKIPPED with "not reached". The exit code is 1 if any check FAILED.
    *Alternative:* an event subscriber. Rejected during implementation: the summaries already hold every count and reason, and they are simpler to test than event streams.

12. **Alert emails stay unprocessed in the real workspace by design.** Processed tracking lives in the workspace database, and the IMAP reader uses `BODY.PEEK[]` without setting flags. The sandbox's empty database therefore leaves the real workspace's tracking and the mailbox untouched. The reader's "newest N" limit keeps the sandbox from ingesting the whole folder.

## Risks / Trade-offs

- [Quick test spends real money: Parallel searches and AI calls] → The sample limits bound it to a few searches and about 10 AI calls. Saved employer sites are copied into the sandbox. Usage is merged into the real caps, so the test cannot exceed them.
- [The quick test's test email ignores the fit score, so a low-scoring job's email is mailed to yourself] → It is only to your own address with the `TEST_RUN:` marker; every other test-mode send rule (free-mail, `needs_review`, cooldown, cap, duplicate guard) still applies.
- [Preparing below the threshold could suggest the job is a good match] → This happens only in the test workspace, and the checklist and the folder note mark it as a quick-test preparation.

- [More search spend: up to 2 requests per new alert job before evaluation, even for jobs that later score low] → The pre-filter already drops many jobs at ingestion. Saved websites are reused. The existing caps bound the spend, and the summary shows it.
- [Wrong same-title ad accepted because no description exists yet to compare] → `find_posting`'s title and employer checks plus the city hint apply. Same-employer ambiguous matches stay unresolved, as for `description_unavailable` jobs today.
- [Workwise alerts without a company never resolve] → They show a visible reason, `describe` and `add <url>` still work, and the parser improves once a real sample exists.
- [Existing users silently change behavior on upgrade] → This is intended (the user asked for search-first as the default). The run summary always names the mode, and docs explain `mode: platform`.
- [Existing tests assume platform fetching for alert jobs] → Pin `sources.alerts.mode: platform` in those fixtures and add new search-first tests next to them.

## Migration Plan

No database migration is needed. Configs without the key get `search_first`. To roll back per workspace, set `sources.alerts.mode: platform`. The setup wizard writes the key explicitly and explains that the Parallel key is needed for search-first.

## Open Questions

- Exact card layout of Workwise alert emails: the parser heuristic is refined once a real email is available in `samples/`.
