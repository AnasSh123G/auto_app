# autoapp

A local command-line tool that finds job ads in Germany, checks each one against your CV, writes a tailored cover letter, builds a complete application folder, saves email applications as drafts in your own mailbox or sends them from it (your choice) and fills web application forms for you to submit. Everything it does is tracked in `applications.csv`.

It never applies on LinkedIn, Indeed or StepStone: it reads the job-alert emails those sites send you and, by default, finds each job's own ad on the employer's website with a web search instead of opening the job board's page (`sources.alerts.mode`, `search_first` or `platform`), uses the Arbeitsagentur, Adzuna and Workwise searches, and leaves every web form's Submit button to you. Only if you switch it on per site (`sources.apply_links_with_login`, off by default) does it open a job page there with your login, to read the company's apply address.

## Quick start

```bash
uv sync
uv run playwright install chromium
uv run autoapp setup        # onboarding wizard: documents, profile, search, mail, AI model, writing
uv run autoapp              # the main menu: run, statistics, applications, settings
uv run autoapp run --dry-run
uv run autoapp status
```

Your personal data lives in `./workspace/` (git-ignored): `config.yaml`, `profile.yaml`, `.env`, `documents/`, `applications/`, `applications.csv`.

## Keys you need

Keys and passwords go in `workspace/.env` (the setup wizard writes them there):

| Variable | Needed? | What it unlocks |
|---|---|---|
| `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` | **Yes**, the key of the one AI provider you pick | Every AI step |
| `AUTOAPP_SMTP_PASSWORD` | **Yes** for email applications | Drafts, sending and test emails (Gmail: an app password) |
| `AUTOAPP_ALERTS_PASSWORD` | Only if you read job-alert emails | Reading the alert mailbox |
| `PARALLEL_API_KEY` | Optional, recommended with alert emails | Finding the employer's own ad and careers site |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Optional, both together | The Adzuna job search |

