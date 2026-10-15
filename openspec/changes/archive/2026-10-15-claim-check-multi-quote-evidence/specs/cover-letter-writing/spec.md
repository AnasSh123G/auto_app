# Spec Delta

## MODIFIED Requirements

### Requirement: Claim check
The writer SHALL tag every factual claim as `cv`, `profile`, `instructions` or `assumed_basic`:
- `cv`, `profile` and `instructions` claims SHALL carry evidence whose every part appears in an accepted evidence source
- `assumed_basic` claims SHALL match a bridgeable ad requirement and stay within the cap

If violations remain after one rewrite, the application SHALL be flagged `needs_review`, with the violations listed.

#### Scenario: Unsupported claim rewritten
- **WHEN** the first draft claims "5 Jahre Erfahrung mit AWS" with no matching evidence and no bridgeable AWS requirement
- **THEN** the letter is rewritten once without that claim

#### Scenario: Violation remains
- **WHEN** the rewritten letter still contains an unsupported claim
- **THEN** the application is prepared, flagged `needs_review`, and the claim is listed in the folder's `README.md`

#### Scenario: Supported letter needs no rewrite
- **WHEN** every claim of the first draft is backed by evidence that passes the check
- **THEN** the letter is accepted after one LLM call and is not flagged `needs_review`

## ADDED Requirements

### Requirement: Evidence made of several quotes
The evidence of a `cv`, `profile` or `instructions` claim SHALL be split into parts at line breaks, semicolons, ` | `, `…` and `...`. Empty parts SHALL be ignored. The claim SHALL be accepted only if every part occurs in an accepted evidence source, compared without regard to case, runs of whitespace, typographic quotes or leading and trailing punctuation. A claim with an unbacked part SHALL fail, and the problem SHALL quote that part.

#### Scenario: Quotes from two CV lines
- **WHEN** a claim's evidence is `Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken; Testen und Überprüfen von Commits und Code anderer Entwickler (Peer Code Reviews)` and both parts are lines of the CV that are not next to each other
- **THEN** the claim passes the check

#### Scenario: Line break or bar as separator
- **WHEN** the same two quotes are joined by a line break or by ` | `
- **THEN** the claim passes the check

#### Scenario: One invented part
- **WHEN** a claim's evidence is `Entwicklung von Python-Services mit FastAPI | 5 Jahre AWS` and the CV contains only the first part
- **THEN** the claim fails, and the problem text contains `5 jahre aws`

### Requirement: Accepted evidence sources
A part of the evidence SHALL count as backed when it occurs in any accepted evidence source, whatever the claim's tag says. The accepted sources SHALL be the CV text used for the letter, the profile text given to the writer, the user's writing instructions, the search profile's extra writing instructions, and the app-supplied facts.

#### Scenario: Wrong tag
- **WHEN** a claim tagged `instructions` quotes a line that is in the CV
- **THEN** the claim passes the check

#### Scenario: Search-profile instructions
- **WHEN** the search profile's writing instructions say "I led a student team of 3" and a claim quotes that sentence
- **THEN** the claim passes the check

### Requirement: App-supplied facts are evidence, the ad is not
The facts the app gives the writer SHALL be accepted evidence: the earliest start date, both as `earliest_start: <date>` and as the request line that names it, and the salary sentence. The job ad text, the job title and the company name SHALL NOT count as evidence.

#### Scenario: Start date from the profile
- **WHEN** the profile's earliest start is `01.11.2026` and the letter says "Ich kann ab dem 01.11.2026 starten" with the evidence `If availability fits naturally, the candidate can start: 01.11.2026`
- **THEN** the claim passes the check

#### Scenario: Salary sentence
- **WHEN** the app asked for the salary sentence "Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr." and a claim quotes it
- **THEN** the claim passes the check

#### Scenario: Quote from the ad
- **WHEN** a claim tagged `cv` says the candidate knows Terraform and its evidence `Grundkenntnisse in Kubernetes, CI/CD, Terraform` occurs only in the job ad
- **THEN** the claim fails the check

### Requirement: Quoting rules given to the writer
The writer's instructions and response format SHALL tell the model to copy evidence word for word in the source's own language, to give one quote per place and separate several quotes with ` | `, never to paraphrase, shorten inside, or translate a quote, and that sentences which only name the position, the company or the attached documents are not claims and are not listed.

#### Scenario: Prompt wording
- **WHEN** the system prompt and the response schema for a letter are built
- **THEN** they state the ` | ` separator, the word-for-word and no-translation rule, and that naming the position, company or attachments is not a claim

#### Scenario: Form answers use the same check
- **WHEN** a free-text form answer's claim has evidence made of two exact CV quotes separated by ` | `
- **THEN** the answer passes the claim check
