# Spec Delta

## Purpose

Keeps a durable record of every job the system saw and every application it prepared or sent, and documents it in a CSV file the user can open and edit in a spreadsheet.

## ADDED Requirements

### Requirement: Persistent job state
The system SHALL persist all jobs, evaluations, application folders, send attempts and status changes in a local database in the workspace, so that runs can resume and no job is processed twice.

#### Scenario: State survives restart
- **WHEN** a run evaluates 10 jobs and the process is restarted
- **THEN** the next run does not evaluate those 10 jobs again

### Requirement: Status lifecycle
Each job SHALL have one status from this set:
- before preparation: `new`, `filtered`, `description_unavailable`, `evaluation_failed`, `skipped`, `review`, `approved`
- preparation and submission: `prepared`, `send_unconfirmed`, `sent`, `submitted`
- after submission: `interview`, `offer`, `rejected`, `withdrawn`, `expired`

Every status change SHALL be recorded with a timestamp.

#### Scenario: Status history
- **WHEN** a job moves from `approved` to `prepared` to `sent`
- **THEN** the job's history shows all three changes with timestamps

### Requirement: Applications CSV
The system SHALL maintain `applications.csv` in the workspace with one row per job whose status is `review` or later. The columns are:
- `id`, `status`, `company`, `job_title`, `location`, `work_mode`, `source`
- `job_url`, `application_url`, `channel`, `contact_person`, `contact_email`, `reference`
- `ad_salary_range`, `salary_expectation`, `fit_score`, `assumed_skills`, `needs_review`
- `date_found`, `date_prepared`, `date_applied`, `folder`, `notes`
- `form_status`, `form_note` (application form finding)
- `employer_site`, `employer_posting`, `employer_outcome` (employer careers discovery; generated, never imported)

#### Scenario: Prepared application appears in CSV
- **WHEN** a job is prepared
- **THEN** `applications.csv` contains a row with its company, job title, job link, salary expectation and folder path

#### Scenario: Skipped jobs excluded by default
- **WHEN** a job is evaluated as `skipped`
- **THEN** it does not appear in `applications.csv` unless `tracking.include_skipped` is `true`

### Requirement: Spreadsheet-friendly CSV format
`applications.csv` SHALL be UTF-8 with a byte-order mark, SHALL use the configured delimiter (default `;` for German spreadsheet locales), and SHALL be written atomically so a crash never leaves a partial file.

#### Scenario: Umlauts display correctly
- **WHEN** the CSV contains the company "Müller & Söhne GmbH" and is opened in a spreadsheet application
- **THEN** the umlauts display correctly

### Requirement: Two-way sync of user-editable columns
Before rewriting `applications.csv`, the system SHALL import changes the user made to the `status` and `notes` columns. A status value outside the lifecycle SHALL be ignored with a warning. All other columns SHALL be regenerated from the database.

#### Scenario: User records an interview in the spreadsheet
- **WHEN** the user changes a row's status from `sent` to `interview` in the CSV and then runs any command
- **THEN** the database status becomes `interview`, and the regenerated CSV keeps `interview`

#### Scenario: Invalid status typed
- **WHEN** the user enters status `maybe later` in the CSV
- **THEN** the status change is ignored, a warning names the row, and the user's `notes` edits are still imported

### Requirement: Status commands
The CLI SHALL provide:
- `status`: counts per status, applications sent today, the review queue (`review` jobs and applications flagged `needs_review`), and the estimated LLM cost of today and of the last run
- `list --status <status>`: the jobs with that status
- `mark <job-id> <status> [--note <text>]`: change a job's status manually

#### Scenario: Mark rejection
- **WHEN** the user runs `mark <job-id> rejected --note "Absage per Mail"`
- **THEN** the job's status is `rejected`, the note is stored, and the CSV row reflects both
