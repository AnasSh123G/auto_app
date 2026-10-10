# Spec Delta

## Purpose

Finds verified employer careers sites from job alerts, remembers them locally across runs, recovers the exact employer vacancy and discovers additional relevant positions without depending on automated job-board browsing.

## Compatibility with existing capabilities

`application-form-finding` remains unchanged: discovery provides a verified employer posting or application entry, not a claim that a form was found. Its guards, four-transition limit and user-triggered filling still apply to each form-finding attempt.

This capability supersedes only name/title-based merging of conflicting, verified employer requisitions in the pending `job-discovery` requirements "Deduplication across sources" and "One job for multi-city postings". Their other scenarios remain in force. Reconcile this exception into the owning capability before either affected change is archived.

## ADDED Requirements

### Requirement: Discover careers sites from alert employers
When enabled, employer discovery SHALL run after alert ingestion and before detail fetching. It SHALL consider unclosed, unfiltered alert jobs in the selected profiles, including earlier alerts awaiting recovery. It SHALL group seeds by verified employer, avoid recursively expanding newly discovered employers, and leave existing sources usable when discovery is disabled or fails.

#### Scenario: Existing LinkedIn alert can be recovered
- **WHEN** an eligible LinkedIn alert has no readable description and employer discovery is enabled
- **THEN** the employer careers workflow runs without needing to read LinkedIn

#### Scenario: Disabled source preserves behavior
- **WHEN** employer discovery is disabled
- **THEN** no employer search or scan runs and existing search/fetch behavior is unchanged

### Requirement: Use the selected search service explicitly
For employers without a verified local site or trusted supplied employer link, the system SHALL use Parallel Search in explicit Fast mode with bounded results. Missing credentials SHALL disable web lookup with a visible reason, not silently select another provider or more expensive mode. Known verified sites SHALL remain scannable without search credentials.

#### Scenario: Unknown employer lookup
- **WHEN** an eligible employer has no verified website and a search key is configured
- **THEN** the request explicitly selects Fast and asks for the employer's official website or careers page

#### Scenario: Missing key with cached site
- **WHEN** no search key exists but the employer has a verified cached careers site
- **THEN** that site can be scanned and no search request is made

### Requirement: Verify employer identity before trusting a site
Search results SHALL be candidates, not proof. A site SHALL be trusted only with corroborating public employer identity evidence, or explicit user confirmation. Namesakes, directories, recruiters and shared ATS domains SHALL NOT establish identity from name similarity or ranking alone. Unresolved competing identities SHALL produce an ambiguous result without importing or linking their vacancies.

#### Scenario: Two employers share a name
- **WHEN** search returns two companies with the alert's name and available evidence cannot distinguish them
- **THEN** neither is automatically cached as verified and the ambiguity is reported

#### Scenario: Employer links to an ATS tenant
- **WHEN** a verified employer careers page links to its specific ATS tenant or employer-specific path
- **THEN** that bounded tenant/path may be trusted, but unrelated employers on the same ATS remain excluded

### Requirement: Persist and reuse verified employer websites
The system SHALL persist verified employer websites, careers/ATS locations, identity evidence, aliases and outcomes locally across runs. Later alerts for that verified employer SHALL reuse the mapping without repeating search. A shared normalized name SHALL NOT merge unrelated identities. Website reuse SHALL be independent of vacancy freshness.

#### Scenario: Another alert arrives next week
- **WHEN** a later command receives another vacancy from an employer with a valid saved mapping
- **THEN** it uses the saved website with zero search requests for that employer

#### Scenario: Requisition expires
- **WHEN** one saved employer vacancy expires but the employer website remains valid
- **THEN** the website mapping remains reusable and no company lookup is repeated merely because the vacancy expired

### Requirement: Allow explicit correction and bounded rediscovery
The user SHALL be able to inspect employer mappings, confirm or replace a website and request fresh lookup. Routine retries SHALL reuse valid mappings. Unreachable or blocked sites SHALL retain their mapping with a failure reason, not trigger evasive search. Definitively invalid websites may be rediscovered once per employer attempt within existing budgets.

#### Scenario: Correct a namesake mapping
- **WHEN** the user supplies the correct employer website for a selected job
- **THEN** the correction is recorded and unrelated employer mappings remain untouched

#### Scenario: Cached host refuses access
- **WHEN** a saved employer site returns HTTP 403
- **THEN** the mapping is retained, the block is reported, and no alternate-host search attempts bypass it

