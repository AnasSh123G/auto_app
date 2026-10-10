# Spec Delta

## Purpose

Lets the user operate the whole tool from one menu instead of remembering commands: start a run, look at results, test the setup, and change settings, without leaving the program until they choose to.

## ADDED Requirements

### Requirement: Menu on start
Running `autoapp` with no subcommand in an interactive terminal SHALL open the main menu. If setup is not complete, the onboarding wizard SHALL run first and the menu opens when it finishes. Without an interactive terminal, or with `--non-interactive`, `autoapp` with no subcommand SHALL print the command help and exit without prompting. Subcommands such as `autoapp run` SHALL keep working exactly as before.

#### Scenario: Menu in a terminal
- **WHEN** the user runs `autoapp` in an interactive terminal with a completed setup
- **THEN** the menu opens and lists its entries

#### Scenario: First start
- **WHEN** the user runs `autoapp` for the first time in a terminal
- **THEN** the onboarding wizard runs, and the menu opens after it finishes

#### Scenario: No terminal
- **WHEN** `autoapp` runs with no subcommand while its output is piped, or from a scheduler
- **THEN** the help text is printed, nothing is prompted, and the exit code is 0

### Requirement: Menu entries
The main menu SHALL offer these entries, in this order: Run now, Statistics, Applications, Review queue, Finish forms, Add a job by URL, Tests, Settings, Quit. Each entry SHALL show a one-line description. Entries whose work does not exist yet SHALL stay visible and say why (for example "Finish forms: no prepared forms"), so the menu does not change shape between visits.

#### Scenario: Entries and hints
- **WHEN** the menu opens while 3 form applications are prepared and 2 jobs wait for review
- **THEN** "Finish forms" shows 3, "Review queue" shows 2, and all nine entries are listed

#### Scenario: Entry with nothing to do
- **WHEN** no form application is prepared
- **THEN** "Finish forms" is listed with the note that no forms are prepared, and choosing it returns to the menu with that message

### Requirement: Stay in the menu until Quit
After an entry finishes, fails or is cancelled with Ctrl-C, the menu SHALL return to the main menu with a short result line. Only Quit, or Ctrl-C at the main menu, SHALL leave the program. The menu SHALL hold no workspace lock and no open database transaction while it waits for input, so that a scheduled run can start in between. An entry that changes data SHALL take the lock for its own duration, like its command.

#### Scenario: Cancel an entry
- **WHEN** the user presses Ctrl-C during the "Add a job by URL" entry
- **THEN** nothing is saved and the main menu appears again

#### Scenario: Scheduled run while the menu is idle
- **WHEN** the menu is waiting for input and a scheduled run starts
- **THEN** the run acquires the lock normally, and the menu does not block it

### Requirement: Entries reuse the commands
Each menu entry SHALL call the same functions as its command (`run`, `stats`, `list` and `mark`, `assist`, `add`, `setup`, `send --test`), so that the menu and the commands cannot behave differently. The menu SHALL contain only prompts and navigation.

#### Scenario: Same result as the command
- **WHEN** the user chooses "Add a job by URL" and enters a link that `autoapp add` would reject
- **THEN** the menu shows the same error message as the command and returns to the menu

### Requirement: Applications entry
"Applications" SHALL list the jobs that have an application (`prepared`, `drafted`, `sent`, `submitted` and later), newest first, with status, company, title and score, and let the user filter by status. Choosing one SHALL open an action list with: open the folder path, show the cover letter text, change the status, re-render after editing the letter, save as draft (email applications), send now (email applications, with confirmation), send a test copy to myself, and fill the form (form applications). Each action SHALL be the existing command for that job.

#### Scenario: Save one email as a draft
- **WHEN** the user opens a prepared email application and chooses "Save as draft"
- **THEN** the draft is saved in their mailbox, the status becomes `drafted`, and the menu says where the draft is

