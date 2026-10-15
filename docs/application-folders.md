# Application folders

When you prepare an application, autoapp creates a folder with all necessary files for review and submission. Each folder is named `application_<company>_<title>_<id>/` and contains:

### Form status in the folder name

An application that needs a web form is saved even when autoapp cannot reach the form. The folder name then
ends with what is missing, so you can see it without opening the folder:

| Ending | Meaning |
|---|---|
| (none) | email application, form found, or form not looked up yet |
| `_NOFORM` | no application form found, or only part of the way |
| `_BROWSER` | the page needs your browser (the site refuses plain requests) |
| `_LOGIN` / `_CAPTCHA` | a login or CAPTCHA stands in the way |
| `_RESTRICTED` | the form is on LinkedIn, Indeed or StepStone (or an Arbeitsagentur ad whose employer lookup has not run yet) |
| `_NOEMPLOYER` | an Arbeitsagentur job whose employer's own ad was not found: the letter is ready, but there is no application page yet |
| `_UNREACHABLE` | the page could not be loaded |

A `_NOEMPLOYER` folder shows no Arbeitsagentur application or job-ad link, plus
`employer ad not found yet: <reason>`. The employer's ad is searched again on later searches (after
`employer_careers.failure_ttl_hours` at the earliest); when it turns up, the status, the ending and the links are
refreshed. An ad hosted directly on Arbeitsagentur also requires a verified employer page. An unsuccessful
lookup does not prove that the job exists only on the board. Legacy board application links are suppressed when
the generated package links are refreshed. Automatic form filling avoids those pages; the explicit
"Open the job link in my normal browser" action can still open the saved source address manually.

`autoapp forms --job <id> --again` (or `assist`) looks again; if the result changes, the folder is renamed
(the ending is added, changed or removed) and the paths inside `README.md`, `job.md` and `apply.html` are updated.
Folders you renamed yourself are left alone. The exact status and reason are also in the `Form status` line of
`README.md` and in `applications.csv`.

## Files in each application folder

| File | Purpose |
|---|---|
| `job.md` | Full job description extracted from the ad |
| `evaluation.md` | Requirements matched against your profile, with scoring details |
| `cover_letter.md` | Generated cover letter in Markdown (editable before render) |
| `cover_letter.pdf` | Rendered PDF (DIN 5008 format, one page) |
| `cover_letter_*.md` | Timestamped backups of cover letters (created on `--force` reruns) |
| `Bewerbungsmappe.pdf` | Merged PDF: cover letter + PDF CV + available PDF certificates and work references |
| `email.eml` | Formatted email ready to send (if email channel) |
| `apply.html` | Offline copy of answers for manual form filling or platform applying |
| `README.md` | Summary with next steps, missing documents, flags for review, and "brush up" list |
| `form_answers.md` | Structured answers generated for web form questions, plus the answers you gave in `assist` |
| `filled_form.md` / `filled_form.html` | Record of each fill, and an offline copy of the filled form page (created by `assist`) |
| `CV_<lang>.pdf` or `.docx` | Copies of your CV in the ad's language |
| Additional documents | Copies of matching-language or neutral certificates, references and other documents |

Applications select one matching-language CV; alternate CVs and opposite-language supporting documents are
excluded from form uploads and email attachments, including when rebuilding an older email draft.
Email applications attach `Bewerbungsmappe.pdf` plus eligible documents not contained in that bundle. Non-PDF CVs and
certificates are attached separately, along with other available documents; paths are deduplicated. Re-rendering
preserves extra attachments whose copied files remain in the folder. A warning reports a combined attachment
size above `email.max_bundle_mb`; the existing sending limit still checks the bundle itself.

Document filenames must be distinct and must not match generated files such as `cover_letter.pdf` or
`README.md`. Preparation reports such conflicts before writing a letter, so a document cannot overwrite a
generated application file.

## Reviewing before you apply

Jobs with an Arbeitnow source keep a clickable attribution in newly generated `job.md`, README
and `apply.html`, even when another source supplied the primary ad URL. The generated
`arbeitnow_url` CSV column retains this source link separately. Only `status` and `notes` are
imported as edits; older CSV files without this new column still work.
Attribution never enters employer-facing cover letters or emails. A later source duplicate does
not rebuild previously edited application packages; its link appears in the next CSV export.

1. **Read `README.md`** for next steps, warnings, and the "brush up on" list (assumed skills)
2. **Read `evaluation.md`** to check requirement matching and the fit score
3. **Read/edit `cover_letter.md`** to verify the content and tone
4. **Run `autoapp render <id>`** to rebuild the PDF after any edits
5. **Check `email.eml`** if applying by email, or `apply.html` if the company has a web form

## Manual applying via apply.html

For non-email applications, preparation first tries to find the actual application page.
`apply.html`, README and `job.md` show the found page or last usable handoff address and a
separate form status/reason. Source URLs remain in the database; Arbeitsagentur URLs are excluded from generated
application links. Finder metadata also keeps
the original entry and the last reached address, so a stale link can be retried.

```bash
autoapp forms --dry-run             # Inspect unresolved prepared forms; no changes or requests
autoapp forms                       # Backfill only prepared non-email jobs without a result
autoapp forms --job <id> --again     # Retry from the original entry, even if previously found
autoapp forms --audit [--json]       # Read-only report of the saved links; changes nothing
```

Refreshing links changes only generated application-link/status blocks, not your letters,
motivation, uploaded documents or other package text. An ambiguous or missing generated
block produces an explicit error rather than rewriting your edits.

`applications.csv` has separate generated `form_status` and `form_note` columns.
Only `status` and `notes` are imported as user edits: edit `notes`, save the CSV, then run
`autoapp forms`; the note is imported before links/status are refreshed and exported.
Changing `form_note` in the CSV does not change your saved notes. Submitted and closed jobs
are not selected for backfill.

If the company requires a web form or if you prefer to apply manually:

1. Open `apply.html` in your browser
2. Read the form field labels and your pre-filled answers
3. Go to the company's application form
4. Copy the answers field by field (use the copy buttons in `apply.html`)
5. Attach `Bewerbungsmappe.pdf` if requested
6. Submit manually

## Auto-send workflow (email)

If the job is eligible for auto-send and you've enabled it:

1. autoapp will automatically send `email.eml` to the address in the ad
2. A copy is saved in your email account's Sent folder
3. The job status changes to `sent` with a timestamp
4. Export the CSV to see all sent applications

**Before enabling auto-send**, run `autoapp run --dry-run` for a few days to review generated applications and coverage letters without sending any emails.

## Commands

```bash
# Prepare a single job (generate all files)
autoapp prepare <job-id>

# Force regenerate (overwrite and backup old cover_letter.md)
autoapp prepare --force <job-id>

# Rebuild PDF after editing cover_letter.md
autoapp render <job-id>

# Send email manually (asks for confirmation)
autoapp send <job-id>

# Send the same email to yourself instead of the employer (nothing is recorded)
autoapp send <job-id> --test

# Mark application as submitted or take other actions
autoapp mark <job-id> submitted
```

## File structure example

```
application_mueller-soehne-gmbh_software-entwickler_a1b2c3/
├── README.md
├── job.md
├── evaluation.md
├── cover_letter.md
├── cover_letter.pdf
├── cover_letter_2026-10-08_14-32-15.md
├── Bewerbungsmappe.pdf
├── email.eml
├── apply.html
├── form_answers.md
├── CV_de.pdf
├── CV_en.pdf
├── Arbeitszeugnis_2022.pdf
├── Diplom.pdf
└── writing_sample_de.md
```
