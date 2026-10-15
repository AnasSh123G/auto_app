# Design

## Context

See `proposal.md` for the evidence. The current code (branch `traycer/fix-mail-drafting`, based on `main` at `0019476`):

- `src/autoapp/writing/claims.py::check_claims(claims, *, sources, bridgeable, decision, max_assumed)` normalizes each source with `normalize()` (NFKC, typographic quotes → ASCII, casefold, dashes unified, whitespace runs → one space, leading/trailing `.,;:!?"'()` stripped). For a `cv`/`profile`/`instructions` claim it checks `normalize(c.evidence) in normalize(sources[c.type])`: one continuous passage, in the tagged source only. Failure message: `"<text>": the quoted evidence does not appear in <the CV|the profile|your writing instructions>; remove the claim or quote the source exactly`.
- Callers (the only two): `src/autoapp/writing/writer.py::LetterWriter._problems` and `FreeTextWriter._problems`. Both pass `sources={"cv": wctx.cv_text, "profile": wctx.profile_text, "instructions": wctx.instructions}`. `FreeTextWriter` is used by `src/autoapp/assist/llm_fill.py` for form answers.
- `LetterWriter.write` calls the model once, checks, rewrites once if there are any problems, then turns the remaining "hard" problems (claim problems included) into `needs_review`.
- `unsupported_numbers(text, sources)` already accepts numbers from the CV, profile, instructions, ad, salary line and earliest start; it is not changed.
- `src/autoapp/writing/prompt.py::build_user` writes the request line `If availability fits naturally, the candidate can start: {job.earliest_start}` and passes the salary sentence as `job.salary_line`. `JobContext.profile_instructions` holds the search profile's extra writing instructions (the user's own words), which today are **not** an evidence source.
- The prompt asks for "an exact short quote from that source as evidence" (`prompt.py`, `FORMAT`), the schema for "a short quote copied exactly from that source" (`schema.py`, `Claim.evidence`).
- Tests: `tests/writing/test_writer.py` (`CV`, `PROFILE`, `INSTRUCTIONS`, `job()`, `_check()`, `GOOD`/`BAD` drafts, `_writer(script)`), and `tests/assist/test_llm_fill.py`.

## Goals / Non-Goals

**Goals:**
- A claim is accepted when every quoted part is word for word in something the user wrote (CV, profile, instructions, search-profile instructions) or in a fact the app itself gave the writer.
- Invented content still fails: a part that appears nowhere fails the whole claim.
- The model is told how to quote so it produces checkable evidence.

**Non-Goals:**
- No fuzzy or semantic matching, no translation of quotes, no LLM-based verification: the check stays a deterministic substring check per part.
- The ad, the job title and the company name never become evidence (they describe the job, not the candidate).
- No change to `assumed_basic` handling, the cap, the number check, the single rewrite, or how `needs_review` reasons are stored and shown.
- No re-check of letters already flagged in the database.

## Decisions

### D1. Split evidence into parts, require every part

```python
# src/autoapp/writing/claims.py
_EVIDENCE_SPLIT = re.compile(r"\n|;|\s*\|\s*|…|\.\.\.")

def evidence_parts(evidence: str | None) -> list[str]:
    """The normalized, non-empty parts of an evidence string (several exact quotes may be joined)."""
    return [p for p in (normalize(x) for x in _EVIDENCE_SPLIT.split(evidence or "")) if p]
```

Split **before** normalizing (`normalize` turns line breaks into spaces). Splitting can never make a true quote fail: every piece of a substring is itself a substring. It also cannot let an invented part pass, because each part must match on its own.

Alternatives: (a) fuzzy matching (token overlap, edit distance) — rejected, it would let paraphrased or altered facts through and its threshold would need tuning; (b) asking the model for a list of quotes (schema change `evidence: list[str]`) — rejected, it changes the structured-output schema for every provider and the old string form would still arrive in rewrites; splitting a string handles both.

### D2. One pool of accepted sources, independent of the tag

