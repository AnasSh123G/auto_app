# job-discovery Specification

## Purpose
Finds job listings in Germany that match the user's filters. It collects them from permitted sources, fetches their full details, normalises them into one job record format, removes duplicates and drops listings that fail rule-based filters before any AI evaluation.

## Requirements

### Requirement: Configurable search profiles
The user SHALL be able to define one or more named search profiles. Each profile requires at least one keyword. It MAY add any of these optional filters:
- excluded keywords and companies
- locations with a radius in km
- work mode (`onsite`, `hybrid`, `remote`, `any`)
- working time (`full_time`, `part_time`, `any`)
- contract type (`permanent`, `fixed_term`, `any`)
- accepted experience levels
- maximum posting age in days
- minimum salary
- agency exclusion
- extra writing instructions

#### Scenario: Two search profiles
- **WHEN** the configuration defines profiles `backend-berlin` and `remote-de`
- **THEN** a search run queries the enabled sources once for each profile and tags each discovered job with the profile name that found it

#### Scenario: Running a single profile
- **WHEN** the user runs `search --profile remote-de`
- **THEN** only the `remote-de` profile is queried

### Requirement: Optional filters
Every filter other than keywords SHALL be optional. An empty or omitted filter SHALL mean no restriction. It SHALL NOT be sent to sources or applied in filtering.

#### Scenario: No location
- **WHEN** a search profile has keywords `Data Engineer` and no location
- **THEN** sources are queried for all of Germany, with no location or radius parameter

#### Scenario: No salary filter
- **WHEN** a search profile defines no minimum salary
- **THEN** no job is filtered out because of its salary, and no salary parameter is sent to any source

#### Scenario: Missing keywords
- **WHEN** a search profile defines no keywords
- **THEN** configuration validation reports that the profile needs at least one keyword

### Requirement: Arbeitsagentur source
The system SHALL search the Bundesagentur für Arbeit Jobsuche API. Every configured filter the API supports (keywords, location, radius, working time including home office, contract type, posting age, agency exclusion) SHALL be sent as API parameters, and the full job details SHALL be fetched for each result.

#### Scenario: Filters are pushed to the API
- **WHEN** a profile specifies keyword `Python Entwickler`, location `München`, radius 30 and maximum posting age 7
- **THEN** the Arbeitsagentur request contains those values as search parameters

#### Scenario: Agency jobs excluded
- **WHEN** a profile sets `exclude_agencies: true`
- **THEN** the Arbeitsagentur request excludes temporary-employment and private-placement listings

### Requirement: Adzuna source
The system SHALL search the Adzuna API for Germany when Adzuna credentials are configured. When they are not, it SHALL skip this source with an informational message.

#### Scenario: Adzuna not configured
- **WHEN** no Adzuna app ID or key is set
- **THEN** the search runs the other sources and reports `adzuna: skipped (not configured)`

### Requirement: Job-alert email source
The system SHALL read job-alert emails from a configured folder of the alert mailbox (see `email-submission` for supported connections). Each email SHALL be matched to a parser by its From address, never by its recipient. Processed emails SHALL be tracked so that none is ingested twice. This change ships parsers for LinkedIn, StepStone and Indeed. More senders SHALL be addable as a new parser without changes elsewhere.

#### Scenario: LinkedIn alert email is parsed
- **WHEN** the alert folder contains an unprocessed email from `jobalerts-noreply@linkedin.com` listing 6 postings, two of which are the same 4flow role in different cities
- **THEN** 6 postings with source `linkedin-alert`, their titles, companies, cities and canonical LinkedIn job URLs are recorded as 5 jobs

#### Scenario: Recipient address does not matter
- **WHEN** a StepStone alert was delivered to a different address than the one configured in the profile
- **THEN** it is parsed like any other StepStone alert

#### Scenario: Email already processed
- **WHEN** an alert email was ingested in an earlier run
- **THEN** a later run does not create job records from it again

#### Scenario: Sender without a parser
- **WHEN** the alert folder contains an alert from a sender without a parser (e.g. XING)
- **THEN** the email is skipped, left unchanged in the mailbox, counted as `unrecognised` in the run summary, and ingested once a parser for it is added

### Requirement: StepStone tracking links
StepStone alert links are tracking redirects that lead to an automatic-login link carrying the job path. The system SHALL resolve each job card's link with a single request that does not follow the redirect, and take the job ID from the redirect's `returnUrl`. It SHALL NOT follow, store or log the login token.

