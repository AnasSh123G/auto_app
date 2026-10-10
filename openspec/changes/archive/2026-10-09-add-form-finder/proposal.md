# Proposal

## Why

Aggregator application links often lead to ads rather than forms, leaving the user to navigate through forwarding pages, overlays and employer pages before assisted filling can help. The motivating example is Adzuna ad 5905884599, the email-or-continue overlay, `cbc-cologne.onapply.de/details/346291.html` and finally `https://cbc-cologne.onapply.de/bewerbung/346291.html`.

Separately, a run evaluates jobs in discovery order, so the few alert-mail jobs (LinkedIn, Indeed, StepStone) can wait behind hundreds of API jobs and never get evaluated in a run that is interrupted or hits a limit. The alert path was read correctly but its jobs stayed `new`.

## What Changes

- Resolve non-email applications during prepare with honest HTTP GETs: four navigation transitions, including redirects, and at most five page requests per attempt. Check each target before requesting it; share pacing, caching and host blocks with detail fetching.
- Blocked HTTP attempts become `needs_browser`. Explicitly starting `assist` authorizes a separate attempt in visible Playwright Chromium, not an automatic browser retry during prepare. Browser blocks still require manual handoff.
- Cover the complete Adzuna/CBC sequence, including safe continuation without entering an email and popup handling. Never submit, create accounts, solve challenges or grant consent; reject optional cookies only when that action is unambiguous.
- Detect the application for the intended vacancy, including rendered controls and frames, without treating newsletter/contact forms or submission-time CAPTCHA widgets as navigation walls.
- Preserve the original application entry point and last reached address. Keep the resolved link/status consistent across `apply.html`, README, `job.md`, CSV and assist; explicit `--again` can repair previously found links.
- Fill only the selected application controls when the user presses `[f]`. Use job-specific prepared/generated answers, show every generated value for review, and leave unsupported facts empty, including selects/radios.
- Allow one optional link-selection model call per transition, at most four per attempt, with public job/link information only and all calls metered.
- Prioritize every known alert source, including StepStone recommended cards, while preserving explicit job-ID order. Retain the already implemented singular-alum parser regression.

## Capabilities

### New Capabilities
- `application-form-finding`: resolving an application link to the real form page, the stop conditions, what is stored, and how assist uses it
- `alert-first-evaluation`: ordering of evaluation and preparation so alert-mail jobs are handled first within a run

### Modified Capabilities

No archived main capabilities exist yet, so there are no main-spec MODIFIED deltas. The application-form-finding spec explicitly supersedes conflicting clauses in pending changes:
- `add-automated-job-applications` / `assisted-form-fill`: allow safe navigation-only apply controls; retain user-triggered filling, manual submission and consent; restricted assist opens only the local helper.
- `add-menu-and-run-modes` / `run-modes`: allow bounded form-finder GETs during prepare in every mode, but no browser, filling or submission during the run.

These supersessions must be reconciled into the owning capabilities before archival; all other existing requirements remain in force.

## Impact

- Code: new `formfinder` package; shared HTTP/run context, prepare/evaluate, assist navigation and filling, package refresh, CSV/reporting, menu and migration v5 for entry/reached URLs and outcome metadata.
- Network: at most five HTTP page requests per finder attempt, paced and cached. Finder/assist automation makes no requests to configured restricted domains.
- LLM: `form_find` defaults to the evaluate model with low effort; no extra retries beyond its four-call maximum. Existing fill truthfulness/reporting needs strengthening.
- No new dependencies (httpx, BeautifulSoup and Playwright are already present).
- Out of scope: submitting forms, solving captchas, creating accounts, LinkedIn/Indeed/StepStone automation, saving filled forms for later.
