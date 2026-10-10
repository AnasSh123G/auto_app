# Design

## Context

See `proposal.md` for motivation and `specs/employer-career-discovery/spec.md` for the contract.

- `steps/search.py` ingests APIs followed by mailbox alerts. Alert emails are marked processed, so subsequent discovery must select persisted jobs rather than depend on rereading emails.
- `steps/pipeline.py` currently runs search, fetch, evaluate and prepare. `steps/fetch.py` reads `job_url` for `new` jobs lacking descriptions; recovered `description_unavailable` jobs need an explicit state transition before evaluation.
- `RawJob` and `discovery/ingest.py` already support source metadata, gap filling and cross-source linking. However, fingerprint and 30-day company/title grouping would collapse distinct requisitions. `job_sources` has globally unique `(source, source_job_id)`, so ATS references must be employer-scoped.
- `SearchProfile` owns keywords and optional exclusions/filters. `discovery/prefilter.py` treats unknown facts conservatively and excludes platform salary estimates from hard salary decisions.
- `sources/budget.py` persists attempted API requests across runs. `RunContext` owns a shared HTTP fetcher with honest User-Agent, explicit redirects, address cache, pacing and host blocks.
- SQLite migrations are append-only. Mutating commands use workspace locking and CSV import/export conventions. Command dry-runs such as `forms --dry-run` differ from the overall pipeline's `run --dry-run`, which permits discovery/network work.
- Only form finding and alert-first evaluation have canonical specs. The pending `job-discovery` spec needs the narrow requisition exception reconciled before archival; it is not a valid target for a main-spec MODIFIED delta today.

## Goals / Non-Goals

**Goals:** independent employer lookup; conservative identity and vacancy evidence; durable local reuse; finite, resumable employer discovery; new jobs flowing through existing safety and evaluation machinery.

**Non-Goals:** no generic autonomous browsing agent, search-engine HTML scraping, arbitrary browser rendering, automatic account access, broad ATS integrations, vacancy monitoring of all historic employers or new submission behavior. No new LLM task for employer identity: uncertain identities stop instead of being guessed.

## Decisions

### 1. Separate employer discovery from form finding

Add an employer discovery service and a pipeline stage between search and fetch. Integrate the same service into the standalone `search` command after ingestion and the new on-demand command; use an explicit orchestration boundary so `run` does not invoke it twice.

Select eligible stored alert jobs in selected profiles, excluding filtered/submitted/closed jobs. Include earlier unavailable jobs and prepared jobs for provenance-only recovery. Preserve alert-first seed order under employer/run limits, group using verified identities, and process each identity once. Do not scan employers newly introduced by related jobs: one-hop expansion only.

Attach exact employer posting provenance to the alert job. For unevaluated jobs, store a safe employer application entry when missing or currently platform-only, preserving the replaced value as provenance. Prefer an explicit apply link, otherwise use the verified posting as the form-finder entry; do not label it a found form. Preserve a valid existing form and its restart entry. Prepared jobs receive metadata only; normal discovery does not invalidate evaluations, rewrite packages or repoint forms behind the user's back.

Prefer a verified employer posting for missing detail fetches without altering `job_url`. If discovery already recovered the description, no platform detail request is needed. Transition a recovered, unevaluated unavailable job to `new`, recording the recovery; do not resurrect filtered/submitted/closed jobs. Reapply ordinary filters using employer facts before evaluation.

**Alternative:** fold company search into `formfinder`. Rejected because finding other vacancies, persistent company identity and listing pagination exceed its per-application navigation contract.

### 2. Use Parallel's v1 Fast Search REST API

Use existing `httpx` with a separately scoped API client, not the employer-page client. `POST https://api.parallel.ai/v1/search`, header `x-api-key`, secret read from `PARALLEL_API_KEY` through existing environment/setup handling. Never forward that header to employer sites or API redirects; refuse endpoint redirects. Explicit request:

```json
{
  "mode": "fast",
  "objective": "Find the official employer website and careers page for the named employer and public location hints.",
  "search_queries": ["Employer Name careers", "Employer Name jobs City"],
  "max_chars_total": 6000,
  "advanced_settings": {"max_results": 10}
}
```

Query strings use actual sanitized public employer hints, not applicant data. The v1 response supplies `results` with URL/excerpts and optional title; parse it with typed, bounded models. Excerpts help prioritize candidates but cannot verify identity or current vacancy availability. Validate every selected address locally. Do not use Parallel Extract, Task, FindAll or a default/advanced mode.

Reserve requests before sending, using `RequestBudget` with source `parallel-search`. Max two requests per employer: first lookup, then a genuinely refined query if a successful result set is insufficient. No automatic retries after errors. 401/403 disable search for the run; 429 stops further calls for the run and reports rate limiting; timeouts/5xx defer the employer without hiding the failure. Cached-site scans remain possible. Parse/provider warnings appear in reports.

