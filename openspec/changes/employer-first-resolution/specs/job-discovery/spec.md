# Spec Delta

## ADDED Requirements

### Requirement: Arbeitsagentur contact details are kept
When the full details of an Arbeitsagentur job are fetched, the system SHALL merge the detail data, including the employer's contact fields such as the application email address, into the job's existing source record instead of dropping it. Evaluation SHALL then see the employer's application email and SHALL choose the email channel when the ad asks for applications by email.

#### Scenario: Ad asks for an application by email
- **WHEN** an Arbeitsagentur job's detail contains the employer's contact email
- **THEN** that contact data is stored with the job's source record and the job is evaluated as an `email` application

## MODIFIED Requirements

### Requirement: Job-alert email source
The system SHALL read job-alert emails from a configured folder of the alert mailbox (see `email-submission` for supported connections). Each email SHALL be matched to a parser by its From address, never by its recipient. Processed emails SHALL be tracked so that none is ingested twice. This change ships parsers for LinkedIn, StepStone and Indeed. More senders SHALL be addable as a new parser without changes elsewhere. An email from a sender with a parser that gives no card, or whose parser fails, SHALL NOT be marked processed until the LLM fallback has read it (see `alert-resolution`).

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
- **WHEN** the alert folder contains an alert from a sender without a parser (e.g. XING) and `sources.alerts.llm_unknown_senders` is false
- **THEN** the email is skipped, left unchanged in the mailbox, counted as `unrecognised` in the run summary, and ingested once a parser for it is added

#### Scenario: Known sender, no cards
- **WHEN** an email from `jobalerts-noreply@linkedin.com` gives zero cards and the fallback is off
- **THEN** the run warns with the sender and subject, and the email stays unprocessed and is read again in the next run
