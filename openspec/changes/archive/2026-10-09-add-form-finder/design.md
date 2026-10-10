# Design

## Context

See proposal.md for the motivating application flow. Today packages prefer the job's `application_url`, then the evaluation's URL, but assist and CSV only use the stored job URL. Assist uses visible Playwright Chromium with an app-managed persistent profile and waits for `[f]` before filling.

`HttpFetcher` identifies itself honestly and remembers host blocks, but its client follows redirects internally. Checking only the initial URL cannot enforce restricted targets, hop budgets or pacing on redirect destinations. Detail fetching currently creates its own fetcher, so state is not shared with preparation.

Recorded October 2026 probes found that the CBC employer/form pages accepted the tool's User-Agent, while an Adzuna tracking link returned 403; the record also reports headless Chromium being blocked. These observations are not a guarantee that visible automation will be accepted. The deterministic CBC fixtures and the user-authorized live check serve different purposes.

The current filler collects every page/frame control, accepts model-selected options without evidence that the candidate has that fact, and only prints generated-answer review warnings. Navigation therefore needs scoped detection and related filling/reporting changes, not merely a different URL.

## Goals / Non-Goals

**Goals:** bounded, restartable navigation; one consistent application link across outputs; conservative handling of ambiguous controls; scoped, user-triggered filling with truthful and visible answers.

**Non-Goals:** identity spoofing, automatic browser retries during prepare, solving challenges, creating accounts, automated login, optional-cookie consent, submission, restricted-domain form automation, a general crawler or durable saved filled sessions.

## Decisions

### 1. Single-response HTTP transport with shared run state

Extend the existing HTTP fetcher with an explicit single-response GET path that disables implicit redirects, including for injected clients. The finder follows Location headers itself, validating the next target and budget before calling that path. Preserve the existing detail-fetch interface and extraction behavior, but use the same controlled redirect transport and run-owned pacing, block memory and response cache. Close the owned client when the run context closes.

Cache responses and expected failures by request address; record redirect responses as well as their destinations. A block belongs to the actual responding host, not the original redirector. A host blocked during detail reading must not be requested again during form finding. Normal HTTP state sharing does not change the separately configured platform-browser detail reader.

Four transitions after the initial page permit at most five HTTP page requests. HTTP redirects, meta refresh, forwarding links and explicitly fetched application iframe targets consume that same budget; no fifth transition or revisited address is requested. A cache hit avoids a request but still consumes a navigation transition. Browser attempts have the same transition budget; assets do not count as page transitions but still pass domain guards.

Alternative rejected: wrapping the existing auto-following `fetch()` in a four-iteration loop. Its hidden requests bypass every relevant guarantee.

### 2. Pure analysis with normalized application context

Keep `formfinder/detect.py`, `links.py`, `finder.py` and `store.py`. Pure detection/ranking consumes normalized control descriptions, surrounding application text and public job identity hints. Static HTML and live DOM adapters share these rules rather than sharing `page.content()` alone.

Detection needs application context plus an upload or name/email controls. Reject newsletter, contact, search and login candidates; stop on known company/reference conflicts or insufficient application evidence. Prefer the intended vacancy's apply action over a generic careers index. A blocking login/challenge wins over form detection; unrelated header sign-in links and final-submit CAPTCHA widgets do not become walls.

The browser adapter waits up to ten seconds for usable controls, examines allowed frames and applications without `<form>`, and returns the selected application scope for mapping, model prompts and filling. Static HTML can follow an application iframe within its budget; when rendering or scope is uncertain, record `partial` for browser/manual handling. Preserve an employer wrapper URL when opening an iframe URL directly would lose required context.

Alternative rejected: treating any file input or name/email pair as enough, or inspecting only the top-level HTML. Both produce false positives or miss supported iframe forms.

### 3. Deterministic ranking and a bounded, private model fallback

Rank safe links using German/English apply text, accessible labels, titles and paths; exclude privacy/legal/share links, restricted targets, unsupported URL schemes and visited addresses. Recognize an explicit no-email continuation in an aggregator overlay separately from application submission. Only when deterministic ranking is inconclusive and there are 2 to 12 safe candidates may `form_find` choose an offered candidate ID or none.

Send public job title/company/reference and sanitized candidate descriptions/addresses only. Strip personal values and tracking/authentication/session tokens; retain the original address only in the local ID-to-link mapping. Treat page text as untrusted data. If a candidate cannot be described safely, exclude it from the prompt.

Add `form_find` throughout task typing, configuration, provider/key resolution, usage labels and setup/docs. Without its own provider/model override it inherits the resolved evaluate provider/model, including evaluate overrides; default effort is low unless explicitly overridden for form_find. A single call is allowed for each available transition, at most four per attempt, including failed calls, with retries disabled for this task. Meter before/after calls as usual.

