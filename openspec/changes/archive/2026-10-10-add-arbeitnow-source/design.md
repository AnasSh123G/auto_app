# Design

## Context

See `proposal.md` for motivation.

- `JobSource.search(profile)` yields `RawJob` records. `steps/search.py` loops sources and selected profiles, isolates failures and records source events; `_store` ingests, filters newly created jobs and fills detail gaps.
- `_default_sources` currently instantiates Arbeitsagentur and Adzuna. Configuration also includes alerts and the newly implemented optional employer-careers workflow. Preserve that in-progress work rather than assuming the earlier checkout.
- Existing `RequestBudget` stores attempted API requests in `api_usage`. Existing normalization, `ingest`, `Candidate`/`check`, source metadata and CSV columns support another source without a schema migration.
- The shared prefilter does not enforce keyword inclusion or geographic radius. Those checks must occur at feed-source matching, not be assumed to happen later.
- `_store` filters the first profile's newly created job but does not reconsider a filtered duplicate from a later profile. Choose a profile across the selected set before yielding, avoiding a source-specific false rejection without broadly refactoring other adapters.
- Package `JobInfo` currently stores source names but not individual source URLs. CSV keeps a source list and primary job URL, which can belong to another source. Neither is sufficient for clickable attribution after cross-source merging.

Official API evidence checked on 2026-10-09:

