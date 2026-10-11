# Assisted form filling

autoapp can open employer job application forms in a persistent browser and fill in fields automatically while you watch. You remain in control: you review every field before the form is submitted.

## Starting a filled application

Run the assist command with a prepared job ID:

```bash
autoapp assist <job-id>
```

- Resolves a missing saved form result for this job, then opens/revalidates its saved application address
- Launches visible **automated Chromium**, not your ordinary browser; it may still be refused by a site
- Follows clear, structurally non-submitting application continuations, including a safe
  “continue without email” link and tracked new tabs, up to four transitions
- Stops at the application before filling; navigation never enters an email, subscribes,
  grants consent, uploads a document or submits
- Prompts you in the terminal with options: `[f] fill / [h] open apply.html / [s] submitted / [q] quit`

An expired saved link permits one fresh attempt from the original entry. Login, CAPTCHA,
HTTP refusal or an ambiguous/unsafe control stops automation with a reason. No identity
change, repeated block retry or challenge evasion is attempted. Explicit “reject optional
cookies” controls are permitted; accepting optional cookies or consent is not. Otherwise,
continue manually. When the page contains no identifiable application, `[f]` also refuses
to fill unrelated controls.
Untracked, script-created popups are refused before their first request; clear new-tab links
are opened in a blank tab whose redirect interception is installed before navigation.
Once automatic navigation has stopped, the browser is yours: the real page stays open, you can
click anything (including apply buttons that open a new tab), and `[f]` fills the newest tab.
The terminal says in plain words what happened and what to do next.

Preparation uses plain GETs, never a browser. `autoapp forms` backfills old packages;
`autoapp forms --again --job <id>` restarts at the original entry for any saved status.
Every form-finding run also saves a trace in the database table `form_attempts`: the strategy (`http`;
`browser` and `ats` are reserved for later), the pages visited with the reason for each (`entry`, `redirect`,
`link`, `model choice`), the final status, a short note and the duration. `form_note` stays the one-line summary.

### Audit of the saved links

`autoapp forms --audit` reads, and never writes, the prepared web-form applications (the database is opened
read-only; no requests are made, no setup is needed). It reports the distribution by form status, the links on job
boards (the built-in directory hosts plus `assist.restricted_domains`) by host, the split by source (a job found by
several sources counts for each) and the failures grouped by cause (from the newest `form_attempts` trace, else
`form_note`). `--json` prints the same as JSON with a stable shape:

```json
{
  "prepared": 42,
  "by_status": {"found": 18, "partial": 9, "unchecked": 3},
  "on_job_board": {"total": 6, "by_host": {"arbeitsagentur.de": 5, "linkedin.com": 1}},
  "by_source": {"arbeitsagentur": {"total": 20, "found": 6}},
  "failures": [{"status": "partial", "cause": "no usable application continuation", "count": 5}]
}
```

`unchecked` means no form status yet. The quick test (`autoapp test`) prints this audit for your real workspace
after its checklist.

Finding a page does not guarantee that its session will remain valid: filling happens when you
sit down to finish it.

## Finish forms

The menu entry **Finish forms** (also offered at the end of an interactive run) lists every
prepared form application, the ones the app can fill first. Each line says in brackets what
it needs, for example `[form found]`, `[click through yourself]`, `[needs your login]` or
`[apply yourself]`. Pick one:

- **Form found or not checked yet:** the browser opens and the app looks for the form.
- **Needs a step from you** (no clear apply button, login, robot check, blocked quick check):
  the reason is shown first, then you choose to open it in the app's browser, open the link in
  your normal browser, mark it as submitted or mark it as skipped.
- **Apply yourself** (LinkedIn, Indeed, StepStone, or the ad is gone): the app's browser is not
  opened. You can open the link in your normal browser, mark it submitted or skip it.

After every job, including one you quit or whose browser you closed, you are back at the list,
so you can open the same job again. Choose **Done** (or **Not now** after a run) to leave.

