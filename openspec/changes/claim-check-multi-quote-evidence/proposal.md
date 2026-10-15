# Proposal

## Why

The claim check that guards every cover letter flags almost every letter as `needs_review`, although the letters invent nothing. On 2026-10-15 the letters for Acme (`<job-a>`, German) and Beispiel Werk (`<job-c>`, English) were regenerated with the configured model and each claim was checked against the user's real CV. Nine of 17 claims were flagged, and all nine were backed by the CV or by data the app itself supplied:

- **7 of 9: quotes joined from several CV lines.** One sentence of a letter usually combines facts from several CV lines. The model quotes each line word for word and joins the quotes with a line break or `; ` (example: `Umsetzung neuer Anforderungen und Fehlerbehebung im Bereich der Backend-Programmierung; Erstellung von Softwaretests (inklusive pytest)`). Every part is in the CV, but `src/autoapp/writing/claims.py::check_claims` only accepts the whole evidence as one continuous passage.
- **1 of 9: a fact the app supplies.** "Ich kann ab dem 01.11.2026 starten" quotes the app's own request line "If availability fits naturally, the candidate can start: 01.11.2026" (`writing/prompt.py::build_user`), which is not among the accepted sources. A claim tagged with the wrong source (an `instructions` claim whose quote is in the profile) fails the same way.
- **1 of 9: a sentence that is not a claim.** "Ich sende meine Bewerbungsunterlagen für die Position …" is listed as a claim because the prompt says to list "every factual claim you made anywhere", and its "evidence" is the app's request.

Effects: almost every letter is flagged `needs_review`; each false flag costs a second (rewrite) LLM call; the rewrite is told to remove true experience; and in review mode the flag kept the email drafts out of Gmail (handled separately by `gmail-drafts-without-local-files`).

## What Changes

- The evidence of a `cv`, `profile` or `instructions` claim MAY consist of several exact quotes. It is split at line breaks, `;`, ` | `, `…` and `...`; the claim is accepted when **every** part occurs word for word (ignoring case, whitespace and quote style, as today) in an accepted source. An invented part still fails the claim, and the problem names that part.
- A part counts as backed when it occurs in **any** accepted source: the CV, the profile text, the user's writing instructions, or the search profile's extra writing instructions. The tag (`cv` / `profile` / `instructions`) no longer has to name the right one.
- Facts the app gives the writer are accepted sources too: the earliest start date (both as `earliest_start: <date>` and as the exact request line) and the salary sentence. **The job ad, the job title and the company name are never accepted as evidence.**
- The prompt and the response schema tell the model: quote the exact words, in the source's language, one quote per place, several quotes separated by ` | `; never paraphrase or translate inside a quote; sentences that only name the position, the company or the attached documents are not claims.
- The same check is used for form answers (`FreeTextWriter`), so they gain the same behaviour.
- Unchanged: `assumed_basic` rules and cap, the independent number check (every number must appear in a source), one rewrite at most, and `needs_review` for whatever is still unsupported after the rewrite.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cover-letter-writing`: the "Claim check" requirement now allows multi-part evidence, accepts any of the accepted sources whatever the tag, accepts app-supplied facts, excludes the ad, and states what is not a claim.

## Impact

- Code: `src/autoapp/writing/claims.py`, `src/autoapp/writing/prompt.py`, `src/autoapp/writing/schema.py`, `src/autoapp/writing/writer.py`.
- Tests: `tests/writing/test_writer.py` (new regression tests; one assertion text kept compatible).
- Behaviour: fewer false `needs_review` flags and fewer rewrite calls; letters already flagged in the database keep their flags until they are prepared again (`autoapp prepare --job <id> --force`).
- The system prompt text changes, so the provider's prompt cache misses once after the update.
- No database, config or dependency changes. Independent of `gmail-drafts-without-local-files`; the two changes touch different files and can be applied in either order.
