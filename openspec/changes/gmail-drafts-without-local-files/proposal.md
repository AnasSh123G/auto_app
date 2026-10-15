# Proposal

## Why

In review mode the user wants exactly one result for every job that takes applications by email: a finished draft (email text, cover letter and every document attached) in the Drafts folder of their Gmail account `alshoker.anas@gmail.com`, and nothing on disk. The app does the opposite today, for two reasons found in the code and in the run of 2026-10-15 16:55 (`workspace/logs/run-20261015-165503-195243.log`):

1. **The draft step skips every job flagged `needs_review`.** `src/autoapp/steps/drafts.py::_reasons_not_draftable` returns "flagged needs_review; fix the letter, run `autoapp render`, then draft it" for these jobs, so they are never appended to Gmail. The letter checker flags almost every letter (all three email jobs in the database, `<job-a>`, `<job-b>` and `<job-c>`, carry it), so the run ended with `drafts: 0 saved` and the `drafts` table is empty, even though `mail.sending` is set up for Gmail and `AUTOAPP_SMTP_PASSWORD` is in `workspace/.env`.
2. **The prepare step always writes a complete local folder** (`workspace/applications/application_<company>_<title>_<id>/` with `cover_letter.md/.pdf`, copies of the CV and certificates, `Bewerbungsmappe.pdf`, `email.eml`, `apply.html`, `form_answers.md`, `job.md`, `evaluation.md`, `README.md`), and the draft step copies the draft from that folder's `email.eml`. So even a successful draft leaves a local copy of everything behind.

The user has asked for this behaviour several times. This change makes it a hard rule.

## What Changes

- **BREAKING (review mode, email applications):** when the run mode is `review` and the evaluation found an application email address (`channel == "email"` and `apply_email` set), the prepare step SHALL NOT create an application folder or write any file under the workspace. It writes the cover letter and the email text with the LLM, keeps the letter text, subject, body, recipient and a Message-ID in the database row of the `applications` table, and leaves the job `prepared` with no folder.
- The draft step builds the complete email (body, cover letter PDF merged into `Bewerbungsmappe.pdf`, every selected document attached) **in a temporary directory outside the workspace**, appends it to the Gmail Drafts folder over IMAP, and deletes the temporary directory whatever happens. The job becomes `drafted`.
- **`needs_review` no longer stops a draft.** A flagged job is drafted like any other; its review reasons are shown in the run feed, the run summary and `autoapp drafts`, so the user reads them before sending the draft. Nothing about the reasons is put into the email itself.
- If drafting fails (wrong password, no network, Drafts folder not found), nothing is written to disk either; the job stays `prepared` with its letter in the database, and the next run or `autoapp draft <id>` tries again without calling the LLM again.
- **Old folders are cleaned up:** an email job that is still `prepared` and has an old folder with `email.eml` (the three jobs above) is drafted from that `email.eml`; after the draft is confirmed in Gmail, the letter text is kept in the database and the folder is deleted.
- `autoapp draft <id> --again`, `autoapp send <id>`, `autoapp send --test` and the menu's application actions work for email jobs that have no folder, by building the email in a temporary directory from the database.
- `autoapp render <id>` for an email job without a folder stops with a message saying the application is a Gmail draft and how to rebuild it (`autoapp draft <id> --again`).
- Unchanged: automatic and test mode still prepare email applications as folders (`email.eml`) and send them as today; form applications (`employer_form`, `platform`, `unknown`) still get their folders in every mode; nothing is ever sent in review mode.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `email-drafts`: drafts are built in a temporary directory from the stored letter, not copied from `email.eml`; `needs_review` no longer blocks a draft; failures leave nothing on disk; old folders are drafted and then removed; `draft --again` rebuilds from the database.
- `application-package`: in review mode, email applications get no folder and no files. "One folder per application", "Email draft for email applications", "Re-render after manual edits" and "Preparation does not overwrite user work" now make that exception.
- `run-modes`: "Review first mode" drafts every prepared email application, including flagged ones, and shows the review reasons with the draft instead of holding it back.

## Impact

- Code: `src/autoapp/steps/prepare.py`, `src/autoapp/steps/drafts.py`, `src/autoapp/steps/send.py`, `src/autoapp/steps/render.py`, `src/autoapp/steps/pipeline.py`, `src/autoapp/commands.py` (`render`, `drafts`), `src/autoapp/menu/main.py` (application actions, review entry), `src/autoapp/db/schema.py` (migration 10: four new columns on `applications`), `src/autoapp/db/repo.py` (`APPLICATION_FIELDS`), `src/autoapp/events.py` (new feed event `email_prepared`), `src/autoapp/dashboard/view.py` (one row), new module `src/autoapp/package/email_message.py`.
- Tests: `tests/steps/test_modes.py` and `tests/steps/test_prepare_step.py` get new tests; existing tests that check an email job's folder must say which mode they run in (`mode="automatic"`) or be updated.
- Docs: `docs/email.md`, `docs/application-folders.md`, `docs/usage-guide.md`.
- Data: the user's database is migrated by adding columns only; nothing is lost. The three old email folders are deleted only after their draft is in Gmail.
- No new dependencies. Gmail is reached through the existing IMAP code (`src/autoapp/mail/drafts.py::ImapDraftStore`) and the existing `mail.sending` account with the app password in `AUTOAPP_SMTP_PASSWORD`. The Gmail MCP connector is not used.
