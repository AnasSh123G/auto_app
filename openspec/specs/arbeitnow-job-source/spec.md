# arbeitnow-job-source Specification

## Purpose
Discovers relevant jobs from Arbeitnow's free public feed and carries their descriptions and attributed source links into the existing search, evaluation and preparation workflow without requiring credentials.

## Requirements

### Requirement: Configurable credential-free source
The system SHALL offer Arbeitnow in setup and source configuration, disabled when omitted to preserve existing network behavior. When enabled it SHALL participate in normal search and run commands without credentials. When disabled it SHALL make no Arbeitnow requests. Invalid settings SHALL produce normal configuration errors.

#### Scenario: Enable without an account
- **WHEN** the user enables Arbeitnow and runs a search
- **THEN** it reads the public feed without asking for a key or enabling a paid API

#### Scenario: Existing configuration
- **WHEN** an existing config omits Arbeitnow
- **THEN** existing sources continue unchanged and no Arbeitnow request occurs

### Requirement: Share a bounded feed across profiles
Each search invocation SHALL fetch each selected feed page at most once and reuse its records across selected profiles. Matching SHALL happen locally using configured keyword phrases in title, tags or description. The source SHALL NOT invent unsupported API filters, crawl all historical pages or repeat requests for each keyword/location.

#### Scenario: Several profiles use one feed
- **WHEN** three selected profiles contain several keywords and locations
- **THEN** they share the same bounded feed pages, and a listing matching none is not ingested

#### Scenario: Selected profile only
- **WHEN** the user runs search for one named profile
- **THEN** only that profile participates in Arbeitnow matching and tagging

### Requirement: Apply known location restrictions conservatively
The source SHALL exclude foreign-edition listings and explicit foreign-country facts from its Germany-focused results. Known locations SHALL respect configured cities/radii using available evidence. Unresolved location or work-eligibility facts SHALL remain unknown and be counted for review, not invented. Remote alone SHALL NOT establish German eligibility or compatibility with every location.

#### Scenario: Foreign edition returned by the German endpoint
- **WHEN** a feed record points to Arbeitnow's UK, French or Swiss edition
- **THEN** it is excluded even if its keywords match or it is remote

#### Scenario: Known location outside a configured radius
- **WHEN** Berlin and Munich are resolvable locations and a profile allows only Berlin within 30 km
- **THEN** the Munich listing is excluded for that profile

#### Scenario: Location cannot be resolved
- **WHEN** a Germany-market record has a location that cannot be compared reliably
- **THEN** its location remains unchanged, uncertainty is reported and remote status is not used to manufacture a match

### Requirement: Normalize feed facts without guessing
Each accepted record SHALL preserve stable Arbeitnow identity, employer/title, source URL and available description/location facts. HTML SHALL become readable text without scripts or executable markup. Only affirmative job-type/remote facts SHALL become normalized fields. Missing salaries, original publication dates, employer references and application URLs SHALL NOT be invented.

#### Scenario: Remote flag is false
- **WHEN** a record has `remote: false` without explicit onsite/hybrid evidence
- **THEN** it does not become a proven onsite-only job

#### Scenario: Description supplied by API
- **WHEN** a valid record contains a nonempty HTML description
- **THEN** readable description text is stored and normal detail fetching need not revisit that listing

#### Scenario: Feed timestamp differs from employer date
- **WHEN** the API supplies only `created_at`
- **THEN** it is recorded as the source listing timestamp, never described as a verified employer publication date

### Requirement: Reuse filters without multi-profile false rejection
Relevant listings SHALL use existing optional prefilters and ingestion safeguards. A listing accepted by any selected matching profile SHALL NOT remain filtered merely because another matching profile rejects it. Matching-profile provenance and a stable primary profile SHALL be retained. Unknown filter facts SHALL retain existing conservative behavior.

#### Scenario: One profile excludes a role another accepts
- **WHEN** a listing matches two profiles and only the first excludes its position type
- **THEN** it is queued under an accepting profile rather than retained as filtered

