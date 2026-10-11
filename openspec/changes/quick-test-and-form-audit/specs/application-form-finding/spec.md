# Spec Delta

## ADDED Requirements

### Requirement: Read-only audit of saved form links
`autoapp forms --audit` SHALL report, for the prepared non-email applications with a folder, the number prepared, their distribution by form status (`unchecked` for none yet), the links on job boards by host (a built-in directory host or an `assist.restricted_domains` entry), the split by source (a job found by several sources counting for each, with how many of each source are `found`) and the failures grouped by cause. It SHALL open the database read-only, make no network or model request, change no row, file or run record, and need no configured setup. With `--json` it SHALL print the same data as JSON with the stable keys `prepared`, `by_status`, `on_job_board` (`total`, `by_host`), `by_source` (per source `total` and `found`) and `failures` (`status`, `cause`, `count`). It SHALL NOT be combined with `--job`, `--again` or `--dry-run`, and `--json` SHALL require `--audit`; either misuse SHALL exit with code 2. A workspace without a database SHALL give an empty audit.

#### Scenario: Audit changes nothing
- **WHEN** the user runs `autoapp forms --audit` on a workspace with prepared applications
- **THEN** the database file is byte-for-byte unchanged and no request is made

#### Scenario: Distribution and job boards
- **WHEN** 42 applications are prepared, 18 are `found`, and 6 have an address on `arbeitsagentur.de` (5) or `linkedin.com` (1)
- **THEN** the report shows found 18 (43%), the other statuses, "on job board: 6 (arbeitsagentur.de 5, linkedin.com 1)" and the split by source

#### Scenario: JSON output
- **WHEN** the user runs `autoapp forms --audit --json`
- **THEN** the output is one JSON object with the keys `prepared`, `by_status`, `on_job_board`, `by_source` and `failures`

#### Scenario: Misuse
- **WHEN** the user runs `autoapp forms --audit --again`, or `autoapp forms --json` without `--audit`
- **THEN** the command prints an error and exits with code 2

### Requirement: Every form-finding run leaves a trace
Each time the form finder walks from an entry address for a job, it SHALL store one trace in the table `form_attempts`: the job, the strategy (`http`, with `browser` and `ats` reserved), the pages visited in order with the reason each was visited (`entry`, `redirect`, `link` or `model choice`), the final status, a note of at most 200 characters and the duration in milliseconds. `form_note` SHALL stay the one-line summary. A trace that cannot be stored SHALL NOT fail form finding. Traces SHALL be removed with their job. The audit SHALL group non-`found` results by cause from the newest trace of each job, and from `form_note` when it has none.

#### Scenario: Trace written
- **WHEN** the finder walks from an employer page through a "Jetzt bewerben" link to a form
- **THEN** one `form_attempts` row exists for the job with two hops (`entry`, `link`), the status `found` and the duration

#### Scenario: Redirect hop
- **WHEN** the entry address redirects once before the page is read
- **THEN** the trace lists the redirect target as a hop with the reason `redirect`

#### Scenario: Trace cannot be stored
- **WHEN** storing the trace fails with a database error
- **THEN** a warning is logged and the form-finding outcome is unchanged

#### Scenario: Failures grouped by cause
- **WHEN** 5 jobs ended `partial` with the note "no form within 4 steps"
- **THEN** the audit lists one failure entry with status `partial`, cause "no form within N steps" and count 5
