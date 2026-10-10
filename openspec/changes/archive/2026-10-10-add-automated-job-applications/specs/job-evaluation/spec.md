# Spec Delta

## Purpose

Decides, with an LLM, whether each job is a good fit for the applicant based on the CV, profile and search profile. It also extracts the facts about the job that the application steps need.

## ADDED Requirements

### Requirement: Fit evaluation output
For each job with a description, the system SHALL produce an evaluation containing:
- a fit score from 0 to 100 and a decision (`apply`, `maybe`, `skip`)
- a short rationale
- each ad requirement with its required proficiency (`basic`, `intermediate`, `advanced`, `unspecified`) and its status (`matched`, `bridgeable`, `missing`)
- dealbreakers
- the seniority level, the required German level and the work mode, where the ad states them

#### Scenario: Suitable job
- **WHEN** a job's requirements largely match the CV and none of the profile's dealbreakers apply
- **THEN** the evaluation contains a fit score, decision `apply`, and the requirements classified as matched

#### Scenario: Dealbreaker present
- **WHEN** the ad requires German at C2 level and the profile states German at B2
- **THEN** the evaluation lists the language requirement as a dealbreaker and the decision is `skip`

### Requirement: Bridgeable requirements
A requirement absent from the CV and profile SHALL be classified `bridgeable` when its proficiency is `basic` or `unspecified` and it is not core to the role. Otherwise it is `missing`. Bridgeable requirements SHALL NOT count as dealbreakers and SHALL weigh much less than missing ones in the fit score.

#### Scenario: Basic tool requirement
- **WHEN** a backend job matching the CV well lists "Grundkenntnisse in Docker", and the CV and profile do not mention Docker
- **THEN** Docker is classified `bridgeable`, and the job can still reach decision `apply`

#### Scenario: Advanced requirement
- **WHEN** the ad requires "mehrjährige Erfahrung als Kubernetes-Administrator", and the CV and profile do not mention Kubernetes
- **THEN** the requirement is classified `missing`

#### Scenario: Core requirement absent
- **WHEN** a "Senior Java Developer" ad requires Java, and the CV and profile have no Java
- **THEN** Java is classified `missing`, not `bridgeable`, even though no proficiency level is stated

### Requirement: Decision thresholds
The final decision SHALL apply the configured thresholds:
- `apply` only if the score is at or above `apply_threshold` and there are no dealbreakers
- `maybe` if the score is at or above `maybe_threshold` but `apply` does not hold
- `skip` otherwise

#### Scenario: Score between thresholds
- **WHEN** `apply_threshold` is 75, `maybe_threshold` is 60, and a job scores 68 with no dealbreakers
- **THEN** the job's decision is `maybe`

#### Scenario: High score with dealbreaker
- **WHEN** a job scores 90 but has one dealbreaker
- **THEN** the job's decision is `maybe`, not `apply`

### Requirement: Application metadata extraction
The evaluation SHALL extract:
- the application channel (`email`, `employer_form`, `platform`, `unknown`)
- the application email address and contact person, if stated
- the reference number
- the documents the ad requests
- the ad's language (`de` or `en`)
- the salary range stated in the ad, if any

#### Scenario: Email application ad
- **WHEN** the ad says "Bitte senden Sie Ihre Bewerbung an jobs@muster-gmbh.de, Ansprechpartnerin Frau Weber, Kennziffer 2026-17"
- **THEN** the evaluation records channel `email`, address `jobs@muster-gmbh.de`, contact `Frau Weber` and reference `2026-17`

#### Scenario: Requested documents
- **WHEN** the ad asks for "Lebenslauf, Anschreiben und Arbeitszeugnisse"
- **THEN** the evaluation lists `cv`, `cover_letter` and `certificates` as requested documents

### Requirement: Grounded email extraction
An extracted application email address SHALL be kept only if it appears verbatim in the job's source text or in a structured contact field from the source. Otherwise the channel SHALL be recorded as `unknown`, with no address.

#### Scenario: Hallucinated address discarded
- **WHEN** the LLM returns `bewerbung@muster.de` but that string does not appear in the ad text or contact fields
- **THEN** the stored evaluation has no email address and the channel is not `email`

### Requirement: Evaluate each job once
A job SHALL be evaluated at most once unless the user explicitly asks for re-evaluation. The evaluation and the model that produced it SHALL be stored with the job.

#### Scenario: Re-evaluation on request
- **WHEN** the user runs `evaluate --job <id> --force`
- **THEN** the job is evaluated again and the new evaluation replaces the old one, keeping a record of the earlier model and score

### Requirement: Review queue for uncertain matches
Jobs with decision `maybe` SHALL NOT be prepared automatically. The user SHALL be able to list them and promote a job to `apply` or demote it to `skip`.

#### Scenario: User promotes a maybe
- **WHEN** the user runs `mark <job-id> apply` on a job with decision `maybe`
- **THEN** the job's decision becomes `apply` and it is prepared on the next `prepare` or `run`
