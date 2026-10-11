# Design

## Context

- `autoapp test` was the quick test (`commands.py`, `SampleLimits` in `steps/context.py`, the checklist in `steps/quicktest.py`). The whole fresh test run is `autoapp run --mode test --fresh`. The menu's Tests entry offers "Quick test" as its default.
- `_sample_jobs` in `steps/prepare.py` prepares 2 jobs: one email application and one form application. The checklist reports form finding as counts per status only.
- `formfinder.find` returns an `Outcome` (status, url, reached, note) and stores nothing about the walk. `applications.form_note` is the one-line summary.
- Phase 3 (better navigation) will be chosen from a measured baseline, so the quick test and a standalone command must show today's distribution, and every form-finding run must leave data for grouping failures.

## Goals / Non-Goals

**Goals:**
- One word, two explicit kinds of test; a bare `test` can never start the expensive one by accident.
- A per-link view of the quick test's form jobs, and a read-only view of the real workspace's saved links.
- Traces of every form-finding run, enough to group failures by cause.

**Non-Goals:**
- No change to how forms are found; no browser or ATS strategy (the `browser` and `ats` strategy values are reserved).
- No change to the quick test's isolation: it still never changes the real workspace.
- No pre-filled offline forms (Phase 4).

## Decisions

1. **Flags, not subcommands.** `test --quick` and `test --full` on one command. A bare `test` prints both choices and exits with 2 (a usage error), so scripts notice. Both flags together also exit with 2. `run --mode test [--fresh]` is untouched, since scripts and systemd units use `run`. `--quick` calls `run_with_mode(..., fresh=True, sample=True)`; `--full` calls it with `sample=False`.
   *Alternative:* keep `test` as the quick test and add a `--full` flag. Rejected in the spec: a bare `test` would still start something, and the name stays ambiguous.

2. **The audit reads the database read-only.** `formfinder/audit.open_readonly` opens the file with `file:...?mode=ro`, so no code path in the audit can write. A workspace without a database gives an empty audit. The audit selects `jobs.status = 'prepared'` joined with applications whose channel is not `email` and that have a folder. `job_sources.source` gives the source split (a job with several sources counts for each). A link is "on a job board" when its host is in `DIRECTORY_HOSTS` or in `assist.restricted_domains` (the configuration is loaded when possible, else the defaults). A workspace without the `form_status` or `form_note` columns still audits, with every job `unchecked`.
   The `--audit` option is allowed without a configured workspace (the setup gate treats it like `--dry-run`).

3. **Failures are grouped by a normalised cause.** For every status other than `found` and `unchecked`, the cause is the note of the newest `form_attempts` trace for the job (else `form_note`), cut at the first `;`, `:` or `(` and with digits replaced by `N`, so equal causes group together ("no form within N steps"). The JSON shape is `{prepared, by_status, on_job_board: {total, by_host}, by_source: {source: {total, found}}, failures: [{status, cause, count}]}`, documented in `docs/assisted-applying.md`.

4. **One trace per `find` call.** `find(ctx, entry, ..., job_id=None)` wraps the existing walk in `_walk`, collects `hops` (url and the reason the page was visited: `entry`, `redirect`, `link` for a clear candidate, `model choice` for a model-picked one) and writes one `form_attempts` row when `job_id` is given. A failure to store (any sqlite error) only logs a warning. The strategy is always `http` today; the table's `CHECK` already allows `browser` and `ats` so Phase 3 needs no migration. `form_note` is unchanged.

5. **Per-link report rules (T6).** The checklist section classifies each prepared form job from its stored outcome: FAILED when the final address host is a job board (`DIRECTORY_HOSTS` or `assist.restricted_domains`), OK when the status is `found`, WARN for every other status. WARN is a new checklist state next to OK, FAILED and SKIPPED and does not change the exit code. `no_employer_link` is WARN, because the app knows it is missing and says why, whereas a job-board link presented as the application is the outcome Phase 1 wants to eliminate. Each line shows the job's short id, its source, the final address, the status with its note and the resolution path read from the traces and seeds (direct search, careers page, `externeURL`, or the job page).

6. **Sample across sources (T6).** `SampleLimits.form_applications` is 3, preparations are `1 email + form_applications`, and evaluations rise from 4 to 6. `_sample_jobs` fills the form slots in the order Arbeitsagentur, alert, other; a source without a candidate gives its slot to the next best score. `jobs_per_source` stays 2, so each source can still supply its slot.

## Risks / Trade-offs

- [A bare `test` used to run the quick test; existing habits and scripts break] → The hint names both choices and the exit code is 2, so nothing starts by accident. Docs, menu and README are updated.
- [The audit cannot see links the finder never ran for] → They show as `unchecked`; `autoapp forms` backfills them.
- [Traces grow the database] → One short row per finder run (note cut at 200 characters, a handful of hops); rows are removed with their job (`ON DELETE CASCADE`).
- [WARN could hide real problems] → WARN lines are printed with their reason, and the real-workspace audit counts them by status.
- [Cause grouping merges unlike causes] → Grouping is a heuristic on the first clause of the note; the raw traces stay in `form_attempts`.

## Migration Plan

Schema version 8 adds the `form_attempts` table (empty at first). No data is converted. Rolling back means ignoring the table. Scripts that call bare `autoapp test` must be changed to `--quick` or `--full`.

## Open Questions

- T6 (form-link sampling and the per-link report with the WARN state) was being implemented on another branch when this change was written. Sections 3 and 4.2 of `tasks.md` are specified from the ticket and are unchecked; the delta specs describe the intended behavior and must be reconciled with that branch before archiving.
- How the "resolution path" is read for each line (direct search, careers page, `externeURL`, job page) is not fixed by the spec beyond the four names. The traces only record hops, so the source of the starting address may need data from `posting_searches` and `employer_seeds`.
- The audit's cause grouping is a text heuristic; Phase 3 may need structured cause codes in `form_attempts`.
- The spec's success targets (at least 85 % of prepared form applications with `found`, at most 3 % on a job board) are measured, not enforced; no command fails on them.
