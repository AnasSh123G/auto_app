# application-form-finding Specification

## Purpose

Takes the user from a job ad to the intended application's form where safe navigation is possible, preserves a restartable entry point, and fills only that application's controls on the user's request. Unsupported or blocked flows are handed over with an explicit reason.

## Requirements

### Requirement: Resolve the form during prepare
For non-email applications without a reusable result, prepare SHALL resolve the application link before rendering the package. The initial entry SHALL be the stored application URL, otherwise the evaluated application URL, otherwise `job_url`. Expected navigation failures SHALL preserve preparation using the last usable link. Email applications SHALL cause no finder requests. Prepare SHALL NOT launch a browser, fill, upload or submit.

#### Scenario: Ad leads to a form
- **WHEN** a job's `application_url` is an employer page whose "Jetzt bewerben" link leads to a page with a file upload and name and email fields
- **THEN** after prepare `application_url` is that form page, `job_url` is unchanged, and `apply.html` links to the form

#### Scenario: Email application
- **WHEN** the application channel is `email`
- **THEN** the finder is not run and no extra request is made

#### Scenario: Network failure
- **WHEN** the employer site cannot be reached
- **THEN** the package is built with the last usable link or the entry point and the outcome is `unreachable` with the network reason

#### Scenario: Application URL exists only in evaluation
- **WHEN** the job has no stored application URL but its evaluation identifies an employer application address
- **THEN** that address is used as the entry point before falling back to the ad

### Requirement: Unexpected errors remain explicit
Unexpected finder, storage or configuration errors SHALL be reported as errors through the command's normal error reporting, not disguised as an unreachable website. Optional model failures SHALL be reported and permit deterministic navigation or a partial outcome; authentication failure SHALL stop further finder model calls in that run. A failed outcome write SHALL NOT be reported as a successfully saved result.

#### Scenario: Storage error
- **WHEN** saving a finder result fails
- **THEN** the command reports the storage error and does not claim that the link or status was saved

### Requirement: Bound and validate every navigation transition
Each attempt SHALL allow four navigation transitions after its starting page, at most five HTTP page requests. HTTP redirects, meta refreshes, forwarding links and fetched application frames SHALL consume this same budget. Every target SHALL be checked before requesting it; loops SHALL stop. HTTP finding SHALL only GET pages, without form submission, button activation or script execution.

#### Scenario: Two links reach the third page
- **WHEN** the ad page links to an employer detail page, which links to "Jetzt bewerben", which is a page with a form
- **THEN** two transitions reach the third page, whose address is saved with `found`

#### Scenario: Hop budget exhausted
- **WHEN** four links have been followed and the page still has no form
- **THEN** the finder stops, saves the last page reached, and records `partial` with the reason "no form within 4 steps"

#### Scenario: HTTP redirects consume the budget
- **WHEN** the starting page returns a redirect chain longer than four transitions
- **THEN** at most five pages are requested, the sixth is not requested, and the outcome is `partial`

#### Scenario: Redirect loop
- **WHEN** a redirect or apply link returns to an address already visited in this attempt
- **THEN** no repeated request is made and the outcome is `partial` with the loop reason

### Requirement: Identify the intended application
A real application SHALL have application-specific context and a file-upload control or name and email controls. Newsletter, contact, search and login forms SHALL NOT qualify. Known company or vacancy-reference conflicts SHALL stop with `partial`; insufficient context SHALL NOT be labelled `found`. A blocking login or challenge SHALL take precedence over form detection; unrelated header sign-in links SHALL NOT count as walls.

#### Scenario: Form on the first page
- **WHEN** the starting page already contains an application form for the intended vacancy
- **THEN** no link is followed and the starting address is kept as the form address

#### Scenario: Newsletter form beside an apply link
- **WHEN** the ad contains a newsletter form with name and email inputs and a separate application link
- **THEN** the newsletter is ignored and the application link remains eligible

#### Scenario: Different vacancy
- **WHEN** an apply link reaches a form identifying a different vacancy reference
- **THEN** the outcome is `partial`, the conflict is explained, and no fields are filled

### Requirement: Detect rendered and embedded applications
Browser finding SHALL apply the same application-context rules to usable rendered controls, including applications without a `<form>` and controls in allowed frames. It SHALL wait a bounded time for rendering before declaring no form. Filling SHALL be scoped to the identified application controls, never unrelated controls elsewhere on the page or in another frame.

