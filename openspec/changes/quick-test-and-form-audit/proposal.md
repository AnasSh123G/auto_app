# Proposal

## Why

`autoapp test` is the quick test, while the whole fresh test run is `autoapp run --mode test --fresh`. The short name makes the cheap check easy to confuse with an expensive full run. Also, the quick test only reports form finding as counts per status. It does not show which link a prepared form job got, whether that link is the form or a job-board page, or how the already prepared applications in the real workspace are doing. The next phase (better navigation to the form, Phase 3) needs that baseline, and the audit needs timing and path data per form-finding run.

This is Phase 2 of the employer-first applying work (requests 4 and 5). It builds on `employer-first-resolution` (Phase 1): the `no_employer_link` status and the Arbeitsagentur rules are what the per-link report classifies.

## What Changes

- `autoapp test` now needs a choice:
  - `autoapp test --quick`: today's quick test (a small sample of every step in a throwaway test workspace, then a checklist).
  - `autoapp test --full`: a whole test run in a fresh test workspace, the same as `autoapp run --mode test --fresh`.
  - `autoapp test` without a flag prints which of the two to pick and exits with code 2. It never starts a run. Passing both flags is an error (exit code 2).
  - `autoapp run --mode test [--fresh]` stays unchanged.
- Menu **Tests**: "Quick test" stays the pre-selected action; the fresh run is labelled "Full test run (fresh)".
- Quick test form-link sampling and per-link report (not implemented yet, ticket T6):
  - the sample prepares up to 3 online-form applications from different sources (one Arbeitsagentur job, one alert job, one other source, falling back to the next best score), plus one email application; evaluations rise to 6 so those slots can fill;
  - the checklist gets a "form links" section with one line per prepared form job: source, final address, form status, note and how the address was found;
  - a link on a job-board host (`DIRECTORY_HOSTS` or `assist.restricted_domains`) is FAILED, a link with status `found` is OK, other statuses (including `no_employer_link`) are WARN;
  - WARN is a new fourth checklist state. It does not change the exit code.
- Read-only form-link audit (T7):
  - `autoapp forms --audit [--json]` reports, for the prepared non-email applications, the distribution by form status, the links on job boards by host, the split by source, and the failures grouped by cause. It changes nothing and makes no request. `--json` has a stable shape.
  - The quick test prints the same audit for the real workspace after its checklist.
  - New table `form_attempts` (schema version 8): every `formfinder.find` run for a job stores one trace with strategy, the pages visited with the reason for each, the final status, a short note and the duration. `form_note` stays the one-line summary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `run-modes`: the quick test command is `autoapp test --quick`, `--full` is the fresh full test run, a bare `test` exits with code 2; the quick test samples form jobs across sources, adds the form-links section and the WARN state, and prints the real workspace's audit.
- `main-menu`: the Tests entry names the quick test `autoapp test --quick` and the fresh test run "Full test run (fresh)".
- `application-form-finding`: a read-only audit of the saved form links (`autoapp forms --audit`) and a stored trace for every form-finding run (`form_attempts`).

## Impact

- CLI: `src/autoapp/commands.py` (`test` options `--quick` and `--full`; `forms --audit` and `--json`; `_audit_data`, `_forms_audit`, `_echo_form_audit`), `src/autoapp/setup/gate.py` (`--audit` needs no setup, like `--dry-run`).
- Audit and traces: `src/autoapp/formfinder/audit.py` (new, read-only), `src/autoapp/formfinder/finder.py` (`find(..., job_id=...)` and `_record`), `src/autoapp/formfinder/store.py` (passes the job id), `src/autoapp/db/schema.py` (`_V8`, table `form_attempts`).
- Quick test sampling and report (T6, not done): `src/autoapp/steps/context.py` (`SampleLimits.form_applications`), `src/autoapp/steps/prepare.py` (`_sample_jobs`), `src/autoapp/steps/quicktest.py` (form-links section, WARN state).
- Menu: `src/autoapp/menu/main.py`.
- Docs: `README.md`, `docs/usage-guide.md`, `docs/assisted-applying.md`; tests `tests/steps/test_quick_test_command.py`, `tests/menu/test_menu.py`, `tests/steps/test_quicktest_report.py`, `tests/steps/test_quick_sample.py`, `tests/db/`, `tests/test_form_audit.py`, `tests/test_formfinder.py`.
- No change to how forms are found: the new data is only recorded.
