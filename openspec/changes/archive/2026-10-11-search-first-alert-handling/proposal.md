# Proposal

## Why

LinkedIn, Indeed, StepStone and Workwise often block the app's automated page reads, so alert jobs frequently end as `description_unavailable` and need manual work. The Arbeitsagentur jobs already avoid this: the app searches `"<company>" "<job title>"` and takes the employer's own ad. Applying that same rule to every job-alert email makes web search, not job-board browsing, the main way the app gets an alert job's ad.

## What Changes

- New setting `sources.alerts.mode` with two values:
  - `search_first` (**new default**): the app reads only the alert email. For each card it takes title, company and location, searches for the employer's own ad, and if that fails it finds the employer's website and scans its careers/jobs pages for the ad. It never opens a LinkedIn, Indeed, StepStone or Workwise job page to read the description.
  - `platform` (today's behavior): job-board pages are read by the detail fetcher as now. Employer discovery stays opt-in through `sources.employer_careers.enabled`.
- In `search_first`, the own-ad search ("Arbeitsagentur rule") runs **before** detail fetching and evaluation for all new alert jobs (LinkedIn, Indeed, StepStone and Workwise alerts). Today it runs only after evaluation, and only for jobs worth applying to. Employer website lookup and careers scan run next for the jobs it did not resolve, whether or not `employer_careers.enabled` is set.
- In `search_first`, an alert job whose ad was not found at the employer becomes `description_unavailable` with a clear reason ("employer ad not found yet", "search key missing", "search cap reached", and so on). It is retried in later runs within the existing failure TTL and caps. It never falls back to the job-board page.
- Workwise alert cards: the parser also takes the company name from the email when the card shows one. This is needed because search-first cannot work without a company.
- Run summary, feed and dashboard show the alert mode and the search-first outcomes per job: found directly, found via careers page, not found, deferred.
- A missing `PARALLEL_API_KEY` in `search_first` produces a visible warning in runs, setup and checks. It does not silently switch to platform browsing.
- Nothing is removed. Setting `sources.alerts.mode: platform` restores today's flow exactly.
- New command `autoapp test`, also offered as **Tests → Quick test** in the menu. It is a short check of every part of the app, instead of a full simulated run. In a throwaway test workspace it:
  - reads one or two alert emails, without marking them as processed;
  - asks each enabled source for a couple of jobs;
  - looks up a couple of employers online on the search-first path;
  - evaluates a few jobs;
  - prepares one email application and one form application;
  - sends at most one `TEST_RUN:` email, to yourself.
  It ends with an OK/FAILED/SKIPPED checklist for each part. A full run takes long and costs a lot; this command checks the whole chain, including the new search-first path, in a few minutes.

## Capabilities

### New Capabilities
- `alert-resolution`: how jobs from alert emails get their ad, controlled by the `sources.alerts.mode` setting. Covers mode selection and its default, the search-first order (direct ad search, then employer site and careers scan), what happens to jobs that are not found, the ban on reading platform pages in search-first, and the reporting.

### Modified Capabilities
- `job-discovery`: detail fetching for alert jobs depends on the alert mode (search-first never reads job-board pages for them). The Workwise alert parser takes the company from the email when present.
- `employer-career-discovery`: in search-first mode, discovery covers every alert source including Workwise alerts and runs regardless of `employer_careers.enabled`. The own-ad search runs before detail fetching for new alert jobs, not only after evaluation.
- `run-modes`: adds the quick test (`autoapp test`). It is a sampled test run in a fresh test workspace, with per-step limits and a checklist report. The run modes themselves (`automatic`, `review`, `test`) and their flag are unchanged.
- `main-menu`: the Tests entry offers Quick test as its default action. It also lists the existing fresh test run, which the code already has but the spec does not.

## Impact

- Config: `src/autoapp/config/models.py` (`AlertsSource.mode`), setup wizard section for sources and employer search (`src/autoapp/setup/sections.py`), menu status line (`src/autoapp/menu/main.py`), docs (`docs/job-sources.md`, `docs/usage-guide.md`, `README.md`).
- Pipeline order: `src/autoapp/steps/pipeline.py`, `src/autoapp/commands.py` (search command), `src/autoapp/employers/service.py` (sources, statuses, pre-fetch own-ad pass), `src/autoapp/steps/fetch.py` (skip platform pages in search-first).
- Parser: `src/autoapp/sources/alerts/workwise.py`.
- Search usage goes up: every new, unfiltered alert job costs up to two Parallel searches before evaluation. Existing per-run, daily and monthly caps still bound it, and saved employer sites keep later lookups free.
- Quick test: a new `test` command in `src/autoapp/commands.py`, reusing `steps/sandbox.py`, `steps/pipeline.py` with per-step sample limits, and the Tests entry in `src/autoapp/menu/main.py`. Its search and AI usage are real and are merged into the real counters, as for `--fresh`.
- Tests under `tests/employers`, `tests/steps`, `tests/alerts` and `tests/config`. Existing tests that rely on platform fetching for alert jobs must pin `mode: platform`.
