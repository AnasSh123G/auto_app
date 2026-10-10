## Context

The tool is a set of Typer commands. `run` ends with a send step that runs when `email.auto_send` is true. Observed in the code:
- `cli.py` has one callback and no default action, so `autoapp` alone prints help.
- `setup` already has a section registry (`setup/wizard.py`: `run_section`, `reset_section`, `run_onboarding`) and a first-run gate (`setup/gate.py`).
- `steps/pipeline.py` calls `send_applications` only `if ctx.config.email.auto_send or ctx.dry_run`.
- `mail/connection.py` has `SmtpMailer` and `append_sent` (IMAP APPEND with a quoted folder name), and `FakeMailer` for tests. There is no Drafts logic and no folder discovery.
- `Status` has no state between `prepared` and `sent`.
- The user's workspace currently has `email.auto_send: true`, which this change migrates.

This change builds on `add-automated-job-applications` (not archived), so all behaviour here is specified as ADDED requirements in new capabilities.

## Goals / Non-Goals

**Goals**
- One entry point (`autoapp`) that reaches every action.
- A decision at the start of every interactive run: send automatically, or save drafts for review.
- In review mode, a guarantee that nothing is sent by the program.
- Safe unattended behaviour: the default is review.

**Non-Goals**
- Unattended browser use, form submission, or changes to the safeguards of automatic mode.
- A full-screen menu framework, or menu-only features.
- Reading or sorting the user's other mail.

## Decisions

### 1. The menu is a thin loop over existing functions
A `menu` package holds `main_menu(ws, prompter)` and one function per entry. Entries call the same Python functions as the commands (not the CLI through a subprocess), using the `Prompter` abstraction the wizard already has (`QuestionaryPrompter` in a terminal, `ScriptedPrompter` in tests). The menu holds no lock: each entry that changes data opens the same `session()` as its command and releases it on return. Ctrl-C inside an entry is caught at the loop and returns to the menu; Ctrl-C at the menu exits.

`@app.callback(invoke_without_command=True)` opens the menu when `ctx.invoked_subcommand is None`, a TTY is attached and `--non-interactive` is not set; otherwise it prints help as today. The first-run gate runs before the menu.

**Entry badges** (counts on "Finish forms" and "Review queue") come from one cheap query each, taken when the menu is drawn.

**Alternative considered:** `questionary` select lists versus a full-screen `Textual` app. Select lists match the wizard, add no dependency and are testable with scripted answers.

### 2. Run mode is an explicit value passed through the run
`RunMode = Literal["automatic", "review", "test"]` is resolved once at the start of `run`: `--mode` flag, else (interactive) the prompt, else `run.default_mode`. It is stored on `RunContext.mode`, printed in the summary and log, shown in the live view, and written to `runs.summary`. The pipeline's last email step becomes:

```
automatic → send_applications(...)     (unchanged safeguards)
review    → draft_applications(...)    (never touches SMTP)
test      → send_test_applications(...) (same eligibility, delivered to yourself, records nothing)
```

`email.auto_send` is removed from the model. A `before` validator on the config reads a legacy `email.auto_send` and sets `run.default_mode` when it is absent, recording a one-time notice on `ctx` that the CLI prints. The wizard's email section writes `run.default_mode` and never writes `auto_send` again.

**Safety properties, each with a test:**
- In review mode, `SmtpMailer.send` is never called. The test injects a mailer whose `send` raises.
- `--non-interactive` with no configured default is `review`; `run.default_mode` cannot be `test`.
- In test mode every outgoing message has `To` equal to the sending address and an SMTP envelope of only that address, so a bug in header handling cannot reach an employer. The test injects a mailer that fails if any envelope recipient differs.
- Automatic mode in a terminal needs the yes/no confirmation unless `--mode automatic` is given explicitly, which is the scripted opt-in.
- A dry run contacts no mailbox in either mode.

### 3. Test run: the same pipeline, a different last step
`send_test_applications` reuses `check_eligibility(manual=False)` from the send step, so it answers exactly "what would a real run send now". Two differences: the daily cap counts real sends plus the test emails already sent in this run (a local counter), and the duplicate guard looks at real send attempts only. For each eligible job it builds the message with `build_test_copy(email.eml, own_address)` (already implemented for `send --test`): subject `TEST_RUN: <subject>`, body `This email is what would be sent to <address> in a real run.` + blank line + original text, `To` replaced by the sending address, Cc/Bcc/Reply-To removed, a fresh Message-ID and Date, attachment unchanged. It is sent with `mailer.send(msg, to_addrs=[own], keep_copy=False)`. It writes no `send_attempts`, `drafts` or status rows, so jobs stay `prepared`. Evaluations, folders, CSV and statistics are recorded as in any run because they are real work. `autoapp send <id> --test` is the single-job version of the same function, and when no job is prepared it sends one plain test mail so the login can be checked.

The earlier two-email idea (a note plus the exact copy) was dropped: one email with the banner on the first line is what the user asked for, and it needs no envelope trick for the visible `To`.