#### Scenario: Salary not supplied
- **WHEN** a relevant listing lacks salary and a selected profile specifies a minimum
- **THEN** no salary is invented and missing salary alone does not hard-filter it

### Requirement: Deduplicate without changing job state
Repeated Arbeitnow records SHALL reuse source identity and normal cross-source deduplication, including verified-requisition protections. Duplicates SHALL retain all source provenance without repeated evaluation or overwrite of user/application state. Arbeitnow-only jobs SHALL remain search-sourced; a job also received through alerts SHALL retain alert-first priority.

#### Scenario: Repeat search
- **WHEN** the same Arbeitnow record appears again
- **THEN** it links to the existing job without resetting status, notes, evaluations or prepared files

#### Scenario: Existing alert job also appears in Arbeitnow
- **WHEN** normal deduplication identifies the listing as the same vacancy
- **THEN** Arbeitnow is added as a source and the job stays alert-prioritized

### Requirement: Bound traffic and stop on refusal
Requests SHALL obey configured page, per-run/day/month and pacing limits; every attempted request SHALL count, including failures. Pagination targets SHALL be confined to the public feed endpoint and validated before request. Loops SHALL stop. Refusals and rate limits SHALL stop source traffic without retries or browser fallback. Interrupted pagination SHALL be reported as partial, not exhaustive coverage.

#### Scenario: Page limit reached
- **WHEN** the configured page cap is reached while a next page exists
- **THEN** no next request is sent, discovered jobs remain and the source reports partial coverage

#### Scenario: Rate limit
- **WHEN** Arbeitnow returns HTTP 429
- **THEN** it sends no more requests in the invocation, retains earlier jobs and records the stop

#### Scenario: Unsafe next link
- **WHEN** pagination points to an unrelated host, private address or another endpoint
- **THEN** the address is not requested and the invalid continuation is reported

### Requirement: Report malformed data and isolate source failures
Malformed individual records SHALL be skipped with a visible count/reason while valid records continue. Invalid page structure and request errors SHALL stop the source with an explicit reason, preserving earlier records and allowing other sources to continue. The source SHALL NOT disguise a schema/network error as a successful empty result.

#### Scenario: One broken record
- **WHEN** a page contains a listing missing a stable ID or usable title/company
- **THEN** the record is counted as invalid and valid siblings still participate in matching

#### Scenario: Later page fails
- **WHEN** page two is malformed after page one produced valid listings
- **THEN** page-one listings remain, the error is reported and alerts/other sources continue

### Requirement: Attribute Arbeitnow on local listing surfaces
Stored jobs SHALL retain Arbeitnow source identity and its listing URL. User-facing job listings and newly generated application helpers/snapshots displaying such jobs SHALL include a clickable Arbeitnow attribution even when another source supplied the primary URL. This SHALL NOT rebuild existing edited packages, add attribution to employer-facing letters or send applicant data to Arbeitnow.

#### Scenario: Cross-source job has an employer primary link
- **WHEN** a package is generated for a job that includes Arbeitnow among its sources but its primary ad URL is elsewhere
- **THEN** its local job snapshot/helper retains a separate clickable Arbeitnow source link

### Requirement: Preserve pipeline limits and application safeguards
Arbeitnow jobs SHALL use existing evaluation/preparation budgets, modes, source reporting and CSV tracking. This source SHALL NOT independently search employer websites, launch browsers, set form status to found, submit applications or alter consent. Overall run dry-run behavior SHALL remain unchanged.

#### Scenario: Shared evaluation budget
- **WHEN** Arbeitnow adds eligible jobs after alert jobs and the run reaches its evaluation cap
- **THEN** existing alert-first ordering applies, additional jobs wait and no extra model budget is granted

#### Scenario: No application URL in feed
- **WHEN** the feed provides only an Arbeitnow listing URL
- **THEN** the URL stays an ad entry, not a verified employer website or found application form