Official documentation checked during planning:
- [Search quickstart](https://docs.parallel.ai/search/search-quickstart)
- [Modes](https://docs.parallel.ai/search/modes)
- [v1 request/response schema](https://docs.parallel.ai/api-reference/search/search)

Fast is documented at $1/1,000 requests. Reports show an estimate at the documented/configured unit rate, excluding credits; they do not promise billed cost or free-tier availability. Mode and API schemas can change, so adapter contract fixtures pin the selected request fields.

**Alternative:** install an SDK or interchangeable providers immediately. Rejected: existing HTTP dependencies suffice and the user chose one provider.

### 3. Store identities, mappings and scans separately

Append a migration containing:

- `employers`: opaque local ID, display/legal name, verified website, identity status/evidence, provenance (`search`, trusted employer link or user confirmation), checked/updated timestamps.
- Employer aliases with disambiguation hints and source associations. Normalized names retrieve candidates but are not globally unique identity keys. Preserve significant legal-name distinctions; do not reuse the current aggressive fingerprint normalization as the sole cache key.
- Employer careers locations: canonical URL, origin and optional ATS tenant/path constraint, referring official URL and verification evidence. Shared ATS origin is never the employer ID.
- Seed associations: job-to-employer identity, original-vacancy reconciliation outcome, selected employer posting URL/reference and checked reason. Existing job-source records retain the alert and attached employer posting.
- Scan runs and listing snapshots: fetched page/provenance, normalized posting facts/reference, freshness timestamps, complete/partial status, processed profiles/seeds, counts and reasons.

Persist minimal public parsed data/evidence rather than full HTML or emails. A verified site does not expire just because its most recent jobs are old. Revisit the saved careers URL when the listing snapshot expires; if it has moved, check links from the still-valid official homepage before searching again. Only definitive official-site invalidation (e.g. 404/410 and no usable official entry), or explicit `--refresh-site`, permits new company lookup; temporary errors/blocks do not.

Complete listing snapshots are fresh for 24 hours by default; failure/ambiguity is negatively cached for 24 hours to avoid repeatedly paying for unresolved names. `--again` bypasses scan/failure freshness but retains verified sites. Snapshots are rematched against new alerts and selected profiles even inside that window. An incomplete snapshot can expose already verified openings but cannot suppress unfinished scanning as a complete result.

User-confirmed sites retain their provenance and are not silently replaced. Refresh proposes candidates requiring confirmation if they conflict with a pinned identity. All writes stay inside workspace locking; read-only inspection opens existing databases without migrating or creating them.

**Alternative:** flat company-name-to-domain JSON cache. Rejected: it cannot safely represent namesakes, ATS tenants, provenance, stale listings or correction.

### 4. Verify official identity and constrain a small crawler

Prefer a verified cache or an already trusted employer source link before search. Search candidates can be fetched for verification within the same employer page budget. Evidence must include employer identity from a public company/legal/contact page plus an independent compatible alert hint (e.g. location) or an official-site-to-tenant link. A plausible hostname, search rank or matching display name alone is insufficient. Recognize recruiter identities separately; never infer an unnamed client company.

Use a deterministic frontier: official homepage/careers links, relevant listing pages, declarative pagination and posting details. Extract JSON-LD `JobPosting`/`ItemList` first, then labeled HTML listings via existing extraction helpers and a small supported generic listing parser. Public JSON/XML feeds are allowed only when explicitly linked or declared by a supported page adapter; never guess hidden endpoints or replay authenticated/XHR calls. Initial coverage includes static employer listings and the CBC/onapply-style HTML detail path, not every ATS.

Prioritize exact seed-title/reference candidates before related postings. Page lists and opening counts remain bounded, including candidate verification pages, redirects, pagination and feeds. Treat omitted pagination/unsupported layout/limits as partial coverage, not no jobs. Preserve checkpoints and snapshots so retries are idempotent; rechecking the frontier is acceptable within request caches/budgets.

Use `ctx.http.fetch_once`, not redirect-following convenience methods. Share cache, delays and blocks with other public HTTP stages. Discovery has its own finite page budget, not a relaxation of form finder's four transitions. Validate targets against restricted domains, HTTP(S) scheme, credential-free URLs, public DNS/IP destinations and verified origin/ATS tenant scope before requesting. Candidate-verification pages are public but not yet trusted for importing jobs; cross-origin links need new identity/tenant evidence before expansion. Check redirects individually; resolve and constrain the actual connection destination to prevent private-address rebinding, not merely string-check the hostname.

Never execute page scripts or activate buttons/forms. Detect blocking login/challenge content and HTTP 403/429/999; preserve mappings and stop the affected host. Do not follow alternate domains to defeat a block.

**Alternative:** universal Playwright crawler. Rejected for this change: it broadens safety, rendering and traffic considerably; unsupported JS sites get an honest manual outcome.

### 5. Distinguish exact matches from additional jobs

Build typed listing records with employer-scoped requisition key, title, locations/work mode, description, posting/expiry dates, employer-stated salary, posting URL and apply URL when present.

Exact match ordering:
1. Employer identity plus same known requisition identifier.
2. Otherwise, a unique normalized full-title match with affirmative compatible location/work mode evidence and the verified employer. Reject conflicting known facts; unknown location is not affirmative evidence. Truncated titles need corroborating reference/location and a unique full-title completion, not broad fuzzy substitution.
3. Ambiguous/conflicting/insufficient evidence stays unresolved; record candidates/reason. A missing match in a partial scan does not imply expiry, and never automatically closes the original job.

Attach an exact-match posting explicitly to the selected job rather than relying on company/title merging to choose it. Additional roles enter `RawJob(source="employer-careers")` with ID scoped by verified employer + tenant + requisition (otherwise canonical employer posting URL), selected profile and provenance.

Reuse `ingest`, but add narrowly scoped identity-aware guards before fingerprint/group fallback. Different known references cannot merge. For separate verified URLs without proof of shared requisition, be conservative: do not collapse employer postings by company/title alone. Genuine same-reference multi-location records still merge. Preserve the existing source behavior for records without employer evidence.

For related roles, compare normalized profile keyword phrases against titles/available descriptions and reuse prefilters. Fetch candidate details when needed within budget, then evaluate using existing models/meter. No semantic LLM discovery pass or extra model budget. Use all selected profiles associated with seeds; store matching-profile provenance and pick the existing stable primary-profile ordering for a single job/evaluation. A failure of one profile must not suppress a match in another.

Exact recovered jobs retain their alert source and alert-first priority. Additional openings are not falsely tagged as alert jobs and do not preempt them. Existing mode/sending safeguards apply unchanged; discovering an opening is not authorization to submit it.

**Alternative:** ingest every search excerpt as a new job. Rejected: search can be stale, incomplete, unrelated or refer to a different employer.

### 6. Configured opt-in, finite defaults and explicit controls

Add `sources.employer_careers` with validated defaults:

| Setting | Default |
| --- | --- |
| enabled | false; setup offers explicit enablement |
| max_employers_per_run | 10 |
| max_search_requests_per_run / day / month | 20 / 50 / 1000 |
| max_pages_per_employer / run | 20 / 100 |
| max_openings_per_employer / run | 50 / 100 |
| scan_ttl_hours / failure_ttl_hours | 24 / 24 |
| search_cost_per_1000_usd (estimate only) | 1 |

The two-search-per-employer ceiling and 10-result response limit are fixed safety bounds. Requests count whether successful or not. No implicit activation just because a key happens to exist: setup explains expected traffic/cost and caches before enabling. A configured enabled source with no key visibly skips unknown lookup but can scan cached sites. On-demand `--discover` explicitly requests the selected work even when routine integration is disabled.

`autoapp employers` defaults to read-only inspection; `--discover`, `--job`, `--profile`, `--again`, `--refresh-site`, `--website` and `--dry-run` follow the spec. `--website` requires `--job` and records explicit confirmation; reject conflicting/invalid combinations clearly. `--refresh-site` is an explicit search action and requires discovery. Mutating commands use standard CSV/lock/event handling. Dry-run validates/selects from read-only state without `RunContext.open` migration/setup side effects.

Add an "Employer sites" menu entry consistent with existing source controls. Emit discrete lookup/scan/reconciliation events and count source-created jobs separately from matched alert jobs. Expose generated employer site/posting/outcome columns in CSV and statistics, separate from editable user notes. Do not modify prepared application files solely for employer metadata.

## Risks / Trade-offs

- Wrong company or recruiter client inference -> conservative identity evidence, namesake separation, visible ambiguity and user correction; lower recall is preferable to applying to the wrong role.
- German searches have uncertain retrieval quality -> retain explicit Fast, combine public company/location and careers/jobs queries, measure employer verification in live acceptance; no silent paid-mode escalation.
- Static coverage misses JS-only careers sites -> partial/manual outcomes; no claim of exhaustive vacancy coverage.
- Keyword discovery can miss semantic synonyms -> use configured profile keywords and descriptions; users can extend profiles without introducing unbounded model discovery.
- Snapshot changes during scanning -> timestamp each record, report partial coverage and revalidate before preparation; never infer closure from absence.
- DNS and redirect guards are more involved than current URL checks -> isolate the public-target guard and test zero private/restricted requests with controlled resolvers/transports.
- Additional traffic/cost -> opt-in defaults, persisted request caps, shared host pacing and indefinite verified-site reuse; report gross estimates, not assumed credits.
- Existing title-based grouping may already have merged distinct legacy alerts -> do not silently split historical applications; flag detected reference conflicts for user review, while preserving newly verified distinct jobs.

## Migration Plan

1. Append employer/cache/provenance tables and indexes without rewriting existing jobs or migrations. No search or network activity occurs during migration.
2. Ship configuration defaulting to disabled; existing configs remain valid. Explain setup and store the key only through existing local secret handling.
3. Add adapters and commands with fixture tests before enabling integration. Reconcile the pending job-discovery requisition exception, preserving all unrelated scenarios.
4. Exercise the pipeline with an independent employer lookup and a second run proving zero repeated company searches. Live validation requires permission and credentials; fixtures alone do not prove provider/site coverage.
5. Roll back operationally by disabling `sources.employer_careers`; retain cached mappings and job provenance. Do not delete discovered jobs, downgrade SQLite or erase user edits/history.