### 4. Drafts: IMAP APPEND with the `\Draft` flag, one per job
`mail/drafts.py`:
- `find_drafts_folder(imap, configured)`: `LIST` and pick the folder with the `\Drafts` special-use attribute (RFC 6154); otherwise try `Drafts`, then `[Gmail]/Drafts`; `mail.sending.drafts_folder` wins. Gmail localises the folder name (`[Gmail]/Entwürfe`), which is why the attribute is used and a fixed name is not. Folder names are sent modified-UTF-7 encoded and quoted. Proton Bridge exposes `Drafts`.
- `save_draft(imap, folder, msg)`: `APPEND folder (\Draft) <internal date> <bytes>`. The message is the application's `email.eml` with only a fresh `Date`; From, To, Subject, body and attachment are untouched. The message keeps its Message-ID.
- Before appending, `SEARCH HEADER Message-ID` in the folder, so a draft whose confirmation was lost is recorded again instead of duplicated.

A new `drafts` table (`job_id` primary key, `message_id`, `folder`, `state` `saved|replaced`, `created_at`, `replaced_at`) is added by migration 4 (the earlier change's migrations 2 and 3 are the Adzuna usage table and the run events). `draft <id> --again` marks the old row `replaced` and inserts a new draft; the program never deletes the user's old draft from the mailbox.

The draft step selects jobs with status `prepared`, channel `email`, a recipient, and no `needs_review`. It ignores the score, cap, cooldown and free-mail rules (a human reads the draft) but reports them as notes, using the same eligibility function as automatic mode with a `report_only` flag so the reasons stay in one place.

### 5. `drafted` status and sent detection
`Status.DRAFTED` goes after `prepared`. `CSV_STATUSES` and the statistics include it. Transitions into `drafted` come only from `prepared`; `mark` can set any later status.

**Sent detection** cannot rely on the Message-ID: webmail often assigns a new one when a draft is sent. So `check_drafts` searches the Sent folder (`\Sent` special-use attribute, or `mail.sending.sent_folder`, as `append_sent` already uses) for `TO <recipient>` and `SUBJECT <subject>` with `SINCE` the draft's date, and compares the Date header to the draft's `created_at`. A match marks the job `sent` with the note "sent by you from the draft". If the draft is gone from Drafts and nothing matches, the job stays `drafted` with the warning "draft missing". The check is read-only and runs at the start of each non-dry run, from the menu, and with `autoapp drafts --check`.

### 6. Forms are not changed
All modes leave form applications as `prepared` with the folder, prepared answers and `apply.html`. "Finish forms" lists jobs with status `prepared` and a form channel and calls `AssistSession` for the one the user picks, exactly as `assist <id>`. After an interactive run, the same list is shown directly with "Not now" as the default. The list asks nothing before it, explains in brackets what each job needs, opens the app's browser only for jobs it can work on, and comes back after every job (also after a quit or a closed browser). The run itself never starts Playwright.

**Assumption to confirm:** "prefilled and saved locally" is read as the prepared folder plus on-demand live filling, because a saved copy of a live form cannot be submitted (research summary in the earlier change). If a run should open and fill every form unattended, that is a separate change.

### 7. Where the prompts live
`menu/run_prompt.py` holds `choose_run(prompter, config, sent_today) -> RunChoice(mode, profile, dry_run)`. `run` (the command) calls it only when a terminal is attached and no `--mode` is given, so the menu and the command show the same prompt. The live view from the statistics change shows the mode in its header.

## Touches in `add-automated-job-applications`

| Area there | What changes here |
|---|---|
| `email-submission`: automatic sending | Runs only in `automatic` mode; the `email.auto_send` switch is replaced by `run.default_mode` |
| `application-tracking`: status lifecycle and CSV | Adds `drafted` between `prepared` and `sent` |
| `setup-onboarding`: email section | Asks for the default run mode and checks the Drafts folder |
| `automation-pipeline`: `run` | Adds `--mode`, the prompt, and the review-mode draft step |
| `run-dashboard`: "Main menu" | Requirement moved to `main-menu` here; its statistics page is reachable from the menu |
| `application-package` | Unchanged; forms behave the same |

## Risks / Trade-offs

- **[A user picks automatic by habit]** → "Full automatic" is never pre-selected unless the default says so, the default is `review`, and the prompt shows today's sent count and asks for confirmation with "no" as the default.
- **[Migration silently keeps sending]** → A workspace with `auto_send: true` keeps behaving the same unattended, but the one-time notice names the new key, and the Settings entry offers the default mode. The proposal flags this as breaking.
- **[Sent detection misses a draft]** → Subject and recipient matching can fail if the user edits the subject. The job then stays `drafted`, which is safe (it is never wrongly marked `sent`), and the user can `mark` it.
- **[Draft folder differs per provider]** → Special-use discovery plus an explicit setting, and a failure message that names the setting.
- **[Drafts pile up]** → One draft per job, and `drafted` jobs are listed in the Applications entry. The program never deletes drafts.
- **[Review mode skips the free-mail and score rules]** → Intentional, because a human reads each draft, but the notes are shown so the choice is informed.

## Migration Plan

1. Migration 4 adds the `drafts` table (additive; no data change).
2. On load, a legacy `email.auto_send` is mapped to `run.default_mode` in memory. The wizard's email section, or Settings, writes the new key and removes the old one the next time it is saved.
3. Rollback: the migration is additive, and a build without this change ignores the new table and unknown config keys are rejected by the model, so downgrading requires removing `run:` from `config.yaml`.

## Open Questions

- Should the draft carry a visible marker (for example a subject prefix)? Default: no, so that sending it by hand sends the exact prepared email.
- Should "Check drafts" also offer to delete drafts of jobs that were marked `skipped`? Default: no; the program never deletes mail.