- [Provider description](https://www.arbeitnow.com/blog/job-board-api): public, no key, European listings, `remote`, and documented pagination; the only documented specialty filter needed elsewhere is visa sponsorship, not generic keyword/location search.
- [Public feed](https://www.arbeitnow.com/api/job-board-api): `data`, `links.next`, and metadata; records have `slug`, `company_name`, `title`, HTML `description`, `remote`, `url`, `tags`, `job_types`, `location`, Unix `created_at`.
- The first page contained German `.com` listings plus `.co.uk`, `.fr` and `.ch` editions. `per_page` was 325, so never assume a fixed page size.
- Metadata says hourly updates and order by `created_at`. This is the source's listing timestamp, not independently verified employer publication.
- [Terms](https://www.arbeitnow.com/terms), section 11: API supplied as-is, permission revocable, link back to Arbeitnow required. This design is local personal job search, not commercial/public redistribution.

## Goals / Non-Goals

**Goals:** bounded credential-free ingestion, useful descriptions, local profile matching, accurate unknown facts, cross-source attribution and existing workflow compatibility.

**Non-Goals:** exhaustive historical inventory, international edition support, paid/custom API, applicant-data transfer, visa-policy features, employer lookup triggered by Arbeitnow-only jobs, new semantic matching/model tasks, or changes to browser/submit/consent behavior.

## Decisions

### 1. A normal optional source with explicit setup enablement

Add `ArbeitnowSource` under `sources` and `sources.arbeitnow` settings:

| Setting | Default |
| --- | --- |
| enabled | false |
| max_pages_per_run | 3 |
| max_requests_per_run / day / month | 3 / 30 / 300 |
| min_delay_s | 3 |

Validate positive limits, consistent run/day/month caps and minimum delay of at least one second. Defaults are conservative app policy, not claimed published provider quotas. Setup offers a no-key enablement prompt and explains coverage/terms. Preserve existing selection on setup redo; preserve unknown adjacent source config keys as existing setup does.

Instantiate the source only when enabled. Pass selected profiles to this adapter from `_default_sources`, with injectable client/clock/sleep/budget for tests. Use an owned short-lived HTTP client with honest User-Agent and finite timeout; close it deterministically after the lazy feed-load attempt. No SDK or new dependency is needed.

**Alternative:** enable automatically in every existing configuration. Rejected to preserve existing network behavior and let users explicitly choose the new source.

### 2. Fetch once, then nominate one profile per listing

Lazily load a finite snapshot the first time `search(profile)` is called; retain the records and failure/stop state for other profile calls. Fetch `GET https://www.arbeitnow.com/api/job-board-api?page=N` with no unsupported query parameters. The first call performs at most the configured number of page requests; later calls reuse memory.

For each unique source record:
1. Validate Germany-market scope and normalize fields.
2. Find selected profiles with an included normalized keyword phrase in title, tags or readable description and no known location mismatch.
3. Apply existing `check(Candidate, profile)` to the matching profiles.
4. Nominate the first accepting profile in configuration order, or the first relevant profile if all are rejected. Store all relevant/accepting profile names in source metadata.
5. Yield that record only when the normal loop reaches its nominated profile. `_store` performs normal ingestion/filtering and reports a rejected record once if all profiles fail.

This avoids multiplying source counts by keyword/profile and avoids a first-profile rejection suppressing an accepted second-profile match. A profile selector constrains the candidates supplied to the adapter, not just the final loop. A listing matching nothing never becomes a job.

Reuse text normalization, keyword boundary matching and relevant employer-discovery matching helpers where their semantics agree; do not introduce fuzzy similarity or hidden model calls. Do not strip location or exclude-keyword facts simply to obtain a match.

**Alternative:** API queries per profile/keyword. Rejected: these filters are not documented and would refetch the same feed. Broadly fixing all sources' multi-profile behavior is also out of scope.

### 3. Constrain pagination and fail explicitly

Reserve a request through `RequestBudget(source="arbeitnow")` before every GET. Failed attempts remain counted. Pace actual requests; use no retry loop. Stop the adapter for the invocation on HTTP 403/429/999, timeout, non-success response or malformed page structure, retaining already parsed records for matching and reporting a distinct reason.

Require a `data` array and valid pagination container. A null next link ends the feed. Otherwise parse `links.next` only to validate HTTPS, exact feed host/path, no credentials/nondefault port/fragment, only the positive `page` parameter and forward sequential progress. Construct the next request locally rather than following arbitrary URLs. Refuse API redirects rather than trusting an unexpected destination. Detect repeated links/pages and duplicate slugs.

Reaching page/request caps with more pages available produces explicit partial coverage. Never stop based on a presumed page size or solely on the oldest date in a page: profile age limits may differ and global ordering across country segments is not guaranteed by the observed mixed feed.

Malformed records are skipped with field/category counts and warnings; malformed envelopes stop the source. Do not log complete descriptions or return a success-shaped empty feed for failures. Source reports/events expose fetched pages, invalid records, excluded editions, unresolved locations and stop reasons in addition to normal job counts.

Do not add a persistent feed cache or cursor migration in this change. Invocation-level reuse and persisted low request caps bound traffic; docs recommend at most hourly routine polling. A later run starts at the newest page, and ordinary deduplication makes repeats safe. This limits recall to the configured recent page window, which must be visible.

**Alternative:** incrementally stop at the first known slug or timestamp. Rejected because it could hide relevant older records for new profiles.

### 4. Conservative Germany-market and location matching

Accept listing URLs only from Arbeitnow's German-market `arbeitnow.com` origin (optional `www`), safe HTTP(S) and expected job-listing paths. Do not regard unrelated or deceptive suffix hosts as valid; foreign editions are excluded offline. Explicit country restrictions in structured location/job facts that contradict Germany also exclude a record.

No API country field exists. A German-market hostname establishes feed scope, not employment eligibility. Preserve location text and flag uncertainty when necessary; do not claim every `.com` listing is physically in Germany.

With configured locations:
- Match normalized city tokens/known aliases where explicit; reuse existing German city coordinate lookup and `distance_km` to reject known locations outside every configured radius.
- Without a radius, require an exact normalized known city match; known other cities are incompatible.
- Unknown coordinates/ambiguous location remain unknown, are reported and retained for normal assessment rather than silently treated as a verified geographic match.
- A source `remote: true` establishes remote work, not worldwide residency/work-permit eligibility. Preserve geography instead of dropping it or admitting foreign editions.

Keep optional location behavior unchanged: omitted locations impose no city restriction, while source Germany-market scope remains. Document city-center distance approximation and unknown-location limitations; do not add a paid geocoding provider.

**Alternative:** trust `remote` or a `.com` URL as proof of German eligibility. Rejected: the provider does not supply that guarantee.

### 5. Map source facts and retain provenance

Use `RawJob(source="arbeitnow", source_job_id=slug)` for the selected German edition. Missing/invalid stable slug, title, company or safe source URL invalidates a record; missing optional facts do not.

Strip script/style elements and convert HTML to readable block-separated text using existing HTML utilities, preserving meaningful headings/lists. Preserve original feed facts in minimal source metadata: tags, job types, remote flag, source-created timestamp, country/location uncertainty and matched profiles. Treat all feed text as untrusted data.

Map `remote: true` to remote; false remains unknown absent explicit affirmative work-mode evidence. Map only recognized, unambiguous job-type tokens to contract/working-time facts; preserve unknown tokens rather than guessing. Missing salary stays unset. Convert valid Unix seconds to UTC source date for the app's existing `posted_at` age approximation, retaining timestamp provenance and explicitly documenting that it is not the original employer publication date. Invalid timestamps leave age unknown and produce a warning; never use "today" as fallback.

The record's URL remains its Arbeitnow ad URL. The feed has no dedicated verified employer application URL/reference: leave `application_url` unset. Existing evaluation/form finding may locate a safe application path later. Skip detail reading naturally when the API description is present; missing descriptions take the existing fetch/unavailable path.

Reuse current source/URL/fingerprint deduplication, including the newly added verified employer requisition protection. Do not infer a verified requisition from a slug or overwrite existing job/application state.

**Alternative:** extract guessed salary/reference/application destinations from slug or arbitrary description links. Rejected: it creates incorrect facts and unsafe navigation.

### 6. Attribution and existing workflow integration

Retain `job_sources` URL/source metadata regardless of which source supplied primary `job_url`. Add a generated `arbeitnow_url` CSV column populated from stored source rows; users still edit only status/notes. Normal source counts/events already flow to summaries/statistics; extend only needed counters and persisted-request quota display, not a new reporting subsystem.

Pass an optional Arbeitnow attribution link into local package data for `job.md`, README and `apply.html`; render a clearly labeled clickable source link with safe URL/HTML escaping. This must work when Arbeitnow is a secondary source. Reuse a single source-link extraction helper for CSV/rendering where appropriate. Do not include provider attribution in employer-facing letters/email content.

Newly generated packages carry attribution; do not retroactively rebuild already edited applications just because a duplicate added a source. The generated CSV/source inspection exposes its backlink immediately. Form-finder link refreshes must preserve the attribution block and normal helper behavior.

`search` and `run` use the existing stage ordering and employer-discovery orchestration. Arbeitnow-only jobs are search-sourced, not alert seeds; jobs merged with alerts retain alert-first priority and may qualify for existing employer recovery. No independent employer scanning is added here.

Overall `run --dry-run` retains its existing network/discovery behavior. Configuration validation and displaying disabled source settings cause no network requests.

## Risks / Trade-offs

- Mixed-country feed and unstructured eligibility -> exclude foreign editions/explicit incompatibility, preserve uncertainty and disclose incomplete geography rather than invent facts.
- Recent-page coverage misses older jobs -> configurable finite pages, partial reporting and no exhaustive-search claim.
- Profile-specific filtering can suppress valid matches -> nominate an accepting profile across the selected set before normal ingestion.
- Source timestamps may differ from original publication -> retain provenance, document age approximation and never default unknown dates to today.
- API fields/terms may change -> typed fixtures, envelope validation, visible failures and link-back; no public/commercial redistribution assumption.
- Extra application attribution fields may disturb existing templates -> optional defaults, escaped links, unchanged non-Arbeitnow snapshots and form-finder refresh regression coverage.
- Concurrent employer-careers work touches adjacent files -> read/integrate latest code during apply and do not change its behavior or task status.

## Migration Plan

1. Add configuration with disabled default and setup prompt; no key, package installation or SQLite schema migration.
2. Add adapter and request accounting using existing `api_usage`, then wire the default source factory and shared profile snapshot.
3. Add generated attribution/reporting with backwards-compatible CSV input and optional render fields; preserve historical jobs/packages.
4. Validate fixtures and one bounded live GET after permission; live tests remain opt-in.
5. Disable `sources.arbeitnow.enabled` to roll back operationally. Keep discovered jobs, attribution and usage history.
