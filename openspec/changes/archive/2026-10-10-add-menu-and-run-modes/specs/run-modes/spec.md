# Spec Delta

## Purpose

Makes the user decide, at the moment a run starts, whether the tool may email employers, must leave every email as a draft for the user to read and send by hand, or should behave exactly like a real run with every email coming to the user instead. Form applications are prepared the same way in all modes.

## ADDED Requirements

### Requirement: Run mode prompt
When `run` starts in an interactive terminal without `--mode`, it SHALL ask which mode to use, with these choices and plain descriptions:
1. **Full automatic:** emails that pass all safeguards are sent to employers; form applications are prepared and saved locally
2. **Review first:** no email is sent; every email application is saved as a draft in your mailbox; form applications are prepared and saved locally
3. **Test run:** works exactly like a real run, but every email that would have been sent comes to you instead, marked as a test; public application-page GETs still occur, but no form is filled, uploaded to or submitted

The choice matching the configured default SHALL be pre-selected. After choosing "Full automatic", the prompt SHALL show how many emails were already sent today against the daily cap and ask for a yes/no confirmation with "no" as the default, because real employers will be emailed. Answering no SHALL return to the mode choice. The same prompt SHALL be shown for "Run now" in the main menu, followed by the choice of all profiles or one profile.

#### Scenario: Choose review first
- **WHEN** the user starts a run in a terminal and picks "Review first"
- **THEN** the run starts without a confirmation, and the mode is shown as "review first" in the top of the live view, the summary and the log

#### Scenario: Confirm automatic
- **WHEN** the user picks "Full automatic" with 2 of 5 emails already sent today
- **THEN** the prompt says "2 of 5 emails sent today" and asks "This will email employers. Continue?", and the run starts only after "yes"

#### Scenario: Decline automatic
- **WHEN** the user answers "no" to the confirmation
- **THEN** the mode choice is shown again and nothing has run

### Requirement: Mode flag and unattended default
`run` SHALL accept `--mode automatic|review|test`, which skips the prompt. Without the flag and without a terminal (or with `--non-interactive`), `run` SHALL use `run.default_mode` from the configuration, which SHALL default to `review`. Automatic mode SHALL only run unattended when `run.default_mode` is explicitly `automatic` or `--mode automatic` is given. `run.default_mode` SHALL accept only `automatic` and `review`, so an unattended run is never a test run by accident. An invalid value SHALL exit with code 2 and a message listing the valid values.

#### Scenario: Scheduled run with the default
- **WHEN** the systemd timer runs `autoapp run --non-interactive` and `run.default_mode` is `review`
- **THEN** the run saves drafts and sends no email

#### Scenario: Explicit mode
- **WHEN** the user runs `autoapp run --mode review` in a terminal
- **THEN** no prompt is shown and the run is in review mode

#### Scenario: Invalid value
- **WHEN** the user runs `autoapp run --mode auto`
- **THEN** the command exits with code 2 and says the valid values are `automatic`, `review` and `test`

### Requirement: Full automatic mode
In automatic mode the send step SHALL behave exactly as specified for automatic sending in `add-automated-job-applications`: only eligible email applications are sent, and the minimum score, the free-mail rule, the daily cap, the company cooldown, the recipient-in-ad rule, the `needs_review` block and the duplicate guard all apply. Email applications that are not eligible SHALL be left as `prepared` with the reason shown, and SHALL NOT be saved as drafts in this mode.

#### Scenario: Eligible and ineligible
- **WHEN** an automatic run has 2 eligible email applications and 1 below the minimum score
- **THEN** 2 emails are sent and the third stays `prepared` with "fit score is below the minimum", and no draft is created

### Requirement: Review first mode
In review mode the run SHALL NOT call the SMTP send function for any application, whatever the score, cap or cooldown state. Every prepared email application without a `needs_review` flag SHALL be saved as a draft as specified in `email-drafts`. Jobs with a `needs_review` flag SHALL stay in the review queue with their reasons and SHALL NOT be drafted. The run summary SHALL state how many drafts were saved and where to find them.

#### Scenario: Nothing is ever sent
- **WHEN** a review-mode run prepares 4 email applications, one of them with a score of 95 and `email.daily_cap` not reached
- **THEN** 4 drafts are saved, 0 emails are sent, and the SMTP send function is never called

#### Scenario: Flagged letter
- **WHEN** one prepared letter is flagged `needs_review` because a claim could not be verified
- **THEN** it is not drafted, and the summary lists it under the review queue with the reason

