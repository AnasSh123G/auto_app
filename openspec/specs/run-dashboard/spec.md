# run-dashboard Specification

## Purpose
Shows the user what the tool is doing and what it has done: a statistics page with the numbers that matter, and a live view during a run that pairs those numbers with a feed of every email and form that was sent or saved.

## Requirements

### Requirement: Statistics page
`autoapp stats` (also reachable from the main menu, which is specified by the `add-menu-and-run-modes` change) SHALL show a statistics page computed from the database. The user SHALL be able to switch the period between this run (the latest run), today, the last 7 days, the last 30 days and all time. The page SHALL include:
- **models in use:** the provider and model for each task (evaluate, write, assist form-fill), and the reasoning level where configured
- **ads:** read (found), new, duplicates, filtered by the rules, evaluated, and the number and share that were approved (decision `apply`, which means a score at or above the apply threshold), maybe and skipped, plus the average fit score
- **applications:** folders saved, split by channel (email, employer form, job-site form); emails sent, emails held back with the reason (cap, minimum score, free-mail, needs review, cooldown) and unconfirmed sends; forms filled with `assist`; applications marked submitted
- **review:** jobs waiting in the review queue and jobs flagged `needs_review`
- **sources:** per source, jobs found and new, the number of alert emails read and unrecognised senders, and the Adzuna requests used against its caps
- **LLM usage:** calls, tokens and the estimated cost for the period, and the cost per prepared application, plus a table with the calls, input tokens (and the cached part), output tokens and estimated cost of each model used in the period and the tasks it was used for
- **pipeline outcomes:** the status counts, the most common missing and bridgeable requirements across evaluated jobs (what to brush up), and errors and warnings of the latest run

All costs SHALL be labelled as estimates and a model without a known price SHALL show "unknown", not zero. An empty database SHALL show zeros and no error.

#### Scenario: Statistics after a run
- **WHEN** the user opens the statistics page after a run that read 108 ads, evaluated 20, approved 4, saved 4 folders and sent 2 emails
- **THEN** the page shows those numbers, an approval share of 20 % of evaluated ads, the models used, and the estimated cost of the run

#### Scenario: Usage per model
- **WHEN** a run used one model for evaluation and another for writing
- **THEN** the page lists each model with its own calls, tokens and estimated cost, and a model without a price shows "unknown"

#### Scenario: Period switch
- **WHEN** the user switches from "today" to "last 7 days"
- **THEN** every number is recomputed for the new period and the label shows the period

#### Scenario: Empty workspace
- **WHEN** the page is opened before any run
- **THEN** all counts are zero, the models in use come from the configuration, and a hint says to run `autoapp run`

### Requirement: Live view during a run
While `run` executes in an interactive terminal, the screen SHALL be a live view with two parts that update as work happens, without scrolling the whole screen:
- **top:** statistics about the current run (current step with progress, elapsed time, models in use, ads read, new, filtered, evaluated, approved, folders saved, emails sent, forms saved, held back, errors, estimated cost so far, and the emails sent today against the daily cap)
- **bottom:** a feed with one line for every email sent, email held back, application folder saved and form filled, newest last, each with the time, the kind, the company and job title, and the recipient address or the folder name. Evaluation outcomes (approved, maybe, skipped, needs review) and warnings and errors SHALL appear in the same feed in a lower-key style, and the user SHALL be able to filter the feed to emails and forms only.

In a dry run, the feed SHALL say "would send" instead of "sent" and the top part SHALL show a "dry run" label. When the run ends, the view SHALL stay on screen with a final summary until a key is pressed, then print the plain summary to the terminal so it remains in the scrollback.

#### Scenario: Feed lines for sent and saved applications
- **WHEN** a run prepares a job for an email application and then sends it
- **THEN** the feed first shows a "saved" line with the company, title and folder name, then a "sent" line with the company, title and the recipient address, and the top counters for folders saved and emails sent each increase by 1

#### Scenario: Dry run labelling
- **WHEN** the run is started with `--dry-run`
- **THEN** the top part shows "dry run" and the feed shows "would send" lines, and no counter claims an email was sent

#### Scenario: Run finishes
- **WHEN** the last step completes
- **THEN** the view shows a final summary and waits for a key, then prints the plain summary text

### Requirement: Events are stored and can be watched from another terminal
Every event shown in the feed SHALL be written to the database with its run, time, kind, job and message, and the run's counters SHALL be stored and updated as the run progresses. The statistics page SHALL, when a run is active in another process, show that run live from the stored data and SHALL mark a run as stale after 15 minutes without an update. Viewing SHALL never block or slow the run, and SHALL never take the workspace lock.

#### Scenario: Watching a scheduled run
- **WHEN** a scheduled run is active and the user opens "Statistics" from the menu in another terminal
- **THEN** the page shows the active run with its counters and feed, refreshing about once per second

#### Scenario: Viewer closed
- **WHEN** the user closes the viewer while the run is active
- **THEN** the run continues unaffected

### Requirement: Plain output when there is no terminal
Without an interactive terminal, with `--non-interactive`, with `--no-dashboard`, or when the terminal is too small, `run` SHALL print one plain line per feed event and the normal summary, and SHALL NOT draw the live view. The feed lines SHALL be the same text in the log file. `NO_COLOR` SHALL be respected, and a narrow terminal SHALL show the top part as a compact list instead of columns.

#### Scenario: Scheduled run
- **WHEN** `autoapp run --non-interactive` runs from the systemd timer
- **THEN** the output and the log contain one plain line per sent or saved application and no screen-control characters

#### Scenario: Narrow terminal
- **WHEN** the terminal is 60 columns wide
- **THEN** the top part is shown as a compact list and the feed lines are shortened without hiding the kind or the company

### Requirement: The view never affects the run or leaks secrets
An error in drawing the view SHALL be logged and SHALL switch the run to plain output; it SHALL NOT stop the run or change any result. The feed and the statistics SHALL NOT show passwords, API keys, login tokens or the full text of a letter. Each line SHALL contain only facts the user's own data already holds (company, title, recipient address, folder name, score).

#### Scenario: Drawing fails
- **WHEN** drawing the view raises an error during a run
- **THEN** the run finishes normally with plain output, and the error is in the log

#### Scenario: No secrets in the feed
- **WHEN** an email fails to send because of a wrong password
- **THEN** the feed line says that the login was rejected and never contains the password
