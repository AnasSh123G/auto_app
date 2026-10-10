# Tasks

## 1. Alert-first ordering and parser regression

- [x] 1.1 Share classification from the known `AlertSource` set, including `stepstone-alert-recommended`, and stably order eligible evaluate/prepare lists before limits; verify recommended cards, mixed-source duplicates, within-group order and explicit ID order in step tests
- [x] 1.2 Cover limits and interruption with the shared LLM meter; verify approved jobs remain resumable when evaluation consumes the preparation budget and that no separate budget is granted
- [x] 1.3 Retain the already implemented singular-alum parser fix; verify its existing LinkedIn regression passes without reimplementing the parser change
- [x] 1.4 Document alert prioritization and shared-budget limitations in job-source/run documentation; verify examples include StepStone recommended cards

## 2. Guarded HTTP transport and finder state

- [x] 2.1 Add an explicit single-response GET path with implicit redirects disabled, including injected clients, while preserving detail-fetch extraction behavior; verify existing HTTP/detail tests and mock redirects through the controlled path
- [x] 2.2 Share run-owned HTTP pacing, host blocks and address caching between detail fetch and prepare/find, and close owned clients at run end; verify redirect-host pacing, responding-host block attribution, no post-block request and no duplicate address fetch
- [x] 2.3 Implement the four-transition/five-page budget, target validation and loop handling in the finder; verify overlong HTTP chains, meta refresh, forwarding links, fetched frames, cache-hit transitions and cycles without real network access
- [x] 2.4 Document bounded GET traffic and honest User-Agent/block handling in job-source documentation; verify counts include the starting page and redirects

## 3. Application detection, ranking and model fallback

- [x] 3.1 Implement pure application-context/scope detection and static control normalization; verify intended first-page forms, newsletter/contact/search rejection, conflicting vacancy identity, login-wall precedence, header sign-in links and submission-time versus blocking CAPTCHA fixtures
- [x] 3.2 Implement safe German/English candidate ranking, redirect/frame parsing and explicit no-email continuation descriptions; verify forbidden/legal/share schemes and targets are excluded and wrong-vacancy/generic-careers links are not accepted as found
- [x] 3.3 Add `form_find` across task typing, configuration, key/provider resolution and usage labels, inheriting evaluate provider/model overrides with low default effort; verify override resolution, provider validation and metered usage in LLM tests
- [x] 3.4 Add sanitized public candidate-ID prompts and one-call-per-transition/four-call-per-attempt fallback with retries disabled; verify token/personal-data omission, untrusted page text, invented selections, exhausted budgets and failed-call counting
- [x] 3.5 Implement explicit outcome mapping and expected-failure handling; verify network/non-block HTTP failure, block, restricted target, login/challenge, absent entry, absent application evidence, model-auth stop and unexpected error reporting are distinguishable
- [x] 3.6 Save personal-data-free fixtures for CBC details/form, aggregator forwarding, walls and false positives; verify detection/ranking tests use those fixtures, and document unsupported/ambiguous outcomes plus model privacy/budget in assisted-applying and LLM documentation

## 4. Storage, prepare and command

- [x] 4.1 Append migration v5 and repository helpers for entry/reached URLs, status, timestamp and note; verify migration from v4, null legacy rows, preserved ad/canonical identity and correct saved wall/last-usable URLs
- [x] 4.2 Resolve non-email jobs before prepare rendering, including evaluation-only application URLs, reuse found results and keep expected navigation failure nonfatal; verify email isolation, all run modes, no browser/fill/upload in prepare and explicit unexpected/save errors
- [x] 4.3 Add a shared targeted link/status refresh path for prepare, forms and assist across `apply.html`, README and `job.md`, plus separate generated CSV columns; verify user-edited notes/letters/motivation survive, legacy packages refresh, repeated retries replace status text and unsafe refresh failures are reported
- [x] 4.4 Add `autoapp forms [--job id] [--again] [--dry-run]`, selecting prepared non-email jobs and excluding submitted/closed jobs; verify default backfill, explicit filtering and entry-based retry for every status including found
- [x] 4.5 Implement strictly read-only forms dry-run inspection outside the mutating session wrapper; verify zero HTTP/LLM calls, unchanged database/history/packages/CSV/notes and no creation or migration of a missing/legacy database
- [x] 4.6 Offer menu backfill with no as default and resolve only a missing status automatically on direct assist; verify declining batch backfill, selected-job behavior and no repeated found-resolution request
- [x] 4.7 Wire outcomes into events, summaries and statistics without duplicating counts; verify the 7-found/3-needs-browser/1-login summary and browser discoveries in dashboard tests
- [x] 4.8 Update README, application-folder/tracking documentation and command help for URL recovery, separate CSV status columns, forms dry run and run-mode GET behavior; verify documented commands and user-note round-trip examples against CLI tests