Invalid/unlisted selections, exhausted budget and expected model errors produce an explained `partial` when heuristics cannot continue. Report model failures; an authentication failure disables further finder model calls for that run. Do not mislabel them as website failures. Alternative rejected: unrestricted model-driven browsing or retries that silently exceed the advertised budget.

### 4. Separate entry, reached address and outcome

Migration v5 adds nullable `applications.form_entry_url`, `form_reached_url`, `form_status`, `form_checked_at` and `form_note`. Initialize the entry once from job application URL, evaluation application URL, then ad URL, before replacing anything. `job_url` and canonical identity remain unchanged.

`form_reached_url` identifies the last reached page, including a login/challenge wall. `jobs.application_url` remains the compatibility address used to open the found form or last usable handoff page; on network/HTTP refusal retain the last usable page, falling back to the entry. A restricted target is not treated as reached. Do not save a local helper URL as the employer address.

Outcomes are `found` (identified application), `partial` (incomplete/ambiguous application path), `needs_browser` (HTTP/browser refusal), `login_required`, `captcha` (blocking challenge), `restricted`, `unreachable` (expected network/non-block HTTP failure), or `none` (no usable entry or no application evidence/continuation). Notes explain the distinction.

Reuse `found` during normal prepare. `forms --again` retries any selected status from the original entry, including `found`; each new command run gets fresh HTTP state. Assist checks the live saved address and can start at most one fresh entry attempt when that address has expired or ceased to be a form. Recognized login/challenge/block walls hand over instead of automatically restarting.

Alternative rejected: overwriting the only application entry or considering found links permanent. ATS session URLs and mistaken resolutions need an explicit repair route.

### 5. One targeted refresh path for persisted results

Use one persistence/package-refresh helper from prepare, forms and assist. Prepare stores the result before rendering; backfill/browser discoveries refresh only generated application-link/status sections in `apply.html`, README and `job.md`, leaving user notes, letters, motivation, PDFs and other prepared content intact. New templates should delimit those generated sections; legacy backfill must recognize their existing generated links rather than replacing whole user-edited files. Unrecognized layouts or write failures are explicit synchronization errors, not claimed success.

Add read-only `form_status` and `form_note` CSV columns instead of appending machine text to editable `notes`. Update the query/export while keeping status/notes import and exported-note comparison unchanged. Retrying replaces generated status text, never accumulates it into user notes. Include finder outcome events, run summary counts and statistics, including browser discoveries.

Catch only expected navigation/model failures as nonfatal resolution outcomes. Unexpected parsing/configuration/storage errors use repository-standard error events/logging and command error reporting. A failed save cannot be announced as a saved result. Alternative rejected: a broad exception handler that labels every bug `unreachable`.

### 6. Prepare, backfill and dry-run behavior

Prepare invokes the finder before package rendering for non-email applications without a reusable result. It performs no browser launch, filling or uploads. Test mode behaves like real preparation, including bounded GETs, while retaining existing email-recipient safeguards. Existing `run --dry-run` preparation semantics remain unchanged; the dedicated `forms --dry-run` below is stricter.

`autoapp forms [--job id] [--again] [--dry-run]` selects prepared, non-email applications; exclude submitted/closed jobs. Normally select missing statuses, or every selected status with again. Run under the existing workspace-lock/export conventions when mutating.

For `forms --dry-run`, use read-only inspection rather than the mutating session wrapper: no migrations, run records, CSV import/export, network/model calls or package writes. List starting addresses and planned changes only; a missing database must not be created.

"Finish forms" offers batch backfill with no as default. Direct assist automatically performs HTTP resolution for its selected job if status is missing, then opens the resulting address. Declining batch backfill does not suppress this single-job behavior. These are decided behaviors, not open questions.

### 7. Guarded visible-browser navigation, not a claimed ordinary-browser session

Only explicitly starting assist authorizes visible Playwright navigation. Install context-level domain request guards before the first goto, including redirects, all frames, popups and resources. Match restricted domains and subdomains with the existing hostname-boundary helper; filter forbidden frame controls before collection and model calls as defense in depth. A restricted application target stops with `restricted`; unrelated forbidden assets are blocked and reported without falsely declaring an otherwise usable allowed form restricted.

Do not assume a page route handler runs for each browser redirect: Playwright documents first-URL-only redirect handling, and context routing does not intercept service-worker-owned requests. Block service workers in the tool-managed context and use interception that exposes each redirected destination before network access (Chromium request interception where necessary). Tests must assert zero requests at the forbidden destination, not merely that the final page was rejected. If a navigation cannot be guarded before its request, refuse automation and hand over.

