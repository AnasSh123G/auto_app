# Spec Delta

## ADDED Requirements

### Requirement: Quick test command
`autoapp test` SHALL run a short check of every step of the app instead of a full simulated run. It SHALL always run in a new test workspace under `workspace/test-runs/`, as a fresh test run does, and SHALL never change the real workspace's jobs, statuses, application folders or CSV. It SHALL accept `--profile` and `--no-dashboard`. It SHALL NOT be selectable as `run.default_mode`.

#### Scenario: Real history untouched
- **WHEN** the user runs `autoapp test` with 120 jobs in the real workspace
- **THEN** afterwards the real workspace still has the same 120 jobs, statuses and CSV, and the test workspace folder is named in the output

### Requirement: Quick test samples each step
The quick test SHALL apply these limits:
- the 1–2 newest alert emails, read but not marked as processed;
- one small query per enabled search source, keeping at most 2 jobs from each;
- at most 2 employer lookups on the active alert path;
- at most 4 evaluations;
- at most 2 preparations, preferring one email and one online-form application;
- at most 1 test email.
Every other rule of the steps SHALL still apply.

#### Scenario: Alert emails stay unprocessed
- **WHEN** the quick test reads 2 alert emails
- **THEN** both are ingested in the test workspace, and the next real run still ingests them

#### Scenario: Each source asked once
- **WHEN** Arbeitsagentur, Adzuna and Workwise are enabled and Arbeitnow is disabled
- **THEN** exactly those three sources are queried, with one small query each, and Arbeitnow is reported as SKIPPED (disabled)

#### Scenario: Employer lookup in search-first
- **WHEN** the alert mode is `search_first` and 3 alert jobs are ingested
- **THEN** at most 2 of them get the direct ad search and, if needed, the employer website and careers lookup, and saved employer sites are used before any new search

### Requirement: Quick test prepares regardless of score
The quick test SHALL prepare its selected jobs even when they score below the apply threshold, so that letter writing and form finding are exercised. The form application SHALL get its package and the read-only form finding, and SHALL NOT be filled in, uploaded to or submitted. The test email SHALL follow test-mode rules: sent only to the user's own address, with `TEST_RUN:` in the subject.

#### Scenario: Low-scoring job still prepared
- **WHEN** the only evaluated jobs score 40 and 55 with an apply threshold of 75
- **THEN** both are prepared in the test workspace, and the checklist reports preparation as OK

#### Scenario: No employer contacted
- **WHEN** the quick test prepares an email application addressed to `jobs@firma.de`
- **THEN** at most one test email arrives in the user's own mailbox, and nothing is sent to `jobs@firma.de`

### Requirement: Quick test checklist and exit code
The quick test SHALL end with one OK, FAILED or SKIPPED line, with a reason, for each of these:
- alert mailbox;
- each alert parser that was used;
- each search source;
- the search key;
- employer lookup;
- reading pages;
- the AI model;
- letter writing;
- form finding;
- the test email.
It SHALL exit non-zero when any line is FAILED. Search requests and AI usage SHALL be added to the real workspace's counters and caps.

#### Scenario: Adzuna key invalid
- **WHEN** Adzuna rejects the key during the quick test
- **THEN** the checklist shows "Adzuna: FAILED" with the reason, the other parts still run and report, and the command exits non-zero

#### Scenario: No alert emails available
- **WHEN** the alert folder has no email
- **THEN** the alert lines are SKIPPED with "no alert email in the folder" and the rest of the test still runs

#### Scenario: Spending is counted
- **WHEN** the quick test makes 3 search requests and 6 AI calls
- **THEN** the real workspace's daily and monthly search counts and its AI cost statistics include them
