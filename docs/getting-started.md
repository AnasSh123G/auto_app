## Getting started

autoapp keeps everything personal in one **workspace** folder and sets it up for you with a guided wizard.
You never have to edit configuration files by hand, although you can.

### 1. Install

```bash
uv sync
```

Run the tool with `uv run autoapp ...`, or activate the virtual environment and run `autoapp ...`.

### 2. Run the wizard

```bash
uv run autoapp setup
```

The workspace is `./workspace` by default. Choose another folder with `--workspace PATH` (or `-w`) or the
`AUTOAPP_WORKSPACE` environment variable. The wizard creates the folder and prints the path of its
`documents/` subfolder; copy your CV (PDF or DOCX), certificates and photo there first or when asked.

Any other command (for example `autoapp run`) starts the same wizard automatically the first time, when you
run it in a terminal. Without a terminal (a timer, a script, `AUTOAPP_NON_INTERACTIVE=1`), such a command
never prompts: it exits with code 2 and the message "Setup is not complete. Run `autoapp setup` first."
`--help`, `setup` and the read-only `status`, `list` and `mark` commands work without a finished setup.

The wizard has eight sections. Every question shows the current value as its default, so pressing Enter keeps it.
Each answer is checked as you type it; a wrong one is explained and asked again.

| Section | What it asks | Stored in |
|---|---|---|
| `documents` | Which file in `documents/` is your CV, certificates, Arbeitszeugnis (`work_reference`), other documents, photo or a writing sample (a proposal is made from the file names), and in which language. A document marked `any` language is sent whatever the language of the ad; one marked German or English only with a letter in that language | `profile.yaml` |
| `profile` | Name, address, email, phone, availability, language levels, extra skills | `profile.yaml` |
| `salary` | Salary expectations: a target, an optional minimum, amounts per seniority and per federal state (for example 52000, but 56000 for jobs in Bayern). The amount is worked out by the program from the job's seniority and location, so the letter and the form always use the same figure. Optional: skip it and nothing is stored | `profile.yaml` |
| `search` | One or more search profiles. Only the keywords are required; every filter (locations and radius, work mode, exclusions, ...) can be left empty | `config.yaml` |
| `sources` | Adzuna keys (optional), optional Arbeitnow (no key), the alert mailbox/folder, detail fetching and optional employer-site discovery | `config.yaml`, `.env` |
| `llm` | A provider and model for each of the four tasks (evaluate, write, form fill, find form), each pre-filled from the previous answer or your saved config, and the API keys, with a table of the estimated cost per job | `config.yaml`, `.env` |
| `email` | The sending mailbox (it offers to reuse the alert mailbox) and the safeguards. The default run mode for scheduled runs is review (drafts only); automatic sending happens only if you choose it | `config.yaml`, `.env` |
| `writing` | Your writing instructions, typed in or edited in `$EDITOR`, and which documents are writing samples | `writing_instructions.md`, `config.yaml`, `profile.yaml` |

Mailbox notes:

- **Gmail**: turn on 2-Step Verification and create an app password at <https://myaccount.google.com/apppasswords>;
  your normal password does not work. The wizard explains the steps.
- **Proton Mail**: needs a paid plan and Proton Mail Bridge running on the same computer. The wizard shows
  the Bridge certificate fingerprint and stores it only after you confirm it.
- **Outlook.com, Hotmail, Live** addresses are not supported yet.

Passwords and API keys are typed hidden and saved only in `.env` (never in `config.yaml` or `profile.yaml`,
and never printed back).

At the end of `llm`, `sources` and `email` the wizard offers to test your settings: one tiny LLM request, the
IMAP login and alert folder, the SMTP login (no email is sent) and whether the Arbeitsagentur API answers.
If a test fails you can re-enter the credentials or continue anyway.

**Try a dry run before you enable automatic sending**: `autoapp run --dry-run`, then read the prepared
applications. A run in review mode (`autoapp run --mode review`) saves drafts instead of sending, and a test run
(`--mode test`) sends every email to you. The `email` section explains the safeguards (daily cap, minimum score, free-mail rule,
company cooldown) before it lets you switch automatic sending on.

### 3. Use the main menu

After setup, start with:

```bash
uv run autoapp
```

In a terminal this opens the main menu: Run now, Statistics, Applications, Review queue, Finish forms, Add a job by URL, Tests, Settings and Quit. Every entry calls the same code as the matching command, so you can use either. Without a terminal, or with `--non-interactive`, `autoapp` prints help instead.

Every interactive run asks how to handle emails: full automatic, review first (drafts in your mailbox) or test run (every email comes to you). Review first is the safe default. See [Email](email.md).

### 4. Change something later

```bash
uv run autoapp setup                       # menu: redo everything, redo a section, reset a section, cancel
uv run autoapp setup --section llm         # redo one section; all other sections stay as they are
uv run autoapp setup --reset email         # return one section to its defaults (asks first)
uv run autoapp setup --reset all           # back up your files, then start over (asks first)
```

A section only writes its own keys, and it keeps comments and anything you edited by hand. For example, a
search profile you added to `config.yaml` yourself survives `setup --section llm`. A section reset keeps the
secrets in `.env` and puts a copy of your files in `backups/`.

`--reset all` copies `config.yaml`, `profile.yaml`, `writing_instructions.md` and `.env` to
`backups/<timestamp>/` and starts onboarding again from the first section. It never touches `documents/`,
the database, `applications/` or `applications.csv`.

If you stop the wizard half way (Ctrl-C), finished sections are kept; the next `autoapp setup` continues
with the remaining ones.

### 5. Where your files live

```
workspace/                  # --workspace, then $AUTOAPP_WORKSPACE, then ./workspace (keep it out of git)
  config.yaml               # search profiles, sources, llm, mail, limits; setup progress
  profile.yaml              # personal data, document roles, salary, extra skills
  writing_instructions.md   # your instructions for the cover-letter writer
  .env                      # API keys and mail passwords (file mode 600)
  documents/                # your CV, certificates, photo (only read, never changed)
  applications/             # one folder per prepared application
  applications.csv          # overview you can open in a spreadsheet
  autoapp.db                # the database
  backups/<timestamp>/      # copies made by `setup --reset`
  logs/
```

Example files with every option explained are in `examples/` (`config.example.yaml`, `profile.example.yaml`,
`.env.example`, `writing_instructions.example.md`).
