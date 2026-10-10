# Proposal

## Why

Today every action is a separate command that you have to remember (`run`, `setup --section email`, `send --test`, `assist <id>`), and a run has one hidden switch for emails, `email.auto_send`, buried in the config. A run can therefore send real emails to employers without you having decided that at the moment you started it. The tool should be started once, show what you can do, and ask the one question that matters at the start of a run: send applications automatically, or save the emails as drafts so you can read them first.

This change builds on `add-automated-job-applications`, whose commands and safeguards it reuses.

## What Changes

- **Main menu.** `autoapp` with no arguments in a terminal opens a menu that you stay in until you choose Quit. Entries:
  - **Run now**
  - **Statistics**
  - **Applications** (browse, open, change status, send or draft one)
  - **Review queue**
  - **Finish forms** (fill prepared form applications in the browser, one at a time)
  - **Add a job by URL**
  - **Tests** (test run, test email for one application, check logins and the AI model, dry run)
  - **Settings** (edit one thing, redo the whole setup, reset a section, change the run mode default)
  - **Quit**

  Without a terminal, `autoapp` prints the help, as before. Every entry calls the same code as its command, so the menu adds no behaviour of its own.
- **Run mode prompt.** When you choose "Run now" (or run `autoapp run` in a terminal), you are asked to pick one of two modes, or a third for testing:
  1. **Full automatic:** emails are sent to employers (with all existing safeguards), and form applications are prepared and saved locally.
  2. **Review first:** no email is sent. Each email application is saved as a **draft in your own mailbox**, with recipient, subject, letter text and attachment, so you can read it and send it by hand. Form applications are prepared exactly as in the automatic mode.

  3. **Test run:** works exactly like a real run, but every email a real run would send comes to you instead, as one email each, with `TEST_RUN: ` in front of the normal subject and the first line "This email is what would be sent to <address> in a real run." Nothing reaches an employer and nothing is recorded as sent.

  Automatic mode asks for a yes/no confirmation because it emails real employers. `--mode automatic|review|test` skips the prompt, and unattended runs use a configured default (`review` unless you change it).
- **Email drafts.** New `autoapp draft <id>` command and the review-mode step save the prepared email to the Drafts folder of the sending mailbox over IMAP. The folder is found automatically (it has a different name in every language and provider), each job gets one draft, and a new status `drafted` marks it. A "Check drafts" action marks a draft as `sent` once the same email shows up in your Sent folder.
- **BREAKING (config):** `email.auto_send` is replaced by the run mode. An existing `auto_send: true` becomes `run.default_mode: automatic` and `false` becomes `review`, with a message the first time it is read. Scheduled runs keep working with the migrated value.
- **Forms stay as they are.** Both modes prepare form applications the same way (application folder, prefilled answers, `apply.html`, `assist` on request). A run never opens a browser by itself. After an interactive run, the menu offers to start "Finish forms".

## Capabilities

### New Capabilities
- `main-menu`: the menu shown by `autoapp` in a terminal, its entries and sub-menus (Applications, Tests, Settings), looping until Quit, and the rule that each entry reuses its command's code.
- `run-modes`: the run mode prompt and flag, full automatic, review-first and test-run behaviour (including the format of test emails), the default for unattended runs, the combination with dry run, the migration of `email.auto_send`, and how form applications behave in both modes.
- `email-drafts`: saving prepared emails as drafts in the user's mailbox, finding the Drafts folder, one draft per job, the `drafted` status, checking Sent to mark drafts as sent, and failure handling.

### Modified Capabilities
None. The behaviour this change touches is specified by `add-automated-job-applications`, which is not archived yet, so there is no base spec to modify. The `design.md` "Touches" section lists exactly which of its requirements this change extends, and the "Main menu" requirement that change's `run-dashboard` spec originally contained has been moved here.

## Impact

- **Code:** a new `menu` package (menu, run prompt, sub-menus); `run` gets `--mode`; a new `drafts` step and `mail/drafts.py` (IMAP folder discovery and APPEND); `Status.DRAFTED`; config `run.default_mode` and `mail.sending.drafts_folder`; wizard `email` section asks for the default mode and checks the Drafts folder; one database migration (`drafts` table).
- **Dependencies:** none new (`imaplib`, `questionary` and `rich` are already used).
- **Config migration:** `email.auto_send` is read once and mapped to `run.default_mode`.
- **Existing change:** the "Main menu" requirement is removed from its `run-dashboard` spec, and its task 14.8 now only exposes the statistics page to the menu.
- **Privacy and safety:** drafts are written only to your own mailbox. In review mode the SMTP send function is never called, which is covered by a test. Credentials are the ones already stored for the sending account.
- **Out of scope:** opening browsers unattended during a run, submitting forms, changing the safeguards of automatic mode, and Outlook.com support.