### Requirement: Test run mode
In test mode the run SHALL execute every step as in automatic mode (search, fetch, filter, evaluate, prepare) and SHALL apply the same eligibility rules as a real automatic run to decide which emails would be sent: minimum score, free-mail rule, `needs_review`, company cooldown, the daily cap counted from real sends plus the test emails already sent in this run, and the duplicate guard. For each email that a real run would send, the system SHALL send one email to the sending mailbox's own address instead, and SHALL NOT contact any employer address. The test email SHALL be the prepared application email with these differences only:
- the subject is `TEST_RUN: ` followed by the normal subject
- the first line of the text is `This email is what would be sent to <actual application email> in a real run.`, followed by an empty line and the normal text
- the recipient is the user's own address, and Cc, Bcc and Reply-To are removed

The letter text and the attachment SHALL be unchanged. A test run SHALL NOT create send attempts, drafts or `sent` statuses, SHALL NOT use up the real daily cap, and SHALL leave each job `prepared`, so that a later real run can still send it. Jobs that a real run would not send SHALL be listed with the reason, and no test email SHALL be sent for them. Everything else a run records (evaluations, application folders, the CSV, statistics) SHALL be recorded as in a real run, and the mode SHALL be shown as "test run" everywhere the mode is shown.

#### Scenario: Test run sends to me only
- **WHEN** a test run prepares 3 email applications that a real run would send to `a@x.de`, `b@y.de` and `c@z.de`
- **THEN** 3 emails arrive in the user's mailbox, each with the subject `TEST_RUN: <normal subject>` and the first line naming its real recipient, and no email goes to those 3 addresses

#### Scenario: Same decisions as a real run
- **WHEN** one of those applications scores below `email.min_score`
- **THEN** no test email is sent for it, and the summary lists it as "would not be sent: fit score is below the minimum"

#### Scenario: Nothing is recorded as sent
- **WHEN** the test run finishes
- **THEN** all jobs are still `prepared`, the send attempts table has no new row, and the daily cap shown by the statistics is unchanged

#### Scenario: Cap is simulated
- **WHEN** `email.daily_cap` is 2 and 3 applications are eligible
- **THEN** 2 test emails are sent and the third is listed as "would not be sent: daily cap of 2 reached"

#### Scenario: Single-job test
- **WHEN** the user runs `autoapp send <id> --test`
- **THEN** one test email in the same format is sent for that job without running the other steps

#### Scenario: Banner in the text
- **WHEN** the user opens a test email for an application addressed to `bewerbung@employer-four.example`
- **THEN** its first line reads "This email is what would be sent to bewerbung@employer-four.example in a real run." and the letter follows

### Requirement: Forms are prepared the same way in every mode
In every mode, online-form applications SHALL receive the application package plus bounded GET-only finding under `application-form-finding`, including test mode and run dry-run. The run SHALL NOT launch a browser, fill, upload or submit. After an interactive run that prepared form applications, the program SHALL show the "Finish forms" list directly, with "Not now" as the default, and SHALL ask nothing before it. Only `forms --dry-run` SHALL be strictly read-only with no finding requests.

#### Scenario: Form application in review mode
- **WHEN** a review-mode run prepares a job whose ad links to an employer form
- **THEN** its folder contains the prepared answers and `apply.html`, the status is `prepared`, no browser was opened, and the summary lists it under "forms to finish"

#### Scenario: Offer to finish forms
- **WHEN** an interactive run ends with 2 prepared form applications
- **THEN** the program shows the list of prepared form applications, and pressing Enter ("Not now") opens no browser

### Requirement: Dry run in every mode
With `--dry-run` or the Tests entry's dry run, no mode SHALL contact the mailbox for sending or drafting. The feed SHALL show "would send" in automatic mode, "would save draft" in review mode and "would send a test email" in test mode, and the prompt SHALL still be shown in a terminal unless the dry run was started from the Tests entry.

#### Scenario: Dry run in review mode
- **WHEN** the user runs `autoapp run --mode review --dry-run`
- **THEN** no draft is appended to any mailbox, and the feed shows "would save draft" lines

### Requirement: Replacement of the auto-send switch
The configuration SHALL have `run.default_mode` (`automatic` or `review`, default `review`), and the `email.auto_send` switch SHALL no longer be asked or written. When a configuration contains `email.auto_send` and no `run.default_mode`, loading SHALL map `true` to `automatic` and `false` to `review` and print a one-line notice with the new key, once per run. When both are present, `run.default_mode` SHALL win. The email section of the wizard SHALL ask for the default mode, explain both modes in plain words, and recommend `review`.

#### Scenario: Old configuration
- **WHEN** a workspace has `email.auto_send: true` and no `run.default_mode`
- **THEN** an unattended run is in automatic mode and prints "email.auto_send is replaced by run.default_mode; using automatic"

#### Scenario: Wizard question
- **WHEN** the user runs the email section of the setup
- **THEN** it asks for the default run mode, `review` is pre-selected, and the answer is written to `run.default_mode`
