# Spec Delta

## MODIFIED Requirements

### Requirement: Tests entry
"Tests" SHALL offer these actions, each showing a clear pass or fail line with the reason on failure:
- a quick test (`autoapp test`): a short check of every step, using one or two alert emails, a couple of jobs per source, a couple of employer lookups, and one email and one form application prepared in a test workspace, ending with a checklist; this SHALL be the pre-selected action
- a test run: a full run in test mode, where every email a real run would send comes to me instead, marked `TEST_RUN:`
- a fresh test run: a test run in a new test workspace where every job counts as new and the real history is not touched
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

#### Scenario: Quick test from the menu
- **WHEN** the user opens "Tests" and presses Enter
- **THEN** the quick test runs without the mode prompt and ends with its OK/FAILED/SKIPPED checklist
