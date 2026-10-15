# application-package Specification

## Purpose
Turns each approved job into a self-contained application folder, so the user can send it in minutes or the system can send it automatically. The folder holds the cover letter (written per the `cover-letter-writing` capability), the selected documents, a combined PDF, prepared form answers, an email draft and a local helper page.

## Requirements

### Requirement: One folder per application
For each job with decision `apply`, the system SHALL create a folder `application_<company>_<title>_<shortid>` in the workspace's applications directory, except for an email application prepared in review mode, which SHALL get no folder (see "No local files for email applications in review mode"). Company and title SHALL be lowercase ASCII slugs with German umlauts transliterated (ä→ae, ö→oe, ü→ue, ß→ss) and SHALL be truncated to keep folder names under 100 characters.

#### Scenario: Folder naming
- **WHEN** a job "Software-Entwickler (m/w/d)" at "Müller & Söhne GmbH" with short ID `a1b2c3` is prepared
- **THEN** the folder `application_mueller-soehne-gmbh_software-entwickler_a1b2c3` is created

#### Scenario: Form status in the folder name
- **WHEN** a form application's application form was not reached (no application found, partial, needs the browser, login, CAPTCHA, restricted or unreachable)
- **THEN** the folder is still saved and its name ends with a marker after the short ID (`_NOFORM`, `_BROWSER`, `_LOGIN`, `_CAPTCHA`, `_RESTRICTED`, `_UNREACHABLE`), keeping the name under 100 characters
- **AND** a found form, an email application or a form not yet looked up has no marker
- **AND** when a later form lookup refreshes the folder, the folder is renamed to match, the recorded folder path and the absolute paths inside `README.md`, `job.md` and `apply.html` follow the rename, and a folder the user renamed is never touched

#### Scenario: Email application in review mode
- **WHEN** a job with channel `email` and an application address found in the ad is prepared in review mode
- **THEN** no folder is created for it

### Requirement: Folder contents
Each application folder SHALL contain:
- `job.md`: the ad snapshot (title, company, location, salary, links, full description)
- `evaluation.md`: score, decision, rationale and gaps
- `cover_letter.md` and `cover_letter.pdf`
- copies of the selected CV and requested certificates
- `Bewerbungsmappe.pdf`
- `form_answers.md`
- `apply.html`
- `README.md`: the application link, next step, missing documents, review reasons and skills to brush up on

#### Scenario: Complete folder
- **WHEN** a job with channel `employer_form` is prepared
- **THEN** its folder contains every file listed above, and `README.md` names the application URL as the next step

### Requirement: Salary expectation rule
When the profile has salary preferences, the salary expectation SHALL be computed deterministically and rounded to the nearest 1,000 EUR:
- If the ad states a range, the profile target is clamped into that range and then raised to the profile minimum (if set) when it falls below it.
- If the ad states no range, the profile target is used.

The expectation SHALL appear in the cover letter only when the ad asks for it. It SHALL always appear in `form_answers.md` and the tracker.

#### Scenario: Target above ad range
- **WHEN** the profile target is 72000, the minimum is 58000, and the ad range is 55000–65000
- **THEN** the salary expectation is 65000

#### Scenario: Platform estimate is not an ad range
- **WHEN** the only salary information is StepStone's estimate "55.000 - 75.000 €/Jahr (geschätzt)"
- **THEN** the expectation is computed as if the ad stated no range

#### Scenario: No salary preferences configured
- **WHEN** the profile has no salary preferences and the ad asks for a salary expectation
- **THEN** no expectation is generated, the letter does not mention salary, and `README.md` notes that the ad asks for a salary expectation

#### Scenario: Ad asks for salary expectation
- **WHEN** the ad says "Bitte nennen Sie Ihre Gehaltsvorstellung"
- **THEN** the cover letter states the computed expectation as an annual gross amount

### Requirement: Document selection
The system SHALL attach the CV in the letter's language (falling back to any CV), and SHALL attach certificates only when the ad requests certificates or references, or when the configuration says to always attach them. A requested document the user does not have SHALL be listed as missing in `README.md`.

#### Scenario: Requested portfolio missing
- **WHEN** the ad requests a portfolio and no document has role `other` tagged as portfolio
- **THEN** `README.md` lists "portfolio" under missing documents

#### Scenario: Documents marked for a language
- **WHEN** a certificate is mapped as German, one as English, and one as "any language", and the letter is in German
- **THEN** the German and the "any language" certificates are attached and the English one is not; a document with no language behaves like "any language"

### Requirement: Combined application PDF
The system SHALL create `Bewerbungsmappe.pdf` containing the cover letter, then the CV, then the attached certificates in profile order. When it exceeds the configured size limit (default 5 MB), it SHALL warn in `README.md` and the run summary.

