# Spec Delta

## ADDED Requirements

### Requirement: Arbeitsagentur jobs never use the Arbeitsagentur page as the application page
For a job from the Arbeitsagentur whose page is on `arbeitsagentur.de`, the form finder SHALL NOT start at, crawl or save the Arbeitsagentur page (or a partner board's page) as the application link. Its starting address SHALL be the employer's own address, in this order: the employer posting matched by employer discovery, the stored form entry, the stored application address, the evaluated application address; each only when it is not a job board, directory, partner board or restricted domain. Without such an address the finder SHALL make no request and the outcome SHALL depend on what the employer lookups did:
- both the direct own-ad search and the careers-page lookup ran and found nothing, and the ad has an `externeURL` or its `allianzpartnerName` is not the Arbeitsagentur: status `no_employer_link`, with the note "employer ad not found yet" and what was searched;
- both lookups ran and found nothing, the ad has no `externeURL` and its `allianzpartnerName` is the Arbeitsagentur itself (posted directly there): status `restricted`, the Arbeitsagentur page is kept as the application address and the note starts with "only on the Arbeitsagentur";
- at least one lookup has not run yet (no search key, cap reached, no company name, careers pages not looked at yet): status `restricted`, no application address, and the note says "employer ad not searched yet" with the reason. This SHALL NOT be `no_employer_link`.
A job with status `no_employer_link` SHALL still be prepared, with its cover letter and package. It SHALL be looked up again within `employer_careers.failure_ttl_hours`, and when the employer's ad turns up, the form status, folder marker and links SHALL be refreshed.

#### Scenario: Both lookups missed
- **WHEN** an Arbeitsagentur job has a jobvector.de `externeURL`, and the own-ad search and the careers-page lookup both found nothing
- **THEN** its form status is `no_employer_link`, no request goes to arbeitsagentur.de or jobvector.de, and the application is still prepared

#### Scenario: Posted directly on the Arbeitsagentur
- **WHEN** an Arbeitsagentur job has no `externeURL`, its partner is the Arbeitsagentur, and both lookups found nothing
- **THEN** its application address stays the Arbeitsagentur page, labelled "only on the Arbeitsagentur", with status `restricted`

#### Scenario: Search not run yet
- **WHEN** no search key is set and an Arbeitsagentur job has not been searched
- **THEN** its status is `restricted` with the note "employer ad not searched yet" and the reason, not `no_employer_link`

#### Scenario: Ad turns up later
- **WHEN** a later run finds the employer's ad for a job that was `no_employer_link`
- **THEN** its form status, folder marker and links are refreshed to the employer's page

## MODIFIED Requirements

### Requirement: Resolve the form during prepare
For non-email applications without a reusable result, prepare SHALL resolve the application link before rendering the package. The initial entry SHALL be the stored application URL, otherwise the evaluated application URL, otherwise `job_url`; for a job from the Arbeitsagentur the initial entry SHALL instead be the employer's own address, or none (see "Arbeitsagentur jobs never use the Arbeitsagentur page as the application page"). Expected navigation failures SHALL preserve preparation using the last usable link. Email applications SHALL cause no finder requests. Prepare SHALL NOT launch a browser, fill, upload or submit.

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

#### Scenario: Arbeitsagentur job with an employer address
- **WHEN** an Arbeitsagentur job has a matched employer posting
- **THEN** the finder starts at that posting and never requests the Arbeitsagentur page

### Requirement: Guard restricted targets before any request
Finder and assist automation SHALL NOT request configured restricted domains or their subdomains, including initial URLs, HTTP or browser redirects, frames, popups and page resources. Defaults SHALL be linkedin.com, indeed.com, indeed.de, stepstone.de and arbeitsagentur.de. A configuration that lists `assist.restricted_domains` without `arbeitsagentur.de` SHALL get it added when the configuration is loaded, with a notice. A blocked navigation target SHALL yield `restricted` and a reason; assist SHALL offer the local helper without visiting that target.

#### Scenario: Restricted redirect
- **WHEN** an allowed tracking URL redirects to linkedin.com
- **THEN** the Location is inspected but LinkedIn is not requested, and the outcome is `restricted`

#### Scenario: Restricted frame or popup
- **WHEN** an employer page attempts to load a restricted frame or popup
- **THEN** its request is prevented, its controls cannot reach detection, the model or filling, and the user is told why

#### Scenario: Browser interception bypass
- **WHEN** a redirect chain or service-worker-owned request would bypass the browser's normal request guard
- **THEN** the request is prevented before reaching the restricted host, or that automatic navigation is refused with a manual handoff

#### Scenario: Older configuration without the Arbeitsagentur
- **WHEN** a workspace's `config.yaml` lists `assist.restricted_domains` without `arbeitsagentur.de`
- **THEN** loading the configuration adds it in memory and prints a notice, and no request goes to arbeitsagentur.de

### Requirement: Outcome is stored and visible
The application SHALL store `form_entry_url`, `form_reached_url`, `form_status`, `form_checked_at` and a one-line `form_note`. Status SHALL be one of `found`, `partial`, `needs_browser`, `login_required`, `captcha`, `restricted`, `unreachable`, `no_employer_link` or `none`. The original entry and `job_url` SHALL be preserved; `application_url` SHALL hold the form or last usable handoff address. Statuses SHALL appear in packages, CSV, statistics and run summaries. For `no_employer_link` the package SHALL show the job's page only as the job ad, never as the application page, and SHALL say that the employer's ad was not found yet, with the reason.

#### Scenario: No usable entry point
- **WHEN** neither the job nor its evaluation provides a usable starting URL
- **THEN** no request is made and the outcome is `none` with the missing-entry reason

#### Scenario: No application evidence
- **WHEN** a reachable starting page has neither application context nor an eligible continuation
- **THEN** the outcome is `none`; an incomplete application path instead yields `partial`

#### Scenario: Summary line
- **WHEN** a run prepared 11 form applications with 7 found, 3 `needs_browser` and 1 `login_required`
- **THEN** the summary shows "forms: 7 found, 3 need your browser, 1 login required"

#### Scenario: Employer ad not found
- **WHEN** an Arbeitsagentur application is prepared with status `no_employer_link`
- **THEN** its README and `apply.html` say "employer ad not found yet" with the reason, show no application page link, and show the Arbeitsagentur page only as the job ad
