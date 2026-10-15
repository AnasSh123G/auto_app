# Design

## Context

See `proposal.md` (Why) for the two root causes. Facts about the current code this design builds on (checked on branch `traycer/fix-mail-drafting`, based on `main` at `0019476`):

- **Run order** (`src/autoapp/steps/pipeline.py::run_pipeline`): … → `prepare_jobs` → in review mode `draft_applications` → export. `ctx.mode` is `"review"`, `"automatic"` or `"test"`; `RunContext.open(ws, mode=None)` falls back to `config.run.default_mode`, which defaults to `"review"` (`src/autoapp/config/models.py:324`). The user's `workspace/config.yaml` has `run.default_mode: review`. **Note:** test contexts created with `RunContext.open(make_workspace(tmp_path))` are therefore in review mode too.
- **Prepare** (`src/autoapp/steps/prepare.py::_prepare_one`) writes the letter with `LetterWriter`, renders `cover_letter.pdf` with `PdfRenderer.html_to_pdf(html, path) -> pages`, copies the selected documents into the folder, merges `Bewerbungsmappe.pdf` with `package.pdf.bundle(letter_pdf, cv, certs, out, max_mb)`, writes `email.eml` with `package.eml.build_application_email(...)` + `write_eml`, writes README/job/evaluation/apply/form_answers, then `repo.upsert_application(folder=..., channel=..., contact_email=r.apply_email, ...)`, `repo.set_needs_review(job_id, review)` and transitions the job to `prepared`.
- **Draft** (`src/autoapp/steps/drafts.py::draft_applications`) picks `prepared` email jobs (`draftable_jobs`), rejects them in `_reasons_not_draftable` (status, channel, **needs_review**, missing `email.eml`), loads `email.eml` with `steps.send.load_message(folder)`, and appends it with a `DraftStore` (`mail/drafts.py::ImapDraftStore`, real IMAP). The `drafts` table records each draft (`job_id, message_id, folder, recipient, subject, state, created_at, replaced_at`).
- **Send** (`src/autoapp/steps/send.py`) reads `email.eml` in three places: `send_applications`, `send_test_email` (+ `_latest_prepared_email_job`) and `send_test_applications`.
- **Render** (`src/autoapp/steps/render.py::render_application`) re-renders from the folder's `cover_letter.md`; `parse_cover_letter_md(text, language)` turns the Markdown back into a `CoverLetter`.
- `CoverLetter.to_markdown()` holds subject, salutation, paragraphs, closing and signature, but **not** `email_body`; the email body only lives in `email.eml` today.
- `Repo.upsert_application` only accepts names in `db/repo.py::APPLICATION_FIELDS`.
- DB migrations are the list `MIGRATIONS = [_V1 … _V9]` in `db/schema.py`; step N moves `PRAGMA user_version` from N-1 to N.
- Gmail is reached with the existing `mail.sending` account (`preset: gmail`, `address: alshoker.anas@gmail.com`, password env `AUTOAPP_SMTP_PASSWORD`, which is set in `workspace/.env`). IMAP APPEND to `[Gmail]/Entwürfe`/`[Gmail]/Drafts` already works in `ImapDraftStore`.

## Goals / Non-Goals

**Goals:**
- In review mode, an email job with a found address never causes a file or folder under the workspace — not in prepare, not in draft, not on failure.
- Every such job ends up as one Gmail draft with the email text, `Bewerbungsmappe.pdf` (cover letter + PDF CV + PDF certificates) and the other selected documents attached, whether or not it is flagged `needs_review`.
- A failed draft can be retried without another LLM call.
- The three existing email folders in the user's workspace are drafted and removed by the next review run.

**Non-Goals:**
- Automatic and test mode keep their folders and `email.eml` (they are not what the user asked about; switching them would rewrite the send/idempotency path). The shared message builder makes a later switch cheap.
- Form applications keep their folders in every mode.
- No change to how the letter checker decides `needs_review` (it flags many letters with "the quoted evidence does not appear in the CV"; that is a separate problem).
- No Gmail labels, no Gmail API/OAuth, no use of the Claude Gmail MCP connector: the app runs unattended and already has working IMAP access.
- No change to which documents are selected (`package.documents_select.select_documents`) or to the email text the LLM writes.

## Decisions

### D1. The rule is "review mode + channel email + address found ⇒ no workspace files"

A helper decides it in one place:

```python
# src/autoapp/steps/prepare.py
def draft_only(ctx: RunContext, channel: str | None, apply_email: str | None) -> bool:
    """Review mode never writes an email application to disk: it goes to the mailbox's Drafts folder only."""
    return ctx.mode == "review" and channel == "email" and bool(apply_email)
```

Alternatives: (a) apply to every mode — rejected for now, see Non-Goals; (b) a config switch — rejected, the user wants no option to get this wrong.