#### Scenario: Job ID recovered
- **WHEN** a StepStone card's link redirects to a login link whose `returnUrl` is `/stellenangebote----13850318-inline.html?…`
- **THEN** the job record gets source job ID `13850318` and the canonical URL `https://www.stepstone.de/stellenangebote--13850318-inline.html`

#### Scenario: Login token never kept
- **WHEN** a StepStone link is resolved
- **THEN** no request is made to the login link, and the `magicLink` value appears in no database row, log line or file

#### Scenario: Link no longer resolvable
- **WHEN** a StepStone tracking link returns an error or an unexpected redirect
- **THEN** the card is stored with its email data, marked `description_unavailable`, and listed for manual handling

### Requirement: Indeed tracking links decoded offline
Indeed email links are ``cts.indeed.com`` tracking links whose payload contains the job link. The system SHALL decode the job key from the payload without any network request, and SHALL NOT keep anything else from the payload, such as the account ID or click tokens.

#### Scenario: Job key from an Indeed match email
- **WHEN** an email from `donotreply@match.indeed.com` suggests "Data Engineer (m/w/d)" at Intrum Deutschland GmbH
- **THEN** a job with source `indeed-match`, source job ID `5b574860bd63a20c` and URL `https://de.indeed.com/viewjob?jk=5b574860bd63a20c` is created, and no tracking link is stored

### Requirement: Listing data from alert cards
The system SHALL keep the listing data an alert card shows: title, company, location, contract type, work mode, working time and salary. A salary marked by the platform as an estimate SHALL be stored as estimated, converted to annual EUR if it is monthly, and SHALL NOT be treated as a salary stated by the employer. A truncated title SHALL be replaced by the full title once job details are fetched.

#### Scenario: Estimated monthly salary
- **WHEN** a StepStone card shows "4.500 - 5.500 €/Monat (geschätzt für Vollzeit)"
- **THEN** the job stores an estimated salary of 54000–66000 EUR per year and no employer-stated salary range

#### Scenario: Truncated title
- **WHEN** a card shows "Senior Data Scientist, Forecasting & Elas..."
- **THEN** the full title from the job page replaces it after detail fetching

### Requirement: Recommendation sections
Jobs listed in an alert's recommendation section, such as StepStone's "Andere Bewerber interessierten sich auch für diese Jobs", SHALL be ingested and evaluated like other jobs, but tagged with a recommendation source (e.g. `stepstone-alert-recommended`) that is visible in the tracker.

#### Scenario: Recommended job tagged
- **WHEN** a StepStone alert lists 8 matches and 4 recommended jobs
- **THEN** 8 jobs get source `stepstone-alert`, 4 get source `stepstone-alert-recommended`, and footer links such as "Weitere passende Jobs anzeigen" create no job

### Requirement: Manual URL import
The user SHALL be able to add a job by URL with an `add <url>` command. The resulting job SHALL then go through the same detail fetching, filtering and evaluation as discovered jobs.

#### Scenario: Manual job added
- **WHEN** the user runs `add https://example-company.personio.de/job/12345`
- **THEN** a job record with source `manual` is created and queued for detail fetching

### Requirement: Job detail fetching without platform automation
For jobs whose source supplies no full description, the system SHALL fetch the job page at a limited rate, without automated search, login or form interaction on LinkedIn, Indeed or StepStone. On a bot challenge or CAPTCHA it SHALL stop fetching from that site for the run, and it SHALL NOT attempt to solve or circumvent challenges.

#### Scenario: Description fetched from alert link
- **WHEN** a job from a StepStone alert has no description and detail fetching is enabled
- **THEN** the system loads the job page, extracts the description, and stores it on the job record

#### Scenario: Bot challenge encountered
- **WHEN** fetching a LinkedIn job page returns a challenge or CAPTCHA page
- **THEN** the job is marked `description_unavailable`, no further LinkedIn pages are fetched in this run, and the run summary reports the block

#### Scenario: Detail fetching disabled
- **WHEN** detail fetching is disabled for a site in the configuration
- **THEN** jobs from that site without a description are marked `description_unavailable` and listed for manual handling

### Requirement: Description pasted manually
The user SHALL be able to provide a job description manually for a job marked `description_unavailable`, after which the job proceeds to evaluation.

#### Scenario: User pastes a description
- **WHEN** the user runs `describe <job-id>` and pastes the ad text
- **THEN** the job's description is stored and the job becomes eligible for evaluation

### Requirement: Normalised job records
Every discovered job SHALL be stored as a normalised record with at least: internal ID, source, source job ID, title, company, location, job URL, application URL if known, description, posting date if known, the ad's salary range if known, and discovery timestamp.

