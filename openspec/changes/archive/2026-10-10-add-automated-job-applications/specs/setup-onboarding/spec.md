# Spec Delta

## Purpose

Guides the user through first-time setup in an interactive terminal wizard. Any part of the setup can be redone or reset later without hand-editing configuration files.

## ADDED Requirements

### Requirement: First-run onboarding
When the workspace has no completed setup, any interactive command other than `--help` SHALL start the onboarding wizard before doing anything else.

#### Scenario: First start
- **WHEN** the user runs `autoapp run` in an empty workspace from a terminal
- **THEN** the onboarding wizard starts, and the run begins only after setup completes

### Requirement: No onboarding in non-interactive mode
When setup is incomplete and the command runs non-interactively, the system SHALL NOT prompt. It SHALL exit with a non-zero code and a message telling the user to run `autoapp setup`.

#### Scenario: Timer fires before setup
- **WHEN** the systemd timer runs `autoapp run --non-interactive` in a workspace without completed setup
- **THEN** the command exits non-zero, and the log says to run `autoapp setup`

### Requirement: Going back on yes/no questions
In an interactive terminal, every yes/no question after the first of a section SHALL offer "Back". Choosing it SHALL ask the previous question of that section again, with the earlier answers kept, without repeating those earlier questions.

#### Scenario: Pressed the wrong key
- **WHEN** the user answers a yes/no question wrongly and the next yes/no question offers Back
- **THEN** choosing Back asks the question before it again, and the answers given earlier in the section are not asked a second time

### Requirement: Setup sections
The wizard SHALL be organised in these sections:
- `documents`: documents folder and document roles
- `profile`: personal data, availability, language levels, extra skills
- `salary`: optional
- `search`: search profiles and optional filters
- `sources`: Adzuna, alert mailbox, detail fetching
- `llm`: provider, models, API keys
- `email`: sending and safeguards
- `writing`: writing instructions and writing samples

Optional sections and questions SHALL be skippable.

#### Scenario: Skipping salary
- **WHEN** the user skips the `salary` section
- **THEN** setup completes, and no salary preferences are stored

### Requirement: Redo a single section
`autoapp setup --section <name>` SHALL re-run only that section. It SHALL pre-fill the current values as defaults and keep every other section unchanged.

#### Scenario: Change LLM provider only
- **WHEN** the user runs `autoapp setup --section llm` and switches from Anthropic to OpenRouter
- **THEN** only the LLM settings and the related key in `.env` change, and the search profiles, profile data and email settings are unchanged

### Requirement: Setup menu after first run
Running `autoapp setup` after setup is complete SHALL offer a menu: redo everything, redo one section, reset one section to defaults, or cancel.

#### Scenario: Menu shown
- **WHEN** the user runs `autoapp setup` in a fully set up workspace
- **THEN** the four options are offered, and nothing changes until one is chosen

### Requirement: Reset with backup
`autoapp setup --reset <section>` SHALL return a section to its defaults after confirmation. `autoapp setup --reset all` SHALL back up the current configuration, profile, writing instructions and `.env` to `workspace/backups/<timestamp>/`, then restart onboarding from scratch. A reset SHALL NOT delete documents, the job database or application folders.

#### Scenario: Full reset
- **WHEN** the user runs `autoapp setup --reset all` and confirms
- **THEN** the previous configuration files are in a timestamped backup folder, onboarding starts from the first section, and existing application folders and `applications.csv` are untouched

#### Scenario: Reset cancelled
- **WHEN** the user runs `autoapp setup --reset email` and declines the confirmation
- **THEN** nothing changes

### Requirement: Immediate validation
Each answer SHALL be validated as it is entered. Invalid input SHALL be re-asked with the reason, without losing earlier answers.

#### Scenario: Invalid radius
- **WHEN** the user enters `abc` as the search radius
- **THEN** the wizard explains that the radius must be a number of kilometres or empty, and asks again

### Requirement: Connection checks
At the end of the `llm`, `sources` and `email` sections, the wizard SHALL offer to test the entered settings:
- the LLM key, with one minimal request
- the IMAP login and the alert folder
- the SMTP login, without sending a message
- whether the Arbeitsagentur API can be reached

The user SHALL be able to continue despite a failed check.

#### Scenario: Wrong IMAP password
- **WHEN** the IMAP check fails with an authentication error
- **THEN** the wizard shows the error and offers to re-enter the credentials or continue anyway

### Requirement: Secret handling in setup
API keys and passwords SHALL be entered without echo and stored only in the workspace `.env`. They SHALL never be written to `config.yaml` or `profile.yaml`, or printed back.

#### Scenario: Entering an API key
- **WHEN** the user types an Anthropic API key in the `llm` section
- **THEN** the key is not displayed, is saved to `.env`, and does not appear in `config.yaml`

### Requirement: Writing instructions entry
The `writing` section SHALL let the user enter or edit free-text writing instructions, stored in `workspace/writing_instructions.md`, either inline or in the user's `$EDITOR`. It SHALL also let the user declare writing samples.

#### Scenario: Instructions saved before automation
- **WHEN** the user writes instructions in the `writing` section and finishes setup
- **THEN** `writing_instructions.md` contains them, and the first `run` uses them for every letter

### Requirement: Hand edits preserved
The files the wizard writes SHALL remain human-editable. A later wizard run SHALL load and keep the user's manual edits as the current values.

#### Scenario: Manual edit then section rerun
- **WHEN** the user adds a search profile by editing `config.yaml` by hand and later runs `setup --section llm`
- **THEN** the hand-added search profile is still present afterwards

### Requirement: Mail provider presets in setup
The `sources` section (alert mailbox) and the `email` section (sending mailbox) SHALL each offer the presets Gmail, Proton Mail Bridge and Other. Each preset SHALL show its prerequisites: for Gmail, 2-Step Verification and an app password; for Proton, a paid plan and a running Bridge. Choosing Proton Bridge SHALL record the Bridge certificate's fingerprint after the user confirms it.

#### Scenario: Gmail app password guidance
- **WHEN** the user selects the Gmail preset for the sending mailbox
- **THEN** the wizard explains how to enable 2-Step Verification and create an app password, then asks for the app password with hidden input

#### Scenario: Same mailbox reused
- **WHEN** the alert mailbox is already configured and the user reaches the `email` section
- **THEN** the wizard offers to reuse the same account for sending

### Requirement: Auto-send stays opt-in during setup
The `email` section SHALL default automatic sending to off. Before the user enables it, the section SHALL explain the safeguards (daily cap, minimum score, free-mail rule, company cooldown) and recommend a dry run.

#### Scenario: Accepting the defaults
- **WHEN** the user presses Enter through the `email` section
- **THEN** `email.auto_send` is `false`