### D2. Store what is needed to rebuild the email in the `applications` row (migration 10)

New nullable columns: `letter_md TEXT` (output of `CoverLetter.to_markdown()`), `email_subject TEXT`, `email_body TEXT`, `message_id TEXT`. The recipient already lives in `applications.contact_email`, the language in `applications.language`, the contact person in `applications.contact_person`. Add the four names to `APPLICATION_FIELDS`.

```python
# src/autoapp/db/schema.py
_V10 = """
ALTER TABLE applications ADD COLUMN letter_md TEXT;
ALTER TABLE applications ADD COLUMN email_subject TEXT;
ALTER TABLE applications ADD COLUMN email_body TEXT;
ALTER TABLE applications ADD COLUMN message_id TEXT;
"""
MIGRATIONS: list[str] = [_V1, _V2, _V3, _V4, _V5, _V6, _V7, _V8, _V9, _V10]
```

Why the database: the user forbids files; the database already stores the ad text, evaluation and status for every job, and without the letter text a failed draft or `draft --again` would need a new LLM call. Attachments are **not** stored: they are rebuilt from the user's own documents in `workspace/documents/` (which exist anyway) each time.

### D3. One builder for the email: `src/autoapp/package/email_message.py`

```python
@dataclass
class BuiltEmail:
    message: EmailMessage
    bundle: Path            # Bewerbungsmappe.pdf (inside the temp dir, or the old folder)
    warnings: list[str]     # e.g. a DOCX CV left out of the bundle, letter longer than one page

class NoStoredEmail(ValueError):
    """The job has neither stored letter text nor an email.eml."""

@contextmanager
def application_email(ctx, job_id, *, renderer_factory=PdfRenderer,
                      message_id: str | None = None) -> Iterator[BuiltEmail]:
    """The job's complete application email.

    An old folder with email.eml is read as it is (legacy). Otherwise the email is
    built from the stored letter in a TemporaryDirectory(prefix="autoapp-email-")
    outside the workspace, which is removed when the with-block ends, also on errors.
    ``message_id`` overrides the stored Message-ID (draft --again).
    """
```

Build steps when there is no folder (all paths inside the temp dir `tmp`):
1. `app = repo.get_application(job_id)`; `job = repo.get_job(job_id)`; if `app["letter_md"]` is empty → `raise NoStoredEmail(...)`.
2. `language = app["language"] or "de"`; `letter = parse_cover_letter_md(app["letter_md"], language)`.

   **Import rule (avoids a circular import):** `steps/send.py` will import this module, and `steps/render.py` imports `steps/send.py`. So `package/email_message.py` must NOT import anything from `autoapp.steps`. Move `parse_cover_letter_md` from `steps/render.py` into `package/email_message.py`, and move `load_message` from `steps/send.py` into `package/eml.py`; keep both names importable from their old modules (`from autoapp.package.email_message import parse_cover_letter_md` in `steps/render.py`, `from autoapp.package.eml import load_message` in `steps/send.py`) because tests import them from there. Type-hint `ctx` with `if TYPE_CHECKING: from autoapp.steps.context import RunContext`.
3. Recipient lines exactly as `render_application`: `[job["company"]]` + `"z. Hd. "`/`"Attn. "` + `app["contact_person"]` when set. `html = R.render_cover_letter_html(letter, personal, recipient, date_line(personal.city, date.today(), language))`.
4. `with renderer_factory() as renderer: pages = renderer.html_to_pdf(html, tmp / "cover_letter.pdf")`; `pages > 1` → warning.
5. Requested documents from the current evaluation: `final = FinalEvaluation.model_validate(json.loads(repo.current_evaluation(job_id)["payload"]))`; `selection = select_documents(ctx.profile, language, list(final.result.requested_documents), ctx.config.writing.always_attach_certificates)` — the same call as `_prepare_one`. Source paths come from `ctx.documents.path_of(entry)` (files in `workspace/documents/`, read only, never copied).
6. `b = bundle(tmp / "cover_letter.pdf", cv_path, cert_paths, tmp / "Bewerbungsmappe.pdf", ctx.config.email.max_bundle_mb)`; `warnings += b.skipped`.
7. Extra attachments exactly as `_prepare_one`: `extras` + photo + every CV/certificate path whose suffix is not `.pdf`, de-duplicated.
8. `msg = build_application_email(sender_name=f"{first} {last}", sender_email=personal.email, to=app["contact_email"], subject=app["email_subject"] or letter.subject, body=app["email_body"] or "", attachment=tmp / BUNDLE, extra_attachments=extras, message_id=message_id or app["message_id"])`.
9. `yield BuiltEmail(msg, tmp / BUNDLE, warnings)`; the `TemporaryDirectory` context removes `tmp`.

