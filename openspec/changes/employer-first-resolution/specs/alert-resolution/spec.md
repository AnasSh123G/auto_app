# Spec Delta

## ADDED Requirements

### Requirement: Alert cards keep a redacted excerpt of their email
For every alert card the system SHALL store, with its source record, a redacted excerpt of the card's block in the email (at most about 1,500 characters) and the email's message id. Redaction SHALL run before anything is stored, sent to a model or sent to a search. It SHALL remove the profile's name, address, email address and phone number, every email address, unsubscribe and tracking links, and the query string of every link. Job links SHALL stay, without their query.

#### Scenario: Excerpt stored without personal data
- **WHEN** a LinkedIn alert email addressed to "Max Mustermann" with an unsubscribe link and tracking parameters is ingested
- **THEN** each card's stored excerpt names the job, company and city, and contains no part of the profile's name, email address or phone number, no unsubscribe or tracking link and no link query string

### Requirement: No alert email is lost silently
An email from a sender that has a parser, and that gives zero cards or makes the parser fail, SHALL NOT be marked processed before the LLM fallback has read it. When the fallback is switched off, is skipped (for example because the extraction cap is reached) or fails, the email SHALL stay unprocessed, SHALL be read again in the next run, and the run SHALL warn with the sender and the redacted subject. When the fallback ran and found no job either, the email SHALL be marked processed and the run SHALL warn that nothing was found.

#### Scenario: Broken layout, fallback finds the jobs
- **WHEN** a LinkedIn alert email yields zero cards from the parser and the fallback names 3 jobs from the redacted email
- **THEN** those 3 jobs enter the search-first path and the email is marked processed exactly once

#### Scenario: Broken layout, fallback off
- **WHEN** a known sender's email yields zero cards and `sources.alerts.llm_fallback` is false
- **THEN** the run warns with the sender and subject, and the email is not marked processed

#### Scenario: Fallback finds nothing
- **WHEN** the fallback reads a zero-card email and names no job
- **THEN** the run warns that the extraction fallback found no job, and the email is marked processed so that it is not read and paid for again

### Requirement: LLM fallback names the company or title the parser could not
When `sources.alerts.llm_fallback` is true (the default), the system SHALL pass the redacted text of a card to the `alert_extract` model when the card has an unknown, empty or placeholder company, or an empty title, or a truncated title with fewer than 2 words left. It SHALL pass the whole redacted email when a known sender's email gives zero cards or the parser fails. It SHALL pass an email from a sender without a parser only when `sources.alerts.llm_unknown_senders` is true and the email reads like a job alert. The model SHALL return a list of title, company, city and optional job link. Every returned value SHALL appear in the text that was given to the model; a value that does not SHALL be dropped. The cards it yields SHALL enter the normal search-first path with the same acceptance checks. At most `sources.alerts.max_llm_extractions_per_run` model calls (default 20) SHALL be made per run, and all of them SHALL count on the shared AI usage meter.

#### Scenario: Card without a company
- **WHEN** a card shows "Backend Developer (m/w/d)" and the parser marks its company unknown, and the redacted text names "Muster GmbH" next to the job
- **THEN** the model's answer names "Muster GmbH", the card is completed with it and the job is searched as "Muster GmbH" "Backend Developer (m/w/d)"

#### Scenario: Invented value rejected
- **WHEN** the model returns a company that does not appear in the text it was given
- **THEN** that value is dropped and the card is treated as having no company

#### Scenario: Extraction cap
- **WHEN** a run has already made `max_llm_extractions_per_run` extraction calls and another card needs one
- **THEN** no further call is made, the card keeps what the parser gave, and a zero-card email stays unprocessed with a warning

#### Scenario: Unknown sender is opt-in
- **WHEN** an alert-like email comes from a sender without a parser and `sources.alerts.llm_unknown_senders` is false
- **THEN** it is not sent to the model and is counted as unrecognised, as before

### Requirement: Search by the alert's text when the employer is not named
When the extraction fallback still yields no company for an alert job that has a title and a redacted excerpt, the system SHALL search for the employer's own ad with that excerpt as the search objective (at most 4,000 characters, introduced by a sentence that marks the text as data and excludes job boards) and queries made from the title and city. One search request per job SHALL be made, within the existing search caps and the miss memory. A result SHALL be accepted only under the existing own-ad rules, with one relaxation: the employer is taken from the page (the ad's company, else the site's own name, never a shared host or a directory) and SHALL also be named in the excerpt. A job found this way SHALL be marked `confidence: from_email_text`, SHALL be flagged `needs_review` with a reason that says the ad was found from the alert's text, and SHALL NOT be sent automatically in `automatic` mode without review. A job without a title or without an excerpt SHALL NOT be searched this way.

#### Scenario: Workwise card without a company
- **WHEN** a Workwise alert card names no company and the search finds a vacancy page that names "Muster GmbH" in its heading context, and "Muster GmbH" is also in the card's excerpt
- **THEN** the job gets that page's description and address, its company becomes "Muster GmbH", and it is `needs_review` with confidence `from_email_text`

#### Scenario: Page employer not in the excerpt
- **WHEN** the search finds a vacancy page whose employer is not named in the alert's text
- **THEN** the page is rejected and the job waits with the reason

#### Scenario: Missing search key
- **WHEN** no search key is set and an alert job qualifies for this search
- **THEN** it is counted in the run's "waiting for the search key" warning and no request is sent

### Requirement: Search and run use the same employer passes
`autoapp search` and `autoapp run` SHALL run the same employer passes for the same configuration: the pre-fetch alert ad search and discovery in `search_first`, and after evaluation the own-ad search whenever employer discovery or the Arbeitsagentur source is enabled, followed by the careers-page lookup for the Arbeitsagentur jobs that search missed.

#### Scenario: Search with employer discovery off
- **WHEN** `employer_careers.enabled` is false, the Arbeitsagentur source is enabled, and the user runs `autoapp search`
- **THEN** the own-ad search for Arbeitsagentur jobs runs, as it does in `autoapp run`

## MODIFIED Requirements

### Requirement: Search-first outcomes are reported
Run summaries and the live feed SHALL state the active alert mode. In `search_first`, they SHALL count alert jobs found by direct search, found via a careers page, not found and deferred, and give each job's reason. The alert section SHALL start with a coverage line: `alerts: N cards -> X searched, Y found directly, Z via careers page, W waiting (reasons), V needed LLM extraction`, with at most the three most common reasons. Search usage SHALL stay reported separately from LLM cost.

#### Scenario: Summary after a search-first run
- **WHEN** a run handled 10 alert jobs: 6 found directly, 2 via careers pages, 1 not found, 1 deferred by the cap
- **THEN** the summary shows the mode `search_first` and those four counts, and the per-job reasons are in the feed

#### Scenario: Coverage line
- **WHEN** a run ingested 12 cards, searched 10 of them, 6 were found directly, 2 via careers pages, 2 wait for "employer ad not found", and the model named the company of 1 card
- **THEN** the summary shows `alerts: 12 cards -> 10 searched, 6 found directly, 2 via careers page, 2 waiting (employer ad not found), 1 needed LLM extraction`
