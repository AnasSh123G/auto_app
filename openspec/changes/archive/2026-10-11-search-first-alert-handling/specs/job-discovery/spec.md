# Spec Delta

## MODIFIED Requirements

### Requirement: Job detail fetching without platform automation
For jobs whose source supplies no full description, the system SHALL fetch the job page at a limited rate, without automated search, login or form interaction on LinkedIn, Indeed or StepStone. On a bot challenge or CAPTCHA it SHALL stop fetching from that site for the run, and it SHALL NOT attempt to solve or circumvent challenges. When the alert mode is `search_first` (see `alert-resolution`), alert jobs SHALL NOT have their LinkedIn, Indeed, StepStone or Workwise page fetched. Only an employer posting found for them is read.

#### Scenario: Description fetched from alert link
- **WHEN** the alert mode is `platform` and a job from a StepStone alert has no description and detail fetching is enabled
- **THEN** the system loads the job page, extracts the description, and stores it on the job record

#### Scenario: Bot challenge encountered
- **WHEN** the alert mode is `platform` and fetching a LinkedIn job page returns a challenge or CAPTCHA page
- **THEN** the job is marked `description_unavailable`, no further LinkedIn pages are fetched in this run, and the run summary reports the block

#### Scenario: Detail fetching disabled
- **WHEN** detail fetching is disabled for a site in the configuration
- **THEN** jobs from that site without a description are marked `description_unavailable` and listed for manual handling

#### Scenario: Search-first alert job with a found employer posting
- **WHEN** the alert mode is `search_first` and an alert job was matched to an employer posting whose description has not been read yet
- **THEN** the detail step reads the employer posting and never the job-board page

#### Scenario: Search-first alert job without an employer posting
- **WHEN** the alert mode is `search_first` and an alert job reaches the detail step with no description and no employer posting
- **THEN** no job-board page is requested and the job is marked `description_unavailable` with the employer-search reason

## ADDED Requirements

### Requirement: Workwise alert cards name the company when shown
The Workwise alert parser SHALL take the company name from the email when the card shows it next to the job link, and SHALL then not mark the company as unknown. When the email does not name the company, the card SHALL keep the company marked unknown. In `search_first` mode, such a job SHALL be `description_unavailable` with reason "alert does not name the employer" and SHALL NOT be searched by title alone.

#### Scenario: Card shows the company
- **WHEN** a Workwise alert card shows "Backend Developer (m/w/d)" with the company line "Muster GmbH"
- **THEN** the job is stored with company "Muster GmbH" and is not marked company-unknown

#### Scenario: Card without a company in search-first
- **WHEN** a Workwise alert card shows only a job link and title, and the mode is `search_first`
- **THEN** no search request is made for it and it is `description_unavailable` with reason "alert does not name the employer"