Legacy branch (folder with `email.eml` exists): `yield BuiltEmail(load_message(folder), folder / BUNDLE, [])`; if `message_id` is given, replace the `Message-ID` header. No temp dir.

Alternatives: keep writing `email.eml` and delete the folder after the draft — rejected: a failed draft would leave files behind, and the user wants none at any moment.

### D4. Prepare in draft-only mode writes the letter, stores it, nothing else

In `_prepare_one`, compute `email_only = draft_only(ctx, r.channel, r.apply_email)` right after `r = final.result`. When true:
- Skip the `folder.exists() and force` backup and `folder.mkdir`.
- Run `_write_letter(...)` with `pdf_path = Path(tmp) / "cover_letter.pdf"` inside `with tempfile.TemporaryDirectory(prefix="autoapp-letter-") as tmp:` (the one-page check still works; the PDF is thrown away).
- Keep `_check_document_names(...)` (duplicate attachment names are still a problem).
- Skip copies, bundle, `form_answers.md`, `apply.html`, `email.eml`, `job.md`, `evaluation.md`, `README.md`.
- `review = list(outcome.needs_review) + page_problems + email_text_reasons(repo, job["id"])`.
- `message_id = make_msgid(domain=personal.email.rpartition("@")[2])`.
- `repo.upsert_application(job["id"], folder=None, channel="email", contact_person=r.contact_person, contact_email=r.apply_email, reference=r.reference, salary_expectation=..., assumed_skills=letter.assumed_skills, language=language, prepared_at=now_iso(), letter_md=letter.to_markdown(), email_subject=letter.subject, email_body=letter.email_body, message_id=message_id)`.
- If the job still has an old folder (`_folder_for` returned an existing directory), delete it with `remove_application_folder` (D7) after the upsert.
- `repo.set_needs_review(job["id"], review)`; transition to `prepared` with note `"email application: letter stored, goes to your mailbox's Drafts (no local folder)"`.
- Return `bool(review)`.

`prepare_jobs` must not report a folder for these jobs: after `_prepare_one`, if the application has no folder emit `ctx.emit("email_prepared", job, detail=f"{app['contact_email']} (letter written; it is saved as a draft in your mailbox, no local folder)", channel="email")` instead of `folder_saved`. Implement the split in `prepare_jobs` where `folder_saved` is emitted today.

Alternative: draft inside prepare — rejected: prepare has no mailbox and runs in modes without drafting; keeping draft as its own step keeps retries and dry runs simple.

### D5. Draft step: no needs_review block, builder instead of email.eml, legacy cleanup

`_reasons_not_draftable(ctx, job, again)` keeps the status and channel checks, **drops the `needs_review` check**, and replaces the `email.eml` check with:

```python
folder = Path(app["folder"]) if app and app["folder"] else None
has_eml = folder is not None and (folder / "email.eml").exists()
if app is not None and not has_eml and not app["letter_md"]:
    reasons.append(f"no stored email for this job; run `autoapp prepare --job {job['id']} --force`")
```

Per job in `draft_applications` (after the existing "already saved" and dry-run handling):
1. `review_reasons = json.loads(job["needs_review_reasons"] or "[]") if job["needs_review"] else []`; store them in a new `DraftSummary.review: dict[str, list[str]]` field.
2. Dry run: compute notes with `check_eligibility(..., bundle=None)` filtered by `_NOTE_MARKERS` (the "missing bundle" reason is not a note marker, so it is dropped) and do **not** build anything.
3. Otherwise open the store as today, then:
   ```python
   new_id = make_msgid(domain=str(app["contact_email"]).rsplit("@", 1)[-1]) if again else None
   with application_email(ctx, job_id, renderer_factory=renderer_factory, message_id=new_id) as built:
       msg = built.message
       notes = _notes(ctx, job_id, built.bundle)   # same as today, path from the builder
       del msg["Date"]; msg["Date"] = formatdate(localtime=True)
       ... existing has_message / append_draft / except blocks, unchanged ...
   ```
   The `with` block must enclose the IMAP calls (the bundle file must exist until `append_draft` returns) and the existing `except MailAuthError / DraftFolderNotFound / MailError` handling. `NoStoredEmail` → `s.not_drafted[job_id] = [str(err)]`, continue.
4. After the `drafts` row is inserted and the status is `drafted`: `repo.upsert_application(job_id, message_id=str(msg["Message-ID"]))`; if the job had a legacy folder, run `_retire_legacy_folder(ctx, job_id, folder, msg)` (D7).
5. Feed: `detail = f"{app['contact_email']} (in {shown}){note_text}"` plus, when `review_reasons`, `" — check before sending: " + "; ".join(review_reasons)` cut to 300 characters. Also `ctx.emit("needs_review", job, detail="drafted; check before sending: " + reasons)` so the review queue still counts it.

