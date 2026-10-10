# Proposal

## Why

Arbeitnow offers a free public jobs API without credentials, adding another route to relevant jobs beyond Arbeitsagentur, Adzuna and alerts. Its listings already include descriptions, so many jobs can proceed to evaluation without visiting a blocked job-board page.

## What Changes

- Add an optional `arbeitnow` source to normal `search` and `run`, with setup enable/disable controls and no API key or new paid service.
- Read a bounded set of public feed pages once per search invocation, sharing the fetched records across selected profiles rather than querying the same feed for each keyword/location.
- Match keywords and supported location facts locally, reuse existing optional prefilters, normal ingestion/deduplication, alert-first ordering and downstream budgets.
- Normalize IDs, titles, employer, description, location, remote flag, job-type facts and source timestamp conservatively; do not invent salary, employer URLs or application-form addresses.
- Keep discovery Germany-focused: exclude foreign-edition listings and explicit incompatible country/location facts. Document that the feed has no structured country/work-eligibility field and remote status alone proves neither German eligibility nor worldwide access.
- Bound requests/pagination, pace requests, stop on refusals and show partial/error outcomes without disrupting other sources.
- Preserve source attribution and a clickable Arbeitnow link in job/application reports, as its API terms require.

## Capabilities

### New Capabilities

- `arbeitnow-job-source`: credential-free, bounded Arbeitnow feed discovery and conversion into the existing job pipeline.

### Modified Capabilities

None. The source extends the pending `job-discovery` capability's source set without changing its general filtering/deduplication contract or the canonical alert-first and form-finding requirements. The capability remains separate from employer-site discovery: an Arbeitnow listing is not a verified employer website.

## Impact

- `sources`, `steps/search.py`, configuration/setup, source reporting, generated job/package attribution and corresponding tests/docs.
- Public `https://www.arbeitnow.com/api/job-board-api`; existing HTTP/HTML dependencies suffice.
- Reuse existing persisted API usage accounting; no employer cache or database-schema change is required.
- Default disabled for omitted configuration, preserving existing network behavior; setup offers enablement without credentials.
- No Remotive/Jobicy integration, paid/private API, arbitrary international expansion, browser scraping, automated submission or changes to other in-progress work.
