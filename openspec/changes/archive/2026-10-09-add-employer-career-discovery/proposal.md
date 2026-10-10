# Proposal

## Why

Alert emails identify promising employers, but platform links can prevent the app from reading the original vacancy or reaching its application. Looking up the employer's own careers site can recover that vacancy and reveal other relevant openings, while remembering verified websites avoids paying for the same employer lookup again.

## What Changes

- Add opt-in employer discovery after alert ingestion and before detail fetching, plus an on-demand command for existing alert jobs.
- Use Parallel Search with explicit `mode: fast` to locate official employer websites and careers pages when no verified local mapping or trusted employer link is available.
- Persist verified employer identity, official website, careers/ATS links, aliases, evidence and scan outcomes locally across runs. Subsequent alerts reuse those sites rather than repeating web search.
- Browse bounded public employer/careers pages and employer-authorized ATS tenants; locate the exact alert vacancy separately from other relevant positions.
- Attach verified employer postings to the original alert job without losing its ad URL or state. Import other relevant openings as employer-careers sources through existing profile filters, evaluation and preparation safeguards.
- Preserve distinct requisitions even when employer/title coincide; retain existing multi-city deduplication when postings genuinely represent one vacancy.
- Report ambiguous employers, missing/expired vacancies, unsupported JavaScript listings, blocks and budget stops explicitly. Allow user correction of cached sites and explicit retry, without asking the user to supply websites for the normal successful path.
- Add persisted search-request caps, bounded page traffic, scan freshness and separate discovery counts. Use public job/employer information only; never send applicant documents or credentials to search.

## Capabilities

### New Capabilities

- `employer-career-discovery`: verified employer-site lookup and persistent reuse, safe careers-site scanning, exact vacancy reconciliation and related opening ingestion.

### Modified Capabilities

None. `application-form-finding` retains its existing navigation and fill contract; discovery supplies verified employer posting/application addresses to it.

The pending `job-discovery` capability in `add-automated-job-applications` owns existing source ingestion, filtering and deduplication. This change adds a source and narrowly supersedes name/title-only merging when verified employer requisitions conflict. Reconcile that exception into the owning pending capability before archival; do not weaken other deduplication or alert-first requirements.

## Impact

- Existing Python CLI, SQLite repository/migrations, search/fetch pipeline, source ingestion, setup/configuration, CSV/reporting and documentation.
- Parallel Search REST API, using existing HTTP dependencies and `PARALLEL_API_KEY`; no mandatory new SDK.
- Additional employer-page GETs in enabled runs, including review/test and run dry-run; a dedicated employer-discovery dry-run remains strictly read-only.
- No LinkedIn/Indeed/StepStone search scraping or application automation, login/challenge bypass, stealth browser, submission or consent. Reading allowed employer sites is distinct from restricted-platform automation.
- Bounded static HTML/structured listings and declarative pagination/ATS feeds are supported. Arbitrary JavaScript execution, guessed private endpoints, whole-web crawling and scheduled monitoring of every cached employer are out of scope.
