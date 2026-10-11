# Spec Delta

## MODIFIED Requirements

### Requirement: Per-task model selection
The configuration SHALL give each LLM task (`evaluate`, `write`, `form_fill`, `form_find`) its own provider and model under `llm.tasks`; there is no shared default. A fifth task, `alert_extract` (reading job-alert cards and emails the parsers cannot read), SHALL be optional: without an entry it SHALL use the `evaluate` model, and the setup SHALL NOT ask for it. The setup SHALL ask for the four in turn, offering for each the value already in `config.yaml` or else the answer just given for the previous task. A configuration with the older single default and optional overrides SHALL still load, taking the old default for every task without an entry (`form_find` and `alert_extract` the evaluate model), and SHALL show a notice to run the setup section.

#### Scenario: Different model for writing
- **WHEN** `llm.tasks.write` names a different model than `llm.tasks.evaluate`
- **THEN** cover letters are generated with that model, and evaluation uses its own

#### Scenario: Setup pre-fills the next task
- **WHEN** the user has answered the model for `evaluate` and `config.yaml` has no entry for `write`
- **THEN** the `write` question offers the `evaluate` answer, and an entry already saved for `write` is offered instead

#### Scenario: Alert extraction without its own entry
- **WHEN** `config.yaml` has no `llm.tasks.alert_extract` entry
- **THEN** the alert extraction fallback uses the `evaluate` model, and loading the configuration shows no notice for the missing entry
