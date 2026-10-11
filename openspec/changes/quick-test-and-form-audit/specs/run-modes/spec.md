# Spec Delta

## MODIFIED Requirements

### Requirement: Quick test command
`autoapp test --quick` SHALL run a short check of every step of the app instead of a full simulated run. It SHALL always run in a new test workspace under `workspace/test-runs/`, as a fresh test run does, and SHALL never change the real workspace's jobs, statuses, application folders or CSV. It SHALL accept `--profile` and `--no-dashboard`. It SHALL NOT be selectable as `run.default_mode`. `autoapp test --full` SHALL run a whole test run in a new test workspace, exactly as `autoapp run --mode test --fresh` does. `autoapp test` without `--quick` or `--full` SHALL print which of the two to choose, SHALL start nothing and SHALL exit with code 2; passing both flags SHALL be an error with exit code 2. `autoapp run --mode test [--fresh]` SHALL keep working unchanged.

#### Scenario: Real history untouched
- **WHEN** the user runs `autoapp test --quick` with 120 jobs in the real workspace
- **THEN** afterwards the real workspace still has the same 120 jobs, statuses and CSV, and the test workspace folder is named in the output

#### Scenario: No flag
- **WHEN** the user runs `autoapp test` without a flag
- **THEN** the output names `autoapp test --quick` and `autoapp test --full`, no run starts, and the exit code is 2

#### Scenario: Both flags
- **WHEN** the user runs `autoapp test --quick --full`
- **THEN** the command reports that the two cannot be combined, starts nothing and exits with code 2

#### Scenario: Full test run
- **WHEN** the user runs `autoapp test --full`
- **THEN** a whole test run starts in a new test workspace, as `autoapp run --mode test --fresh` would, and every email a real run would send comes to the user, marked `TEST_RUN:`

### Requirement: Quick test samples each step
The quick test SHALL apply these limits:
- the 1–2 newest alert emails, read but not marked as processed;
- one small query per enabled search source, keeping at most 2 jobs from each;
- at most 2 employer lookups on the active alert path;
- at most 6 evaluations;
- at most 4 preparations: one email application and up to 3 online-form applications, the form applications taken from different sources in this order: one Arbeitsagentur job, one alert job, one job from another source; a source without a candidate gives its slot to the next best score;
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

#### Scenario: Form jobs from different sources
- **WHEN** evaluated form-channel jobs exist from the Arbeitsagentur, a LinkedIn alert and Adzuna
- **THEN** the quick test prepares one of each, plus one email application

#### Scenario: Source without a candidate
- **WHEN** no evaluated form-channel job comes from an alert
- **THEN** the alert slot goes to the best-scoring remaining form-channel job

### Requirement: Quick test checklist and exit code
The quick test SHALL end with one OK, FAILED, WARN or SKIPPED line, with a reason, for each of these:
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
It SHALL then list the form links (see "Quick test reports each form link"). It SHALL exit non-zero when any line is FAILED; WARN SHALL NOT change the exit code. Search requests and AI usage SHALL be added to the real workspace's counters and caps.

#### Scenario: Adzuna key invalid
- **WHEN** Adzuna rejects the key during the quick test
- **THEN** the checklist shows "Adzuna: FAILED" with the reason, the other parts still run and report, and the command exits non-zero

#### Scenario: No alert emails available
- **WHEN** the alert folder has no email
- **THEN** the alert lines are SKIPPED with "no alert email in the folder" and the rest of the test still runs

#### Scenario: Spending is counted
- **WHEN** the quick test makes 3 search requests and 6 AI calls
- **THEN** the real workspace's daily and monthly search counts and its AI cost statistics include them

#### Scenario: Only warnings
- **WHEN** every part is OK, SKIPPED or WARN
- **THEN** the exit code is 0

## ADDED Requirements

### Requirement: Quick test reports each form link
For each online-form application the quick test prepared, it SHALL print one line in a "form links" section: the job's short id, its source, the final address, the form status, the note and how the address was found (direct search, careers page, `externeURL` or the job page). A line SHALL be FAILED when the final address is on a job board (a built-in directory host or an `assist.restricted_domains` entry), except an ad that exists only on the Arbeitsagentur (note "only on the Arbeitsagentur"), which SHALL be WARN; OK when the form status is `found`; and WARN for every other status, including `no_employer_link`.

#### Scenario: Link on a job board
- **WHEN** a prepared Arbeitsagentur application's final address is on `arbeitsagentur.de` or a prepared alert application's is on `linkedin.com`
- **THEN** its line is FAILED with the host named, and the command exits non-zero

#### Scenario: Employer form found
- **WHEN** a prepared Arbeitsagentur application's final address is `careers.ejot.com/job/123#apply` with status `found`
- **THEN** its line is OK and names the Arbeitsagentur as the source

#### Scenario: Partial result
- **WHEN** a prepared application ends `partial` on a page that is not a job board
- **THEN** its line is WARN with the note, and the exit code is not affected by it

#### Scenario: Ad only on the Arbeitsagentur
- **WHEN** a prepared Arbeitsagentur job was posted directly there and its link is the Arbeitsagentur page with the note "only on the Arbeitsagentur"
- **THEN** its line is WARN, not FAILED, and the exit code is not affected by it

### Requirement: Quick test shows the real workspace's saved form links
After its checklist the quick test SHALL print the form-link audit of the real workspace (see `application-form-finding`) as the baseline of today's links. It SHALL read the real workspace without writing to it. When the audit cannot be read, the quick test SHALL say so and continue.

#### Scenario: Baseline after the checklist
- **WHEN** the real workspace has 42 prepared non-email applications and the quick test finishes
- **THEN** the output ends with the audit lines: found count and share, other statuses, links on job boards by host and the split by source

#### Scenario: Real database unreadable
- **WHEN** the real workspace's database cannot be opened read-only
- **THEN** the output says the existing form links are not available, and the exit code is decided by the checklist alone
