# Job sources

autoapp finds jobs from Arbeitsagentur, Adzuna, Workwise, optional Arbeitnow, and job-alert emails.

## Arbeitnow (optional, no key)

Enable it in setup's Sources section or set `sources.arbeitnow.enabled: true`.
The [free public API](https://www.arbeitnow.com/blog/job-board-api) needs no account.
Its [terms](https://www.arbeitnow.com/terms) require a link back: CSV and newly generated local
job snapshots/helpers retain a clickable Arbeitnow source link, including cross-source duplicates.
No applicant information is sent to Arbeitnow; attribution is not added to employer-facing letters.

```yaml
sources:
  arbeitnow:
    enabled: true
    max_pages_per_run: 40
    max_requests_per_run: 40
    max_requests_per_day: 200
    max_requests_per_month: 1000
    min_delay_s: 3
```

These are app-selected courtesy limits, not published provider quotas. Existing configs omit
Arbeitnow and keep it disabled. Requests, including failures, count against persisted limits.
Poll at most hourly: the provider reports hourly updates. Selected profiles share one bounded
feed snapshot per invocation; there are no repeated requests per keyword/city.

Keyword phrases match titles, tags or descriptions locally. Foreign editions and explicit foreign
country facts are excluded. Known city/radius mismatches are excluded; city-center distances are
approximate, and unresolvable locations are retained with uncertainty reported. A German-market
URL or remote flag does not prove German work/residency eligibility. Unknown salaries/types
remain unknown. API `created_at` is used as a source-listing date for age filtering, not a verified
original employer publication date.

Most records already contain descriptions and skip detail-page fetching. API listing links remain
ad entries, not verified employer websites or found forms. Other filters, deduplication, alert-first
ordering, LLM limits and application safeguards are unchanged. Only selected profiles participate;
if several match, the first accepting profile is primary and all matches are kept in provenance.

The recent-page window is not exhaustive. Page/usage limits, refusals, malformed pages and bad
pagination are reported explicitly; earlier valid records remain usable and other sources continue.
Individual malformed records are counted and skipped. There are no retries, browser fallback or
paid endpoint calls. Overall `run --dry-run` still permits discovery/network work.

Run summaries and Statistics show Arbeitnow's matched/new/duplicate jobs separately from feed
pages, request attempts, invalid records, foreign exclusions and unresolved locations. Request
usage is shown against daily/monthly caps, including usage from earlier runs even if the source
is later disabled. Empty, partial and failed feeds are distinct outcomes.

## Arbeitsagentur (no setup needed)

The Arbeitsagentur source is free and requires no API key. It searches [arbeitsagentur.de](https://arbeitsagentur.de) via a public endpoint.

Enable it in `config.yaml`:

```yaml
sources:
  arbeitsagentur:
    enabled: true
```

An Arbeitsagentur job is applied to at the **employer**, never on arbeitsagentur.de: `arbeitsagentur.de` is
in `assist.restricted_domains`, so its page is not followed or saved as the application link. Jobs whose
ad is only a link to a partner board such as jobvector.de count as board jobs too: the employer's own ad is
searched by company and title (like alert jobs). What that search misses goes on to the employer's careers
pages, after evaluation. When both found nothing, the job is still prepared but marked `no_employer_link`
(folder ending `_NOEMPLOYER`): the Arbeitsagentur page appears only as **Job ad**, never as the application
page, and both lookups repeat after `failure_ttl_hours`. A lookup that did not run (no `PARALLEL_API_KEY`, a
used-up limit) leaves the job `restricted` with that reason instead. An ad posted only on the Arbeitsagentur
(no `externeURL`, the Arbeitsagentur as partner) keeps its Arbeitsagentur link, labelled "only on the
Arbeitsagentur". A contact email in the Arbeitsagentur detail is read at evaluation, so the job can
be an email application.

## Adzuna job search API

1. Sign up for a free developer account at [developer.adzuna.com](https://developer.adzuna.com/)
2. Note your **App ID** and **App Key**
3. Add them to `.env`:
   ```
   ADZUNA_APP_ID=your-app-id
   ADZUNA_APP_KEY=your-app-key
   ```
4. Enable in `config.yaml`:
   ```yaml
   sources:
     adzuna:
       enabled: true
   ```

The free tier is reportedly limited to about 25 requests a minute, 250 a day and 1,000 a month
(Adzuna does not publish exact numbers; check your dashboard). autoapp stays under that on its own:
it waits about 2.6 seconds between requests and counts every request in its database, so the caps
hold across runs. When a cap is reached, or Adzuna answers "too many requests" (HTTP 429), the
search stops, keeps the jobs already found, and the run summary says why. Change the caps if your
limits differ:

```yaml
sources:
  adzuna:
    enabled: true
    max_requests_per_run: 40
    max_requests_per_day: 150
    max_requests_per_month: 900
```

Each keyword in each city costs up to 3 requests (50 jobs per page), so 3 keywords in 2 cities is
at most 18 requests per run.

## Workwise (on by default, no key)

[Workwise](https://www.workwise.io) is searched with the same request its own job search page sends
(`search.workwise.io/v2/searches`), without a login: once per keyword of each search profile, at most
`max_pages_per_keyword` pages of `page_size` jobs, `min_delay_s` apart, within persisted caps per run, day and month
(requests that fail count too). The app identifies itself as `autoapp`, as with every source.

```yaml
sources:
  workwise:
    enabled: true
    max_pages_per_keyword: 2
    page_size: 50
    max_requests_per_run: 30
    max_requests_per_day: 100
    max_requests_per_month: 1500
    min_delay_s: 2
```

Workwise searches by keyword only. The distance to the profile's cities is checked by the app from the coordinates
Workwise sends for each job (radius from the profile, 30 km when none is set); remote jobs always count as near, and
jobs without coordinates or a city the app does not know are kept for the evaluation to judge. A job found by two
keywords is stored once. Workwise's salary range is the employer's figure for the job and is used like an advertised
salary. The description is read from the job page (`www.workwise.io/job/<id>-<slug>`, which carries JSON-LD) by the
fetch step.

Applications on Workwise are made on Workwise with a Workwise account. Log in once in the app's browser with
`uv run autoapp login workwise`; the form finder and the assisted filling then work on the Workwise job page.

**Workwise alert emails** (any `@workwise.io` sender) are HTML only. Each job card is one link to
`workwise.io/jobsuche?eTT=...&id=<jobId>&lT=...` whose text gives the title, the company, the city and tags
("Homeoffice möglich" is read as hybrid, "40 Stunden" / "20 bis 40 Stunden" as the working time). The `eTT` (email
tracking) and `lT` (login) tokens are never stored or followed: the card's address is the token-free
`https://www.workwise.io/job/<id>`. The salary tag is not stored (the card fields only hold platform estimates).
"Alle passenden Jobs anschauen" links carry no job id and are not cards; a Workwise mail without a job id link is
account mail. Links of the older form `workwise.io/job/<id>-<slug>` (also inside a tracking link that carries it
URL-encoded) are still taken. Workwise alert jobs are not sent to employer discovery: they are applied
to on Workwise, which the app can open.

## Email alerts from LinkedIn, StepStone, Indeed and Workwise

### Setup: Gmail

1. Create a Gmail filter to label job alert emails:
   - Go to [mail.google.com](https://mail.google.com) → Settings → Filters and Blocked Addresses
   - Create a filter with search criteria: `from:(jobalerts-noreply@linkedin.com OR jobagent.stepstone.de OR indeed.com OR workwise.io)`
   - Apply label: Create a new label called `Jobalerts`
   - Skip the inbox (optional but keeps alerts separate)

2. Configure autoapp:
   ```yaml
   sources:
     alerts:
       enabled: true
       folder: Jobalerts
   ```

### Setup: Proton Mail or custom IMAP

1. Create or designate a folder for alerts
2. Configure the folder name in `config.yaml`:
   ```yaml
   sources:
     alerts:
       enabled: true
       folder: Jobalerts  # Exact folder name on your server
   ```

### Create job alerts on the job boards

**LinkedIn:**
1. Go to [linkedin.com/jobs](https://linkedin.com/jobs)
2. Search for roles with your keywords and filters
3. Click "Create job alert"
4. Set frequency to daily or weekly

**StepStone:**
1. Go to [stepstone.de](https://stepstone.de)
2. Create a saved search with your keywords and filters
3. Create a job alert by email

## Alert jobs: search first (default) or platform

An alert email only shows a card: title, company and city. `sources.alerts.mode` decides how the app gets the full
ad of each card:

```yaml
sources:
  alerts:
    enabled: true
    folder: Jobalerts
    mode: search_first   # search_first (default) or platform
    llm_fallback: true                 # the AI reads cards and emails the parsers cannot (default)
    max_llm_extractions_per_run: 20    # at most this many AI reads per run
    llm_unknown_senders: false         # also read alert-like emails from unknown senders (default off)
```

- **`search_first` (default).** The app reads only the alert email. For each new job it searches
  `"<company>" "<job title>"` for the employer's own ad. If that finds nothing it looks up the
  employer's website (or reuses one it saved before) and reads the job ads on its careers page. LinkedIn, Indeed,
  StepStone and Workwise job pages are **never opened** to read a description, so their bot blocks cannot
  stop the app. This works for every alert sender, Workwise included, and it needs `PARALLEL_API_KEY` in
  `workspace/.env` (see the next section). It runs before the pages are read and before evaluation, even when
  `sources.employer_careers.enabled` is off; that setting then only decides whether the employer's *other*
  openings are added as new jobs.
- **What if the ad is not found?** The job waits as `description_unavailable` and the reason is shown in the
  tracker, the log and the run summary: `employer ad not found`, `search key missing`,
  `search cap reached`, `alert does not name the employer`, and so on. It is searched again in a later run (after
  `failure_ttl_hours`, or as soon as a cap allows). You can paste the text yourself with `autoapp describe <id>`
  or add the employer's link with `autoapp add <url>`. The app never falls back to the job-board page.
- **`platform`.** The earlier behavior: the job-board page is read slowly with your browser profile (see
  [Detail fetching](#detail-fetching-page-parsing)), and employer lookup only happens when
  `sources.employer_careers.enabled` is true. Set it to `platform` if you prefer that.

### When the parser cannot read a card or an email (AI fallback)

The parsers can miss: a layout changes, a card does not name its employer (Workwise), a title is cut to a word or
two, or a whole email gives no card. With `sources.alerts.llm_fallback: true` (default) the app then asks the
`alert_extract` model (one of the entries under `llm.tasks`; without an entry it uses your `evaluate` model, and its
calls count in the run's AI cost and call limit):

1. **Extraction.** The model reads only the card's text, or the text of the whole email that gave no card, and names
   `{title, company, city, link}`. Anything it names that is not written in that text is thrown away, so it cannot
   invent a job. The text it sees is the *redacted* excerpt stored with the card: your name, address, email and phone
   number, every email address, unsubscribe and tracking links and all link parameters are removed before anything is
   stored or sent. At most `max_llm_extractions_per_run` calls are made per run.
2. **Search by the card's text.** If the employer is still not named, the same redacted text (at most 4,000
   characters) is put into the web search's objective, together with the job title and city. A page is accepted
   only when it is this job's ad, the employer it names is also named in the card's text, and it is not a job board.
   Such a job is marked **needs review**, is evaluated and prepared as usual, and is **never sent automatically**:
   check the employer and the ad before you apply. In `review` mode it is not drafted unless you ask for that job.

Current Indeed job alerts are parsed directly from their title and `company - city` lines, without an extraction
model call when both are present. Some sponsored links contain only opaque click tokens, with no readable job key;
those links are discarded and the card is resolved by employer/title search. A readable job key is kept in a
canonical Indeed URL. Salary badges in this layout are ignored.

Account and marketing mail is not sent to the model: an email from a known sender (LinkedIn, StepStone, Indeed,
Workwise) that holds no job link for that platform (for example "confirm your email") is marked as processed as "not
a job alert", without an AI call and without a warning. When in doubt, for example an email without a readable text,
it is treated as an alert. Each parser decides this next to its link patterns (`is_job_alert`).

An email from a known sender that looks like an alert (it has a job link) but gives no card is marked as processed
only after the model has read it. If the model
found nothing either, the run warns (sender and subject) and the email is not read, or paid for, again. If the model
could not run (the extraction cap was reached, no key, a failed call) or `llm_fallback` is `false`, the run shows
the same warning and the email stays unprocessed and is read again in the next run. Emails from senders without a
parser are only counted as unrecognised, unless `llm_unknown_senders: true`: then those that read like a job alert
(subject or start of the text) go through the same fallback and their jobs are listed under the source `llm-alert`.

The `alerts:` line of the run summary ends with `N needed LLM extraction`.

Every run summary starts with `alert mode: ...` and, in `search_first`, counts how many alert jobs were found by
searching, found on a careers page, not found or deferred. Search usage is reported separately from AI cost.

## Employer careers pages (optional, off by default)

Alert emails (LinkedIn, Indeed, StepStone) often link to pages the tool cannot read. With
`sources.employer_careers.enabled: true` the tool looks up the employer's **own** careers site
after reading the alerts, reads the original vacancy there and finds other openings that match
your search profiles. It only reads public pages; it never logs in, fills or submits anything.

- **Two searches per job.** First the employer's website: one search that includes the ad's quoted
  title, then its imprint and job list are checked (each country domain separately). After
  evaluation, every job worth applying to that is still on a job board or the Arbeitsagentur gets a
  second search, `"<company>" "<job title>"`,
  and a result is used only when the page is this ad (title in the heading, company named, most of
  the ad's text on it). See the usage guide, section 14.
- **Many openings.** Above `max_openings_per_employer` the jobs your alerts ask for are taken first,
  and their pages are still read; the scan then counts as partial. A login box in a page header (a
  customer portal) is not taken for a login wall.

- **Setup:** `autoapp setup --section sources` asks whether to enable it and stores the key as
  `PARALLEL_API_KEY` in `workspace/.env`. Without a key, employers that are not saved yet are
  skipped, and employers whose site is already saved are still scanned.
- **What is stored locally** (in `autoapp.db`, never in git): verified employer websites and
  careers/ATS addresses, name aliases, why each site was trusted, the result for each alert job,
  and the vacancies read at the last scan. A saved website is reused for every later alert from
  that employer, so each employer is searched once, not once per alert.
- **Limits** (all in `sources.employer_careers`): 10 employers, 20 searches, 20 pages per employer
  and 50 openings per employer per run, 100 pages per run in total, plus 50 searches per day and
  1,000 per month. A search that fails still counts, nothing is retried automatically and each
  employer gets at most two searches. When the run's page total (`max_pages_per_run`) is used up,
  the remaining employers are postponed to the next run (`LOOKUP DEFERRED`), with no search spent
  and no failure recorded; raise it together with `max_employers_per_run`, since checking one new
  employer reads several pages.
- **Search mode and cost:** every lookup explicitly uses Parallel's `fast` mode with at most 10
  results; the tool never switches to a more expensive mode or another provider by itself. Fast
  is documented at $1 per 1,000 requests. Reports show requests and an estimate at that rate
  (`search_cost_per_1000_usd`); the estimate is gross, so it does not subtract any free credit
  your account may have, and it is not a bill.
- **When the search fails:** a missing key skips lookups with a visible reason; a rejected key (401/403)
  or a rate limit (429) stops further searches for that run; a timeout or a 5xx defers only that
  employer. Employers whose site is already saved are scanned in all of these cases.
- **Commands:** `autoapp employers` shows what is saved (read-only). `autoapp employers --discover` looks up
  and scans the employers of your alert jobs even when the routine source is off; add `--job <id>` or
  `--profile NAME` to narrow it, `--again` to scan again (it never searches again for a saved website, but it retries a lookup that
  failed less than 24 hours ago, which does search),
  `--refresh-site` to look the website up again, `--website URL --job <id>` to confirm the right website
  (that site is kept until you change it). `--dry-run` lists the employers, the saved sites and the planned
  actions without a search, page or model request and without writing or migrating anything. This differs from
  `autoapp run --dry-run`, which still searches and reads pages and only skips sending.
- **What is sent to the search service:** only the public employer name, the job title and the
  city from the alert. Never your CV, profile, letters, mailbox contents, email addresses or the
  tracking links of the alert.

### How an employer's careers site is read

The tool follows only public links with plain GET requests: no scripts, no logins, no forms, no
browser. It stays inside the verified employer website and the career-site tenant that website
links to (for example `acme.onapply.de`, or `boards.greenhouse.io/acme` but never other companies
on the same platform), and every address is checked before it is requested. Restricted platforms
(LinkedIn, Indeed, StepStone), private or internal addresses, links with credentials and
non-standard ports are never requested.

Supported layouts:

- JSON-LD `JobPosting` and `ItemList` data, and static HTML lists whose entries link to a vacancy page
  (also when the address has no "job" in it, like `/software-engineer-m-w-d-40624.htm`);
- pagination by `rel="next"`, "Weiter/Next" links and `?page=N` links;
- RSS/Atom feeds that the page declares with `<link rel="alternate">`;
- career-site tenants that the employer's own careers page declares (onapply-style widgets).

When something cannot be read, the result is a partial scan with the reason, never "no vacancies":

| Situation | What happens |
| --- | --- |
| Site needs JavaScript to show its vacancies | recorded as unsupported; open the site yourself |
| HTTP 403/429/999, login or challenge page | the saved site is kept, marked blocked, the host is skipped for the rest of the run; no other host is tried and no new search is made |
| Careers address gone (404/410) | the tool looks for the new careers link on the saved homepage first; only if the website itself is gone (and you did not confirm it) is a new lookup allowed |
| Page or opening limit reached | the scan is saved as partial; the next scan continues with the pages not yet read |
| Linked career site shows no sign of the employer | not imported |

A complete scan is reused for 24 hours (`scan_ttl_hours`), also for new alerts and other search
profiles. `--again` scans again; it never searches for a saved website again. A failed or ambiguous lookup
is not repeated for 24 hours (`failure_ttl_hours`) unless you pass `--again` or `--refresh-site`.

### Alert vacancy versus other openings

For each alert job the tool decides whether the employer's careers site lists **the same vacancy**:

1. a requisition reference shown in the ad that equals the posting's reference, or
2. otherwise a **unique** posting with the same title (gender markers like `(m/w/d)` and case are ignored)
   whose location agrees with the alert. An unknown location is not evidence; an ad whose title was cut
   off needs a reference to be confirmed; two similar postings, a different requisition or another city
   leave the alert **unresolved**, and the candidates are listed. A vacancy that is not found is never
   closed or marked expired: it may simply be on a page that was not read.

A matched alert keeps its own link and status. Before it is evaluated the tool may fill in the missing
description from the posting and replace a link that only points to a job platform with the employer's
posting (the old link is saved as provenance); it never overwrites a found form, your notes, an
evaluation or a prepared package. Only an unevaluated "description unavailable" job that got its
description back returns to "new".

**Additional openings** of the same employer enter the normal pipeline as source `employer-careers`
when a keyword of one of the search profiles appears in the title (or two different keywords in the
description) and the profile's filters accept it (exclusions, posting age, salary stated by the
employer; unknown values never filter). When several profiles match, the job records all of them and
uses the first one in your configuration. Closed postings are skipped. Each posting is one job:
two requisitions with the same title and city stay two jobs, while one requisition listed in several
cities is one job with those locations. The matched alert posting is not imported a second time, so
run totals count recovered alerts and new employer jobs separately.

## Detail fetching (page parsing)

In the default `search_first` alert mode (see above) alert jobs are not fetched from the job boards at all;
this section applies to `sources.alerts.mode: platform` and to jobs from other sources.

### Alert-first processing

Evaluation and preparation process eligible alert-email jobs before search-only jobs, preserving
discovery order within each group. This includes LinkedIn alerts, Indeed alerts and matches, and
both matching and recommended StepStone cards. A job seen by search and an alert is processed
once as an alert job. Explicit `--job` selections retain the order you supplied.

The existing evaluation/preparation limits still apply. All model work shares one run budget:
if evaluation exhausts it, approved jobs remain waiting for preparation on a later run. Alert
priority does not reserve extra calls or guarantee preparation within the same run.

autoapp can fetch the full job description from LinkedIn, Indeed, and StepStone job pages to get more context than what arrives in alert emails.

Configure which sites to fetch and how often:

```yaml
detail_fetch:
  linkedin: true   # Fetch LinkedIn job pages
  indeed: true     # Fetch Indeed job pages
  stepstone: true  # Fetch StepStone job pages
  max_per_site: 20 # At most 20 detail pages per run, per site
  min_delay_s: 20  # Minimum delay between fetches (seconds)
  max_delay_s: 40  # Maximum delay between fetches (seconds)
```

**Residual risk:** Fetching job pages from job boards uses the user's own browser profile and respects per-site limits and delays. autoapp stops on the first bot challenge (CAPTCHA, "Just a moment" message, or 403/429 response). This is low-volume, user-initiated access, but some job boards may consider it automated. You can disable fetching for any site or turn it off entirely by setting `max_per_site: 0`.

### Company apply links with your login (off by default)

LinkedIn, StepStone and Indeed no longer show the employer's apply address to visitors who are not logged in
(checked on a LinkedIn job page in October 2026: the "Bewerben" button is marked as external but only opens a
sign-in prompt, and the address is not in the page). The default way to the employer's form is employer discovery
(above). As an opt-in per site, the app can read the address with your login:

```yaml
sources:
  apply_links_with_login:
    linkedin: false
    stepstone: false
    indeed: false
    max_per_run: 20
    min_delay_s: 20
    max_delay_s: 40
```

When a job's application address is still on one of these sites after employer discovery, and the site is switched
on, the app opens that job page in its browser profile (log in once with `uv run autoapp login linkedin`, `stepstone`
or `indeed`). It looks for "apply on the company website" (German and English wording); a link that shows its target
is read directly (platform redirect links such as `/redir/redirect?url=...` are unwrapped), otherwise that control is
clicked and the first navigation that leaves the site gives the address. That navigation is stopped before the
employer page loads, and every window it opened is closed. A single "continue to the company site" step is taken if
the site shows one. Easy Apply, "Schnellbewerbung", "Mit Indeed bewerben" and similar on-site applications are never
clicked; buttons inside forms are never pressed, form submissions are blocked, and nothing is typed. A login wall
or a robot check ends the attempt (a robot check also stops that site for the rest of the run).

The found address becomes the job's application address and the form is then found on the employer's site. This
happens in the prepare step (headless, with the same browser profile) and when you open such a job in Finish forms
(in the visible browser). Each attempt is logged as an `APPLY LINK` line. Using a logged-in account with an
automated browser is against these sites' terms; the pace (one page at a time, `min_delay_s`–`max_delay_s` apart,
`max_per_run`) keeps the risk low but not zero.

### Finding the application page

Preparing a non-email application follows redirects and clear German/English apply links,
forwarding pages and application frames. One attempt permits the starting GET plus at most
four transitions: five page GETs total, including redirects and meta refreshes. Cache hits
still consume transitions. Detail fetching and finding share one run-owned cache, per-host
pacing and host-block memory. The HTTP client identifies itself as `autoapp` rather than
imitating a browser. HTTP 403, 429 or 999 stops requests to that responding host for the run;
there is no automatic browser retry during preparation.

These requests run in automatic, review and test modes, and with `run --dry-run`: that option
prevents sending/drafting, not public-page GETs. Only `forms --dry-run` performs strictly
read-only inspection with no HTTP or model calls. Login, challenge, restricted and ambiguous
pages retain an explained handoff address rather than promising a found form.

## Adding a parser for another sender

To add support for a new job alert sender (e.g., XING), you need:

1. **A parser module** in `src/autoapp/sources/alerts/` (e.g., `xing_parser.py`)
2. **An anonymised fixture** (a sample email from that sender with personal data removed) in `samples/`
3. **A registry entry** to connect the sender's email address to the parser

Example: adding XING alerts:

**Step 1:** Create `src/autoapp/sources/alerts/xing_parser.py`

```python
from email.message import EmailMessage
from autoapp.sources.alerts.models import AlertCard

SUPPORTED_FROM = ("jobs@xing.com",)

def parse(message: EmailMessage) -> list[AlertCard]:
    """Parse a XING alert email and return job cards."""
    # Extract cards from the email body
    # Return a list of AlertCard objects
    return []
```

**Step 2:** Save an anonymised sample email as `samples/xing-alert.eml`

**Step 3:** Register the parser in the alert reader

Once a real XING email arrives in your mailbox, export it to `samples/`, anonymise it (replace personal names, addresses, links with placeholders), and add the parser entry. The registry will automatically pick it up.

## Supported filters

Filters vary by source. autoapp applies only the filters you configure:

| Filter | Arbeitsagentur | Adzuna | Alert emails |
|---|---|---|---|
| Keywords (required) | ✓ | ✓ | ✓ |
| Locations | ✓ | ✓ | ✓ |
| Radius | ✓ | ✓ | Limited |
| Work mode (remote, hybrid, onsite) | ✓ | ✓ | ✓ |
| Working time (full-time, part-time) | ✓ | ✓ | ✓ |
| Contract type (permanent, fixed-term) | ✓ | ✓ | ✓ |
| Minimum salary | ✓ | ✓ | Alert sender only |
| Exclude agencies | ✓ | - | - |
| Posting age | ✓ | ✓ | - |