#### Scenario: Salary range captured
- **WHEN** an Adzuna result has `salary_min` 55000 and `salary_max` 70000
- **THEN** the job record stores an ad salary range of 55000–70000 EUR

### Requirement: Deduplication across sources
The system SHALL detect duplicates by source job ID, canonical job URL, and a fingerprint of normalised company name, normalised title and city. A duplicate SHALL be linked to the existing record rather than evaluated again.

Exception for verified employer requisitions (employer-career-discovery): postings read from a verified employer careers site are identified by an employer-scoped requisition reference, or by their address when they have none. Such a posting SHALL NOT be merged by company/title fingerprint with another job, and a role-based match from another source SHALL NOT link to one of several jobs that hold different verified requisitions of the same employer. A source posting that merely repeats the same employer-scoped requisition SHALL still be linked to the existing job.

#### Scenario: Same job from two sources
- **WHEN** a job "Backend Developer (m/w/d)" at "Muster GmbH" in Berlin arrives from Arbeitsagentur and later from a StepStone alert
- **THEN** only one job record exists, listing both sources, and it is evaluated at most once

#### Scenario: Gender suffix normalisation
- **WHEN** two titles differ only by `(m/w/d)` versus `(w/m/d)`
- **THEN** they produce the same fingerprint

#### Scenario: Distinct requisitions of one employer
- **WHEN** a verified employer lists two "Data Engineer" postings in Köln with different requisition references
- **THEN** two jobs are kept, and a later alert for "Data Engineer" in Köln is not linked to either of them by title alone

### Requirement: One job for multi-city postings
Postings from the same company with the same normalised title, discovered within 30 days of each other, SHALL be treated as one job, whatever their cities. The job SHALL use the city closest to the search profile's location, or the first one seen when the profile has no location. The other postings SHALL be linked to it as additional locations.

This grouping by company and title SHALL NOT merge postings of a verified employer that carry different employer-scoped requisition references (employer-career-discovery). The same employer-scoped requisition listed in several cities SHALL remain one job with those locations.

#### Scenario: Same role in two cities
- **WHEN** a LinkedIn alert lists "AI Engineer (d/w/m) - im Bereich Consulting" at 4flow in Düsseldorf and in Rüsselsheim
- **THEN** one job is created with both cities linked, and at most one application is prepared

#### Scenario: Different roles at one company
- **WHEN** the same company posts "Data Engineer" and "Data Scientist"
- **THEN** two separate jobs are created

#### Scenario: Same requisition in several cities
- **WHEN** a verified employer lists one requisition in Köln and in Berlin
- **THEN** one job is created with both cities linked

#### Scenario: Same title and city, different requisitions
- **WHEN** a verified employer lists two requisitions with the same normalised title in the same city
- **THEN** two jobs are created and neither is collapsed into the other

### Requirement: Rule-based pre-filtering
Before any LLM call, the system SHALL drop jobs that clearly fail a configured rule of the search profile:
- excluded keywords in the title
- excluded companies
- posting age over the limit
- an ad salary maximum below the minimum salary
- a work mode that cannot match
- a contract or position type the profile excludes, also read from alert cards (e.g. "Freie Mitarbeit")

Rules whose filter is not configured SHALL NOT be applied. The reason for each drop SHALL be recorded.

#### Scenario: Excluded keyword in title
- **WHEN** a profile excludes `Werkstudent` and a job is titled `Werkstudent Softwareentwicklung`
- **THEN** the job is marked `filtered` with the reason `excluded keyword: Werkstudent` and is not sent to the LLM

#### Scenario: Filtered from the alert card
- **WHEN** a profile's contract type is `permanent` and a StepStone card shows "Freie Mitarbeit/Projektmitarbeit"
- **THEN** the job is marked `filtered` before any job page is fetched

#### Scenario: Estimated salary does not filter
- **WHEN** a profile's minimum salary is 60000 and a card shows only an estimated 48000–55000 EUR
- **THEN** the job is not filtered out for salary

#### Scenario: Company blocklist
- **WHEN** the configuration lists `Example Consulting AG` as an excluded company
- **THEN** jobs from that company are marked `filtered` and are not evaluated

### Requirement: Source failure isolation
A failure in one source SHALL NOT stop the other sources. The run summary SHALL report each source's result count or error.

#### Scenario: One API down
- **WHEN** the Adzuna API returns HTTP 503 during a search run
- **THEN** the Arbeitsagentur and alert-email sources still run, and the summary reports the Adzuna error