#### Scenario: Change status
- **WHEN** the user chooses "Change status" and picks `interview`
- **THEN** the status is stored with a status-history entry, as `mark` would do

### Requirement: Review queue entry
"Review queue" SHALL list the jobs with a `review` status or a `needs_review` flag together with their reasons. For each job the user SHALL be able to read the evaluation summary, mark it as apply or skip, and, for a flagged letter, open the cover letter path and re-render it after editing.

#### Scenario: Approve a job from the queue
- **WHEN** the user chooses a job in `review` and picks "Apply"
- **THEN** the job becomes `approved` and is prepared in the next run

### Requirement: Tests entry
"Tests" SHALL offer these actions, each showing a clear pass or fail line with the reason on failure:
- a test run: a full run in test mode, where every email a real run would send comes to me instead, marked `TEST_RUN:`
- send a test email for one application to myself (`send --test`)
- check the logins: the alert mailbox over IMAP, the sending mailbox over SMTP and IMAP (including that the Drafts folder is found), the AI model with one minimal call, and the Adzuna key
- run a dry run (a full run that sends nothing, saves no drafts and marks no alert emails as processed)

#### Scenario: Login check finds a problem
- **WHEN** the app password for the sending mailbox is wrong
- **THEN** "Check logins" shows a failure for the sending mailbox with the reason and a hint to run the email section of the setup, and still reports the other checks

#### Scenario: Test run from the menu
- **WHEN** the user chooses "Test run"
- **THEN** the mode prompt is not shown, the run executes in test mode, and the emails arrive in the user's own mailbox

#### Scenario: Dry run from the menu
- **WHEN** the user chooses "Run a dry run"
- **THEN** the run mode prompt is not shown, the run is a dry run, and no email, draft or processed-email mark is produced

### Requirement: Settings entry
"Settings" SHALL list the setup sections by plain names with their current value in one line (for example "Search: 2 profiles, 7 keywords"), and offer:
- edit one thing, which runs that wizard section with its current answers as defaults
- redo the whole setup, after a backup
- reset one section to its defaults, after confirmation
- change the default run mode for unattended runs

Edits SHALL be written to the configuration with comments preserved, as the wizard does, and SHALL apply to the next run.

#### Scenario: Edit the search filters
- **WHEN** the user chooses "Settings", then "Search", changes the posting age to 14 days and finishes
- **THEN** `config.yaml` has the new value, other keys and comments are unchanged, and the menu returns to the Settings list

#### Scenario: Redo everything
- **WHEN** the user chooses "Redo the whole setup" and confirms
- **THEN** a timestamped backup is written first, then the wizard runs every section

### Requirement: Add a job by URL entry
"Add a job by URL" SHALL ask for a link, add the job like `autoapp add`, and offer to paste the ad text when the page cannot be read.

#### Scenario: Unreadable page
- **WHEN** the user adds a link whose page cannot be fetched
- **THEN** the menu offers to paste the ad text and, if the user pastes it, stores it like `describe`

### Requirement: Finish forms list
"Finish forms" SHALL list the prepared form applications without asking anything first, the ones the app can fill first, each with a short plain reason in brackets of what it needs. Picking a job whose form was found or not yet checked SHALL open it in the assist browser. For any other job the reason and what to do SHALL be shown first, with the choices to open it in the assist browser, open the link in the user's normal browser, mark it submitted, mark it skipped, or go back; jobs on restricted sites or whose page is gone SHALL NOT offer the assist browser. After every job, including a quit, an error or a closed browser, the list SHALL be shown again so the same job can be opened again.

#### Scenario: Back to the list after closing the browser
- **WHEN** the user closes the browser window during a form and presses `q`
- **THEN** no error ends the program and the list of prepared forms is shown again, still containing that job

#### Scenario: A LinkedIn application
- **WHEN** the user picks a prepared job whose application is on linkedin.com
- **THEN** the terminal says the site is never automated and offers the normal browser, submitted, skipped or back, and no assist browser opens