### Requirement: Bound search usage and expose costs
Every attempted search request SHALL count against persisted per-run, daily and monthly caps, including failures. Each employer attempt SHALL permit at most two search requests, with no automatic request retries. Cap exhaustion SHALL defer remaining lookups without discarding jobs. Reports SHALL separate search usage/estimated gross cost from LLM costs and SHALL NOT assume provider credits are available.

#### Scenario: Monthly cap reached
- **WHEN** the monthly search cap has been consumed
- **THEN** no further search request is sent, cached sites remain usable and pending lookups are reported

#### Scenario: Failed search costs a request
- **WHEN** a search times out or returns an error
- **THEN** its request reservation remains counted and it is not automatically retried

### Requirement: Keep search inputs public
Search requests SHALL contain only sanitized public employer and job hints. Applicant profile, CV, letters, mailbox contents, email addresses, account identifiers and authentication/tracking tokens SHALL NOT be sent. Search text and page contents SHALL be treated as untrusted data. API credentials SHALL remain local and be redacted from logs.

#### Scenario: Alert contains personalized tracking information
- **WHEN** an employer lookup is constructed from an alert
- **THEN** only public employer/vacancy hints are sent, not the original email or personalized link

### Requirement: Traverse bounded employer careers content
Scanning SHALL use public GETs on verified employer origins and explicitly authorized ATS tenants/paths, following declarative careers links, listing pagination and public feeds. Redirects and feed requests SHALL consume page budgets. Per-attempt and run limits SHALL bound pages and ingested openings; loops SHALL stop. Incomplete scans SHALL be reported as partial, not as proof that no jobs exist.

#### Scenario: Careers page has pagination
- **WHEN** a verified careers page links to further listing pages within the remaining budget
- **THEN** relevant listings on those pages are considered, and each request consumes the scan budget

#### Scenario: JavaScript-only directory
- **WHEN** no supported declarative listing or feed can be read
- **THEN** the scan records unsupported rendering/manual handling rather than claiming no vacancies

### Requirement: Preserve navigation restrictions during discovery
Every search-result, page, redirect and feed target SHALL be validated before requesting it. Restricted platforms and subdomains, non-public network targets, unsafe schemes and credentials in URLs SHALL be rejected. Discovery SHALL stop on blocks, login or challenges; it SHALL NOT launch a browser, fill, upload, submit, accept consent or evade access restrictions.

#### Scenario: Search returns LinkedIn instead of a careers site
- **WHEN** a candidate or redirect points to a restricted platform
- **THEN** that target is not requested by employer discovery

#### Scenario: Redirect points to a private service
- **WHEN** an otherwise public candidate redirects to a loopback or private-network address
- **THEN** the destination is rejected before any request is sent

### Requirement: Match the alert vacancy conservatively
The original alert job SHALL be linked only to a verified posting for the same vacancy. Matching SHALL prioritize employer-scoped requisition identity; without it, a unique title match requires compatible location/work mode and employer evidence. Conflicting references, multiple plausible matches or title-only similarity SHALL remain unresolved. Additional roles SHALL NOT substitute for the original vacancy.

#### Scenario: Same title with different references
- **WHEN** the employer lists two Data Engineer vacancies with different requisition IDs and the alert identifies one
- **THEN** only that requisition is attached to the alert job

#### Scenario: Alert lacks enough evidence
- **WHEN** several employer postings plausibly match an alert
- **THEN** no posting is selected as the original vacancy and the candidates/reason are reported

### Requirement: Enrich original jobs without destructive rewrites
Verified postings SHALL retain provenance and original ad identity. Before evaluation, they may fill missing details or replace platform-only application entries. Routine scans SHALL NOT overwrite user text, valid found forms, evaluations, packages or submission history. Only recovered, unevaluated `description_unavailable` jobs may return to `new`; other statuses SHALL remain unchanged.

#### Scenario: Recover an unavailable description
- **WHEN** an unevaluated alert job lacks a description and its exact employer posting is verified
- **THEN** the description and safe employer entry become available for normal filtering/evaluation without a duplicate job, and a recovered unevaluated `description_unavailable` job returns to `new` with a recorded reason

#### Scenario: Previously prepared application
- **WHEN** discovery finds the employer posting for a prepared job
- **THEN** its provenance is saved but edited application files and existing evaluation/status remain unchanged

