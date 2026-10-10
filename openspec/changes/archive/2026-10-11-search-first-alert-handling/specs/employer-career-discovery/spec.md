# Spec Delta

## MODIFIED Requirements

### Requirement: Discover careers sites from alert employers
When enabled, employer discovery SHALL run after alert ingestion and before detail fetching. It SHALL consider unclosed, unfiltered alert jobs in the selected profiles, including earlier alerts awaiting recovery. It SHALL group seeds by verified employer, avoid recursively expanding newly discovered employers, and leave existing sources usable when discovery is disabled or fails. In alert mode `search_first`, discovery SHALL run for alert jobs the direct own-ad search did not resolve, regardless of `employer_careers.enabled`, and SHALL include `workwise-alert` jobs whose company is known. Importing an employer's additional openings SHALL still require `employer_careers.enabled`.

#### Scenario: Existing LinkedIn alert can be recovered
- **WHEN** an eligible LinkedIn alert has no readable description and employer discovery is enabled
- **THEN** the employer careers workflow runs without needing to read LinkedIn

#### Scenario: Disabled source preserves behavior
- **WHEN** employer discovery is disabled and the alert mode is `platform`
- **THEN** no employer search or scan runs and existing search/fetch behavior is unchanged

#### Scenario: Search-first with discovery switched off
- **WHEN** the alert mode is `search_first`, `employer_careers.enabled` is false, and the direct search did not find a StepStone alert job's ad
- **THEN** the employer's website is looked up or reused and scanned for that vacancy, but other openings found there are not imported as new jobs

#### Scenario: Workwise alert in search-first
- **WHEN** the alert mode is `search_first` and a Workwise alert job with a known company was not found by the direct search
- **THEN** that employer is included in discovery like a LinkedIn, Indeed or StepStone alert employer