#### Scenario: Application in an iframe
- **WHEN** the employer page embeds an allowed ATS application frame
- **THEN** browser finding detects the frame's application controls and filling targets those controls only

#### Scenario: JavaScript application without a form element
- **WHEN** application-specific controls appear after rendering without a `<form>` wrapper
- **THEN** browser finding recognizes them and records `found` rather than continuing past them

### Requirement: Ambiguous links have a bounded model fallback
Without a clear apply link, the finder SHALL either stop with an explained partial outcome or ask `form_find` to select one offered safe candidate or none. Unlisted selections SHALL be rejected. At most one model call per available transition and four per attempt SHALL be made, including failed calls; no additional retries are allowed. All calls SHALL count toward the shared usage meter and run limit.

#### Scenario: Model chooses a listed link
- **WHEN** a page has an ambiguous "Zur Stelle" candidate and the model selects its offered identifier
- **THEN** the corresponding safe link is followed; privacy and other excluded links are not candidates

#### Scenario: Model invents an address
- **WHEN** the model answers with an address that is not on the page
- **THEN** it is ignored and the finder stops with `partial`

#### Scenario: Shared call limit exhausted
- **WHEN** an ambiguous page is reached and the shared model-call limit is exhausted
- **THEN** no model request is made and the finder records `partial`, not `unreachable`

### Requirement: Link-selection prompts contain only public information
Link-selection prompts SHALL contain only public job title, company/reference hints and sanitized candidate texts/addresses. CV, profile, letters, prepared answers, personal data and tracking, authentication or session tokens SHALL NOT be sent. Selections SHALL use offered candidate identifiers mapped locally to the original links. Page-provided text SHALL be treated as untrusted data, never instructions.

#### Scenario: No personal data in the call
- **WHEN** a candidate URL contains an email address or session token
- **THEN** that value is omitted from the prompt while any allowed selection is mapped to its original address locally

### Requirement: Respect blocks and restricted sites
HTTP finding SHALL use the tool's own User-Agent and no browser cookies. A 403, 429 or 999 SHALL disable the responding host for the run and record `needs_browser`, with no automatic retry or browser fallback during prepare. HTTP detail reading and finding SHALL share host blocks, randomized per-host pacing and an address cache, including redirects. Each address SHALL be fetched at most once per run.

#### Scenario: Aggregator refuses the tool
- **WHEN** Adzuna's tracking link answers 403 to the tool
- **THEN** the finder makes no further HTTP request to that host in this run, records `needs_browser`, and the package retains the last usable page, or the ad if none was reached

#### Scenario: Host already blocked during detail reading
- **WHEN** detail reading has already received a 403 from a host needed by the finder
- **THEN** the finder sends no further request to that host and records `needs_browser`

#### Scenario: No browser imitation
- **WHEN** the finder sends any request
- **THEN** the User-Agent is the tool's own and no cookies from the user's browser are used

### Requirement: Guard restricted targets before any request
Finder and assist automation SHALL NOT request configured restricted domains or their subdomains, including initial URLs, HTTP or browser redirects, frames, popups and page resources. Defaults SHALL be linkedin.com, indeed.com, indeed.de and stepstone.de. A blocked navigation target SHALL yield `restricted` and a reason; assist SHALL offer the local helper without visiting that target.

#### Scenario: Restricted redirect
- **WHEN** an allowed tracking URL redirects to linkedin.com
- **THEN** the Location is inspected but LinkedIn is not requested, and the outcome is `restricted`

#### Scenario: Restricted frame or popup
- **WHEN** an employer page attempts to load a restricted frame or popup
- **THEN** its request is prevented, its controls cannot reach detection, the model or filling, and the user is told why

#### Scenario: Browser interception bypass
- **WHEN** a redirect chain or service-worker-owned request would bypass the browser's normal request guard
- **THEN** the request is prevented before reaching the restricted host, or that automatic navigation is refused with a manual handoff