`draft_applications` gets a new keyword `renderer_factory: Callable[[], AbstractContextManager[PdfRenderer]] = PdfRenderer`; `run_pipeline` passes `overrides["renderer_factory"]` when present (as it does for prepare).

Summary line (`RunSummary.lines`): after the existing `drafts:` line add, when `d.review` is non-empty, `"drafts to check before sending: " + ", ".join(f"{j} ({'; '.join(r)[:120]})" for j, r in d.review.items())`.

### D6. Send, test send, render, CLI and menu use the builder

- `steps/send.py`: replace every `load_message(folder)` / `folder / BUNDLE` / "email.eml is missing" pair with `with application_email(ctx, job_id, renderer_factory=renderer_factory) as built:` and `bundle=built.bundle`; `NoStoredEmail` becomes the reason `"no stored email; run `autoapp prepare --job <id> --force`"`. `_latest_prepared_email_job` accepts a job with `email.eml` **or** `letter_md`. Keep `load_message` (the builder's legacy branch and tests use it). Add a `renderer_factory` keyword (default `PdfRenderer`) to `send_applications`, `send_test_email` and `send_test_applications`, and pass the pipeline override.
- `steps/render.py::render_application`: at the top, if `app["channel"] == "email"` and (`not app["folder"]` or the folder does not exist) and `app["letter_md"]`: `raise ValueError(f"job {job_id} has no local folder: it is a draft in your mailbox's Drafts folder. Edit the draft there, or run `autoapp draft {job_id} --again` to save a new one from the stored letter.")`. The `render` command already turns `ValueError` into exit code 1.
- `commands.py::drafts` (list): select also `j.needs_review, j.needs_review_reasons`; after each row print `"    check before sending: " + "; ".join(reasons)` when flagged.
- `commands.py::draft`: print `"check before sending: …"` for `s.review.get(job_id)`.
- `menu/main.py::application_actions`: when `email and folder is None`, the "folder" action says `"No local folder: this application is a draft in your mailbox's Drafts folder."`, the "letter" action prints `app["letter_md"]` (or "No cover letter yet."), and the "render" action is not offered. `entry_review`: offer "letter" when the app has a folder **or** `letter_md`; for a folderless app return the `letter_md` text.

### D7. Deleting folders safely

```python
# src/autoapp/package/folders.py
def remove_application_folder(ctx, folder: Path) -> bool:
    """Delete an application folder, only if it lies inside the workspace's applications directory."""
    root = ctx.ws.applications_dir.resolve()
    target = folder.resolve()
    if target == root or root not in target.parents or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
```

`_retire_legacy_folder(ctx, job_id, folder, msg)` in `steps/drafts.py`: read `folder / "cover_letter.md"` if present; body = `msg.get_body(("plain",)).get_content()` if present; `repo.upsert_application(job_id, letter_md=..., email_subject=str(msg["Subject"]), email_body=..., message_id=str(msg["Message-ID"]), folder=None)`; then `remove_application_folder(ctx, folder)`. It runs only after the draft is appended **and** recorded, so a failure keeps the folder.

### D8. Feed event `email_prepared`

`src/autoapp/events.py`: add `"email_prepared"` to `FEED_KINDS`, label `"EMAIL READY"` in the labels dict, a `RunStats.emails_prepared: int = 0` counter incremented for it, and a row `("emails prepared", rs.emails_prepared)` next to `("folders saved", …)` in `dashboard/view.py` (run section, around line 259).

## Risks / Trade-offs

- [The PDF is built twice (once in prepare to check the page count, once in draft)] → Accepted: it costs about a second per job and avoids keeping any file between the steps.
- [The date line in the letter is the day of drafting, not of preparing] → They are the same run in practice; `draft --again` shows the new day, which is what a re-sent letter should say.
- [Documents in `workspace/documents/` changed between prepare and draft] → The draft uses the current files, which is what the user expects.
- [Deleting the old folders removes a user-edited `cover_letter.md`] → Only after the draft (built from that folder's `email.eml`, which `render` keeps in sync) is safely in Gmail; the letter text is copied into the database first.
- [Many existing tests assume review-mode contexts write folders for email jobs] → Tasks list each affected test file; tests about sending/automatic behaviour pin `mode="automatic"`, tests about drafting are rewritten.
- [`needs_review` letters now reach the mailbox] → They are drafts, never sent; the feed, summary and `autoapp drafts` say "check before sending" with the reasons.

## Migration Plan

1. Migration 10 runs automatically when the app opens the database (`open_db` → `migrate`); it only adds nullable columns.
2. The next review-mode run drafts the three old `prepared` email jobs (`537eab`, `bdc372`, `6a1e45`) from their `email.eml` and deletes their folders.
3. Rollback: revert the merge commit. Old code ignores the new columns; jobs prepared draft-only would have no folder, so old code would report "email.eml is missing" for them — run `autoapp prepare --job <id> --force` after a rollback.
