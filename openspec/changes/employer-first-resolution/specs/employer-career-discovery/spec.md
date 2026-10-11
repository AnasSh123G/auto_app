# Spec Delta

## ADDED Requirements

### Requirement: Arbeitsagentur jobs get the employer's own ad like alert jobs
An Arbeitsagentur job SHALL be resolved to the employer's own ad and application address through the same two steps as an alert job: the direct own-ad search, then the employer's careers pages. The Arbeitsagentur page SHALL NOT be accepted as an employer ad or as the employer's website. For Arbeitsagentur jobs the own-ad search SHALL stay after evaluation, so that it only runs for jobs worth applying to and can compare the ad's full text. After that search, the Arbeitsagentur jobs it did not resolve SHALL go to employer discovery, in the same run and whether or not `employer_careers.enabled` is set; related openings found there SHALL be imported only when `employer_careers.enabled` is set.

#### Scenario: Own-ad search misses, careers page matches
- **WHEN** an evaluated Arbeitsagentur job is not found by the direct search but the employer's verified careers page lists the same vacancy
- **THEN** the job is matched to that listing and its application address is the employer's page

#### Scenario: Discovery switched off
- **WHEN** `employer_careers.enabled` is false, the Arbeitsagentur source is enabled and the own-ad search missed a job
- **THEN** the employer's careers pages are still looked up for that job, and no related openings are imported

### Requirement: Partner boards are not employer addresses
An address from an Arbeitsagentur ad's `externeURL` whose `allianzpartnerName` is set, is not the Arbeitsagentur itself and is not the employer SHALL count as a job board, not as the employer's address. `jobvector.de` SHALL be treated as a job board for all employer lookups. The own-ad search SHALL NOT be skipped for a job whose current address is such a board.

#### Scenario: Partner board link
- **WHEN** an Arbeitsagentur job's `externeURL` points to jobvector.de with `allianzpartnerName` "jobvector.de"
- **THEN** the own-ad search runs for that job, and a hit on the employer's own page becomes its application address

#### Scenario: Partner board is never an employer site
- **WHEN** a search result for an employer is a jobvector.de page
- **THEN** it is not accepted as the employer's ad or website

## MODIFIED Requirements

### Requirement: Discover careers sites from alert employers
When enabled, employer discovery SHALL run after alert ingestion and before detail fetching. It SHALL consider unclosed, unfiltered alert jobs in the selected profiles, including earlier alerts awaiting recovery. It SHALL group seeds by verified employer, avoid recursively expanding newly discovered employers, and leave existing sources usable when discovery is disabled or fails. In alert mode `search_first`, discovery SHALL run for alert jobs the direct own-ad search did not resolve, regardless of `employer_careers.enabled`, and SHALL include `workwise-alert` jobs whose company is known. After evaluation, discovery SHALL also run for the Arbeitsagentur jobs the own-ad search did not resolve, regardless of `employer_careers.enabled`. Importing an employer's additional openings SHALL still require `employer_careers.enabled`.

#### Scenario: Existing LinkedIn alert can be recovered
- **WHEN** an eligible LinkedIn alert has no readable description and employer discovery is enabled
- **THEN** the employer careers workflow runs without needing to read LinkedIn

#### Scenario: Disabled source preserves behavior
- **WHEN** employer discovery is disabled and the alert mode is `platform`
- **THEN** no employer search or scan runs for alert jobs and existing search/fetch behavior is unchanged

#### Scenario: Search-first with discovery switched off
- **WHEN** the alert mode is `search_first`, `employer_careers.enabled` is false, and the direct search did not find a StepStone alert job's ad
- **THEN** the employer's website is looked up or reused and scanned for that vacancy, but other openings found there are not imported as new jobs

#### Scenario: Workwise alert in search-first
- **WHEN** the alert mode is `search_first` and a Workwise alert job with a known company was not found by the direct search
- **THEN** that employer is included in discovery like a LinkedIn, Indeed or StepStone alert employer

#### Scenario: Arbeitsagentur job missed by the own-ad search
- **WHEN** the own-ad search after evaluation did not find an Arbeitsagentur job's ad
- **THEN** that job's employer is included in discovery in the same run