## Employer postings found from alert emails

When employer discovery (see [Job sources](job-sources.md#employer-careers-pages-optional-off-by-default)) finds the
employer's own posting for a LinkedIn, Indeed or StepStone alert, that posting becomes the job's application link
before evaluation (the platform link stays as the ad link). It is only a verified posting, **not a found form**:
preparation still runs the form finder from it with the usual four transitions, and `assist` still starts only
when you ask. Discovery itself never opens a browser, fills, uploads, accepts consent or submits anything.
`autoapp employers --discover --dry-run` shows what would be looked up without any request.

## First-time logins

The browser maintains a persistent profile in `workspace/browser-profile/`. The first time you visit an employer's site:

1. The browser opens normally (not headless)
2. If login is required, log in once
3. The browser remembers your session for future runs
4. Your login session is stored **locally** and never sent to autoapp or any service

Once logged in on a site, future `assist` runs will have access to your account and any saved preferences.

## What gets filled automatically

### Auto-filled
- **Personal data:** first name, last name, email, phone, address, postal code, city
- **Salary expectations:** yearly salary target (from your profile and the ad)
- **Availability:** start date / notice period (from your profile)
- **Finding the form:** with `assist.navigate_mode: model` (the default) the `form_find` model chooses each click
  from the job page to the application form: it sees the page's title, a short text excerpt and a numbered list
  of what can be clicked, and picks one or stops (login, robot check, no online application). It also handles
  apply buttons that only work with JavaScript and apply buttons that open a new window. The rules stay in code:
  the list never contains anything that submits, sends, signs in or creates an account, nothing is typed,
  restricted sites and background form posts stay blocked, personal data from your profile is taken out of what
  the model sees, and the app's own form check decides that the form was reached. After `navigate_max_steps`
  clicks (default 8) the app hands over to you. About one model call per click; without a model the link rules are
  used (`navigate_mode: rules` always uses them).
- **Who decides what goes where:** with `assist.fill_mode: model` (the default) the `form_fill` model reads the
  form's fields together with your CV, profile and saved answers (`form_answers.md`) and chooses a value for each.
  A value is only used when the model quotes a phrase that really is in your material, the value itself comes from
  that material (a date or phone number may be written another way, but nothing worked out or made up), a chosen
  option is one of the field's own options, and it fits the field's length. When the model skips a field your
  profile answers, your profile's value is used; your prepared motivation text is kept as it is. Values that differ
  from your profile, or that your profile does not have, are listed for you to check. The fixed answers (LinkedIn,
  no referral, never worked there), uploads and consent boxes are never left to the model. Set `fill_mode: rules`
  for the old behaviour (rules first, the model only for fields they cannot place), or `llm_answers: false` for no
  model at all. Your CV text is sent to the model for each fill, the same as when the letter is written.
- **Uploads:** your CV (matched by language), cover letter, certificates (if requested), Arbeitszeugnisse (role `work_reference`) for a field that asks for them, and `other` documents for fields like "Andere" / "Other documents". A single-file field gets only the first matching file
- **Free-text answers:** job-specific motivation generated by LLM on `[f]` (checked against
  style rules and length limits); factual questions without a known answer stay empty
- **Select/radio options:** only choices supported by known candidate facts; matching an
  offered option alone is not sufficient evidence

### Never filled (safety)
- **Submit buttons:** you must click Submit manually
- **Consent/privacy checkboxes:** these are never touched, even if unchecked
- **CAPTCHAs:** autoapp cannot solve these; you fill them manually
- **Restricted domains:** LinkedIn, Indeed, StepStone and Indeed.de forms are blocked (require manual form or Easy-Apply)

## Restricted domains

These sites are not supported for automatic form filling:

- `linkedin.com` (LinkedIn Easy-Apply)
- `indeed.com` (Indeed Apply form)
- `indeed.de` (Indeed Germany)
- `stepstone.de` (StepStone direct apply)

For these sites, use the `apply.html` page to copy your answers and fill the form manually.
Assist opens **only this local helper**, not the restricted website. Restricted subdomains,
redirect destinations, popups, frames and resources are blocked before requesting them.

## Terminal keys during filling

```
[f]   Fill the form now showing (the newest tab; or the next step if multi-step)
[o]   Open the job page again in this tab (if it shows an error or looks offline)
[h]   Show my prepared answers (apply.html) to copy by hand
[s]   Mark the application as submitted
[q]   Back (asks whether you submitted it)
```

- **After `[f]` fill:** Review every field. Green = filled, red = empty and required.
  The terminal lists every generated field/value, even without model warnings, and skipped
  optional fields or field/model errors under “needs your input”. Existing user-set values
  are preserved. Only the identified application is mapped, uploaded to and highlighted,
  including an allowed embedded application frame.
- **Multi-step forms:** Press `[f]` again after completing step 1 to fill step 2
- **After submission:** Press `[s]` to mark the job status as `submitted` and close

## Multi-step forms

If a form has multiple steps:

1. Press `[f]` to fill the first page
2. Review fields and click "Next" in the browser
3. Press `[f]` again to fill the second page
4. Repeat until the final page
5. Review and click Submit in the browser
6. Press `[s]` in the terminal to confirm

## Manual smoke test on a real employer ATS

Before trusting assisted filling on a live employer site, test it on a real job posting:

1. **Prepare a test job:**
   ```bash
   autoapp prepare <test-job-id>
   ```

2. **Run assisted fill:**
   ```bash
   autoapp assist <test-job-id>
   ```

3. **Verify:**
   - [ ] Browser opens to the application form
   - [ ] Before `[f]`, personal data and uploads are untouched
   - [ ] After `[f]`, personal data fields (first name, email, phone, etc.) are filled correctly
   - [ ] Salary field matches your expectation (if shown)
   - [ ] CV is matched by language
   - [ ] Certificates are listed if requested
   - [ ] Free-text fields contain generated answers (not empty)
   - [ ] Consent checkboxes remain **unchecked** (auto-fill does not tick them)
   - [ ] All fields are highlighted (green = filled, red = empty and required)
   - [ ] No submit button is clicked by autoapp
   - [ ] Terminal correctly reports the fields as filled

4. **Do not submit** on the real employer's form unless you want to apply

If any field is wrong (e.g., wrong CV language, broken mapping), continue manually using
`apply.html` and disable `assist.llm_answers` if generated answers are not appropriate.

## Commands

```bash
# Prepare a job (creates application folder)
autoapp prepare --job <job-id>

# Open browser and fill form
autoapp assist <job-id>

# Mark the job as submitted (after you manually click Submit)
autoapp mark <job-id> submitted

# Review what was filled
autoapp render <job-id>  # Rebuild the PDF after editing the letter
```

## Troubleshooting

**Navigation stops or the employer page appears blank:**
- The terminal says why it stopped and what to do. The real page stays open so you can click
  through yourself; only when the page is blank or a browser error does a local
  `navigation_handoff.html` page explain it instead.
- Use `[h]` to open `apply.html`, or continue at the displayed address in your ordinary browser.
- If you close the tab, `[f]` opens it again. If you close the whole browser window, press `[q]`
  to go back to the list and open the job again.
- Background non-GET requests are blocked during automatic navigation, but are not
  misreported as an application submission. Actual non-GET page navigation remains refused.

**Form didn't fill or filled incorrectly:**
- The form may have unexpected structure (nested iframes, non-standard labels)
- Manual filling via `apply.html` is always available as a fallback

**"Restricted domain" error:**
- The form is on LinkedIn, Indeed or StepStone
- Use `apply.html` to copy your answers and fill manually
- These sites require their own apply flows, which autoapp does not automate

**Browser session expired:**
- The login session may have timed out
- Press `[q]` to quit, then run `autoapp assist <job-id>` again
- Log in manually in the browser window (it will be remembered for the next run)
