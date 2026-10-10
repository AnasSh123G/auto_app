# Tasks

## 1. Source configuration and setup

- [x] 1.1 Add `ArbeitnowConfig` and `sources.arbeitnow` with disabled default, page/request caps and pacing from the design; verify old configs make no new requests, enablement needs no secrets and invalid limits/delays fail through config tests.
- [x] 1.2 Add the no-key enable/disable prompt to setup sources and existing source settings access; verify first setup/redo preserves adjacent Adzuna, alerts and employer-careers settings and requires no Arbeitnow credentials.
- [x] 1.3 Document enablement, free/public API terms, attribution and app-selected traffic defaults in `docs/job-sources.md` and config examples; verify documented config loads and setup tests match the shown behavior.

## 2. Feed parsing, safe pagination and traffic accounting

- [x] 2.1 Implement a typed Arbeitnow adapter and small representative fixtures for the current feed schema; verify valid records preserve slug/title/company/ad URL, HTML becomes readable inert text, remote false stays unknown and missing salary/application/reference facts are not fabricated.
- [x] 2.2 Normalize source-created timestamps/job-type facts conservatively with original metadata; verify invalid dates/types produce visible warnings, source dates use UTC, unknown optional fields remain unknown and no original employer publication date is claimed.
- [x] 2.3 Implement finite page loading with endpoint-constrained next links, no redirects/retries and owned-client cleanup; verify null next, variable page size, duplicate slugs, loops, malicious URLs, invalid envelopes/records and later-page failure retain valid earlier records without unsafe requests.
- [x] 2.4 Reuse persisted `RequestBudget` and injectable pacing; verify exact page/run/day/month boundaries, failures counted, 403/429/999 stop all later profile requests and reopening a run does not reset daily/monthly usage.
- [x] 2.5 Document finite coverage, hourly polling guidance, date provenance and failure/partial behavior in `docs/job-sources.md`; verify adapter fixture cases match those descriptions and no paid/private endpoint is used.

## 3. Profile matching and pipeline integration

- [x] 3.1 Add Germany-market URL/country scope and local keyword/location matching using existing normalization/city-distance helpers; verify foreign editions/deceptive domains, unrelated roles, omitted location, known radius mismatches and unknown eligibility/locations behave exactly as specified without a geocoding/model call.
- [x] 3.2 Nominate one stable accepting profile across the selected profiles and preserve profile provenance; verify one profile's exclusion does not suppress another's accepted match, all-rejected relevant jobs remain filtered, selected-profile runs stay scoped and multiple keywords/profiles do not inflate counts.
- [x] 3.3 Wire the adapter into `_default_sources`/`run_search` with one shared feed snapshot and source isolation; verify normal `search`/`run` participation, API descriptions skip detail requests, incomplete descriptions use existing fallback and failed Arbeitnow traffic does not stop APIs or mailbox alerts.
- [x] 3.4 Cover ingestion and ordering regressions: verify repeated slugs/cross-source duplicates preserve state, notes and prepared files, verified-requisition safeguards remain intact, alert duplicates stay alert-first and Arbeitnow-only jobs do not trigger alert-employer expansion or gain extra evaluation/preparation budget.
- [x] 3.5 Update README/source workflow documentation for local profile matching, partial geography and unchanged overall run dry-run semantics; verify CLI/config examples and pipeline fixture outputs, preserving all adjacent source behavior.

## 4. Attribution and reporting

- [x] 4.1 Add safe reusable Arbeitnow source-link extraction and generated `arbeitnow_url` CSV output; verify secondary-source links survive merges, old CSV imports still accept status/notes edits and malicious/non-Arbeitnow URLs cannot enter attribution.
- [x] 4.2 Pass optional attribution into newly rendered `job.md`, README and `apply.html`; verify clickable escaped links, absence from employer-facing letters/emails, unchanged non-Arbeitnow templates and no retrospective rebuild of edited applications.
- [x] 4.3 Extend source events/summary/statistics with fetched pages, invalid/foreign/uncertain counts and persisted request usage as needed; verify successful-empty, partial and error outcomes remain distinguishable and job totals are not inflated.
- [x] 4.4 Update application-folder/source/CSV documentation for link-back attribution and quota reporting; verify package/CSV/reporting fixtures and form-finder refresh tests preserve the new attribution and existing application links/user content.

## 5. Integration acceptance

- [x] 5.1 Add a complete mocked search-to-preparation scenario with a matching Arbeitnow job, a rejected foreign-edition job, an alert duplicate and a second matching profile; verify exactly the bounded page requests, correct unique jobs, descriptions, alert priority and attributed outputs without submission/browser actions.
- [x] 5.2 Run targeted source, config/setup, search/pipeline, ingestion, CSV/render/dashboard and form-finder tests plus lint; verify no new dependency/schema migration is required and run the existing full regression suite after integration.
- [x] 5.3 With explicit permission, run one bounded live API check through the implemented adapter and a setup/CLI walkthrough in a disposable workspace; record observed schema, source counts, excluded editions/uncertainty and honest coverage limitations, then verify strict OpenSpec validation before archival.

## Acceptance notes

2026-10-09: Implementation and fixture-based integration are complete. The full regression suite
passed (1,329 passed, 4 existing opt-in live tests skipped); repository lint and strict change
validation passed. No dependency or schema migration was added. CLI help confirms the documented
setup/search entry points. The mocked end-to-end run verifies secondary-source attribution,
alert-first ordering, a second accepting profile, no actual sending and preservation of edited
packages on repeated ingestion.

Task 5.3 remains intentionally unchecked: this apply invocation did not authorize a live API
acceptance request or the associated disposable-workspace walkthrough. Earlier public API reads
during planning are not evidence that the implemented adapter passed live acceptance.