### Requirement: Stop at logins, accounts and captchas
Blocking login/account-creation walls SHALL yield `login_required`; blocking CAPTCHA or anti-bot challenges SHALL yield `captcha`. The reached wall address SHALL be saved for manual handoff. A CAPTCHA widget used only for final submission SHALL NOT prevent detecting or filling the application, but SHALL remain untouched and listed for the user. The tool SHALL NOT log in, create accounts or solve challenges.

#### Scenario: Login wall
- **WHEN** the "apply" link leads to a page with an email and password input and the text "Anmelden"
- **THEN** the outcome is `login_required`, the address of that page is saved, and no input is made

#### Scenario: Submission-time CAPTCHA
- **WHEN** an otherwise usable application contains a reCAPTCHA widget for final submission
- **THEN** the application is `found`, safe fields can be filled on request, and the CAPTCHA is left for the user

### Requirement: Never submit or grant consent during navigation
Browser navigation SHALL stop before filling and SHALL NOT activate submit-capable controls, controls associated with a form, or ambiguous scripted actions. Apply-labelled controls qualify only as navigation, never submission. Consent, terms, legal declarations and CAPTCHA controls SHALL remain untouched. Cookie banners SHALL only be dismissed through unambiguous rejection of optional cookies; otherwise navigation SHALL hand over.

#### Scenario: Apply-labelled submit control
- **WHEN** a button labelled "Jetzt bewerben" has submit type, an implicit submit type or a form association
- **THEN** it is not activated and the user is told to continue manually

#### Scenario: Cookie banner
- **WHEN** a banner offers "Accept all" and an unambiguous reject-optional-cookies action
- **THEN** only the rejection action is eligible, and if no safe rejection exists the tool hands over without granting consent

### Requirement: Outcome is stored and visible
The application SHALL store `form_entry_url`, `form_reached_url`, `form_status`, `form_checked_at` and a one-line `form_note`. Status SHALL be one of `found`, `partial`, `needs_browser`, `login_required`, `captcha`, `restricted`, `unreachable` or `none`. The original entry and `job_url` SHALL be preserved; `application_url` SHALL hold the form or last usable handoff address. Statuses SHALL appear in packages, CSV, statistics and run summaries.

#### Scenario: No usable entry point
- **WHEN** neither the job nor its evaluation provides a usable starting URL
- **THEN** no request is made and the outcome is `none` with the missing-entry reason

#### Scenario: No application evidence
- **WHEN** a reachable starting page has neither application context nor an eligible continuation
- **THEN** the outcome is `none`; an incomplete application path instead yields `partial`

#### Scenario: Summary line
- **WHEN** a run prepared 11 form applications with 7 found, 3 `needs_browser` and 1 `login_required`
- **THEN** the summary shows "forms: 7 found, 3 need your browser, 1 login required"

### Requirement: Persisted links stay consistent and restartable
Every saved result, including browser discoveries, SHALL refresh `apply.html`, README, `job.md` and CSV without rebuilding letters/documents or overwriting user edits. CSV SHALL expose generated form status/note separately from editable user notes. Normal preparation SHALL reuse `found`; explicit `--again` SHALL restart from the preserved entry for any status, including `found`. Assist SHALL allow recovery when a saved form URL expires.

#### Scenario: Not repeated
- **WHEN** a later prepare encounters a previously found application without an explicit retry
- **THEN** its result is reused without a finder request

#### Scenario: Repair an expired or incorrect found URL
- **WHEN** the user runs `autoapp forms --job <id> --again` for a found application
- **THEN** resolution restarts from its original application entry, not the stale form, and updates all link/status surfaces

#### Scenario: Browser discovery and user notes
- **WHEN** assist saves a newly reached form and the user has edited CSV notes or application text
- **THEN** the new link/status appears on all affected surfaces while user notes and prepared text remain unchanged

### Requirement: Resolve existing applications on demand
`autoapp forms` SHALL resolve prepared non-email applications without a status, optionally limited by `--job <id>`; `--again` SHALL include every status in that selection. It SHALL print each outcome and saved address. "Finish forms" SHALL offer backfilling first with no as default. Direct assist SHALL resolve a missing status for that job before opening a browser. Submitted or closed applications SHALL NOT be backfilled.

#### Scenario: Backfill
- **WHEN** the user runs `autoapp forms` and three prepared applications have no status
- **THEN** each gets an outcome, and their `apply.html` links point to the resolved form pages where found