## 5. Guarded assist navigation and scoped filling

- [x] 5.1 Install restricted-target interception before first navigation, prevent redirect/service-worker bypass and filter forbidden frame controls before detection/model/filling; verify initial URLs, real server-side redirect chains, subdomains, popups, frames and resources produce zero requests at restricted destinations, not merely a rejected final page
- [x] 5.2 Add the rendered-control adapter with bounded waits and selected application scope; verify delayed JS/no-form applications, allowed iframes, unrelated page/frame controls and manually advanced form steps in browser tests
- [x] 5.3 Implement structural navigation safety, four transitions, four safe in-page actions and loop detection; verify apply-labelled submit/default-submit/image/form-associated controls, ambiguous handlers and consent are refused, safe cookie rejection works and no submit events occur
- [x] 5.4 Add the full local Adzuna/email-free/CBC path and new-tab tracking; verify the final CBC-equivalent form is found within budget with no email sent, subscription, consent, personal-data fill, upload or submission during navigation
- [x] 5.5 Open/revalidate saved addresses, allow one fresh entry attempt for expired links, stop on browser refusal/walls and persist results through the shared refresh path; verify no identity change/repeated blocked attempt, manual handoff reasons and synchronized package/CSV links
- [x] 5.6 Keep filling behind `[f]`, scope mapping/uploads/LLM/highlighting to the identified application and preserve user-set values; verify navigation never prefills, repeated multi-step filling works, outside controls stay untouched and consent/CAPTCHA remain manual
- [x] 5.7 Require known-fact support for select/radio choices and unknown free-text facts, and report every generated field/value plus skipped optional fields and errors; verify unknown driving-licence controls stay unset and warning-free generated answers are still listed
- [x] 5.8 Update assisted-applying documentation for visible automated Chromium, `[f]`, no-email navigation, cookie rejection, restricted local-helper behavior and manual fallback; verify the documented interaction sequence in session/menu tests

## 6. Integration and archival readiness

- [x] 6.1 Verify end-to-end local prepare/backfill/assist scenarios cover shared redirect guards, URL recovery, user-content preservation, scoped filling and every run mode; run the full test suite, lint and strict OpenSpec validation
- [x] 6.2 Only with explicit user authorization, run GET-only finding on selected real prepared jobs (including a3e1b3 and mittwald 485701 if still present); report reached URLs/reasons without asserting fixture success guarantees live acceptance
- [x] 6.3 Have the user check `autoapp assist a3e1b3` in a real terminal: verify accepted navigation reaches the CBC form, `[f]` fills only after review authorization and no consent/submission occurs; if the live site refuses automation, verify the explicit handoff instead
- [x] 6.4 Before archiving any affected change, reconcile the named supersessions into the pending assisted-form-fill and run-modes owning capabilities; validate all affected artifacts and verify archive order cannot restore conflicting requirements

Live acceptance on 2026-10-09: Adzuna returned HTTP 403 and browser navigation did not complete the aggregator chain. Starting at the employer details URL supplied by the user, HTTP finding reached the matching CBC form with HTTP 200; that verified form address was saved. The user confirmed assist opened the live form, filled on request, left consent unticked and did not submit automatically. This does not establish end-to-end discovery from Adzuna. Mittwald's saved LinkedIn entry was reported restricted without requesting LinkedIn.