```python
def check_claims(claims, *, sources, bridgeable, decision, max_assumed,
                 app_facts: Iterable[str] = ()) -> list[str]:
    haystacks = [normalize(v or "") for v in sources.values()] + [normalize(f) for f in app_facts if f]
    ...
    parts = evidence_parts(c.evidence)
    if not parts:
        problems.append(f'"{c.text}": no evidence quote from {_SOURCE_NAMES[c.type]}; remove it or quote the source')
        continue
    missing = next((p for p in parts if not any(p in h for h in haystacks)), None)
    if missing is not None:
        problems.append(
            f'"{c.text}": the quoted evidence does not appear in the CV, the profile or your writing '
            f'instructions: "{missing[:120]}"; remove the claim or quote the source exactly'
        )
```

The message keeps the substring `does not appear in the CV` (an existing test and the `needs_review` texts users have seen rely on it) and names the failing part, which tells the rewrite exactly what to fix. `app_facts` defaults to empty so any other caller keeps working. The `assumed_basic` branch and the cap are unchanged.

Why ignore the tag: the tag only says where the model thinks the fact came from. A fact that is in the user's own CV is true whatever the model tagged it; failing it only produced rewrites and false flags.

### D3. App-supplied facts and the instructions source, built in one place

```python
# src/autoapp/writing/prompt.py
def start_line(earliest_start: str) -> str:
    return f"If availability fits naturally, the candidate can start: {earliest_start}"

def app_facts(job: JobContext) -> list[str]:
    """Facts the app itself gives the writer; accepted as claim evidence. Never the ad, title or company."""
    facts: list[str] = []
    if job.earliest_start:
        facts += [f"earliest_start: {job.earliest_start}", start_line(job.earliest_start)]
    if job.salary_line:
        facts.append(job.salary_line)
    return facts

def evidence_sources(wctx: WritingContext, job: JobContext) -> dict[str, str]:
    """What the user wrote: CV, profile, writing instructions plus the search profile's extra instructions."""
    instructions = "\n".join(t for t in (wctx.instructions, job.profile_instructions or "") if t.strip())
    return {"cv": wctx.cv_text, "profile": wctx.profile_text, "instructions": instructions}
```

`build_user` uses `start_line(...)` instead of its own f-string, so the accepted fact and the request text can never drift apart. Both `LetterWriter._problems` and `FreeTextWriter._problems` call `check_claims(..., sources=evidence_sources(wctx, job), app_facts=app_facts(job), ...)`.

`job.description`, `job.title` and `job.company` are deliberately absent. A test proves an ad-only quote fails.

### D4. Prompt and schema wording

Replace the `- claims: …` line of `FORMAT` in `prompt.py` with these two bullets (exact text):

```
- claims: list every factual statement about the candidate that you made anywhere (experience, skills, degrees, certificates, languages, dates, availability, salary), typed cv / profile / instructions / assumed_basic. Sentences that only name the position, the company or the attached documents are not claims; do not list them.
- evidence: copy the exact words from the source, in the source's own language (quote a German CV in German even when you write in English). Never paraphrase, shorten inside or translate a quote. When one statement draws on several places in the sources, give one exact quote per place and separate the quotes with " | ". For assumed_basic, give the bridgeable requirement instead.
```

In `schema.py`:
- `Claim.evidence` description: `"For cv/profile/instructions: the exact words from the source, in the source's language. Several quotes from different places are separated by ' | '. Never paraphrase or translate."`
- `LetterDraft.claims` description: `"Every factual statement about the candidate in paragraphs, motivation or email. Sentences that only name the position, the company or the attachments are not claims."`
- `FreeTextAnswer.claims` description: `"Every factual statement about the candidate in the answer, typed and with exact evidence (several quotes separated by ' | ')."`

## Risks / Trade-offs

- [Very short parts (e.g. `Linux`) match easily] → No worse than today, where a single short quote already passes; each part must still be in the user's own text, and the independent number check stays.
- [A real CV line containing `;` or `|` is split] → Each piece is still in the CV, so it still passes.
- [The model may stop listing a real claim, calling it "not a claim"] → The wording limits the exception to sentences that only name the position, company or attachments; the number check still covers invented figures.
- [System prompt text changes] → One prompt-cache miss per provider after the update.

## Migration Plan

No data migration. Letters prepared after the update use the new check; jobs already flagged keep their reasons until re-prepared with `autoapp prepare --job <id> --force`. Rollback: revert the merge commit.