Prefer navigation to validated anchor hrefs over arbitrary event handlers. Allow only clearly navigation-only buttons outside forms, with explicit non-submit type and no form association. Refuse submit/image/default-submit controls, form-associated actions, consent/legal controls and ambiguous scripted actions regardless of label. Block tool-triggered form submissions during navigation as an additional safeguard; text patterns supplement structural checks, not replace them. Stop navigation at the detected application scope before any filling.

Safe in-page no-email continuation or reject-optional-cookie actions can dismiss overlays; never fill the email box, subscribe to alerts or click Accept all. Permit at most four such in-page actions per attempt, separately from the four navigation transitions, and detect repeated states. If safe continuation is missing, hand over. A popup/new tab becomes the tracked application page and consumes the existing transition budget rather than starting a new one.

On a browser 403/429/999 or blocking challenge, save the handoff reason, stop automatic navigation and retain terminal/manual controls. Do not switch identity, solve a challenge or repeatedly retry. Restricted initial targets open local apply.html only. Alternative rejected: assuming headed Chromium cannot be blocked, or calling automated clicks person-driven merely because the browser is visible.

### 8. Existing user-triggered filling with necessary grounding/reporting fixes

Reaching a form saves its link and offers the existing `[f]` prompt; navigation never automatically transfers personal values or files. On each fill trigger, re-detect the application scope on the tracked live page, including manually advanced steps and allowed frames. Keep manual login/CAPTCHA completion possible after handoff, but never automate those actions. Consent remains untouched, and the user still advances steps and submits.

Reuse deterministic mapping, prepared motivation, document selection and free-text generation. Scope collection, extra assignments and highlighting to the selected application's controls, and preserve existing values. For selects/radios, require both membership in the offered options and evidence that the chosen option answers a known candidate fact; list membership alone does not establish a driving licence or qualification. Unsupported facts stay unset even in free text.

Return every generated field/value for review, not just warnings, and propagate skipped/unsupported optional fields and model/action errors to the terminal report. Required consent/CAPTCHA steps remain manual needs-input items. Alternative rejected: treating the existing option validation and review-warning list as sufficient to meet the revised contract.

### 9. Stable alert-first ordering using the known source set

Use the existing `AlertSource` values as the shared classification set, including `stepstone-alert-recommended`; do not infer alert origin from suffixes. A job with any alert source qualifies, including search/alert duplicates. Sort each eligible step list stably before limits, preserving explicit ID order.

The LLM meter is still shared across evaluation, writing, finding and filling. Alert-first does not reserve a separate preparation budget or promise that approved alerts are prepared after evaluation exhausts it. Preserve the existing singular-alum fix and regression. Alternative rejected: `endswith("-alert")`/`endswith("-match")`, which misses recommended StepStone cards.

### 10. Explicit precedence for pending spec conflicts

There are no archived main specs yet. The application-form-finding spec names the pending assisted-form-fill and run-modes clauses it supersedes, retaining all unrelated requirements. Implement against that combined contract. Before archiving any affected change, incorporate those supersessions into the owning capabilities so archive order cannot restore stale prohibitions or restricted-site behavior.

Alternative rejected: declaring that no requirements are modified solely because the earlier changes are unarchived.

## Risks / Trade-offs

- Visible automation may still be refused: provide explicit manual handoff; deterministic CBC fixtures cannot establish live acceptance.
- Application identity/context may be ambiguous: prefer `partial` over filling an unrelated form, show the reached address/reason, and allow an entry-based retry.
- Session-specific form addresses expire: preserve entry/reached URLs, re-detect during assist and allow explicit repair instead of promising permanent bookmarks.
- Conservative button/consent rules may require manual interaction: accepted in exchange for avoiding accidental submission or authorization.
- A GET-only prepare still contacts employer sites: disclose this in every run mode; up to five HTTP page requests per attempt, shared caching/pacing and host blocks limit traffic.
- HTML layouts or package edits may defeat safe targeted refresh: surface synchronization errors and preserve user content rather than silently rewriting it.

## Migration Plan

Append migration v5 with the five nullable application columns; do not edit earlier migrations or change existing records eagerly. Backfill initializes entry/outcome metadata on demand. Add CSV columns compatibly so existing status/notes edits still round-trip.

Deploy HTTP guards/state sharing before enabling finding during prepare, then persistence/refresh and the forms command, then guarded assist navigation and scoped filling. Deterministic tests must be green before the separate user-authorized live check.

Rollback should disable the feature while retaining the additive columns and entry addresses. Do not assume session-specific resolved URLs remain valid; use the preserved entry for manual recovery. An older binary rejects a newer schema under the existing migration policy, so binary downgrade requires a compatible database backup rather than merely ignoring columns.
