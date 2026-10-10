# Spec Delta

## Purpose

Generates cover letters and other application text that read like a capable person wrote them. The text presents the applicant's experience persuasively and honestly, follows the user's own writing instructions, and fits on one page.

## ADDED Requirements

### Requirement: Letter language
The cover letter SHALL be written in the ad's language unless the configuration forces one. The CV variant in that language SHALL be used as the main source when one exists.

#### Scenario: German ad gets a German letter
- **WHEN** the ad's language is `de`
- **THEN** the cover letter is written in German, and the German CV variant is used if one exists

### Requirement: No invented facts
The letter SHALL NOT state facts that the CV, the profile or the user's writing instructions do not support. Facts here means employers, job titles, dates, degrees, certifications, language levels, years of experience and numeric results.

#### Scenario: Years of experience
- **WHEN** the CV shows 3 years as a backend developer and the ad asks for 5 years
- **THEN** the letter does not claim 5 years of experience

#### Scenario: Fact from writing instructions
- **WHEN** the writing instructions say "I used Terraform in personal projects" and the CV does not mention Terraform
- **THEN** the letter may say the applicant has used Terraform in personal projects

### Requirement: Persuasive framing
The letter SHALL present experience the CV or profile supports in its strongest truthful form:
- active verbs, with ownership and impact
- the scope of the work and its relevance to the ad

It SHALL NOT use hedging phrases ("some experience", "a little", "basic knowledge") for skills the CV shows.

#### Scenario: Stronger framing of a CV fact
- **WHEN** the CV says "Maintained CI pipelines used by 4 teams"
- **THEN** the letter may describe the applicant as responsible for the CI pipelines of four teams, and does not describe it as minor support work

### Requirement: Assumed basic skills for close matches
When a job's decision is `apply`, the letter MAY claim basic working familiarity with up to `writing.max_assumed_skills` (default 5) ad requirements that are absent from the CV and profile. This applies only if the evaluation classified those requirements as `bridgeable`. Assumed skills SHALL be worded as familiarity or working knowledge, never as years or expertise.

#### Scenario: Basic Docker requirement
- **WHEN** a job with decision `apply` asks for "Grundkenntnisse in Docker", which the evaluation classified as bridgeable, and the CV does not mention Docker
- **THEN** the letter may state working familiarity with Docker without naming years or expert-level use

#### Scenario: Advanced requirement not assumed
- **WHEN** the ad asks for "5+ Jahre Erfahrung mit Kubernetes" and the CV does not mention Kubernetes
- **THEN** the letter does not claim Kubernetes experience

#### Scenario: Cap reached
- **WHEN** `writing.max_assumed_skills` is 5 and the ad has 7 bridgeable requirements
- **THEN** at most 5 of them are mentioned as assumed skills

### Requirement: Claim check
The writer SHALL tag every factual claim as `cv`, `profile`, `instructions` or `assumed_basic`:
- `cv`, `profile` and `instructions` claims SHALL carry an evidence quote that appears in that source
- `assumed_basic` claims SHALL match a bridgeable ad requirement and stay within the cap

If violations remain after one rewrite, the application SHALL be flagged `needs_review`, with the violations listed.

#### Scenario: Unsupported claim rewritten
- **WHEN** the first draft claims "5 Jahre Erfahrung mit AWS" with no matching evidence and no bridgeable AWS requirement
- **THEN** the letter is rewritten once without that claim

#### Scenario: Violation remains
- **WHEN** the rewritten letter still contains an unsupported claim
- **THEN** the application is prepared, flagged `needs_review`, and the claim is listed in the folder's `README.md`

### Requirement: Assumed skills disclosed to the user
Every assumed skill used in an application SHALL be listed in the folder's `README.md` under "Before an interview, brush up on" and in the tracker's `assumed_skills` column.

#### Scenario: Assumed skill listed
- **WHEN** a letter claims working familiarity with Docker as an assumed skill
- **THEN** the application's `README.md` and its CSV row both list Docker

### Requirement: Human writing style
Generated prose SHALL NOT contain:
- an em dash, an en dash, or a spaced hyphen used as punctuation (hyphens inside compound words such as "Software-Entwickler" are allowed)
- any phrase from the built-in or user-added lists of phrases that sound AI-written
- exclamation marks or rhetorical questions

A violating draft SHALL be rewritten once. Remaining dash violations SHALL then be fixed automatically, and remaining phrase violations SHALL flag the application `needs_review`.

#### Scenario: Dash used as punctuation
- **WHEN** a draft contains "Ich bringe Erfahrung mit — vor allem in Python"
- **THEN** the final letter contains no em dash

#### Scenario: Compound word hyphen kept
- **WHEN** a letter contains "Backend-Entwicklung"
- **THEN** it is not flagged as a style violation

#### Scenario: Stock AI phrase
- **WHEN** a draft contains "I am writing to express my interest"
- **THEN** the draft is rewritten, and the final letter does not contain that phrase

#### Scenario: User extends the phrase list
- **WHEN** the user adds "spannende Herausforderung" to `writing.banned_phrases`
- **THEN** letters containing that phrase are treated as style violations

### Requirement: Length limit
The letter body SHALL NOT exceed `writing.max_words` (default 300). The rendered letter, including sender block, address, date, subject and signature, SHALL fit on one A4 page.

#### Scenario: Draft too long
- **WHEN** a draft body has 380 words and `writing.max_words` is 300
- **THEN** the letter is rewritten to at most 300 words before rendering

### Requirement: Custom writing instructions
The user's global writing instructions, plus any instructions attached to the job's search profile, SHALL be included in every writing request. They SHALL take precedence over the default style preferences. The no-invented-facts rule, the length limit and the letter format SHALL still apply.

#### Scenario: Instruction followed
- **WHEN** the writing instructions say "Mention that I can start within 4 weeks"
- **THEN** every generated letter mentions a start within 4 weeks

#### Scenario: Per-profile instructions
- **WHEN** search profile `startups` has the extra instruction "Use a more informal tone" and a job was found by that profile
- **THEN** that job's letter is written with both the global instructions and the extra instruction

### Requirement: Writing samples as voice reference
When the profile declares documents with role `writing_sample`, their text SHALL be used as a reference for the applicant's voice and tone. Sentences from the samples SHALL NOT be copied verbatim.

#### Scenario: Sample letter provided
- **WHEN** the user declares `my_old_letter.pdf` as a writing sample
- **THEN** generated letters use it as a voice reference and do not contain any sentence copied verbatim from it

### Requirement: German business letter format
Cover letters SHALL follow German business letter conventions:
- the sender block and the date with the place
- a subject line that includes the reference number if one exists
- a personal salutation when a contact person is known ("Sehr geehrte Frau Weber,"), otherwise "Sehr geehrte Damen und Herren,"
- a closing and the applicant's name

#### Scenario: Unknown contact person
- **WHEN** the evaluation has no contact person and the language is `de`
- **THEN** the salutation is "Sehr geehrte Damen und Herren,"

#### Scenario: Reference number in subject
- **WHEN** the ad's reference number is `2026-17`
- **THEN** the subject line contains `2026-17`

### Requirement: Same rules for all generated prose
The truthfulness, style and instruction rules SHALL also apply to the short motivation text in `form_answers.md`, to the email body, and to free-text answers generated during assisted form filling.

#### Scenario: Motivation text style
- **WHEN** the motivation text is generated for `form_answers.md`
- **THEN** it contains no dash used as punctuation and no listed AI phrase, and it follows the writing instructions
