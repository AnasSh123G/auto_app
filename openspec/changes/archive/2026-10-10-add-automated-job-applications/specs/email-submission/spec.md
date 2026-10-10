# Spec Delta

## Purpose

Fully automatic submission for the case where it is legitimate: job ads that ask for applications by email. The email is sent from the user's own mailbox, with safeguards against sending to the wrong place, sending twice or sending too much.

## ADDED Requirements

### Requirement: Opt-in automatic sending
Automatic sending SHALL be off by default and SHALL only happen when `email.auto_send` is `true` in the configuration. With it off, email applications are prepared but not sent.

#### Scenario: Default configuration
- **WHEN** a prepared job has channel `email` and the configuration does not enable `email.auto_send`
- **THEN** no email is sent, and the job stays `prepared` with `email.eml` available

### Requirement: Auto-send eligibility
A prepared application SHALL be sent automatically only if all of these hold:
- the decision is `apply` and the channel is `email`
- the address is grounded in the ad
- the recipient domain is not on the free-mail list
- the fit score is at or above `email.min_score`
- every requested document the user has is attached
- the bundle is within the size limit
- the application is not flagged `needs_review`

#### Scenario: Application flagged for review
- **WHEN** an otherwise eligible application is flagged `needs_review` because of an unsupported claim
- **THEN** it is not sent automatically, and it appears in the review queue of `status`

#### Scenario: Free-mail recipient
- **WHEN** the extracted address is `jobs.muster@gmail.com` and `gmail.com` is on the free-mail list
- **THEN** the application is not sent automatically, and it is flagged for manual review with the reason

#### Scenario: Score below auto-send minimum
- **WHEN** `email.min_score` is 85 and the job scored 80
- **THEN** the application is not sent automatically, and it remains `prepared`

### Requirement: Volume and duplicate limits
The system SHALL NOT send more than `email.daily_cap` applications per calendar day (Europe/Berlin), SHALL NOT send twice for the same job, and SHALL NOT email the same company again within `email.company_cooldown_days`.

#### Scenario: Daily cap reached
- **WHEN** `email.daily_cap` is 5 and 5 applications were already sent today
- **THEN** further eligible applications stay `prepared` and are sent on a later day

#### Scenario: Same company cooldown
- **WHEN** an application to "Muster GmbH" was sent 3 days ago and the cooldown is 14 days
- **THEN** a new eligible application to "Muster GmbH" is not sent automatically, and it is flagged for review

### Requirement: Supported mail connections
Mail SHALL be sent and read through standard IMAP/SMTP logins with an app-specific or bridge password over TLS or STARTTLS. Presets SHALL exist for Gmail (app password) and Proton Mail Bridge (local IMAP/SMTP). Any other server can be configured manually. Credentials SHALL come from environment variables only. Outlook.com personal accounts are not supported in this change.

#### Scenario: Gmail preset
- **WHEN** the user picks the Gmail preset and enters an app password
- **THEN** mail is read from `imap.gmail.com` and sent through `smtp.gmail.com` over TLS with that app password

#### Scenario: Proton Bridge preset
- **WHEN** the user picks the Proton Bridge preset
- **THEN** mail is read and sent through Bridge's local IMAP and SMTP ports on `127.0.0.1`, using the Bridge password and trusting only the Bridge certificate recorded during setup

#### Scenario: Outlook.com chosen
- **WHEN** the user tries to configure an `outlook.com` or `hotmail.com` address
- **THEN** setup explains that Microsoft no longer allows password logins for third-party apps and that Outlook.com is not supported yet

### Requirement: Separate alert and sending mailboxes
The mailbox that receives job alerts and the mailbox that sends applications SHALL be configurable independently. They MAY be the same account.

#### Scenario: Different accounts
- **WHEN** alerts arrive at a Proton address through Bridge and applications are configured to go out from Gmail
- **THEN** alerts are read from the Proton folder, and applications are sent from the Gmail address

### Requirement: Sending through the user's mailbox
Emails SHALL be sent from the sending mailbox's address. The sent copy SHALL appear exactly once in that mailbox's Sent folder. The system SHALL IMAP-append a copy only when the provider does not save SMTP-sent mail itself (Gmail and Proton Bridge do).

#### Scenario: Successful send
- **WHEN** an eligible application is sent
- **THEN** the email with `Bewerbungsmappe.pdf` attached is delivered via SMTP, exactly one copy appears in the Sent folder, and the tracker records status `sent` with the timestamp and Message-ID

### Requirement: Idempotent send recording
Before sending, the system SHALL record a send attempt with a pre-generated Message-ID. If a previous attempt has no confirmed result, the system SHALL NOT resend automatically and SHALL flag the job for manual verification.

#### Scenario: Crash during send
- **WHEN** a run was interrupted after a send attempt was recorded but before success was confirmed
- **THEN** the next run does not resend, and it lists the job as `send_unconfirmed` for the user to check

### Requirement: Send failure handling
If SMTP sending fails, the job SHALL stay `prepared`, the error SHALL be recorded, and sending SHALL be retried in a later run up to a configured number of attempts.

#### Scenario: SMTP authentication failure
- **WHEN** the SMTP server rejects the credentials
- **THEN** no emails are sent in this run, each eligible job keeps status `prepared`, and the summary reports the authentication error

### Requirement: Manual send command
The user SHALL be able to send a prepared email application with `send <job-id>`, regardless of `email.auto_send`. The command SHALL show recipient, subject and attachments and ask for confirmation, unless `--yes` is given.

#### Scenario: Confirmed manual send
- **WHEN** the user runs `send <job-id>` and confirms
- **THEN** the email is sent under the same duplicate rules, and the job becomes `sent`

### Requirement: Dry run
With `--dry-run`, the system SHALL perform every eligibility check and report which emails would be sent, but SHALL NOT contact the SMTP server or change the mailbox.

#### Scenario: Dry run report
- **WHEN** the user runs `run --dry-run` with 2 eligible email applications
- **THEN** the summary lists both as "would send" and their statuses stay `prepared`
