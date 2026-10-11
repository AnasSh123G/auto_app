# Spec Delta

## MODIFIED Requirements

### Requirement: One folder per application
For each job with decision `apply`, the system SHALL create a folder `application_<company>_<title>_<shortid>` in the workspace's applications directory. Company and title SHALL be lowercase ASCII slugs with German umlauts transliterated (ä→ae, ö→oe, ü→ue, ß→ss) and SHALL be truncated to keep folder names under 100 characters.

#### Scenario: Folder naming
- **WHEN** a job "Software-Entwickler (m/w/d)" at "Müller & Söhne GmbH" with short ID `a1b2c3` is prepared
- **THEN** the folder `application_mueller-soehne-gmbh_software-entwickler_a1b2c3` is created

#### Scenario: Form status in the folder name
- **WHEN** a form application's application form was not reached (no application found, partial, needs the browser, login, CAPTCHA, restricted, unreachable or employer ad not found)
- **THEN** the folder is still saved and its name ends with a marker after the short ID (`_NOFORM`, `_BROWSER`, `_LOGIN`, `_CAPTCHA`, `_RESTRICTED`, `_UNREACHABLE`, `_NOEMPLOYER`), keeping the name under 100 characters
- **AND** a found form, an email application or a form not yet looked up has no marker
- **AND** when a later form lookup refreshes the folder, the folder is renamed to match, the recorded folder path and the absolute paths inside `README.md`, `job.md` and `apply.html` follow the rename, and a folder the user renamed is never touched

#### Scenario: Employer ad not found
- **WHEN** an Arbeitsagentur application is prepared and neither the own-ad search nor the careers pages found the employer's ad
- **THEN** the folder name ends with `_NOEMPLOYER`, `README.md` and `job.md` say "none yet" for the application page, and `apply.html` shows no application link
