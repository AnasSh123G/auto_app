# alert-resolution Specification

## Purpose
Decides how a job that arrived through a job-alert email gets its full ad: by searching for the employer's own posting (search-first, the default) or by reading the job board's page (platform, the earlier behavior).

## Requirements

### Requirement: Selectable alert mode with search-first default
The configuration SHALL offer `sources.alerts.mode` with values `search_first` and `platform`. A missing value SHALL mean `search_first`. An unknown value SHALL be rejected at config load with a message naming the allowed values. The mode SHALL apply to every alert source: `linkedin-alert`, `indeed-alert`, `indeed-match`, `stepstone-alert`, `stepstone-alert-recommended` and `workwise-alert`.

#### Scenario: Config without a mode
- **WHEN** a workspace config has no `sources.alerts.mode`
- **THEN** alert jobs are handled search-first

#### Scenario: Old behavior chosen explicitly
- **WHEN** the config sets `sources.alerts.mode: platform`
- **THEN** alert jobs are handled exactly as before this change, including detail fetching of job-board pages and employer discovery only when `employer_careers.enabled` is set

#### Scenario: Misspelled mode
- **WHEN** the config sets `sources.alerts.mode: serch_first`
- **THEN** loading the config fails and the message lists `search_first` and `platform`

### Requirement: Search-first uses only the alert email's content
In `search_first`, the system SHALL take each alert job's title, company and location from the email. It SHALL NOT open the job's LinkedIn, Indeed, StepStone or Workwise page, in the app's browser or over HTTP, to read the description, title or company. This SHALL NOT change link handling done during ingestion: StepStone's single non-following redirect resolution and Indeed's offline decoding stay as they are.

#### Scenario: LinkedIn card in search-first
- **WHEN** a LinkedIn alert card for "Data Engineer (m/w/d)" at "AITAD GmbH" is ingested in search-first mode
- **THEN** no request is sent to linkedin.com for that job during detail fetching, evaluation or preparation

#### Scenario: Workwise card in search-first
- **WHEN** a Workwise alert card is ingested in search-first mode
- **THEN** its workwise.io job page is not requested to read its description or company

### Requirement: Direct own-ad search runs first
In `search_first`, every new, unfiltered alert job without a description SHALL first get the direct own-ad search, before detail fetching and evaluation. The search query is the employer and the job title, with the city when known. A result SHALL be used only under the existing own-ad acceptance rules: an employer page or employer applicant system, open, naming the title and the employer. A verified ad SHALL supply the description and the application address.

#### Scenario: Ad found on the employer's site
- **WHEN** the search for "Intrum Deutschland GmbH" "Data Engineer (m/w/d)" returns the vacancy on Intrum's careers site
- **THEN** the job gets that page's description and address, stays `new` and is evaluated in the same run

#### Scenario: Result is another job board
- **WHEN** the only search results are pages on StepStone or another job board
- **THEN** none is used, and the job continues to the employer website step

### Requirement: Employer careers pages are the fallback
In `search_first`, an alert job the direct search did not resolve SHALL go to employer discovery in the same run. Discovery looks up or reuses the employer's verified website, scans its careers or jobs pages, and matches the vacancy with the existing conservative rules. This SHALL happen whether or not `employer_careers.enabled` is set. The existing employer, page and search caps SHALL still apply.

#### Scenario: Ad only reachable via the careers page
- **WHEN** the direct search finds nothing usable but the employer's website is verified and its careers page lists the same vacancy
- **THEN** the job is matched to that listing, its description is recovered, and it is evaluated in the same run

#### Scenario: Employer site known from an earlier alert
- **WHEN** a new alert job's employer already has a saved verified website
- **THEN** that website is scanned without a new website search

### Requirement: Unresolved alert jobs wait without platform fallback
In `search_first`, an alert job whose ad was not found at the employer SHALL become `description_unavailable` with a reason that names the cause: not found, ambiguous employer, careers site unreadable, search key missing, search cap reached or deferred. It SHALL NOT fall back to the job-board page. Such a job SHALL stay eligible for later runs within the existing failure TTL and caps, and for `describe` and manual URL import.

#### Scenario: Nothing found
- **WHEN** neither the direct search nor the employer's careers pages yield the vacancy
- **THEN** the job is `description_unavailable` with reason "employer ad not found", and no platform page was requested

#### Scenario: Retried next day
- **WHEN** such a job's last miss is older than `employer_careers.failure_ttl_hours`
- **THEN** a later run searches for it again, and on success it returns to `new`

#### Scenario: Monthly search cap reached
- **WHEN** the monthly search cap is exhausted before an alert job is searched
- **THEN** the job is `description_unavailable` with reason "search cap reached", the reason is not stored as a miss, and the job is searched in a later run once the cap allows

### Requirement: Missing search key is visible
In `search_first`, a missing `PARALLEL_API_KEY` SHALL produce a run warning that names the number of alert jobs left waiting. Setup and the connection checks SHALL say that search-first alert handling needs this key. Jobs with an employer whose verified website is already saved SHALL still be scanned. The system SHALL NOT switch to platform mode by itself.

#### Scenario: Run without a key
- **WHEN** search-first mode is active, no search key is set and 7 new alert jobs have no saved employer site
- **THEN** the run warns that 7 alert jobs wait for the search key, and no LinkedIn, Indeed, StepStone or Workwise page is requested

### Requirement: Search-first outcomes are reported
Run summaries and the live feed SHALL state the active alert mode. In `search_first`, they SHALL count alert jobs found by direct search, found via a careers page, not found and deferred, and give each job's reason. Search usage SHALL stay reported separately from LLM cost.

#### Scenario: Summary after a search-first run
- **WHEN** a run handled 10 alert jobs: 6 found directly, 2 via careers pages, 1 not found, 1 deferred by the cap
- **THEN** the summary shows the mode `search_first` and those four counts, and the per-job reasons are in the feed

### Requirement: Platform mode keeps the earlier order
In `platform`, the run order and the own-ad search SHALL be unchanged. Employer discovery runs before detail fetching only when `employer_careers.enabled` is set. Job-board pages are fetched as configured in `detail_fetch`. The own-ad search runs after evaluation for jobs worth applying to.

#### Scenario: Platform mode with employer discovery off
- **WHEN** the mode is `platform` and `employer_careers.enabled` is false
- **THEN** LinkedIn, Indeed and StepStone alert jobs are fetched from their job pages, and only Arbeitsagentur jobs get the own-ad search after evaluation