Where to get each key: [Keys you need](docs/usage-guide.md#keys-you-need) in the usage guide.

## Commands

Run each command from the project folder as `uv run autoapp <command>`, for example
`uv run autoapp run --mode test --fresh`.

| Command | What it does |
|---|---|
| `setup [--section NAME] [--reset NAME\|all]` | Onboarding wizard; redo or reset one section or everything |
| (no command) | The main menu, in a terminal. Without a terminal, or with `--non-interactive`, it prints help |
| `run [--sources email-only\|full\|web-only] [--mode automatic\|review\|test] [--fresh] [--redo] [--dry-run] [--non-interactive] [--no-dashboard] [--profile NAME]` | Search → fetch details → evaluate → prepare → send, draft or test-send → export. Interactive runs ask for job sources and delivery mode unless supplied; preselected delivery modes and scheduled runs default to Full sources. `--fresh` (test runs only) starts from an empty job history in `workspace/test-runs/`. `--redo` reprocesses unsent jobs within the selected sources, in your real workspace |
| `stats [--period run\|today\|7d\|30d\|all]` | Statistics; live while a run is active |
| `draft <id> [--again]`, `drafts [--check]` | Save one email application as a draft in your mailbox; list drafts, or mark the ones you sent by hand as `sent` |
| `reset-data [--answers] [--logins] [--yes]` | Delete all job data (history, folders, CSV, logs, test runs) and keep the setup; shows what goes and asks first |
| `login <linkedin\|stepstone\|indeed\|workwise>` | Log in once in the app's browser (kept for later): Workwise applications, and company apply links with your login (`sources.apply_links_with_login`, off by default) |
| `test --quick` / `test --full` `[--profile NAME] [--no-dashboard]` | `--quick`: a small sample of every step (a few alert emails, jobs, employer lookups, one email and one form application) in a throwaway test workspace, then an OK/FAILED/SKIPPED checklist. Sends at most one `TEST_RUN:` email, to you; your real history is not touched; exit code 1 on a failure. `--full`: a whole test run in a fresh test workspace (same as `run --mode test --fresh`). Without either flag nothing starts and the exit code is 2 |
| `send <id> [--test]` | Send one prepared email now; with `--test`, send the email to yourself instead |
| `search`, `evaluate`, `prepare` | Individual steps |
| `add <url>`, `describe <id>` | Add a job by link; paste an ad whose page could not be read |
| `render <id>` | Rebuild PDFs and the email after editing `cover_letter.md` |
| `assist <id>` | Open the application form and fill it on request; you submit |
| `forms [--job <id>] [--again] [--dry-run] [--audit [--json]]` | Find and refresh prepared application links; dry run only lists candidates; `--audit` is a read-only report of the saved links (status, job boards, sources, causes) |
| `employers [--discover] [--job <id>] [--profile NAME] [--again] [--refresh-site] [--website URL] [--dry-run]` | Show saved employer careers sites; with `--discover`, look up and scan the employers of your alert jobs (opt-in). `--dry-run` makes no request and writes nothing |
| `status`, `list --status S`, `mark <id> STATUS [--note]` | Track applications (`mark <id> apply` promotes a review job) |
| `export` | Write `applications.csv` (also done after every command) |

## Main menu and run modes

`autoapp` with no arguments opens a menu in a terminal. Without a terminal, or with `--non-interactive`, it prints help.

| Entry | What it does |
|---|---|
| Run now | Choose Email only / Full / Web only, then how to send or draft applications |
| Statistics | Opens the statistics screen (see below) |
| Applications | Pick an application by status. Show its folder or letter, change its status, re-render the letter, save it as a draft, send it (asks first), send a test copy to yourself, or fill its form |
| Review queue | Jobs that need your decision or a fixed letter. Show the evaluation, then apply, skip, show the letter path or re-render |
| Finish forms | Fill prepared form applications in your browser, one at a time; says what each one needs and comes back to the list after each |
| Employer sites | Show saved employer careers sites, preview or run a lookup, scan saved sites again, confirm the right website for one job |
| Redo unsent | Process every job that is not sent again, as if it were new (asks first, backs the database up) |
| Add a job by URL | Add one ad by its link; paste the text if the page cannot be read |
| Tests | Quick test (the default), test run, full test run (fresh), test email for one application, check logins (mailboxes, Drafts folder, AI model, Adzuna), dry run |
| Settings | Edit one setup section, set the default run mode for scheduled runs, redo the whole setup (a backup is made first), reset one section |
| Quit | Leave the menu |

Every interactive `autoapp run` asks how to handle emails. `--mode` skips the question.

| Mode | What happens to emails |
|---|---|
| Full automatic (`automatic`) | Emails that pass the safeguards are sent to employers. It asks for confirmation and shows emails sent today against the daily cap |
| Review first (`review`) | Every email is saved as a draft in the Drafts folder of your mailbox. Nothing is sent. You read the draft and send it yourself |
| Test run (`test`) | Exactly like a real run, but every email a real run would send goes to your own sending address, with the subject `TEST_RUN: <normal subject>`. Nothing is recorded |

Form applications are prepared the same way in every mode, including bounded GET requests to find the actual application page. Unresolved employer/ATS forms use a bounded, headless browser fallback with the configured `form_find` model. It never fills, uploads, creates accounts or submits; after an interactive run it offers to fill the prepared forms (default no). Test mode redirects email to you, but still requests public application pages. Scheduled runs use `run.default_mode` (default `review`, never `test`). See [Email](docs/email.md) for drafts, test runs and safeguards.

`email.auto_send` no longer exists. An old config is migrated on load: `true` becomes `run.default_mode: automatic`, `false` becomes `review`.

## Supported alert emails

autoapp reads the job-alert emails that arrive in your mailbox. Set the alert mailbox and folder in `autoapp setup`
(section `sources`); nothing else is needed from you, and you do not have to provide sample emails.

| Site | Recognised by the sender address |
|---|---|
| LinkedIn | `jobalerts-noreply@linkedin.com` |
| Indeed | `indeed.com` senders, including `match.indeed.com` |
| StepStone | `@jobagent.stepstone.de` |
| Workwise | `@workwise.io` (and the former `@campusjaeger.de`) |
| XING | XING sender domains |

The parsers go by the sender, not by who the email is addressed to, so they work the same for every user. They were
written against these sites' current layouts and are tested on anonymised examples, so a changed or foreign-language
layout can make a parser miss a card.

- **AI fallback (on by default, `sources.alerts.llm_fallback`).** When a parser cannot read a card or a whole email,
  the AI model reads it instead. It only sees a redacted excerpt (no name, address, email, phone or tracking links),
  and anything it names that is not in that text is thrown away. It needs your AI model key and is capped at
  `sources.alerts.max_llm_extractions_per_run` (default 20) reads per run. Without a key, or with the fallback off,
  such emails are only counted as unrecognised and tried again next run.
- **Other senders (off by default, `sources.alerts.llm_unknown_senders`).** Alerts from sites without a parser are
  skipped. Set this to `true` to let the AI fallback read emails from unknown senders that look like a job alert. Their
  jobs are listed under the source `llm-alert` and are marked **needs review**: check each one before you apply.

Details, settings and how to add a parser for another site are in [Job sources](docs/job-sources.md).

## Arbeitnow jobs (optional, free)

Enable Arbeitnow with `autoapp setup --section sources` or `sources.arbeitnow.enabled: true`.
It needs no API key. One bounded feed is shared across your selected profiles, matched locally
and deduplicated against existing jobs. API descriptions usually avoid another detail-page request.
Foreign editions are excluded; unclear location/remote eligibility still needs checking.
Local packages and CSV retain an Arbeitnow source link. See [Job sources](docs/job-sources.md#arbeitnow-optional-no-key)
for request caps, partial coverage and the unchanged network behavior of `run --dry-run`.

## Workwise jobs (on by default, free)

Workwise is searched with its public job search, once per keyword of your search profiles, within request caps
(`sources.workwise`); jobs farther than your profile's radius are left out locally. Workwise alert emails are read
too. Applying on Workwise needs your Workwise account: log in once with `uv run autoapp login workwise`. See
[Job sources](docs/job-sources.md#workwise-on-by-default-no-key).

## Employer careers sites (optional)

Alert emails often link to pages the tool cannot read. With `sources.employer_careers.enabled: true` (asked in `autoapp setup --section sources`; needs a Parallel Search key in `workspace/.env` as `PARALLEL_API_KEY` for employers that are not saved yet) the tool looks up the employer's own careers site, remembers it, reads the original vacancy there and finds other openings that match your search profiles. It only reads public pages. See [Job sources](docs/job-sources.md#employer-careers-pages-optional-off-by-default).

```bash
uv run autoapp employers                      # what is saved (read-only)
uv run autoapp employers --discover --dry-run # what a lookup would do: no request, nothing written
uv run autoapp employers --discover           # look up and scan now
uv run autoapp employers --discover --job <id> --again   # scan a saved site again (no search); retries a failed lookup now
uv run autoapp employers --website https://example-firma.de --job <id>   # confirm the right site
```

Two different dry runs: `autoapp run --dry-run` still searches and reads pages (it only sends nothing), while `employers --discover --dry-run` neither requests nor writes anything, not even a database migration. A verified employer posting is not a found application form; form finding stays a separate step.

## Statistics and the main menu

`autoapp stats` (menu: Statistics) shows what the tool did for a period: `run`, `today`, `7d`, `30d` or `all`. Press `p` to change the period and `q` to go back. It covers the models in use, ads read, new, duplicates and filtered, jobs evaluated and approved, folders saved, emails sent and held back (with reasons), forms filled, the review queue, per-source counts, Adzuna requests against their caps, LLM calls, tokens and estimated cost (in total and for each model; OpenRouter prices are fetched automatically), cost per application, jobs by status, and "to brush up" (the requirements most often missing or only partly met). Secrets are never shown.

During an interactive `autoapp run` the screen becomes a live view: run statistics on top and a feed below with one line per email, draft or test email, saved folder and filled form. Approvals, skips and warnings appear in a quieter style. Press `f` to show only emails and forms, `q` to hide the view (the run continues with plain lines). `--no-dashboard`, `--non-interactive`, no terminal, or a terminal narrower than 60 columns print plain lines instead.

```
╭──────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Run [review first] step: draft   elapsed 2:55                                                    │
│ search > fetch > evaluate > prepare > draft                                                      │
│                                                                                                  │
│ ads read 69             new 65               filtered 14                     evaluated 3         │
│ approved 2 (67 %)       folders saved 2      drafts saved 1                  forms saved 1       │
│ held back 0             errors 0             est. cost so far $0.2100        sent today 0 of 5   │
│ models: evaluate: openrouter qwen/qwen3.8-flash (low)   write: openrouter                        │
│ anthropic/claude-sonnet-5.5 (medium)                                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Feed (f: emails and forms only) ────────────────────────────────────────────────────────────────╮
│ 14:00:05 SEARCH        arbeitsagentur: 50 found, 50 new, 0 duplicates, 14 filtered               │
│ 14:00:09 SEARCH        alerts: 19 found, 15 new, 4 duplicates, 0 filtered                        │
│ 14:01:00 APPROVED    Acme GmbH, AI & Digital Solutions Engineer  score 82                        │
│ 14:01:10 SKIPPED     Muster-IT GmbH, Software-Entwickler  score 25                               │
│ 14:01:20 APPROVED    Beispiel Werk SE, Fullstack Engineer I  score 82                            │
│ 14:02:00 FOLDER SAVED Acme GmbH, AI & Digital Solutions Engineer  application_acme-gmbh_ai-digi…  │
│ 14:02:30 FOLDER SAVED Beispiel Werk SE, Fullstack Engineer I  application_beispiel-werk-se_full…  │
│ 14:02:40 DRAFT SAVED Acme GmbH, AI & Digital Solutions Engineer  → hr@firma.example (in [Gmail…  │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
q: hide this view (the run continues)   f: filter the feed
```

### Feed tags

Each feed line starts with a tag that names the thing and the outcome. Dim tags are quiet; the others are always shown, also with the `f` filter.

| Tag | Meaning |
|---|---|
| SEARCH | one source finished: ads found, new, duplicates, filtered |
| DETAILS | the full job pages were read: how many, how many unavailable |
| APPROVED / MAYBE / SKIPPED | evaluation result with the score: prepare, ask you, or drop |
| NEEDS REVIEW | a job or letter needs your check before it goes on |
| EMPLOYER SITE | one employer's own website was looked up: `website verified (saved site)`, `(new lookup)`, `no website could be verified`, or `several companies share this name` |
| EMPLOYER SCAN | the employer's careers page was read |
| JOB FOUND | an alert job's original posting was found on the employer's site (its address follows) |
| AD SEARCH | the search for a job's own ad by company and title (LinkedIn, StepStone, Indeed and Arbeitsagentur jobs) found nothing usable, with the reason per result |
| JOB NOT FOUND | the employer is known but the alert's job could not be matched; the reason follows |
| NEW JOB | another opening of that employer was added as a job |
| LOOKUP DEFERRED | an employer lookup was postponed (employer or page limit for this run, search cap or missing key) |
| FOLDER SAVED / FOLDER RENAMED | an application folder was written, or renamed to show its form status |
| FORM CHECK | the real application page was looked for; the result follows (found, needs your browser, login, ...) |
| FORM FILLED | `assist` filled a form; nothing was submitted |
| EMAIL SENT / WOULD SEND / EMAIL HELD / EMAIL FAILED | a real email went out, would go out (dry run), was stopped by a safeguard (reason follows), or failed |
| DRAFT SAVED / WOULD DRAFT / DRAFT EXISTS | review mode: saved as a draft, would be (dry run), or already in your mailbox |
| TEST SENT / WOULD TEST | test mode: sent to yourself marked `TEST_RUN`, or would be (dry run) |
| WARNING / ERROR | something did not work fully, or a step failed |

Open `autoapp stats` in another terminal to watch a scheduled run live. It never takes the run lock. A run with no update for 15 minutes is marked stale.

## Documentation

- [Getting started](docs/getting-started.md): the setup wizard and where files live
- [Job sources](docs/job-sources.md): Arbeitsagentur, Adzuna, Workwise, optional Arbeitnow, alert emails, employer sites and company apply links with your login
- [AI providers and cost](docs/llm-providers.md): Claude, Gemini, OpenRouter, cost per job
- [Cover letters](docs/cover-letters.md): what the writer may claim, style rules, your own instructions
- [Application folders](docs/application-folders.md): every file and the manual-apply workflow
- [Email](docs/email.md): Gmail app passwords, Proton Bridge, run modes, drafts, test runs, safeguards, rollout
- [Assisted applying](docs/assisted-applying.md): filling web forms with you in control
- [Scheduling](docs/scheduling.md): the daily systemd timer and watching it live

## Development

```bash
uv run pytest          # browser tests need Chromium; live API tests need the provider's key
uv run ruff check src tests
```
