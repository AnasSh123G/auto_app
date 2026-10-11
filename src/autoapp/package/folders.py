"""Application folder names that show whether the application form could be reached.

A form application whose form was not found keeps its folder, with a marker after the job id
(``application_<company>_<title>_<id>_NOFORM``). A found form, an email application and a form not
looked up yet have no marker. The marker follows the form status whenever the folder's files are
refreshed, so a later successful `autoapp forms --again` removes it.
"""

from __future__ import annotations

import re
from pathlib import Path

from autoapp.package.slug import PREFIX, folder_name

MARKERS = {
    "none": "NOFORM", "partial": "NOFORM", "needs_browser": "BROWSER", "login_required": "LOGIN",
    "captcha": "CAPTCHA", "restricted": "RESTRICTED", "unreachable": "UNREACHABLE",
    "no_employer_link": "NOEMPLOYER",
}
_TEXT_SUFFIXES = {".md", ".html"}


def marker_for(channel: str | None, form_status: str | None) -> str | None:
    return MARKERS.get(form_status or "") if channel and channel != "email" else None


def _ours(name: str, job_id: str) -> bool:
    """Only folders this tool named (``application_*_<id>`` with an optional marker) are ever renamed."""
    sid = re.escape(job_id)
    return name.startswith(PREFIX) and re.search(rf"_{sid}(?:_[A-Z]+)?$", name) is not None


def _rewrite_paths(folder: Path, old: Path, new: Path) -> None:
    """README, job and helper pages name files by absolute path and file:// address; follow the rename."""
    olds = sorted({x for a in (old, old.resolve()) for x in (str(a), a.as_uri() if a.is_absolute() else "") if x},
                  key=len, reverse=True)
    news = {}
    for a, b in ((old, new), (old.resolve(), new.resolve())):
        news[str(a)] = str(b)
        if a.is_absolute():
            news[a.as_uri()] = b.as_uri()
    pattern = re.compile("(?:" + "|".join(re.escape(x) for x in olds) + r")(?![\w.-])")
    for path in folder.iterdir():
        if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = pattern.sub(lambda m: news[m.group(0)], text)  # one pass: a new name never matches again
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def sync_folder_name(ctx, job_id: str) -> Path | None:  # noqa: ANN001 - RunContext
    """Rename the job's folder to match its form status; returns the folder now in use."""
    repo = ctx.repo
    app, job = repo.get_application(job_id), repo.get_job(job_id)
    if app is None or job is None or not app["folder"]:
        return None
    current = Path(app["folder"])
    if not current.is_dir() or not _ours(current.name, job_id):
        return current
    wanted = current.parent / folder_name(job["company"], job["title"], job_id, marker_for(app["channel"], app["form_status"]))
    if wanted == current or wanted.exists():
        return current
    current.rename(wanted)
    _rewrite_paths(wanted, current, wanted)
    repo.upsert_application(job_id, folder=str(wanted))
    ctx.emit("folder_renamed", job, detail=f"{current.name} -> {wanted.name}")
    return wanted
