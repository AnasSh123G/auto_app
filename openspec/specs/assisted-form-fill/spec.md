# assisted-form-fill Specification

## Purpose
For applications that must go through a web form, opens the live application page in a real browser, fills in the form fields and attaches the files on the user's request, then leaves the final review and the Submit click to the user.

## Requirements

### Requirement: Assist session
`assist <job-id>` SHALL open/revalidate the saved application address (or job URL if none) in visible automated Chromium with a persistent profile. Restricted initial URLs SHALL open only local `apply.html`. Navigation SHALL follow `application-form-finding` safeguards; logins stay manual and are remembered locally.

#### Scenario: Open application page
- **WHEN** the user runs `assist <job-id>` for a prepared job with an application URL
- **THEN** a browser window opens at that URL using the persistent profile

#### Scenario: Job not prepared
- **WHEN** the user runs `assist` for a job that has no application folder
- **THEN** the command stops and tells the user to run `prepare` first

### Requirement: Fill on user request
The system SHALL fill the current page only when the user triggers it from the terminal. It SHALL support repeated triggers for multi-step forms, and SHALL include form fields inside embedded frames.

#### Scenario: Multi-step form
- **WHEN** the user triggers fill on step 1, clicks "Weiter" in the browser, and triggers fill again
- **THEN** the fields on step 2 are filled as well

#### Scenario: Form in an iframe
- **WHEN** the application form is rendered inside an iframe from an applicant-tracking system
- **THEN** the fields inside the iframe are detected and filled

### Requirement: Field values come from prepared data
Field values SHALL come only from the profile, standard answers and the application folder (form answers, cover letter text, documents). Free-text questions with no prepared answer MAY be answered by the LLM within the field's length limit, following the `cover-letter-writing` rules.

#### Scenario: Salary field
- **WHEN** the form has a field labelled "Gehaltsvorstellung (brutto/Jahr)"
- **THEN** it is filled with the salary expectation from `form_answers.md`

#### Scenario: Length-limited free text
- **WHEN** a textarea "Warum möchten Sie bei uns arbeiten?" has a maximum length of 500 characters
- **THEN** the filled answer is at most 500 characters

### Requirement: Document upload
File inputs SHALL receive the matching file from the application folder: CV fields get the CV, cover letter fields get `cover_letter.pdf`, certificate or "other documents" fields get the certificates. A single generic upload field gets `Bewerbungsmappe.pdf`.

#### Scenario: Separate upload fields
- **WHEN** the form has separate upload fields labelled "Lebenslauf" and "Anschreiben"
- **THEN** the CV is uploaded to "Lebenslauf" and `cover_letter.pdf` to "Anschreiben"

### Requirement: Never submit or consent on the user's behalf
The system SHALL NOT submit, send, grant consent or solve CAPTCHAs. Navigation-only apply controls MAY be followed only when structurally non-submitting and unambiguous under `application-form-finding` safeguards; default/explicit submit, image-submit and form-associated ambiguous controls remain forbidden. Optional-cookie acceptance, privacy consent, terms and legal declarations stay for the user.

#### Scenario: Privacy consent checkbox
- **WHEN** the form contains a required checkbox "Ich habe die Datenschutzerklärung gelesen"
- **THEN** the checkbox is left unticked and highlighted as needing the user's action

#### Scenario: Submit button present
- **WHEN** filling completes on the final form step
- **THEN** the submit button has not been clicked, and the terminal tells the user to review and submit

### Requirement: Fill report and highlighting
After each fill, the system SHALL visibly highlight filled fields and required fields left empty in the page. It SHALL print a summary listing the filled fields and the fields needing user input.

#### Scenario: Unknown required field
- **WHEN** a required field "Wie haben Sie von uns erfahren?" has no prepared answer and LLM answering is disabled
- **THEN** the field is left empty, highlighted, and listed under "needs your input"

### Requirement: Restricted domains
Automation SHALL NOT request configured restricted domains or their subdomains (default: linkedin.com, indeed.com, indeed.de, stepstone.de), including redirects, popups, frames and resources. For an initial restricted URL, assist SHALL open only the folder's local `apply.html`, not the website; restricted frame controls SHALL be excluded before detection, model use and filling.

#### Scenario: LinkedIn Easy Apply job
- **WHEN** the user starts assist for a page under `linkedin.com`
- **THEN** only local `apply.html` opens, no website request or filling occurs, and the terminal explains the restriction

### Requirement: Submission confirmation
When the user ends an assist session, the system SHALL ask whether the application was submitted, and record status `submitted` with the date if confirmed.

#### Scenario: User confirms submission
- **WHEN** the user ends the session and answers "yes" to "Did you submit the application?"
- **THEN** the job's status becomes `submitted` with today's date in the tracker