#### Scenario: Decline menu backfill
- **WHEN** the user declines the "Finish forms" backfill offer
- **THEN** no batch resolution runs; opening an individual assist session can still resolve that selected job

### Requirement: Form-resolution dry run is read-only
`autoapp forms --dry-run` SHALL list only selected starting addresses and planned changes. It SHALL make no network or model requests and no changes to database, run history, package files, CSV or user notes.

#### Scenario: Dry run
- **WHEN** the user runs `autoapp forms --dry-run`
- **THEN** it lists the addresses it would start from and changes nothing

### Requirement: Assist authorizes a separate visible-browser attempt
Explicitly starting assist SHALL authorize a separate attempt in visible automated Chromium with its local persistent profile, not the user's ordinary browser. It SHALL open the saved address and safely navigate incomplete paths, using four transitions per attempt. If a saved address has expired, at most one fresh attempt from the entry SHALL run. Browser refusal SHALL stop with a reason and manual handoff, without evasion.

#### Scenario: Aggregator needs the browser
- **WHEN** the user runs `assist` for an Adzuna job whose status is `needs_browser`
- **THEN** a separate visible-browser attempt starts and, if accepted by the site, safely follows the application path and saves any found form

#### Scenario: Browser also blocked
- **WHEN** the visible browser encounters a 403 or blocking anti-bot challenge
- **THEN** no alternate identity, repeated navigation or challenge-solving is attempted, and assist reports the block and hands over

#### Scenario: Restricted
- **WHEN** the job's address is on linkedin.com
- **THEN** only `apply.html` is opened and no navigation or filling is attempted

### Requirement: Complete the Adzuna email-free application path
On an accepted aggregator page, assist SHALL recognize an unambiguous continuation without email as navigation, without entering an email or opting into alerts. It SHALL follow allowed popups/new tabs within the same transition budget and save the employer form. Unsafe or ambiguous continuation controls SHALL cause manual handoff, not speculative clicks.

#### Scenario: Full CBC fixture path
- **WHEN** the Adzuna fixture for ad 5905884599 opens an email-or-continue overlay, offers a safe no-email continuation to the CBC details page, and that page links to "Jetzt bewerben"
- **THEN** assist reaches the application corresponding to `https://cbc-cologne.onapply.de/bewerbung/346291.html`, saves `found`, and stops before filling; no email is entered or sent, no consent granted and no application submitted

#### Scenario: Apply opens a new tab
- **WHEN** a safe application action opens the allowed employer page in a new tab
- **THEN** navigation and subsequent filling track that tab, retain the original entry and use the same transition budget

### Requirement: Fill with job-specific answers
Assist SHALL fill only when the user presses `[f]`, including repeated triggers on manually advanced steps. Only the identified application's controls SHALL receive prepared values/files. Motivation SHALL be job-specific; enabled generated answers SHALL use this job, evaluation and known profile facts. Unknown facts SHALL remain empty for every control type. User-set values, consent, terms and CAPTCHA controls SHALL remain untouched.

#### Scenario: Free-text question
- **WHEN** the user presses `[f]` and the form asks "Warum möchten Sie bei uns arbeiten?"
- **THEN** an answer written for this job and company is filled and flagged "check this generated answer"

#### Scenario: Unknown fact
- **WHEN** a text, select or radio field asks for a driving licence class that the profile does not state
- **THEN** it is left unset and listed under "needs your input", even if optional or an offered option seems plausible

#### Scenario: Navigation does not prefill
- **WHEN** assist reaches the application form
- **THEN** it saves the address and offers `[f]`; no personal values or files are transferred before that trigger

### Requirement: Every generated answer and unresolved field is reported
After filling, assist SHALL list each generated field and value for checking, regardless of model review warnings. It SHALL list unsupported or skipped application fields, including optional fields, under "needs your input", and report model or field-action errors. Required empty fields, consent and CAPTCHA steps SHALL remain visibly identified for manual completion.

#### Scenario: Generated answer without warnings
- **WHEN** a generated answer passes its truth/style checks without warnings
- **THEN** its field label and filled value are still displayed as a generated answer to check

#### Scenario: Optional unsupported field
- **WHEN** an optional application field cannot be answered from known facts
- **THEN** it stays empty and is included in the unresolved-field report
