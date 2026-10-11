# Tasks

## 1. Command naming (T5)

- [x] 1.1 In `src/autoapp/commands.py`, make `autoapp test --quick` run the quick test, `--full` run the fresh full test run (same as `run --mode test --fresh`), a bare `test` print a hint naming both and exit with code 2 without starting anything, and both flags together exit with code 2
- [x] 1.2 Keep `run --mode test [--fresh]` unchanged
- [x] 1.3 Menu Tests entry: keep "Quick test" as the pre-selected action and rename the fresh run to "Full test run (fresh)" in `src/autoapp/menu/main.py`
- [x] 1.4 Update `README.md`, `docs/usage-guide.md` (command table and Quick test section), `tests/steps/test_quick_test_command.py` and `tests/menu/test_menu.py`; verify with `uv run pytest tests/test_docs.py`

## 2. Form-link audit and attempt traces (T7)

- [x] 2.1 Add table `form_attempts` as schema version 8 in `src/autoapp/db/schema.py` (job, strategy `http|browser|ats`, hops, status, note, duration, created time; index on job and id) and migration tests in `tests/db/`
- [x] 2.2 Record a trace in `formfinder/finder.py` (`find(..., job_id=...)`, hops with url and reason `entry|redirect|link|model choice`); a trace that cannot be stored never fails form finding; `form_note` stays the one-line summary; pass the job id from `formfinder/store.py`
- [x] 2.3 Add `src/autoapp/formfinder/audit.py`: open the database with SQLite `mode=ro`, count prepared non-email applications by status, job-board links by host, source split and failures grouped by cause (newest trace, else `form_note`); text lines and a stable data shape
- [x] 2.4 Add `autoapp forms --audit [--json]` in `src/autoapp/commands.py`: no setup needed, cannot be combined with `--job`, `--again` or `--dry-run`, `--json` needs `--audit`; print the audit at the end of the quick test for the real workspace
- [x] 2.5 Tests: the database checksum before and after the audit is equal (using `tests/helpers.py`, in `tests/test_form_audit.py`); a finder run writes a trace row with hops; `--json` shape; schema and migration tests in `tests/db/`
- [x] 2.6 Document the audit, the JSON shape and the traces in `docs/assisted-applying.md` and the usage guide

## 3. Quick test form-link sampling and per-link report (T6)

- [ ] 3.1 Add `form_applications: int = 3` to `SampleLimits` in `src/autoapp/steps/context.py`, make preparations 1 email plus the form applications, and raise evaluations to 6
- [ ] 3.2 In `steps/prepare._sample_jobs`, pick form-channel jobs across sources (Arbeitsagentur, alert, other), falling back to the next best score when a source has no candidate; keep search sampling large enough to fill the slots (`jobs_per_source` stays 2)
- [ ] 3.3 In `steps/quicktest.py`, add the "form links" section with one line per prepared form job: source, final address, status, note and resolution path (direct search, careers page, `externeURL`, job page); FAILED when the host is a job board, OK when `found`, WARN otherwise
- [ ] 3.4 Add the fourth checklist state WARN that does not change the exit code; `no_employer_link` is WARN
- [ ] 3.5 Update `tests/steps/test_quicktest_report.py` and `tests/steps/test_quick_sample.py` (fake sources, up to 3 form jobs from different sources, one line per link, a link on `arbeitsagentur.de` or `linkedin.com` is FAILED, exit code reflects FAILED only)
- [ ] 3.6 Update the quick test section and example output in `docs/usage-guide.md`

## 4. Verification

- [x] 4.1 `uv run pytest tests/test_docs.py` passes with the new command names
- [ ] 4.2 Run `autoapp test --quick` against the real workspace configuration after section 3 and check that every prepared form job has one line and that the baseline audit is printed
