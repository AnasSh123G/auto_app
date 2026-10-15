# autoapp usage guide

This guide shows how to use autoapp day to day: what to press, what each screen means, and what to do when
something looks wrong. For the settings in detail see the other pages in `docs/`; this page is the one to keep open
while you work.

**On this page**

1. [What autoapp does, in one minute](#1-what-autoapp-does-in-one-minute)
2. [Starting it](#2-starting-it)
3. [First-time setup](#3-first-time-setup)
4. [A normal day](#4-a-normal-day)
5. [The main menu, entry by entry](#5-the-main-menu-entry-by-entry)
6. [Run modes: what happens to the emails](#6-run-modes-what-happens-to-the-emails)
7. [The live run screen and its tags](#7-the-live-run-screen-and-its-tags)
8. [What happens to a job (statuses)](#8-what-happens-to-a-job-statuses)
9. [Application folders and the CSV](#9-application-folders-and-the-csv)
10. [Web forms: Finish forms and assist](#10-web-forms-finish-forms-and-assist)
11. [Safe ways to try things (dry run, test run, fresh test run, redo)](#11-safe-ways-to-try-things)
12. [Costs and limits](#12-costs-and-limits)
13. [The settings you will change most](#13-the-settings-you-will-change-most)
14. [Job sources and employer sites](#14-job-sources-and-employer-sites)
15. [Cover letters](#15-cover-letters)
16. [Running it on a schedule](#16-running-it-on-a-schedule)
17. [Problems and fixes](#17-problems-and-fixes)
18. [Command cheat sheet](#18-command-cheat-sheet)
19. [Where everything is stored](#19-where-everything-is-stored)

---

## 1. What autoapp does, in one minute

```
 find jobs  ->  read the ad  ->  judge it against your CV  ->  write a letter  ->  build a folder  ->  email / form
 (search)       (fetch)          (evaluate)                    (prepare)           (prepare)           (send/draft/assist)
```

| Step | What happens | Uses the AI? |
|---|---|---|
| **Search** | Reads the job-alert emails (LinkedIn, Indeed, StepStone, Workwise, XING) in your mailbox first, then searches Arbeitsagentur, Adzuna, Workwise and optionally Arbeitnow | no |
| **Fetch** | Reads the full text of ads that arrived with only a short snippet. Alert jobs get their ad by a web search for the employer's own posting (`sources.alerts.mode: search_first`, the default), not from LinkedIn, Indeed, StepStone or Workwise | no |
| **Evaluate** | Scores each job from 0 to 100 against your CV and decides: apply, maybe, or skip | yes (task `evaluate`) |
| **Prepare** | Writes a cover letter, renders PDFs, builds the application folder, looks for the real application form | yes (tasks `write`, `form_find`) |
| **Send / draft** | Email applications: saved as a draft in your mailbox, or sent, or sent to you as a test | no |
| **Assist** | Web-form applications: opens the form in a browser and fills it when you press `f`. **You** press Submit | yes (task `form_fill`) |

Three rules the program never breaks:

- **It never presses Submit on a web form.** You always do.
- **It never lies in a letter.** Only facts from your CV, profile and writing instructions are used.
- **It never applies on LinkedIn, Indeed or StepStone.** By default it only reads their alert emails and looks for the
  same job on the employer's own site. Only if you switch on **company apply links with your login** (off by default,
  section 14) does it open a job page there, to read where "apply on company website" leads. It never uses Easy Apply
  and never types there. Applications that only live on those sites are done by you, with the answers in `apply.html`.

---

## 2. Starting it

Always start it from the project folder (`/home/anas/dev/auto_app`) with `uv run`:

```fish
cd ~/dev/auto_app
uv run autoapp
```

| You type | You get |
|---|---|
| `uv run autoapp` | The **main menu** (needs a terminal) |
| `uv run autoapp run` | One full run, asking you how to handle emails |
| `uv run autoapp --help` | The list of all commands |
| `uv run autoapp <command> --help` | Every option of one command |

Good to know:

- The workspace (your data) is `./workspace` unless you pass `-w <folder>` or set `AUTOAPP_WORKSPACE`.
- Typing just `autoapp` (without `uv run`) only works if the project's virtual environment is active.
- Only one run can use a workspace at a time. A second one stops at once with exit code 3 and says another run is
  active. Statistics can always be opened next to a running run.

### Moving around in menus

- Use the **arrow keys** and **Enter** to pick an entry.
- **Ctrl-C** cancels the current entry and returns to the menu. At the main menu it leaves the program.
- In yes/no questions of the setup you can answer **b** (back) to return to the previous question.

---

## 3. First-time setup

```fish
uv run autoapp setup
```

The first time you run any command in a terminal, the wizard starts by itself. It has eight sections. Every question
shows the current value as the default, so **Enter keeps it**. Wrong answers are explained and asked again.

| Section | What you tell it | Notes |
|---|---|---|
| `documents` | Which files in `workspace/documents/` are your CV, certificates, Arbeitszeugnisse (role `work_reference`), photo, writing samples, other documents (role `other`, for "Andere" upload fields), and in which language | A document marked language **any** is sent with every application. One marked `de` or `en` is sent only with a letter in that language |
| `profile` | Name, address, email, phone, start date, notice period, language levels, extra skills | Stored in `profile.yaml` |
| `salary` | Target salary, optional minimum, amounts per seniority and per federal state | Example: 52000 everywhere, 56000 in Bayern. The program decides the figure from the job's level and place, so the letter and the form always agree |
| `search` | One or more search profiles: keywords and optional filters | Only the keywords are required |
| `sources` | Adzuna keys (optional), Arbeitnow, Workwise, the alert mailbox and folder, detail fetching, employer-site discovery, and how to get past LinkedIn/StepStone/Indeed links (employer site only, or also with your login) | Passwords and keys go into `.env`, never into `config.yaml` |
| `llm` | One provider and model **per task**: evaluate, write, form fill, find form | After the first answer, the next ones are pre-filled |
| `email` | Sending mailbox, safeguards, default run mode for scheduled runs | Default mode is `review` (drafts only) |
| `writing` | Your writing instructions and which documents are writing samples | Stored in `writing_instructions.md` |

At the end of `llm`, `sources` and `email` the wizard offers to **test** what you entered (one tiny AI request, the
mailbox logins, the Arbeitsagentur API). A failed test lets you re-enter the data.

### Changing something later

```fish
uv run autoapp setup --section llm      # redo one section; everything else stays
uv run autoapp setup --reset email      # one section back to defaults (asks first)
uv run autoapp setup --reset all        # back up your files, then start over (asks first)
```

The same is available in the menu under **Settings**. Your hand-edits and comments in `config.yaml` survive a
section redo. Backups go to `workspace/backups/<time>/`. `documents/`, the database and your application folders are
never touched.

### Mailbox cheat sheet

| Provider | What you need |
|---|---|
| **Gmail** | 2-Step Verification on, then an *app password* from <https://myaccount.google.com/apppasswords> (your normal password does not work) |
| **Proton Mail** | A paid plan and Proton Mail Bridge running on the same computer. Setup shows the Bridge certificate fingerprint; confirm it once |
| **Other IMAP/SMTP** | Host, port and TLS mode from your provider |
| **Outlook.com / Hotmail** | Not supported yet |

---

## 4. A normal day

The simplest daily routine, from safest to most automatic.

### The careful routine (recommended while you are still testing)

1. `uv run autoapp` and choose **Run now**.
2. Choose where to find jobs: **Email only**, **Full**, or **Web only**, then choose **Review first**.
   Every email application becomes a **draft** in your mailbox. Nothing is sent.
3. Watch the live screen (section 7). At the end press q or Enter when it says so (not needed when forms are ready to finish: the list opens straight away under the pinned statistics).
4. At the end, if there are web-form applications, the **Finish forms** list appears (section 10). Press Enter for
   "Not now", or pick a job to work on.
5. Open your mail program, read the drafts, send the good ones yourself.
6. Choose **Review queue** in the menu for jobs the AI was unsure about (section 5).
7. Open **Statistics** to see what it cost.

### The fully automatic routine (only once you trust it)

Choose **Full automatic** when a run asks, or set `run.default_mode: automatic` for scheduled runs. Emails that pass
the safeguards (section 6) are sent to employers. Start with a low daily cap.

### After you apply by hand

Tell the program, so it does not process the job again and your CSV stays right:

```fish
uv run autoapp mark <id> submitted          # you sent the application yourself
uv run autoapp mark <id> interview          # later: they invited you
uv run autoapp mark <id> rejected --note "form letter on 12 Oct"
```

---

## 5. The main menu, entry by entry

```
Run now · Statistics · Applications · Review queue · Finish forms · Employer sites
Redo unsent · Add a job by URL · Tests · Settings · Quit
```

| Entry | What it is for | Details |
|---|---|---|
| **Run now** | The whole pipeline | Asks for job sources, then application delivery mode (section 6), then shows the live screen |
| **Statistics** | What the tool did and what it cost | Press `p` to change the period (run, today, 7d, 30d, all), `q` to go back |
| **Applications** | Browse jobs by status | Per job: show folder path, show the letter, change status, re-render PDFs after editing, save a draft, send now (asks first), send a test copy to yourself, fill the form |
| **Review queue** | Jobs that need your decision | Shows the score, the reasons and missing requirements. Choose **Apply** (it is prepared in the next run) or **Skip** |
| **Finish forms** | Fill web-form applications | See section 10 |
| **Employer sites** | Careers sites found for your alert jobs | Show, preview, look up, scan again, confirm a website for one job (section 14) |
| **Redo unsent** | Process every unsent job again | Section 11 |
| **Add a job by URL** | One job you found yourself | Paste the link. If the page cannot be read, it offers to let you paste the ad text |
| **Tests** | Safe experiments | Quick test (the default), test run, full test run (fresh), test email, check logins, dry run |
| **Settings** | Change setup | Edit a section, default run mode for scheduled runs, redo or reset, delete all job data (keeps the setup) |
| **Quit** | Leave | |

The text in brackets next to *Finish forms* and *Review queue* shows how many jobs are waiting.

---

## 6. Run modes: what happens to the emails

Every interactive run asks this question first (unless you pass `--mode`).

| Mode | Emails | When to use it |
|---|---|---|
| **Review first** (`review`) | Each one is saved as a **draft** in your mailbox. Nothing is sent | Normal use while you want to read everything |
| **Test run** (`test`) | Each one is sent to **your own** address with `TEST_RUN:` in the subject. Nothing reaches employers, nothing is recorded | Check that real emails look right |
| **Full automatic** (`automatic`) | Emails that pass the safeguards are **sent to employers** | Only after you trust the letters. Asks for a confirmation (default no) |

Web-form applications are handled the same way in all three modes: a folder with prepared answers is built and the
form is looked up, but **no browser opens during a run**.

### The safeguards (automatic mode)

| Safeguard | Default | Meaning |
|---|---|---|
| `min_score` | 80 | Only jobs scoring this or more are sent automatically |
| `daily_cap` | 100 | At most this many emails per calendar day (Berlin time) |
| `company_cooldown_days` | 14 | No second email to the same company within this many days |
| Free-mail rule | on | Recipients at gmail.com, gmx.de, web.de and similar go to manual review |
| `needs_review` flag | always | A letter with warnings is never sent or drafted |
| `max_attempts` / `max_bundle_mb` | 3 / 10.0 | Retries for a failed send, and the largest allowed attachment bundle |

Review mode shows these as notes only. Your own values are in `workspace/config.yaml` under `email:`.

### Where should this run find jobs?

**Run now** first offers three source choices; **Full** is the default:

| Source choice | Jobs found and processed |
|---|---|
| **Email only** | Jobs from alert emails, including XING. Employer pages are still fetched for those jobs' descriptions and application forms; website sources and additional employer openings are skipped. |
| **Full** | Email alerts first, then enabled website sources. |
| **Web only** | Enabled website sources; the alert mailbox is not read. |

The choice also applies to saved pending jobs, including evaluation, preparation and delivery.
A job independently found by both an email alert and a website belongs to both choices. Matching an
email job to its employer's page alone does not turn it into a website-discovered job. Manual jobs
and employer openings without an email source are included in Web only. Other jobs stay saved for
a later run. The selection lasts for this run and does not change your source settings.

This source choice is separate from **Review first**, **Full automatic**, and **Test run**, which
still determine how application emails are delivered. Web only can still send or draft applications
if that is the delivery mode you choose.

For CLI runs, use `uv run autoapp run --sources email-only`, `--sources full`, or `--sources web-only`.
For example, `uv run autoapp run --sources web-only --mode review --non-interactive` discovers website
jobs and prepares drafts without reading alert emails. Scheduled runs and preselected delivery modes
use Full unless `--sources` is supplied; quick tests continue to sample all configured sources.

### Which mode do scheduled runs use?

`run.default_mode` in `config.yaml` (`review` or `automatic`; never `test`). Your file has a short comment block
there so you can switch by moving the `#`.

---

## 7. The live run screen and its tags

During an interactive run the terminal becomes one screen: **statistics pinned on top**, the **log** below (one line
per event, plus everything else the run prints: the summary, warnings, form-filling messages), and **questions at the
bottom**. The statistics stay in view the whole time, from the search until the last prepared form is dealt with
(**Finish forms** and `autoapp assist` use the same screen).

```
╭─ Run [review first]  step: prepare   elapsed 2:55 ────────────────────────────────╮
│ ads read 69      new 65        filtered 14        evaluated 3                      │
│ approved 2       folders saved 2     drafts saved 1     forms saved 1             │
│ AI calls 12      AI tokens 48,200    est. cost so far $0.21                        │
│ models: evaluate: openrouter openai/gpt-6-luna (low)   calls 9 · tokens 31,000     │
╰────────────────────────────────────────────────────────────────────────────────────╯
╭─ Feed ─────────────────────────────────────────────────────────────────────────────╮
│ 14:01:00 APPROVED     FEAG GmbH, AI Engineer   score 82                            │
│ 14:02:40 DRAFT SAVED  FEAG GmbH, AI Engineer   -> jobs@feag.de                     │
╰────────────────────────────────────────────────────────────────────────────────────╯
```

- **Scroll the log** with the mouse wheel or **PgUp/PgDn** (with no question open also `k`/`j` one line, `u`/`d`
  half a screen). While you are scrolled back, new lines do not move what you read; **End** or `G` jumps to the
  latest.
- **`f`** shows only emails and forms in the log.
- **Questions** (which form to work on, the form's personal questions) open under the log: ↑/↓ and Enter for a list,
  type and Enter for an answer. **Ctrl-C** cancels a question; with no question open it stops the run.
- To select text with the mouse (to copy a path), hold **Shift** while dragging: the screen uses the mouse for
  scrolling.

> **When the run is over and no forms follow, the screen waits** so you can scroll through the log. That is not a hang:
> it says "Finished … press q or Enter to close". When prepared forms follow, the list opens straight away instead.

When the screen closes, the lines it printed and the final statistics are written to the normal terminal (statistics
last), and every line is in `workspace/logs/`.

The screen is replaced by plain lines with `--no-dashboard`, `--non-interactive`, no terminal, or a terminal narrower
than 60 columns.

### Feed tags

| Tag | Meaning |
|---|---|
| SEARCH | One source finished: found, new, duplicates, filtered |
| APPLY LINK | Reading a LinkedIn/StepStone/Indeed job page with your login (only when switched on): waiting, found the company address, or why not |
| PAGES TO READ | The fetch step starts: how many job pages it will read, per site |
| PAGE WAIT | A pause before the next page (LinkedIn, Indeed and StepStone: 20–40 s each, so the site is not overloaded); says how long |
| PAGE READ | One job page was read |
| PAGE FAILED | One page could not be read (HTTP error, timeout, or no job description in it) |
| PAGE BLOCKED | The site refused the app (bot check, login wall, HTTP 403/429); its other pages are skipped in this run |
| AD CLOSED | The ad page says it no longer takes applications ("No longer accepting applications", "Diese Stelle ist nicht mehr verfügbar"): the job is marked `expired` and not evaluated |
| PAGE SKIPPED | A page was not tried: the site was blocked earlier, switched off in `detail_fetch`, its per-run cap was reached, or the job has no address |
| DETAILS | Full job pages were read: how many, how many unavailable |

Every PAGE line starts with the progress, for example `[4/34]`, and ends with how many pages are left. During the
fetch step the statistics header also shows `step: fetch (page 4 of 34, waiting 25 s)`, counting down while it waits.
| APPROVED / MAYBE / SKIPPED | Evaluation result with the score: prepare it, ask you, or drop it |
| NEEDS REVIEW | A job or letter needs your check before it goes on |
| EMPLOYER SITE | One employer's own website was looked up: `website verified (saved site / new lookup / link from the alert / confirmed by you)`, `no website could be verified`, or `several companies share this name`, followed by the reason when it was not verified |
| EMPLOYER SCAN | The employer's careers page was read |
| JOB FOUND | The alert job's original posting was found on the employer's site (by its careers list, or by searching company and title) |
| AD SEARCH | The search for an alert job's own ad by company and title found nothing usable: the reason for each result (a job board, a list page, a different ad with this title, the company not named) |
| JOB NOT FOUND | The employer is known but the job could not be matched; the reason follows |
| NEW JOB | Another opening of that employer was added |
| LOOKUP DEFERRED | An employer lookup was postponed to the next run (employer or page limit for this run, search cap or missing key) |
| FOLDER SAVED / FOLDER RENAMED | A folder was written, or renamed to show its form status |
| FORM CHECK | The real application page was looked for; the result follows |
| FORM FILLED | `assist` filled a form. Nothing was submitted |
| EMAIL SENT / WOULD SEND / EMAIL HELD / EMAIL FAILED | A real email went out / would go out (dry run) / a safeguard stopped it (reason follows) / it failed |
| DRAFT SAVED / WOULD DRAFT / DRAFT EXISTS | Review mode: saved / would be saved (dry run) / already in your mailbox |
| TEST SENT / WOULD TEST | Test mode: sent to you / would be sent (dry run) |
| WARNING / ERROR | Something did not work fully / a step failed |

"WOULD ..." always means **dry run**: it shows what would have happened and does nothing.

### Statistics page

`uv run autoapp stats --period run|today|7d|30d|all` (or **Statistics** in the menu). It shows, per period:

- the models in use, with **calls, tokens and estimated cost for each model**
- ads read, new, duplicates, filtered; jobs evaluated and approved; folders saved
- emails sent and held back (with reasons), drafts, forms
- the review queue; per-source counts; Adzuna requests against their caps
- cost per application, jobs by status, and **"to brush up"** (requirements most often missing in the ads)

You can open it in a second terminal while a run is active; it then shows that run live.

---

## 8. What happens to a job (statuses)

```
 new ──evaluate──> approved ──prepare──> prepared ──> drafted / sent          (email)
                │                            └──────> submitted (you, form)    (web form)
                ├─> review  ──(you: Apply)──> approved
                ├─> skipped
                └─> evaluation_failed / description_unavailable
 filtered (removed by your keyword rules before the AI sees it)
```

| Status | Meaning | What you do |
|---|---|---|
| `new` | Found, not judged yet | Nothing; the next run evaluates it |
| `filtered` | Your exclude rules removed it | Nothing |
| `description_unavailable` | The ad text could not be read | **Add a job by URL** to paste the text, or `describe <id>` |
| `evaluation_failed` | The AI call failed (for example a rate limit). Later runs do **not** retry it by themselves | `uv run autoapp evaluate --job <id> --force`, or **Redo unsent** for all of them |
| `skipped` | Score below the "maybe" threshold | Nothing. Change it with `mark <id> apply` if you disagree |
| `review` | Score in the "maybe" range, or flagged | Decide in the **Review queue** |
| `approved` | Good fit, waiting for its folder | Nothing; the same or next run prepares it |
| `prepared` | Folder and letter are ready, not sent yet | Send/draft it, or fill its form |
| `drafted` | Saved as a draft in your mailbox | Read and send it from your mail program |
| `send_unconfirmed` | The send may have gone out but was not confirmed | Check your Sent folder, then `mark <id> sent` |
| `sent` | Email went out | Wait for an answer |
| `submitted` | You applied through a web form | Wait for an answer |
| `interview`, `offer`, `rejected`, `withdrawn`, `expired` | Your own tracking | Set with `mark` |

A job also becomes `expired` on its own when its ad page says it no longer takes applications (for example
LinkedIn's "No longer accepting applications"). The fetch step notices this before the job is evaluated, so no
letter is written for it; the log shows `AD CLOSED`.

Thresholds (in `config.yaml` under `evaluation:`): score **75 or more** is "apply", **60 to 74** is "maybe"
(review), below 60 is "skip". Setting a status by hand always works: `mark <id> <status>`, with the shortcuts
`apply` (promote a review job) and `skip`.

**Alert jobs go first.** Jobs that came from your alert emails are evaluated and prepared before jobs that only
came from a search.

---

## 9. Application folders and the CSV

Every prepared application gets a folder in `workspace/applications/`, named
`application_<company>_<title>_<id>` and possibly ending in a marker.

### What is inside

| File | Purpose |
|---|---|
| `README.md` | Start here: next steps, warnings, missing documents, "brush up before an interview" list, form status |
| `evaluation.md` | Requirement-by-requirement match and the score |
| `cover_letter.md` | The letter as text. **Edit this one**, then run `render` |
| `cover_letter.pdf` | The letter as a one-page PDF |
| `Bewerbungsmappe.pdf` | Letter + CV + certificates in one PDF |
| `email.eml` | The finished email (email applications) |
| `apply.html` | Your answers with copy buttons, for forms you fill by hand |
| `form_answers.md` | Prepared answers for common form questions, including the motivation text and answers you gave in `assist` |
| `job.md` | The job ad as saved |
| `cover_letter.<time>.md` | Older letters, kept when a folder is rebuilt |

### Editing a letter

1. Edit `cover_letter.md` in the folder.
2. `uv run autoapp render <id>` rebuilds the PDFs and the email. No AI is used and your edits are never blocked.

### The ending of the folder name (web forms)

An application that needs a web form is **always saved**, even when the form was not reached. The ending tells you
why, without opening the folder:

| Ending | Meaning |
|---|---|
| *(none)* | Email application, form found, or not looked up yet |
| `_NOFORM` | No application form found, or only part of the way |
| `_BROWSER` | The site refuses plain requests; it needs a real browser |
| `_LOGIN` / `_CAPTCHA` | A login or a robot check is in the way |
| `_RESTRICTED` | The form is on LinkedIn, Indeed or StepStone (or an Arbeitsagentur ad not yet searched at the employer) |
| `_NOEMPLOYER` | An Arbeitsagentur job whose employer's own ad was not found; the letter is ready, apply once the ad turns up |
| `_UNREACHABLE` | The page could not be loaded (often: the ad is gone) |

When a later look changes the result, the folder is renamed and the paths inside its files are updated. A folder you
renamed yourself is left alone.

### `applications.csv`

A spreadsheet overview of every job from `review` onwards, rewritten after every command. You may edit exactly two
columns: **`status`** and **`notes`**. Save the CSV and run any command; those two columns are imported. All other
columns are generated and your changes to them are ignored.

---

## 10. Web forms: Finish forms and assist

Web-form applications are the part that needs you. Open the list with the menu entry **Finish forms**, or let it
appear at the end of an interactive run (Enter means "Not now").

### The list

```
23 form application(s) are prepared. The app can fill 3 of them as soon as they open ...

  74464c  ITS Informationstechnik Service GmbH: Softwareentwickler*in ...   [form found]
  0813f0  advasolutions SE: Python Developer Stations                       [blocked the quick check]
  ae6a8f  Almedia: Junior Software Engineer, Data                           [robot check]
  6c8179  SEW-EURODRIVE GmbH: AI Engineer                                  [click through yourself]
  f4099c  TaskVerse: Python Developer                                       [apply yourself]
  f0e5bf  Siemens Energy: Digital Process & Agent Automation Engineer       [ad gone]
  Done
```

Jobs the program can fill come first. The bracket says what the job needs from you:

| Bracket | What it means | What you do |
|---|---|---|
| `form found` | The application form was found | Pick it; press `f` in the browser |
| `not checked yet` | The program has not looked yet | Pick it; it looks first |
| `blocked the quick check` | The site refused the program's background check, which often still works in a real browser | Pick it, open it in the app's browser; if the page loads, click through |
| `needs your login` | The site wants you to log in | Log in in the browser window (it is remembered), open the application, press `f` |
| `robot check` | A CAPTCHA, which is never solved for you | Solve it yourself, then press `f` |
| `click through yourself` | No apply button the program may safely click (many Arbeitsagentur ads build their button with scripts) | Click "apply" yourself, then press `f` when the form shows |
| `apply yourself` | The job is on LinkedIn, Indeed or StepStone, and neither the employer's own site nor (if switched on) the page read with your login gave a company apply address | Open it in your normal browser. If it has "Apply on company website", click it, copy that address and choose **paste its address**: the app opens and fills the employer's form. Otherwise apply there with `apply.html` |
| `apply on the site` | Read with your login: the job only takes applications on LinkedIn/Indeed/StepStone itself (for example Easy Apply) | Apply there yourself with the answers in `apply.html`, then mark it submitted |
| `employer ad not found` | An Arbeitsagentur job: the search by company and title and the employer's careers pages both ran and found no ad (folder ending `_NOEMPLOYER`) | The letter is ready. The app looks again on later searches; or open the job in your browser, follow it to the employer and **paste its address** |
| `employer ad not searched` | An Arbeitsagentur job whose employer search has not run (no `PARALLEL_API_KEY`, a used-up limit or no company name) | Set the key (or wait for the limit), then search again; or paste the employer's address |
| `apply on the Arbeitsagentur` | The ad is posted only on the Arbeitsagentur (no employer ad exists) | Apply on the Arbeitsagentur page with the answers in `apply.html` |
| `ad gone` / `page not loading` | The job page no longer loads | Check the link in your own browser; if the ad is gone, mark it skipped |

### What happens when you pick a job

- **`form found` or `not checked yet`:** the app's browser opens straight away.
- **Anything else:** the reason and the next step are shown first, then you choose:
  - open it in the **app's browser** (you click through, then the app fills the form),
  - **paste the address of the real form** you found yourself (the app opens and fills it),
  - open the link in **your normal browser**: you stay in this menu, with **paste its address** as the next
    choice, so you can find the form there and paste its address right away,
  - **mark it as submitted** (you already applied),
  - **mark it as skipped**,
  - go **back to the list**.
  
  For `apply yourself` (LinkedIn, Indeed, StepStone) the app's browser is only offered when **company apply links with
  your login** is on for that site: then it reads the company address from the job page in the app's browser and
  continues on the employer's form. Pasting the employer's form address always works.

**After every job you are back at the list**, also after a quit, an error, or a browser you closed. So you can open the
same job again. Choose **Done** (or **Not now** right after a run) to leave.

### Inside the browser: the four keys

```
[f]  fill the form now showing (the newest tab; press again for the next step of a multi-step form)
[o]  open the job page again in this tab (use it when the tab shows an error or looks offline)
[h]  show my prepared answers (apply.html), to copy by hand
[s]  I submitted it: mark the job as submitted
[q]  back to the list (asks "Did you submit the application?")
```

### Step by step: one application

1. Pick the job from the list. The browser opens and the terminal says what it found.
2. If the form is not showing yet, click through the site yourself (log in, accept cookies as you wish, press apply).
3. Press **`f`**. The program fills your name, contact data, salary, start date, uploads CV and letter, and writes
   an answer for open questions. Fields it filled are green; empty required ones are red. A list in the terminal
   says what it filled and what **needs your input**.
4. Read **every** field. Generated answers are marked "check this generated answer". Fix anything wrong.
5. Multi-step form? Click "Next" in the browser, press `f` again.
6. Click **Submit yourself** in the browser.
7. Press **`s`** in the terminal. The job becomes `submitted`.

**Questions the program answers by itself.** Some questions get the same answer in every application, so it never
asks a model and never asks you:

- "Where did you hear about this position?" is answered **LinkedIn** (in a drop-down it picks LinkedIn; if the list
  has no LinkedIn, the nearest general source such as social media or job board, then "Other").
- Questions about a **referral** ("Were you referred by an employee?") are answered **No**.
- Questions like "Have you worked at *this company* before?" are answered **No**.

**Personal questions.** For every other field it cannot fill from your profile, and the writing model could not
answer, the program asks you in the terminal (a number picks a drop-down option; **Enter** leaves the field for you
in the browser). Your answer is saved in two places:

- `form_answers.md` in the application folder, and
- `workspace/form_answers.md` (short answers only), which is read before the next question is asked. The same
  question in another form is then filled automatically. Long free-text answers belong to one job, so they stay in
  that job's folder. You can edit `workspace/form_answers.md` by hand: one `## Question` heading per answer.

**What gets saved.** Every time `f` fills a form, the app appends a record to `filled_form.md` in the application
folder: when, on which address, which fields it filled, what still needs your input, the generated answers and the
answers you gave. It also saves the page itself as **`filled_form.html`**: an offline copy with the filled fields
that you can open in any browser to read through. A banner at the top lists your prepared files (CV, letter) as
links. Limits you should know:

- Scripts are removed (so nothing empties the fields again); style sheets and small images are embedded.
- Sending from the copy only works when the site uses a plain HTML form; the copy's form posts to the employer's real
  address, but it **never carries the uploaded files**, so attach them yourself. Most modern application systems
  need their own scripts to submit, so then submit in the live browser instead.
- A multi-step form keeps one copy per page (`filled_form.<date>.html` for the earlier ones).

If you clicked through to the form yourself, its address is remembered as the found form: next time the job opens
straight on it, and the folder loses its `_NOFORM`-style ending. In the Finish forms list such a job shows
`(filled <date>)`.

### Your own directions for each model

Each of the four models (`evaluate`, `write`, `form_fill`, `form_find`) can get your own directions in
`config.yaml`, under `llm.tasks.<task>.instructions`:

```yaml
llm:
  tasks:
    form_fill:
      provider: anthropic
      model: claude-sonnet-5-5
      instructions: |
        For "years of experience" questions, count from my first full-time job.
```

They are added to the model's built-in prompt. They cannot switch off the safety rules: no invented facts, the fixed
output format, and page text is never obeyed. `autoapp setup` keeps them when you change the models.

### What the program fills, and what it never touches

| Filled | Never touched |
|---|---|
| Name, email, phone, address | The **Submit** button |
| Salary expectation (from your profile and the job's state) | **Consent and privacy** checkboxes |
| Start date / notice period | **CAPTCHAs** |
| Uploads: CV (matching language), cover letter, certificates, Arbeitszeugnisse (`work_reference`), `other` documents | Facts it does not know: they stay empty and are listed |
| Motivation and free-text answers written for this job | Anything on a restricted site |

### If something goes wrong in the browser

| Situation | What to do |
|---|---|
| You closed the **tab** | Press `f` again; it opens the page again |
| You closed the **whole browser window** | Press `q`; you are back at the list and can open the job again |
| `f` says no application form is showing | Open the form in the browser window, then press `f` again |
| The tab shows an error or "no internet" | The safety checks refused a page while the app was clicking through, and the browser kept its error screen | Press **`o`** in the terminal: the job page opens again and everything is allowed from then on. The app now does this by itself when it ends on an error page |
| The page is blank | A plain "Continue yourself" page explains it; `h` shows your answers |
| The login expired | Press `q`, open the job again, log in (the profile is kept in `workspace/browser-profile/`) |
| The app's browser seemed to have no internet (fixed on 2026-10-09) | It froze page loads while waiting for a key | Nothing to do any more: pages now load while the terminal waits |

### Looking for forms without a browser

```fish
uv run autoapp forms --dry-run            # list what would be looked up (no requests, no changes)
uv run autoapp forms                      # look for forms that have no result yet
uv run autoapp forms --job <id> --again   # look again from the original link
uv run autoapp assist <id>                # open one job directly in the browser
uv run autoapp forms --audit [--json]     # read-only report of the saved form links (no requests, no changes)
```

`forms --audit` counts the prepared web-form applications by form status (found, partial, none, need your browser,
restricted, ...), how many links point at a job board (and which), the split by source and the most common reasons
a link was not found:

```
existing form links (42 prepared):
  found 18 (43%) · partial 9 · none 5 · needs browser 4 · restricted 3 · unchecked 3 · on job board: 6 (arbeitsagentur.de 5, linkedin.com 1)
  by source: arbeitsagentur 20 (found 6) · linkedin-alert 12 (found 8) · stepstone-alert 10 (found 4)
  causes: partial: no usable application continuation 5 · none: no usable application continuation 3
```

The quick test prints the same report for your real workspace after its checklist.

---

## 11. Safe ways to try things

Five ways to try the program without sending anything to employers. They differ in what they touch:

| Option | Command | Emails | Your real job history | Costs AI money? |
|---|---|---|---|---|
| **Quick test** | `uv run autoapp test --quick` (menu: Tests > Quick test) | At most one, to **you**, subject `TEST_RUN: ...` | **Not touched.** A small sample of every step in `workspace/test-runs/<time>/`, then a checklist | a little |
| **Dry run** | `uv run autoapp run --dry-run` (menu: Tests > Dry run) | none sent, none drafted; shows `WOULD ...` | Evaluations and folders are still made; mails are not marked processed | yes |
| **Test run** | `uv run autoapp run --mode test` | Each goes to **you**, subject `TEST_RUN: ...` | Normal run; skips jobs already in the history | yes |
| **Full test run (fresh)** | `uv run autoapp test --full` (or `run --mode test --fresh`; menu: Tests > Full test run (fresh)) | Each goes to **you** | **Not touched.** Runs in `workspace/test-runs/<time>/` with an empty history, so every job counts as new | yes |
| **Redo unsent** | `uv run autoapp run --redo` | Whatever mode you pick | **Reset on purpose** for unsent jobs (a backup is made first) | yes |

### Quick test

`uv run autoapp test --quick` (menu: **Tests > Quick test**, the pre-selected entry) checks the whole chain in a few minutes
instead of simulating a whole run. In a throwaway workspace under `workspace/test-runs/<time>/` it:

- reads the newest alert email of **each known sender** (LinkedIn, StepStone, Indeed, Workwise), one each, taken from the
  headers of the 60 most recent messages of the alert folder (only the headers are fetched first, nothing is
  marked read or changed in your mailbox). Account or marketing mail from those senders (no job link) is skipped, so it
  neither hides the alerts behind it nor shows up as a failed parser. The emails are **not** marked as processed in
  your real workspace, so your next real run still reads them;
- asks each enabled source (Arbeitsagentur, Adzuna, Workwise, Arbeitnow) for a couple of jobs, keeping at most 2 from each (Arbeitnow, which is
  read page by page, only 3 pages);
- looks up at most 2 alert employers online: the own-ad search, then the employer's website and careers page;
- chooses the jobs to prepare (below) **before** the own-ad search and the careers lookup of the Arbeitsagentur jobs, so a
  job below your threshold gets the same employer search as in a real run (these jobs go first within the 2 lookups);
- evaluates at most 6 jobs and prepares at most 4: one email application and up to three online-form applications,
  taken from different sources (one Arbeitsagentur job, one alert job, one other; a source with no candidate gives
  its slot to the next best score), **even if they score below your threshold**, so that letter writing and the
  read-only form search are exercised (nothing is filled in, uploaded or submitted);
- sends at most one `TEST_RUN:` email, to your own address only. The fit score does not stop it, every other
  send rule does.

It ends with a checklist, one line per part, each `OK`, `WARN`, `FAILED` or `SKIPPED` with the reason. A "form links" section adds one line per
prepared form job: job id, source, the final link, the form status with its note, and in brackets how the link was
found (`direct search`, `careers page`, `externeURL` or `job page`):

```
quick test checklist:
  OK       alert mailbox: 2 alert emails read (the newest of each sender), 18 job cards
  OK       alert parser linkedin: 6 cards
  OK       source arbeitsagentur: 3 jobs found, 3 new
  FAILED   source adzuna: error: Adzuna rejected the app ID or key (HTTP 401)
  OK       source workwise: 4 jobs found, 4 new
  SKIPPED  source arbeitnow: disabled
  OK       search key: PARALLEL_API_KEY is set
  OK       employer lookup: 3 search requests, 0 saved sites used, 1 ads found by search, 1 on careers pages
  OK       reading pages: 2 job pages, 4 employer pages, 1 ads read from search results
  OK       AI model (evaluation): 4 jobs evaluated
  OK       letter writing: 2 applications prepared
  OK       form finding: 1 found, 1 partial
  SKIPPED  test email: no email application was prepared
form links:
  OK       a1b2c3  arbeitsagentur → https://careers.ejot.com/job/123#apply  found (employer page, 2 hops) [careers page]
  FAILED   d4e5f6  linkedin-alert → https://www.linkedin.com/jobs/view/1  restricted (own ad not found) [job page]
  WARN     0789ab  arbeitsagentur → no link  no_employer_link (employer ad not found yet) [job page]
quick test: 11 OK, 1 WARN, 2 FAILED, 2 SKIPPED
```

(An example; yours depends on your sources and on what the newest alert emails contain.)

`SKIPPED` means that part could not be tried this time (for example no job alert email among the newest in the folder, a disabled source,
or no email application among the jobs it prepared, so no test email); run the quick test again later or use a
**test run** for those parts;
After the checklist it prints the read-only form-link audit of your real workspace (`autoapp forms --audit`), as a
baseline for the form finding.
`WARN` marks a form link that was not found (for example `no_employer_link`, `partial` or `needs_browser`) and does not
change the exit code; a form link that ends on a job board (LinkedIn, Arbeitsagentur, ...) is `FAILED`.
`FAILED` makes the command exit with code 1. Your real jobs, statuses, folders and CSV are not touched. The searches
and AI calls are real, a handful in total, and are added to your real counters and limits. `--profile NAME` uses one
search profile only. It cannot be set as `run.default_mode`. `uv run autoapp test` without `--quick` or `--full`
starts nothing: it prints the two choices and exits with code 2.

### Dry run

`uv run autoapp run --dry-run` does everything except sending, drafting and marking alert emails. It still searches,
reads pages and uses the AI. The feed shows `WOULD SEND`, `WOULD DRAFT` or `WOULD TEST`. **If you got nothing in
your inbox after a dry run, that is correct.** To receive emails, run a **test run**.

### Test run

`uv run autoapp run --mode test` is a real run in which every email goes to your own sending address instead of
the employer. Each says on its first line "This email is what would be sent to <address> in a real run." Nothing is
recorded, so a job can still be sent for real afterwards. Because it uses your real history, it does nothing when
every job found is already in it.

### Fresh test run

`uv run autoapp run --mode test --fresh` (or `uv run autoapp test --full`; menu: **Tests > Full test run (fresh)**) solves exactly that. It copies your
settings, documents and saved employer sites into `workspace/test-runs/<time>/`, starts with an empty job history,
and treats every ad found, including all alert emails of the last 30 days, as new. Your real jobs, folders and CSV are
not touched. The AI calls it makes are real, so they are added to your real statistics. Delete the `test-runs/`
folder when you do not need it any more. To work with its forms:

```fish
uv run autoapp -w workspace/test-runs/<time>
```

### Redo unsent

Use this after you changed prompts, models or settings and want every job that is **not sent** to be processed again,
as if it were new.

```fish
uv run autoapp run --redo --dry-run     # only print the plan
uv run autoapp run --redo               # asks first (default no)
```

It shows what it will redo, asks, backs up your database to `workspace/backups/redo-<time>/autoapp.db`, and sets
every unsent job back to `new`. They are evaluated again, their folders rebuilt in place (the **old letter stays
beside the new one** as `cover_letter.<time>.md`) and their forms looked up again.

| Redone | Left alone |
|---|---|
| `skipped`, `review`, `approved`, `prepared`, `evaluation_failed`, `description_unavailable` | `sent`, `submitted`, `interview`, `offer`, `rejected`, `withdrawn`, `expired` |
| | `drafted` (a draft exists in your mailbox) and `send_unconfirmed` (the mail may have left) |
| | `filtered` (your rules removed it) |

The per-run limits still apply. A large redo is finished by the **next normal runs**. **Do not pass `--redo` again**:
that would start from the beginning.

### Start over with your setup kept

To throw away everything the program has collected and start again from an empty history, with your setup intact:

```fish
uv run autoapp reset-data                       # shows what goes and what stays, then asks (default no)
uv run autoapp reset-data --answers --logins    # also delete saved form answers and browser logins
```

Menu: **Settings > Delete all job data**, which also asks about the form answers and the logins first.

| Deleted | Kept |
|---|---|
| The job history `autoapp.db` (jobs, evaluations, sent mails, processed alert emails, employer lookups, statistics) | `config.yaml`, `profile.yaml`, `.env` |
| `applications/` (every folder and letter), `applications.csv` | `documents/`, `writing_instructions.md`, `backups/` |
| `logs/`, `test-runs/` | `openrouter-prices.json` (the saved model prices), `form_answers.md` and `browser-profile/` (your logins), unless you add `--answers` / `--logins` |

Nothing is deleted until you say yes; there is no backup, so it cannot be undone. Without a terminal (a script) it
refuses unless you add `--yes`. Because processed alert emails are forgotten too, the next run reads the alert emails
in your folder again. It does not run while another run is active.

---

## 12. Costs and limits

### Where the money goes

| Task | When | Typical size |
|---|---|---|
| `evaluate` | Once per new job | Many calls, so a cheap model is fine |
| `write` | Once per prepared application | Few calls, so a good writer is worth it |
| `form_find` | Ambiguous HTTP links, or the headless fallback on unresolved employer/ATS pages | At most four HTTP choices plus `assist.navigate_max_steps` (default 8) browser choices |
| `form_fill` | When you press `f` in assist | A few per form |
| `alert_extract` | Only when an alert parser cannot name a card's employer or title, or finds no card | At most `sources.alerts.max_llm_extractions_per_run` (20) calls per run |

Each task has its own model (`llm.tasks` in `config.yaml`). Costs are **estimates** from your token counts and the
model's price. The provider's bill is what counts. OpenRouter prices are fetched automatically (once a day, no key);
a price in `llm.prices` always wins.

### The limits of one run

| Setting (`limits:`) | Default | Effect |
|---|---|---|
| `max_cost_per_run_usd` | **3.0** | The run stops asking the AI once its estimated cost reaches this. The remaining jobs wait for the next run |
| `max_llm_calls_per_run` | 500 | Same, counted in calls |
| `max_evaluate_per_run` | 200 | At most this many jobs are evaluated |
| `max_prepare_per_run` | 50 | At most this many letters/folders are made |

The run summary says which limit stopped it, for example "AI cost limit of $3.00 per run reached". The call that crosses
the limit is still paid, so a run can end a little above it. A model whose price is unknown cannot be counted; the
summary warns about it. Add the price under `llm.prices`. Set a limit to `0` to allow nothing.

### Other limits

| What | Limit |
|---|---|
| Adzuna | About 25 per minute, 250 per day, 1000 per month; the program keeps a safe margin and stops cleanly |
| Arbeitnow | 3 requests per run, 30 per day, 300 per month (courtesy limits) |
| Workwise | 30 requests per run, 100 per day, 1500 per month, 2 seconds apart; 2 pages of 50 jobs per keyword |
| Company apply links with your login | Off by default; when on, 20 job pages per run, 20 to 40 seconds apart; stops for a site at its first robot check |
| Employer lookups | 10 employers, 20 searches per run; 50 per day; 1000 per month; about $1 per 1000 searches |
| Detail pages of LinkedIn/Indeed/StepStone | 20 per site per run, 20 to 40 seconds apart; stops at the first CAPTCHA |

---

## 13. The settings you will change most

All are in `workspace/config.yaml` (or through **Settings** in the menu). Edit it by hand any time; setup keeps your
changes.

### Search profiles

```yaml
search_profiles:
  - name: junior-backend/ML
    keywords: [Junior Backend developer, Python developer, Junior Data Engineer]
    exclude_keywords: [senior, lead, manager, frontend]   # job titles containing these are dropped
    working_time: full_time
    experience_levels: [Junior]
    max_posting_age_days: 7
    exclude_agencies: true
    # locations: [{name: Berlin, radius_km: 30}]   work_mode: hybrid   min_salary: 45000
```

Only `keywords` are required. Everything else is optional, and an unknown value in a job (for example an unstated
salary) never filters it out.

### Models

```yaml
llm:
  tasks:
    evaluate:  {provider: openrouter, model: openai/gpt-6-luna}
    write:     {provider: openrouter, model: anthropic/claude-haiku-5.5}   # add effort: medium|high if you like
    form_fill: {provider: openrouter, model: anthropic/claude-sonnet-5.5}
    form_find: {provider: openrouter, model: openai/gpt-6-luna}
```

Providers: `anthropic`, `google`, `openrouter`. Keys in `.env`: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
`OPENROUTER_API_KEY`. Redo it with `uv run autoapp setup --section llm`.

### What each model does

The four tasks can use four different models, or the same one. The alert-job search in search-first mode is **not** a
fifth model: it is Parallel Search, a web search.

**`evaluate`: judges each job against your CV.** Runs on every new job (alert jobs first), up to
`max_evaluate_per_run`.
- *Gets:* the ad text, your CV and your profile.
- *Returns:* a fit score (0 to 100) with a short reason; every requirement of the ad marked matched, bridgeable or
  missing, with evidence; dealbreakers, seniority, German level, work mode; the application channel (email, employer
  form, platform or unknown); the apply email exactly as the ad words it, contact person, reference number, requested
  documents, ad language and any stated salary.
- *The model only classifies.* The program makes the decision: it re-checks which gaps are really bridgeable and
  re-checks dealbreakers against your profile, applies your `apply_threshold` (75) and `maybe_threshold` (60) to get
  apply, maybe or skip, and keeps an apply email only if it appears word for word in the ad.
- *If a call fails,* the job becomes `evaluation_failed` and is tried again on a later run.
- *Size:* many calls, so a cheap model with low effort (the default) is enough.

**`write`: writes the cover letter and the email text.** Runs on approved jobs, up to `max_prepare_per_run`.
- *Gets:* the job, your CV, your profile, your writing instructions and the requirements that matched or are
  bridgeable.
- *Returns:* the letter paragraphs, a short motivation line, the email body and a list of claims, each tied to
  evidence.
- *Checked by the program:* every claim against your CV and profile, every number, and your style rules (dashes,
  banned phrases, exclamation marks, connectors, length). If anything fails, the model gets **one** combined rewrite
  request listing every problem. Whatever it cannot fix is marked `needs_review`, which blocks automatic sending and
  drafting. A PDF longer than one page is rewritten once, shorter.
- *Also used* for free-text questions in web forms, under the same rules (see `form_fill`).
- *Size:* few calls, so a stronger model is worth it. The default effort is medium.

**`form_fill`: fills in what the rule-based filler could not.** Runs only in `autoapp assist` (you start it; never part
of a normal run).
- *Gets:* the form fields the program could not place, plus the facts it knows about you.
- *Free-text questions* are answered under the same truth and style rules as the letters, within the field's
  `maxlength`.
- *Short text, dropdowns and radio buttons* get one structured call. The answer must be one of the field's own options
  or one of your known facts; anything else is dropped, so it cannot invent a value.
- *Never given to it:* file uploads, checkboxes and consent fields.
- You review the form and press Submit yourself. Every generated answer and every field left empty is listed.
- *Privacy:* it does use your details, because you started it.

**`form_find`: finds the right link to an application form.** Runs while preparing applications, and in assist when
`assist.navigate_mode` is `model`. Preparation first tries HTTP, then a fresh headless browser on unresolved
employer/ATS pages when `assist.browser_fallback` is true (the default). Both use the configured `form_find` model.
Account/profile creation remains a manual handoff.
- *Choosing a link:* a clear apply link needs no model call. When a job page has several plausible links, the model
  picks one or none: at most four calls per attempt, no retries.
- *Navigating (assist):* at each step it sees the page title, address, a short text excerpt and a numbered list of
  clickable elements, and picks one to click or stops with a reason.
- *Limits the program enforces:* only listed elements can be clicked, and the list never contains anything that
  submits, signs in or creates an account. Nothing is ever typed. A login or robot check is handed to you. Restricted
  sites stay blocked and the number of pages is capped. The form detector, not the model, decides that a form was
  found. If the model cannot be used, the rule-based navigation takes over.
- *Privacy:* it sees only public job hints and short page texts and addresses. Your profile, CV, letters and answers
  are never sent, and tokens and email addresses are removed.
- *Size:* cheap and rare; the default effort is low.

**Shared by all four:** one budget (`max_llm_calls_per_run` and `max_cost_per_run_usd`, see section 12), usage counted
per task on the statistics page, and `llm.tasks.<task>.instructions` for your own directions (section 10). The quick
test exercises `evaluate`, `write` and `form_find`; `form_fill` is not covered because it only runs in assist.

### Run mode, safeguards and limits

```yaml
run:
  default_mode: review          # automatic | review   (scheduled runs; "test" is not allowed here)
email:
  daily_cap: 20
  min_score: 80
  company_cooldown_days: 14
limits:
  max_cost_per_run_usd: 3.0
evaluation:
  apply_threshold: 75
  maybe_threshold: 60
```

### Salary

In `profile.yaml`. The program works out one figure from the job's level and **federal state** and uses it everywhere
(letter, forms). A state override beats the level amounts. Example: 52000 as the target, 56000 for Bayern.

### Letters

```yaml
writing:
  language: auto        # auto = the ad's language, or de / en
  max_words: 300
  max_assumed_skills: 5
```

Your tone and emphasis go into `workspace/writing_instructions.md`.

---

## 14. Job sources and employer sites

| Source | Needs | Notes |
|---|---|---|
| **Arbeitsagentur** | nothing | Free public search |
| **Adzuna** | App ID and key in `.env` | Free tier, request caps are kept for you |
| **Arbeitnow** | nothing (turn on in setup) | Free feed; the link back to Arbeitnow is kept in the CSV |
| **Workwise** | nothing (on by default) | Its public job search, by the keywords of your search profiles; jobs farther than your radius are left out. Applying there needs a Workwise account: log in once with `uv run autoapp login workwise` |
| **Alert emails** | A mailbox folder (for example label `Jobalerts`) | LinkedIn, Indeed, StepStone and Workwise alerts are read |
| **Employer careers sites** | `PARALLEL_API_KEY` (optional, for new employers) | See below |

### Setting up the alert folder (Gmail)

1. In Gmail create a filter `from:(jobalerts-noreply@linkedin.com OR jobagent.stepstone.de OR indeed.com OR workwise.io)`
   that applies the label `Jobalerts`.
2. Set `sources.alerts.folder: Jobalerts`.
3. Create alerts on the job boards (daily or weekly).

### Workwise

Workwise is searched like the other sources: once per keyword of each search profile, at most 2 pages of 50 jobs, with
a pause between requests and caps per run, day and month (`sources.workwise`). Workwise has no location filter the app
can use, so the distance to your profile's cities is checked by the app from the coordinates Workwise gives (a remote
job always counts as near). The full description comes from the job page in the fetch step.

Workwise alert emails are read too. Until a real Workwise alert email is available as a template, the app takes every
link to a Workwise job from the email; the title and company then come from the job page.

You apply to Workwise jobs on Workwise itself, with your Workwise account: run `uv run autoapp login workwise` once,
then the app's browser is logged in when it opens a Workwise job.

### Alert jobs: search first or platform

By default (`sources.alerts.mode: search_first`) the app never opens a LinkedIn, Indeed, StepStone or Workwise job page
to read an alert job. It takes the title, company and city from the alert email, searches for the employer's own ad
and, if that fails, reads the job ads on the employer's careers page. This needs `PARALLEL_API_KEY` in
`workspace/.env`; without it, alert jobs wait with the reason "search key missing" and the run says so. Set
`sources.alerts.mode: platform` to get the earlier behavior (the job-board page is read, often blocked). The run
summary names the mode and counts the outcomes.

### Getting past LinkedIn, StepStone and Indeed links

Without a login, these sites no longer show where a job is really applied to, and the app never opens them on its
own. There are two ways to still reach the employer's form:

1. **The employer's own careers site (the default).** Two searches, no account involved:
   - **Employer discovery** (below) finds the employer's website, reads its job list and takes the job with the
     same title there. When a company has sites in several countries (`chefsculinar.se`, `chefsculinar.de`), each
     is checked, the German one first.
   - **Own-ad search**, after evaluation: for every job worth applying to (approved or in review) that is still on
     LinkedIn, StepStone, Indeed **or the Arbeitsagentur**, and for jobs whose ad could not be read, the app
     searches `"<company>" "<job title>"` and opens the results. For Arbeitsagentur jobs this is always on (their
     pages mostly send you on to the employer and do not work well in the app's browser); for the other sites it
     comes with employer discovery. It needs `PARALLEL_API_KEY` in `workspace/.env`. Employer discovery leaves `reserved_for_own_ad_search` (default 25) searches per run and day unused, so
     this search is never starved by it; a job not searched because the daily limit was reached shows `AD SEARCH ... daily request limit`. A result is used only when it is this ad:
     not a job board, not closed, the title in the page heading, the company named, and most of the ad's text
     on the page. Two ads with the same title (an Angular and a Java "Software Engineer") are not mixed up. A
     job where nothing matched is searched again after `failure_ttl_hours` at the earliest. The log line
     `AD SEARCH` gives the reason for every result that was not taken.

   Either way the job then points at the employer's ad. If its folder was already prepared (`_RESTRICTED` or
   `_NOEMPLOYER`), the form is checked again at once and the folder renamed. An Arbeitsagentur job whose search
   **and** careers lookup both ran without finding the employer's ad is marked `_NOEMPLOYER` ("employer ad not
   found"): its letter is still prepared, the Arbeitsagentur page is shown only as **Job ad**, and the lookups are
   repeated after `failure_ttl_hours`. A lookup that did not run at all (no key, a limit) is not a miss; the job
   stays `_RESTRICTED` with the reason. An application form that is drawn by JavaScript (some job
   portals) first gets the bounded browser fallback. If it remains unresolved, it is marked `need your browser`;
   **Finish forms** opens it in the app's browser. A page that requires creating a profile is marked `login required`
   and needs manual continuation.
2. **Read the company apply link with your login (off by default).** In **Settings > sources** choose "Also read
   them from the job page with my login" and the sites. Then log in once in the app's browser:

   ```fish
   uv run autoapp login linkedin     # or stepstone, indeed
   ```

   For a job the employer lookup could not match, the app opens that one job page in its browser (with your login),
   finds "apply on company website" and notes the address it leads to; the employer's page itself is not loaded
   there. Then the form is found and filled on the employer's site as usual. This happens while a run prepares the
   job, and also when you open such a job in **Finish forms**. A line `APPLY LINK` in the log says what happened.

   It never uses Easy Apply, never types, never logs in for you and never sends anything there. Jobs that only take
   applications on the site itself show `apply on the site`. One page at a time, 20 to 40 seconds apart, at most 20
   per run; a site that shows a robot check is not tried again in that run.

   **The risk:** this is automated use of your logged-in account, which these sites' terms forbid. At this pace the
   risk of a restriction is low, but not zero. That is why it is off by default and switched on per site.

```yaml
sources:
  apply_links_with_login:
    linkedin: true     # off (false) by default
    stepstone: false
    indeed: false
    max_per_run: 20
```

### Employer sites (optional)

Alert emails often link to pages the program cannot read. With `sources.employer_careers.enabled: true`, the program
looks up the employer's **own** careers site, reads the original vacancy there (so the job gets a real description and
a real application link) and finds other matching openings. It only reads public pages.

```fish
uv run autoapp employers                        # what is saved (read-only)
uv run autoapp employers --discover --dry-run   # what a lookup would do: no request, nothing written
uv run autoapp employers --discover             # look up and scan now
uv run autoapp employers --discover --job <id> --again                # scan a saved site again (no search)
uv run autoapp employers --website https://firma.de --job <id>        # you confirm the right website
```

Recruiters and directories (for example staffing companies, or sites that only list jobs) are deliberately **not**
treated as the employer. In that case the result is "no website could be verified" or "several companies share this
name", which is an honest answer, not an error.

Only the public employer name, the job title and the city are ever sent to the search service. Never your CV,
profile, mailbox contents or email addresses.

---

## 15. Cover letters

What the writer **may** do:

- Use any fact from your CV, profile and writing instructions.
- For nice-to-have skills you only half have, say "working knowledge of X" or "familiar with X", up to
  `max_assumed_skills` (default 5) per letter, **only** for a job that scored "apply". Every such skill is listed under
  "Before an interview, brush up on" in the folder's `README.md`, so nothing in a letter surprises you in an interview.

What it **never** does:

- Invent employers, titles, dates, degrees, certificates, language levels or years of experience.
- Use dashes as punctuation, exclamation marks, question marks, stock phrases ("I am writing to express my
  interest"), or too many connector words.

A letter that breaks a rule is **not sent or drafted**. It is flagged `needs_review` and appears in the Review queue.
A letter that is longer than one page is rewritten shorter once; if it still does not fit, it is flagged.

To get a different tone, edit `workspace/writing_instructions.md` and add writing samples (role
`writing_sample`) in the setup's `documents` section. To rewrite one letter from scratch:

```fish
uv run autoapp prepare --job <id> --force     # the old letter is kept as cover_letter.<time>.md
```

---

## 16. Running it on a schedule

A scheduled run is `autoapp run --non-interactive`. It never asks anything and uses `run.default_mode`
(`review` unless you changed it).

```fish
mkdir -p ~/.config/systemd/user
cp deploy/systemd/autoapp.service deploy/systemd/autoapp.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now autoapp.timer
systemctl --user list-timers             # shows the next run (08:00 by default)
journalctl --user -u autoapp -n 50       # what the last run did
loginctl enable-linger $USER             # run even while you are logged out
```

Open **Statistics** in another terminal to watch a scheduled run live. Run logs are in `workspace/logs/`.
Web forms are prepared by scheduled runs, but never filled: use **Finish forms** when you sit down.

Before you trust it: use review mode for a while, then a test run, then automatic with a low `daily_cap`.

---

## 17. Problems and fixes

| What you see | Why | What to do |
|---|---|---|
| `command not found` / "command unknown" | You are not in the project folder, or the virtual environment is not active | `cd ~/dev/auto_app` and use `uv run autoapp ...` |
| "Setup is not complete" | A scheduled or scripted run before setup finished | Run `uv run autoapp setup` in a terminal |
| The run screen does not move after the run | It waits so you can read the log | Press q or Enter |
| Dry run, but no email in your inbox | A dry run sends nothing by design | Use a **test run**, or **Review first** to get drafts |
| No drafts in Gmail | Drafts are only made in **Review first** runs. A test run (also every fresh test run) sends test emails instead | Choose **Review first** when a run asks, or `uv run autoapp draft <id>` for one job. Drafts land in `[Gmail]/Drafts` |
| Test run does nothing | Every job found is already in the history | Use a **fresh test run** |
| "AI cost limit of $3.00 per run reached" | The run spent its budget | Run again later, or raise `limits.max_cost_per_run_usd` |
| "LLM call limit ... reached" | Too many AI calls in one run | Same: run again, or raise `max_llm_calls_per_run` |
| Jobs stuck as `evaluation_failed` (often HTTP 429) | The AI provider rate-limited you; failed jobs are not retried automatically | Try one again with `evaluate --job <id> --force`, or use **Redo unsent** |
| "Another autoapp run is active in ..." (exit code 3) | A second run on the same workspace | Wait for the other one to finish, or close its terminal. Statistics still opens. A crashed run never leaves a stale lock |
| No drafts appear in your mailbox | Wrong Drafts folder, or login problem | **Tests > Check the logins**; it reports the Drafts folder found. Set `mail.sending.drafts_folder` if needed |
| An evaluation failed with "maximum context length" | A PDF job ad was read as text (fixed on 2026-10-09: PDFs are no longer read as pages and very long ads are cut) | `evaluate --job <id> --force` after the fix |
| The app says "no price known for X" | Cost limit cannot count that model | Add its price under `llm.prices` |
| "no website could be verified" for an employer | A recruiter, a directory or an unclear name | Normal. Confirm it yourself with `employers --website URL --job <id>` |
| An Arbeitsagentur job says `click through yourself` | The search for its own ad on the employer's site found nothing (see `AD SEARCH` in the log), and its apply button is built by scripts | Open it in your normal browser, follow it to the employer's form, then **paste its address** |
| A LinkedIn / Indeed / StepStone job says `apply yourself` | The employer's own site was not found, and reading the link with your login is off (or found nothing) | Paste the company's form address in **Finish forms**, or switch on company apply links with your login (section 14), or apply in your own browser with `apply.html`, then `mark <id> submitted` |
| `APPLY LINK ... asks for a login` | Company apply links with your login is on, but the app's browser is not logged in there | `uv run autoapp login linkedin` (or stepstone, indeed) once |
| The browser closed in the middle of a form | You or the site closed it | Press `q`, pick the job again from the list |
| The letter reads wrong | Style or facts | Edit `cover_letter.md`, then `render <id>`; or change `writing_instructions.md` and use `prepare --force` |
| Gmail login fails | A normal password was used | Create an **app password** and put it in `.env` |

### Where to look

- `workspace/logs/run-<time>.log`: the full log of every run
- `uv run autoapp status`: counts per status, today's sends, the review queue, cost
- `uv run autoapp list --status review`: jobs in one status
- `uv run autoapp stats --period run`: the latest run

---

## 18. Command cheat sheet

Everything also works with `-w <workspace>` in front (`uv run autoapp -w workspace/test-runs/<time> ...`).

### Everyday

| Command | What it does |
|---|---|
| `uv run autoapp` | Open the menu |
| `uv run autoapp run` | Full run, asks the mode |
| `uv run autoapp run --mode review` / `test` / `automatic` | Full run, no question |
| `uv run autoapp stats [--period run\|today\|7d\|30d\|all]` | Statistics |
| `uv run autoapp status` | Counts per status, today's sends, review queue, cost |
| `uv run autoapp list --status <status>` | Jobs in one status |
| `uv run autoapp mark <id> <status> [--note "..."]` | Change a status (`apply` and `skip` also work) |

### Trying things safely

| Command | What it does |
|---|---|
| `uv run autoapp run --dry-run` | Do everything except sending, drafting, marking mails |
| `uv run autoapp run --mode test` | Every email to yourself |
| `uv run autoapp run --mode test --fresh` | Same, from an empty history in `workspace/test-runs/` |
| `uv run autoapp run --redo [--dry-run]` | Process every unsent job again (backup first) |
| `uv run autoapp send <id> --test` | One test email to yourself |

### One step at a time

| Command | What it does |
|---|---|
| `uv run autoapp search [--profile NAME]` | Search only |
| `evaluate [--job ID] [--force]` | Evaluate new jobs, or one again |
| `prepare [--job ID] [--force]` | Make the folders, or rebuild one |
| `render <id>` | Rebuild PDFs and email after editing `cover_letter.md` |
| `draft <id> [--again]`, `drafts [--check]` | Save a draft, list drafts, find the ones you sent by hand |
| `send <id>` | Send one prepared email (asks first) |
| `add <url>`, `describe <id>` | Add a job by link, paste the text of an ad that could not be read |

### Forms and employers

| Command | What it does |
|---|---|
| `uv run autoapp forms [--job ID] [--again] [--dry-run]` | Look for application forms |
| `uv run autoapp assist <id>` | Open one job's form in the browser |
| `uv run autoapp login <linkedin\|stepstone\|indeed\|workwise>` | Log in once in the app's browser (kept for later) |
| `uv run autoapp employers [--discover] [--job ID] [--again] [--refresh-site] [--website URL] [--dry-run]` | Employer careers sites |

### Setup

| Command | What it does |
|---|---|
| `uv run autoapp setup` | The wizard |
| `uv run autoapp setup --section <name>` | Redo one section |
| `uv run autoapp setup --reset <name\|all>` | Reset one section, or back up and start over |
| `uv run autoapp reset-data [--answers] [--logins] [--yes]` | Delete all job data and keep the setup (asks first) |
| `uv run autoapp export` | Rewrite `applications.csv` (also done after every command) |

---

## 19. Where everything is stored

```
workspace/                      (private; keep it out of git)
  config.yaml                   search profiles, sources, models, limits, mail
  profile.yaml                  your data, document roles, salary
  writing_instructions.md       your instructions for the letter writer
  form_answers.md               your answers to form questions, reused in later forms
  .env                          API keys and mail passwords (never printed)
  documents/                    your CV, certificates, photo (only read, never changed)
  applications/                 one folder per prepared application
  applications.csv              the overview; edit only status and notes
  autoapp.db                    the database (jobs, evaluations, usage, employer sites)
  openrouter-prices.json        fetched model prices (refreshed daily)
  browser-profile/              the app browser's saved logins
  logs/                         one log per run
  backups/<time>/               copies made by setup resets and redo
  test-runs/<time>/             fresh test runs (delete when done)
```

Other pages with more depth:

- [Getting started](getting-started.md) · [Job sources](job-sources.md) · [AI providers and cost](llm-providers.md)
- [Cover letters](cover-letters.md) · [Application folders](application-folders.md) · [Email](email.md)
- [Assisted applying](assisted-applying.md) · [Scheduling](scheduling.md)
