# alert-first-evaluation Specification

## Purpose

Makes sure jobs that arrived through the user's alert mails are evaluated and prepared before jobs from search sources, so that a run that is interrupted or reaches a limit still handles the alerts.

## Requirements

### Requirement: Alert jobs first
Within each run step, eligible jobs with any alert source SHALL precede other jobs, stably within each group and before limits. Alert sources SHALL include `linkedin-alert`, `indeed-alert`, `indeed-match`, `stepstone-alert` and `stepstone-alert-recommended`. A job found by both alert and search SHALL count as alert-sourced. Explicit job-ID selections SHALL preserve the requested order instead.

#### Scenario: Limit reached
- **WHEN** 80 search jobs and 12 alert jobs are eligible, the evaluation limit is 50 and the shared LLM budget permits 50 evaluations
- **THEN** all 12 alert jobs and 38 search jobs are evaluated and 42 wait for the next run

#### Scenario: Interrupted run
- **WHEN** a run is stopped after the first 20 evaluations of eligible jobs
- **THEN** every alert job was among those 20, as long as there are 20 or fewer

#### Scenario: Explicit job ids
- **WHEN** evaluate or prepare is given explicit job IDs
- **THEN** only the eligible selected jobs are processed, in the order given

#### Scenario: Recommended StepStone card
- **WHEN** a job has source `stepstone-alert-recommended` and otherwise precedes or follows search jobs in discovery order
- **THEN** it belongs to the alert-first group, not the search group

#### Scenario: Alert and search duplicate
- **WHEN** a job's sources include both `adzuna` and `indeed-match`
- **THEN** it is processed once in the alert-first group

### Requirement: Limits remain shared across steps
Evaluation and preparation SHALL retain their existing eligibility rules and per-step limits. The LLM call limit SHALL remain shared across evaluation, writing, finding and filling; ordering SHALL NOT reserve a separate budget or guarantee preparation after evaluation exhausts it. Jobs not processed because of a limit SHALL retain their resumable state and be reported as waiting.

#### Scenario: Evaluation consumes the model budget
- **WHEN** alert-first evaluation exhausts the shared call budget before preparation can write letters
- **THEN** no extra budget is granted, approved jobs remain waiting for preparation, and the run reports the limit

### Requirement: Alert cards are parsed into the right fields
The LinkedIn alert parser SHALL treat badge lines such as "Apply with resume & profile", "1 school alum", "2 school alumni", "3 connections" and "This company is actively hiring" as badges and not as title, company or location, so that every card has title, company and city in the right fields.

#### Scenario: Singular alum badge
- **WHEN** a card reads "Softwareentwickler Java (m/w/d)", "Bayerische Versorgungskammer", "Greater Munich Metropolitan Area", "1 school alum"
- **THEN** the title, company and location are those first three lines and "1 school alum" is a badge
