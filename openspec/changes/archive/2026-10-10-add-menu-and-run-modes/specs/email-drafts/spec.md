# Spec Delta

## Purpose

Saves prepared application emails as drafts in the user's own mailbox, so that the user can read each one in their normal mail program and send it by hand.

## ADDED Requirements

### Requirement: Save a prepared email as a draft
The system SHALL save a prepared email application as a draft by adding it over IMAP to the Drafts folder of the sending mailbox, flagged as a draft. The draft SHALL contain the recipient address from the ad, the subject, the email text and the `Bewerbungsmappe.pdf` attachment exactly as in the application folder's `email.eml`, so that sending it by hand sends what was prepared. The system SHALL NOT send the email.

#### Scenario: Draft content
- **WHEN** the draft for a prepared job is saved
- **THEN** opening it in the mail program shows the same To address, subject, text and attachment as `email.eml`, and the mailbox's Sent folder is unchanged

#### Scenario: Recipient address
- **WHEN** the prepared email is addressed to `bewerbung@genese.de`
- **THEN** the draft's To field is `bewerbung@genese.de`, and the address was found verbatim in the ad

### Requirement: Finding the Drafts folder
The Drafts folder SHALL be found with the server's special-use attribute for drafts. If that is not offered, the system SHALL try `Drafts` and `[Gmail]/Drafts`. The user SHALL be able to name the folder with `mail.sending.drafts_folder`, which SHALL take priority. If no folder can be found, drafting SHALL fail with a message that names the setting to fill in, and no email SHALL be sent as a fallback.

#### Scenario: Gmail in German
- **WHEN** the Gmail account's interface language is German and its drafts folder is named `[Gmail]/Entwürfe`
- **THEN** the draft is saved to that folder because the special-use attribute identifies it

#### Scenario: Folder not found
- **WHEN** the server offers no drafts attribute and neither fallback name exists
- **THEN** the draft is not saved, the message says to set `mail.sending.drafts_folder`, and nothing is sent

### Requirement: One draft per job
Each job SHALL have at most one saved draft. A repeated run, `draft <id>` or a menu action SHALL NOT create a second draft for a job that already has one, and SHALL say that a draft exists. `draft <id> --again` SHALL save a new draft, for example after the user deleted the old one or edited the letter and re-rendered it, and SHALL record that the earlier draft was replaced.

#### Scenario: Second run
- **WHEN** a review-mode run finds a job that already has a draft
- **THEN** no new draft is saved, and the job is listed as "draft already saved"

#### Scenario: Edited letter
- **WHEN** the user edits `cover_letter.md`, runs `render`, and then `draft <id> --again`
- **THEN** a new draft with the new attachment is saved and the database records that it replaced the earlier one

### Requirement: Which jobs are drafted
In review mode and for `draft <id>`, the system SHALL draft every job that is `prepared`, uses the email channel, has a recipient found in the ad and has no `needs_review` flag. The minimum score, the daily cap, the company cooldown and the free-mail rule SHALL NOT prevent a draft, because the user reads every draft. When one of them would have blocked an automatic send, the feed, the summary and the application's README SHALL say so, for example "sent to this company 3 days ago" or "recipient is a free-mail address".

#### Scenario: Free-mail recipient
- **WHEN** a prepared email is addressed to a free-mail address
- **THEN** it is drafted, and the feed line says the recipient is a free-mail address

#### Scenario: Below the automatic threshold
- **WHEN** a job scored 82 with a minimum score of 85
- **THEN** it is drafted with the note "below the automatic-send score", and would not have been sent in automatic mode

### Requirement: Drafted status
The status lifecycle SHALL include `drafted`, set when a draft is saved and placed between `prepared` and `sent`. A `drafted` job SHALL appear in `applications.csv`, in the statistics ("drafts saved") and in the Applications list. The user SHALL be able to set `sent` or `submitted` by hand with `mark`. Sending a job from the app (`send <id>`) while it is `drafted` SHALL warn that a draft exists and ask for confirmation.

#### Scenario: Status after drafting
- **WHEN** a draft is saved for a prepared job
- **THEN** its status is `drafted`, the history records the change, and the CSV row shows `drafted`

### Requirement: Detect drafts that were sent
"Check drafts" (menu entry, `autoapp drafts --check`, and the start of each run) SHALL look in the Sent folder for an email to the same recipient with the same subject that arrived after the draft was saved, and mark the job `sent` with a note "sent by you from the draft". A draft that is no longer in the Drafts folder and has no match in Sent SHALL be reported as "draft missing", and the status SHALL stay `drafted`. Checking SHALL be read-only and SHALL be skipped in dry runs.

#### Scenario: User sent the draft
- **WHEN** the user sends a draft by hand and the next check finds the email in Sent
- **THEN** the job becomes `sent`, and the statistics count it as sent by hand

#### Scenario: Draft deleted
- **WHEN** the draft was deleted and nothing matches in Sent
- **THEN** the job stays `drafted` and the check reports "draft missing" with the hint `draft <id> --again`

### Requirement: Draft failures
A failure while saving drafts SHALL leave the affected job `prepared`, record the reason, and let the next run try again. A login failure SHALL stop the drafting for the run and be reported as an error with a hint to run the email section of the setup. Saving SHALL be safe to repeat: a draft that was appended but whose confirmation was lost SHALL be found again by its Message-ID before another is created.

#### Scenario: Wrong password
- **WHEN** the IMAP login for the sending mailbox is rejected
- **THEN** no draft is saved, all jobs stay `prepared`, the run reports one error, and the other steps still complete

#### Scenario: Lost confirmation
- **WHEN** a draft was appended but the connection dropped before the program recorded it
- **THEN** the next attempt finds the draft by its Message-ID in the Drafts folder, records it, and does not create a duplicate