#### Scenario: Bundle order
- **WHEN** an application includes a cover letter, a CV and two certificates
- **THEN** `Bewerbungsmappe.pdf` pages appear in that order

### Requirement: Prepared form answers
`form_answers.md` SHALL contain ready-to-paste values for common application form fields: personal data, salary expectation (if configured), earliest start date or notice period, work authorisation, language levels, a short motivation text (max 1,000 characters, written under the `cover-letter-writing` rules), and LinkedIn URL.

#### Scenario: Motivation text length
- **WHEN** form answers are generated
- **THEN** the motivation text is at most 1,000 characters and is in the ad's language

### Requirement: Email draft for email applications
For jobs with channel `email` prepared in automatic or test mode, the system SHALL create `email.eml` addressed to the extracted address, with a subject "Bewerbung als <title>" (plus the reference number if any), a short body in the ad's language, and `Bewerbungsmappe.pdf` attached. In review mode the same email SHALL be saved only as a draft in the sending mailbox, as specified in `email-drafts`, and no `email.eml` SHALL be written.

#### Scenario: Draft opens in a mail client
- **WHEN** the user opens `email.eml` of a job prepared in automatic mode in a desktop mail client
- **THEN** recipient, subject, body and the PDF attachment are present

#### Scenario: Review mode writes no email file
- **WHEN** an email application is prepared in review mode
- **THEN** no `email.eml` exists anywhere in the workspace for that job

### Requirement: Offline apply helper page
`apply.html` SHALL work offline with no external resources. It SHALL show links to the job ad and the application page, each prepared answer with a copy-to-clipboard button, and the local paths of the files to upload.

#### Scenario: Copy an answer
- **WHEN** the user opens `apply.html` and clicks the copy button next to the salary expectation
- **THEN** the salary expectation text is copied to the clipboard

### Requirement: Re-render after manual edits
The user SHALL be able to edit `cover_letter.md` and run `render <job-id>` to regenerate `cover_letter.pdf`, `Bewerbungsmappe.pdf` and `email.eml` without calling an LLM. For an email application that has no folder because it was prepared in review mode, `render <job-id>` SHALL change nothing, SHALL say that the application is a draft in the mailbox that can be edited there or rebuilt with `draft <job-id> --again`, and SHALL exit with a non-zero code.

#### Scenario: Edited letter is re-rendered
- **WHEN** the user changes a sentence in `cover_letter.md` and runs `render <job-id>`
- **THEN** the regenerated `cover_letter.pdf` and `Bewerbungsmappe.pdf` contain the changed sentence

#### Scenario: Render of a draft-only application
- **WHEN** the user runs `render <job-id>` for an email application prepared in review mode
- **THEN** no file is written, the message names `draft <job-id> --again`, and the exit code is 1

### Requirement: Preparation does not overwrite user work
Re-preparing a job that already has a folder SHALL require `--force`. Before regenerating, the system SHALL keep the previous `cover_letter.md` as a timestamped backup. Re-preparing an email application in review mode (with `--force`) SHALL replace its stored letter and email text and SHALL NOT keep a backup file on disk; an old folder of that job SHALL be deleted when the new letter is stored.

#### Scenario: Prepare without force
- **WHEN** `prepare --job <id>` runs for a job whose folder already exists
- **THEN** nothing is regenerated and the command reports that `--force` is required

#### Scenario: Force re-prepare in review mode
- **WHEN** `prepare --job <id> --force` runs in review mode for an email application with an old folder
- **THEN** a new letter and email text are stored for the job, the old folder is deleted, and no file is written to the workspace

### Requirement: No local files for email applications in review mode
When the run mode is review and the evaluation found an application email address for a job, preparing the job SHALL write no file or folder in the workspace. The cover letter, the subject, the email text, the recipient and a Message-ID SHALL be stored in the database, and the job SHALL become `prepared`. Any PDF needed to check the letter's length SHALL be written to a temporary directory outside the workspace and deleted before the job is finished.

#### Scenario: Review-mode preparation of an email job
- **WHEN** a review-mode run prepares a job with channel `email` and address `jobs@muster-gmbh.de`
- **THEN** the job is `prepared`, its application record has no folder and holds the letter text, subject, email text, recipient and Message-ID, and the workspace's applications directory has no entry for the job

#### Scenario: Form job in review mode
- **WHEN** a review-mode run prepares a job with channel `employer_form`
- **THEN** its folder is created with every file listed under "Folder contents", as before

#### Scenario: Email job in automatic mode
- **WHEN** an automatic-mode run prepares a job with channel `email`
- **THEN** its folder with `email.eml` is created as before
