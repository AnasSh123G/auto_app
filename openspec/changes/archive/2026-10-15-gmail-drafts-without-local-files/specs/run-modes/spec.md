# Spec Delta

## MODIFIED Requirements

### Requirement: Review first mode
In review mode the run SHALL NOT call the SMTP send function for any application, whatever the score, cap or cooldown state. Every prepared email application SHALL be saved as a draft as specified in `email-drafts`, including applications flagged `needs_review`, and no file or folder SHALL be written to the workspace for an email application. A flagged application SHALL be drafted and SHALL stay in the review queue with its reasons, and the feed and summary SHALL tell the user to check it before sending. The run summary SHALL state how many drafts were saved and where to find them.

#### Scenario: Nothing is ever sent
- **WHEN** a review-mode run prepares 4 email applications, one of them with a score of 95 and `email.daily_cap` not reached
- **THEN** 4 drafts are saved, 0 emails are sent, and the SMTP send function is never called

#### Scenario: Flagged letter
- **WHEN** one prepared letter is flagged `needs_review` because a claim could not be verified
- **THEN** it is drafted, and the summary lists it as "check before sending" with the reason

#### Scenario: No local copy
- **WHEN** a review-mode run drafts 3 email applications
- **THEN** the workspace's applications directory has no folder for any of the 3 jobs, and the summary says the drafts are in the mailbox's Drafts folder