### Requirement: Discover additional relevant openings
Each scan SHALL inspect other openings of the verified employer against the seed jobs' selected search profiles, using title and available description keywords plus existing filters. Matching openings SHALL enter the normal evaluation pipeline as distinct employer-careers sources. Unknown filter facts SHALL NOT be fabricated. Unrelated or closed openings SHALL NOT be queued as relevant jobs.

#### Scenario: Employer advertises several related positions
- **WHEN** a Data Engineer alert leads to careers listings with another matching Data Engineer role, a matching BI Developer role and an unrelated accountant role
- **THEN** roles matching configured profile keywords are considered for ingestion, the accountant is not, and no application is automatically submitted

#### Scenario: Different profiles share an employer
- **WHEN** selected seed jobs associate the employer with multiple search profiles
- **THEN** discovered openings are checked against each relevant profile without duplicating the vacancy

### Requirement: Preserve vacancy distinctions during deduplication
Original-vacancy reconciliation and additional-role ingestion SHALL reuse source/URL deduplication while preventing merges between conflicting verified employer requisitions. A single vacancy repeated across alerts, pages or locations SHALL remain one job. Different roles or requisitions SHALL stay separate even when normalized employer/title match.

#### Scenario: Same title is not the same vacancy
- **WHEN** two verified postings share employer, title and city but have distinct requisition IDs
- **THEN** two jobs are retained instead of being collapsed by fingerprint or multi-city grouping

#### Scenario: Same requisition in several cities
- **WHEN** the same employer-scoped requisition appears in several locations
- **THEN** it is retained as one vacancy with those locations

### Requirement: Reuse scans without suppressing new alert matching
Verified website reuse SHALL be persistent; listing scans SHALL use a separate freshness window and retained listing snapshots. A fresh snapshot SHALL be reusable for a new alert or newly selected profile without another scan. Partial/failed snapshots SHALL NOT count as complete. Repeated scans SHALL be idempotent and new alerts SHALL remain eligible when budgets defer processing.

#### Scenario: Multiple alerts from one employer
- **WHEN** several eligible alert jobs share an employer in one run
- **THEN** the employer is scanned once and the results are reconciled against all those jobs

#### Scenario: New profile uses a fresh snapshot
- **WHEN** a new selected profile needs an employer whose complete listing snapshot is still fresh
- **THEN** the snapshot is checked against that profile instead of hiding its relevant openings or repeating the scan

### Requirement: Provide on-demand and read-only controls
`autoapp employers` SHALL inspect cached sites; `autoapp employers --discover` SHALL run eligible employer lookup/scanning, optionally limited by job/profile. `--again` SHALL bypass scan freshness, not valid website reuse; `--refresh-site` SHALL explicitly request lookup. `--website` SHALL confirm a site for a selected job. `--dry-run` SHALL describe intended work without requests, writes, migration or setup side effects.

#### Scenario: Inspect planned work
- **WHEN** the user runs `autoapp employers --discover --dry-run`
- **THEN** selected employers, cached sites and planned actions are listed and all workspace contents remain unchanged

#### Scenario: Force scan of a known employer
- **WHEN** the user runs `autoapp employers --discover --job <id> --again`
- **THEN** the saved website is scanned again without searching for it anew

### Requirement: Report employer discovery independently
The system SHALL expose saved website/careers locations, exact-match outcomes, scan timestamps/reasons, added/deduplicated openings and request usage through command output, run summaries and statistics. Counts SHALL not inflate existing job totals. Employer-posting discovery SHALL NOT be reported as a found application form.

#### Scenario: Careers posting found but form unknown
- **WHEN** discovery verifies a job posting whose form has not yet been examined
- **THEN** it reports a verified employer posting, leaving form status to the existing form finder

### Requirement: Isolate expected failures and preserve resumability
Expected provider, network, identity and unsupported-layout failures SHALL be recorded with distinct reasons and SHALL NOT stop other employers or sources. Authentication failure SHALL stop further search calls for that run; already verified sites may still be scanned. Unexpected parser/storage errors SHALL use explicit error reporting, not a successful empty result.

#### Scenario: Search authentication fails
- **WHEN** Parallel rejects credentials
- **THEN** no further search calls are made in that run, the error is visible and cached-employer scans remain possible

#### Scenario: Scan budget interrupts processing
- **WHEN** the page or opening budget is exhausted before an employer finishes
- **THEN** the scan is partial, existing discovered jobs persist and remaining seed jobs are reported as deferred
