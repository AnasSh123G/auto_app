# Tasks

Read `proposal.md`, `design.md` and `specs/cover-letter-writing/spec.md` first. Work in `/home/anas/.traycer/worktrees/anassh123g__auto_app/traycer-fix-mail-drafting` on branch `traycer/fix-mail-drafting` and run every command from there. Run tests with `uv run pytest -q <path>` and lint with `uv run ruff check src tests`.

**The rule this change enforces:** a claim passes when *every* part of its evidence appears word for word in something the user wrote (CV, profile, writing instructions, search-profile instructions) or in a fact the app gave the writer (start date, salary sentence). The job ad, job title and company name are *never* evidence. Do not add fuzzy matching, translation or any other loosening beyond this.

**Privacy rule for tests:** do not copy text from `/home/anas/dev/auto_app/workspace/documents/` (the user's real CV) into the repository. Use the invented German CV lines given below.

## 1. The checker (`src/autoapp/writing/claims.py`)

- [x] 1.1 Add `_EVIDENCE_SPLIT` and `evidence_parts(evidence)` exactly as in design D1, below `normalize`. Update the module docstring's first bullet to: "``cv`` / ``profile`` / ``instructions`` claims need evidence whose every part (several exact quotes may be joined by line breaks, ``;``, `` | `` or ``…``) occurs in an accepted source: the CV, profile, instructions or an app-supplied fact. The tag does not have to name the right source; the ad is never a source." Verify: in a new test `test_evidence_parts_split_and_normalize` in `tests/writing/test_writer.py`, `evidence_parts("A b\nC; D | E…F...G")` returns `["a b", "c", "d", "e", "f", "g"]`, `evidence_parts("  ")` and `evidence_parts(None)` return `[]`, and `evidence_parts("Master of Science | Uni")` returns `["master of science", "uni"]`.
- [x] 1.2 Change `check_claims` as in design D2: add the keyword `app_facts: Iterable[str] = ()`, build one `haystacks` list from all `sources` values plus `app_facts`, replace the single-passage check with the per-part check, and use the new failure message (it must contain `does not appear in the CV` and the first failing part in quotes, cut to 120 characters). Keep the "no evidence quote" message (now triggered when `evidence_parts` is empty), the whole `assumed_basic` branch and the cap check exactly as they are. Remove `norm_sources` if it becomes unused. Verify: `uv run pytest -q tests/writing/test_writer.py` still passes, including `test_invented_aws_years_rejected` (its assertion `"does not appear in the CV" in probs[0]` must hold without editing it).

## 2. Sources and prompt (`src/autoapp/writing/prompt.py`, `src/autoapp/writing/schema.py`)

- [x] 2.1 Add `start_line`, `app_facts` and `evidence_sources` to `prompt.py` exactly as in design D3 (place them after `bridgeable_candidates`). In `build_user`, replace `lines.append(f"If availability fits naturally, the candidate can start: {job.earliest_start}")` with `lines.append(start_line(job.earliest_start))`. Verify: a test `test_app_facts_and_sources` asserts `app_facts(job())` is `[]`; `app_facts(job(earliest_start="01.11.2026", salary_line="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr."))` equals `["earliest_start: 01.11.2026", start_line("01.11.2026"), "Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr."]`; `start_line("01.11.2026") in build_user(job(earliest_start="01.11.2026"))`; no element of `app_facts(...)` contains the job's `description`, `title` or `company`; `evidence_sources(WCTX, job(profile_instructions="I led a student team of 3"))["instructions"]` contains both `INSTRUCTIONS` and `"I led a student team of 3"`.
- [x] 2.2 Replace the `- claims: …` bullet of `FORMAT` with the two bullets from design D4 (copy the text exactly, including the straight double quotes around ` | `). Update the three schema descriptions in `schema.py` exactly as listed in design D4. Verify: a test `test_quoting_rules_in_prompt_and_schema` asserts that the joined text of `build_system(WCTX)` contains `separate the quotes with " | "`, `Never paraphrase, shorten inside or translate a quote` and `are not claims`, and that `Claim.model_fields["evidence"].description` contains `' | '` and `Never paraphrase or translate`, and `LetterDraft.model_fields["claims"].description` contains `are not claims`. `test_system_prompt_parts_and_order` must still pass.

## 3. Writers use the new sources (`src/autoapp/writing/writer.py`)

- [x] 3.1 In `LetterWriter._problems` and `FreeTextWriter._problems`, replace `sources={"cv": wctx.cv_text, "profile": wctx.profile_text, "instructions": wctx.instructions}` with `sources=evidence_sources(wctx, job)` and add `app_facts=app_facts(job)` to both `check_claims(...)` calls. Import both from `autoapp.writing.prompt` in the existing import block. Do not change `unsupported_numbers` calls, lint, the rewrite logic or `needs_review`. Verify: `uv run pytest -q tests/writing tests/assist` passes.
- [x] 3.2 Update the module docstring of `writer.py` only if it mentions how evidence is matched (it currently does not; leave it if so). Verify: `git diff src/autoapp/writing/writer.py` shows only the import and the two `check_claims` argument changes (plus a docstring line if one was needed).

## 4. Regression tests (`tests/writing/test_writer.py`)

Add a section `# --- claim evidence: several quotes, accepted sources ---` after the existing claim tests, with a second, invented CV that has the relevant lines **not** next to each other:

```python
CV_DE = (
    "Werkstudent Backend, Beispiel Energie GmbH\n"
    "Umsetzung neuer Anforderungen und Fehlerbehebung im Backend\n"
    "Pflege der internen Dokumentation\n"
    "Erstellung von Softwaretests (inklusive pytest)\n"
    "Werkstudent, Beispiel Web GmbH\n"
    "Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken\n"
    "Betreuung von Kundenanfragen\n"
    "Testen und Überprüfen von Code anderer Entwickler (Peer Code Reviews)\n"
    "Kenntnisse\nPytorch\nNumpy\nPandas\nDocker\nLinux\n"
    "Sprachen: Deutsch (C1), Englisch (C2)"
)
SOURCES_DE = {"cv": CV_DE, "profile": "name: Max\nearliest_start: 01.11.2026", "instructions": "Kurz schreiben."}

def _check_de(claims, app_facts=()):
    return check_claims(claims, sources=SOURCES_DE, bridgeable=[], decision="apply", max_assumed=5,
                        app_facts=app_facts)
```

- [x] 4.1 `test_quotes_from_several_cv_lines_pass` — `pytest.mark.parametrize` over the separators `"; "`, `"\n"`, `" | "`, `" … "`: evidence `"Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken" + sep + "Testen und Überprüfen von Code anderer Entwickler (Peer Code Reviews)"`, type `cv` → `_check_de` returns `[]`. Add a second case for the English claim "At the energy company I implemented backend requirements and wrote pytest tests." with evidence `"Umsetzung neuer Anforderungen und Fehlerbehebung im Backend; Erstellung von Softwaretests (inklusive pytest)"` → `[]`, and the skills list `"Pytorch; Numpy; Pandas; Docker; Linux"` → `[]`. Verify: the tests pass, and they fail if you temporarily revert step 1.2 (check once, then restore).
- [x] 4.2 `test_one_invented_part_fails_and_is_named` — evidence `"Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken | 5 Jahre AWS"` → exactly one problem, containing `does not appear in the CV` and `"5 jahre aws"`. Also `"Pytorch; Numpy; Kubernetes"` → one problem naming `kubernetes`. Verify: the test passes.
- [x] 4.3 `test_wrong_tag_still_passes` — a claim of type `instructions` and one of type `profile`, both quoting `Erstellung von Softwaretests (inklusive pytest)` → `[]`. Verify: the test passes.
- [x] 4.4 `test_app_supplied_start_date_and_salary_pass` — build `j = job(earliest_start="01.11.2026", salary_line="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.")`; claims: `Claim(text="Ich kann ab dem 01.11.2026 starten.", type="instructions", evidence=start_line("01.11.2026"))` and `Claim(text="Gehalt 65.000", type="profile", evidence="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.")` → `_check_de(claims, app_facts=app_facts(j))` returns `[]`; and without `app_facts` the start-date claim fails (the salary claim too). Verify: the test passes.
- [x] 4.5 `test_ad_text_is_never_evidence` — with `j = job()` (its `description` contains `Grundkenntnisse in Kubernetes, CI/CD, Terraform`), a claim `Claim(text="Terraform-Erfahrung", type="cv", evidence="Grundkenntnisse in Kubernetes, CI/CD, Terraform")` checked with `check_claims(..., sources=evidence_sources(WCTX, j), app_facts=app_facts(j), bridgeable=[], decision="apply", max_assumed=5)` returns one problem. Also a claim quoting the job title `"Backend Engineer (m/w/d)"` fails. Verify: the test passes.
- [x] 4.6 `test_search_profile_instructions_are_evidence` — `j = job(profile_instructions="I led a student team of 3.")`; call `check_claims([Claim(text="Team geleitet", type="instructions", evidence="I led a student team of 3")], sources=evidence_sources(WCTX, j), app_facts=app_facts(j), bridgeable=[], decision="apply", max_assumed=5)` → `[]`. Verify: the test passes.
- [x] 4.7 `test_multi_quote_draft_needs_one_call` — copy `GOOD`, change its first claim's evidence to `"Entwicklung von Python-Services mit FastAPI | Backend Developer, Muster GmbH, 2022 bis heute"` (both parts are in the existing `CV` constant), run `_writer([draft]).write(WCTX, job(), FRAME)` → `out.llm_calls == 1` and `out.needs_review == []`. Verify: the test passes (with the old checker it would need 2 calls).
- [x] 4.8 `test_form_answer_accepts_multi_quote_evidence` — `FreeTextWriter` with `FakeLLM(script={"form_fill": [{"answer": "Ich baue seit 2022 Python-Services und möchte das bei Ihnen fortsetzen.", "claims": [{"text": "Python seit 2022", "type": "cv", "evidence": "Entwicklung von Python-Services mit FastAPI | Backend Developer, Muster GmbH, 2022 bis heute"}]}]})` → `out.needs_review == []` and `len(llm.calls) == 1`. Verify: the test passes.
- [x] 4.9 Run the whole writing and assist suites: `uv run pytest -q tests/writing tests/assist tests/test_coverage_gaps.py`. Verify: 0 failures.

## 5. Docs

- [x] 5.1 In `docs/cover-letters.md`, find the section that explains the claim check / `needs_review` (search for "claim" and "evidence"). Add 3–5 sentences: evidence may be several exact quotes joined by ` | `; every quote must be word for word in the CV, profile, writing instructions or the search profile's instructions (or the start date / salary sentence the app supplies); the ad never counts; a `needs_review` reason "the quoted evidence does not appear in the CV, the profile or your writing instructions: \"…\"" names the quote that was not found. If the file has no such section, add a short "How claims are checked" section at the end. Verify: `uv run pytest -q tests/test_docs.py` passes.

## 6. Whole suite, lint, validation

- [x] 6.1 Run `uv run pytest -q`. Verify: 0 failures, 0 errors. If a test outside `tests/writing` and `tests/assist` fails because it asserted the old message `does not appear in your writing instructions` or `does not appear in the profile`, change only that assertion to the new message; do not loosen anything else.
- [x] 6.2 Run `uv run ruff check src tests`. Verify: "All checks passed!".
- [x] 6.3 Run `openspec validate claim-check-multi-quote-evidence --strict`. Verify: "Change 'claim-check-multi-quote-evidence' is valid".

## 7. Commit and merge into main

- [ ] 7.1 Run `git status --short`. Stage **only** this change's files: `git add src/autoapp/writing tests/writing docs/cover-letters.md openspec/changes/claim-check-multi-quote-evidence` (plus any test file you had to touch in 6.1, by explicit path). Do not stage `openspec/changes/gmail-drafts-without-local-files` or files of another change unless that change is already implemented and committed by its own tasks; never stage anything under `workspace/`. Commit:

  ```
  fix: accept claim evidence made of several exact quotes

  The claim check rejected almost every letter: one sentence often draws
  on several CV lines, and the model quotes each line exactly but joins
  them with a line break or "; ". Evidence is now split into parts and
  every part must occur word for word in the CV, profile, writing
  instructions, search-profile instructions or an app-supplied fact
  (earliest start, salary sentence), whatever the claim's tag. The ad,
  job title and company name are never evidence. The prompt and schema
  ask for exact quotes in the source's language separated by " | " and
  say that naming the position, company or attachments is not a claim.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

  Verify: `git log -1 --stat` lists only the intended files.
- [ ] 7.2 Merge into `main`, which is checked out at `/home/anas/dev/auto_app` (you cannot check out `main` in this worktree). Run `git -C /home/anas/dev/auto_app status --short`; if it shows any change, stop and report (do not stash or discard the user's work). If clean: `git -C /home/anas/dev/auto_app merge --no-ff traycer/fix-mail-drafting -m "Merge branch 'traycer/fix-mail-drafting' into main"`. On conflicts run `git -C /home/anas/dev/auto_app merge --abort` and report the files; do not resolve by guessing. Do not push unless the user asks. Verify: `git -C /home/anas/dev/auto_app log --oneline -2` shows the merge commit, and `cd /home/anas/dev/auto_app && uv run pytest -q tests/writing` passes.

## 8. Live check with the configured model (after the merge)

- [ ] 8.1 From `/home/anas/dev/auto_app`, regenerate one German and one English letter **without writing files or changing job state**, using the configured `write` model: write a throwaway script in the session scratchpad (not in the repo) that opens `RunContext.open(Workspace(Path("workspace")), start_run=False)`, builds `WritingContext`/`JobContext` for jobs `537eab` (German) and `6a1e45` (English) the same way `src/autoapp/steps/prepare.py::_prepare_one` does, calls `LetterWriter(...).write(...)`, and prints `outcome.llm_calls` and `outcome.needs_review` for each. This costs a few model calls. Verify: report both results. Expected: at most a few `needs_review` reasons, none of them a quote that is word for word in the CV. If a reason still names a quote that is in the CV, report it with the quote; do not change the checker again in this change.

## Workflow follow-up

- Jobs already flagged keep their old reasons. After the user agrees, re-prepare them with `autoapp prepare --job <id> --force` (this calls the model again).
- Archive with `/opsx:archive claim-check-multi-quote-evidence` after review, which syncs the delta into `openspec/specs/cover-letter-writing/spec.md`.
