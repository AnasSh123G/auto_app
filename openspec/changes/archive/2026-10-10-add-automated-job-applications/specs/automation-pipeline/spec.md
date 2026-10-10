# Spec Delta

## Purpose

Chains discovery, evaluation, preparation, sending and tracking into one safe, repeatable command. The command can run unattended on a schedule and protects the user's personal data and credentials.

## ADDED Requirements

### Requirement: End-to-end run command
`run` SHALL execute these steps in order, each operating on the jobs left by the previous step:
1. search
2. fetch details
3. rule-based filter
4. evaluate
5. prepare approved jobs
6. auto-send eligible email applications
7. export the CSV

It SHALL print a summary with the counts for each step and the estimated LLM cost of the run.

#### Scenario: Normal run
- **WHEN** the user runs `run` with valid configuration
- **THEN** each step runs once in order, and a summary shows the jobs found, filtered, evaluated, approved, prepared and sent, the estimated LLM cost, and any errors

### Requirement: Individual step commands
Each step SHALL also be available as its own command (`search`, `evaluate`, `prepare`, `send`, `export`), operating on the jobs currently in the matching status.

#### Scenario: Prepare only
- **WHEN** the user runs `prepare` while 3 jobs have status `approved`
- **THEN** folders are created for those 3 jobs, and no search or evaluation happens

### Requirement: Per-run limits
The configuration SHALL bound each run with a maximum number of jobs evaluated, a maximum number of applications prepared, and the email daily cap. Work beyond a limit SHALL wait for the next run.

#### Scenario: Preparation limit
- **WHEN** `limits.max_prepare_per_run` is 10 and 14 jobs are approved
- **THEN** 10 are prepared, and 4 remain `approved` for the next run

### Requirement: Non-interactive operation
With `--non-interactive` (or when not attached to a terminal), commands SHALL never prompt. They SHALL skip steps that need confirmation and exit non-zero on configuration or authentication errors.

#### Scenario: Scheduled run with bad credentials
- **WHEN** a scheduled `run --non-interactive` starts and the SMTP password is wrong
- **THEN** the run completes the steps that need no email, does not prompt, logs the error, and exits with a non-zero code

### Requirement: Single instance
Only one run SHALL operate on a workspace at a time. A second concurrent run SHALL exit immediately with a message.

#### Scenario: Overlapping scheduled runs
- **WHEN** a run is still in progress and another `run` starts on the same workspace
- **THEN** the second run exits without doing any work and reports that another run is active

### Requirement: Run logs without secrets
Each run SHALL write a log file in the workspace's log directory. API keys, passwords and authentication tokens SHALL never appear in logs or console output.

#### Scenario: Secret redaction
- **WHEN** an HTTP error message includes the request's API key
- **THEN** the logged message shows the key as `***`

### Requirement: Workspace privacy
All personal data (documents, profile, database, application folders, browser profile, logs) SHALL live under the workspace directory. The repository SHALL ignore the workspace directory and `.env` in version control by default.

#### Scenario: Personal files not committed
- **WHEN** the user runs `git status` after a run in the default workspace location
- **THEN** no workspace files or `.env` appear as untracked or modified

### Requirement: Scheduling support
The project SHALL include an example systemd user service and timer that run `run --non-interactive` daily, with instructions to install and enable them.

#### Scenario: Daily timer
- **WHEN** the user installs the example timer
- **THEN** `run --non-interactive` executes once per day, and its log is written to the workspace log directory

### Requirement: Run events
Every step SHALL report its progress and each noteworthy result (application folder saved, email sent or held back, form filled, job approved, maybe or skipped, needs review, warning, error) as a run event with the time, kind, job, company, title and a short message. `run` SHALL write each event to the log and to the database and SHALL accept `--no-dashboard` to print plain event lines instead of the live view.

#### Scenario: Events from a step
- **WHEN** the prepare step saves a folder for an email application
- **THEN** a "saved" event with the company, title and folder name is written to the log and the database, and shown in the live view when one is active

#### Scenario: Disabling the live view
- **WHEN** the user runs `autoapp run --no-dashboard`
- **THEN** one plain line is printed per event and the usual summary follows

