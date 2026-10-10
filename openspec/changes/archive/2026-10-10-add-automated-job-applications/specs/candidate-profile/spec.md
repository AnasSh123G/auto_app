# Spec Delta

## Purpose

Holds everything the system knows about the applicant: one documents folder with CVs, certificates and attachments, and a profile with personal data, standard answers and salary preferences that every later step draws on.

## ADDED Requirements

### Requirement: Single documents folder
The system SHALL read all applicant documents (CVs, certificates, photo, other attachments) from one configurable documents folder inside the workspace and SHALL NOT modify or delete files in that folder.

#### Scenario: Documents are discovered
- **WHEN** the documents folder contains `Lebenslauf_de.pdf`, `CV_en.pdf` and `Arbeitszeugnis_Firma.pdf`
- **THEN** the system lists all three files as available documents

#### Scenario: Missing documents folder
- **WHEN** the configured documents folder does not exist
- **THEN** the system stops with an error naming the expected path and does not continue the run

### Requirement: Document roles and languages
Each document SHALL have a role (`cv`, `certificate`, `photo`, `writing_sample`, `other`) and an optional language (`de`, `en`). The roles and languages SHALL be declared in the profile. The onboarding `documents` section SHALL propose an initial declaration from the file names for the user to confirm.

#### Scenario: Onboarding proposes a mapping
- **WHEN** the user reaches the `documents` onboarding section and the folder contains `Lebenslauf_de.pdf` and `Zeugnis_2023.pdf`
- **THEN** the proposed profile marks `Lebenslauf_de.pdf` as role `cv` with language `de` and `Zeugnis_2023.pdf` as role `certificate`

#### Scenario: Undeclared file is ignored
- **WHEN** a file exists in the documents folder but is not declared in the profile
- **THEN** the system does not attach it to any application and reports it as unmapped in `status`

### Requirement: At least one CV
The profile SHALL declare at least one CV document. The system SHALL refuse to evaluate or prepare applications without one.

#### Scenario: No CV declared
- **WHEN** the profile declares no document with role `cv`
- **THEN** evaluation and preparation commands stop with an error explaining that a CV is required

### Requirement: CV text extraction
The system SHALL extract plain text from CV documents in PDF and DOCX format so it can be used for evaluation and writing. It SHALL report an error for a CV with no extractable text.

#### Scenario: PDF CV is extracted
- **WHEN** the declared CV is a text-based PDF
- **THEN** the system produces its plain-text content for use in evaluation

#### Scenario: Scanned CV without text
- **WHEN** the declared CV is an image-only PDF with no extractable text
- **THEN** the system reports that the CV has no readable text and asks the user to provide a text-based PDF or DOCX

### Requirement: Applicant profile data
The profile SHALL hold the applicant's personal data (name, address, email, phone, LinkedIn URL), work authorisation, earliest start date or notice period, language levels, and a set of standard answers that may be reused in applications.

#### Scenario: Standard answer is reused
- **WHEN** the profile defines the standard answer `notice_period: "3 Monate zum Monatsende"`
- **THEN** every prepared application that needs a notice period uses exactly that text

### Requirement: Optional salary preferences
Salary preferences SHALL be optional. When present, they consist of a target annual gross salary in EUR, an optional minimum, and optional per-seniority overrides of both.

#### Scenario: No salary configured
- **WHEN** the profile defines no salary preferences
- **THEN** profile validation passes, and no salary expectation is computed for any job

#### Scenario: Seniority override applies
- **WHEN** the profile defines a target of 60000, a `senior` override of 75000, and a job is evaluated as senior
- **THEN** the salary target used for that job is 75000

#### Scenario: Regional override applies
- **WHEN** the profile defines a target of 52000 and a `bayern` regional override of 56000, and a job's location is in Bayern (the state's name, a postcode or a known larger city)
- **THEN** the salary target used for that job is 56000, whatever its seniority, and the profile minimum still applies unless the override has its own
- **AND** a job elsewhere, or with an unrecognised location, uses 52000 (or its seniority override)

### Requirement: Additional skills outside the CV
The profile SHALL allow listing skills that are not in the CV, each with a level (`basic`, `good`, `strong`) and an optional context, such as "personal projects". Listed skills SHALL count as profile evidence for evaluation and writing.

#### Scenario: Extra skill used as evidence
- **WHEN** the profile lists `Docker` at level `basic` with context "Home server und private Projekte"
- **THEN** the evaluation counts Docker as matched at a basic level, and a letter may mention it as profile-backed

### Requirement: Profile validation
The system SHALL validate the profile and configuration at startup and SHALL report every invalid or missing required field before doing any network or LLM work.

#### Scenario: Invalid email in profile
- **WHEN** the profile email is `max.mustermann@`
- **THEN** the system reports that the profile email is invalid and makes no network or LLM calls
