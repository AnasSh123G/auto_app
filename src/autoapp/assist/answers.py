"""Questions in a form that only the candidate can answer, and where those answers are kept.

``assist`` asks for each personal question it could not answer itself. The answers go into the application
folder's ``form_answers.md`` and, when they are short and not tied to one job, into ``form_answers.md`` in
the workspace folder. The latter is read before the next question is asked, so a question is only asked once.
Both files are plain Markdown (``## Question`` followed by the answer) and can be edited by hand.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autoapp.assist.fields import FieldInfo
from autoapp.assist.mapper import Assignment, _iso_date, _numeric, _pick_option, norm, truncate_at_word

WORKSPACE_HEADER = (
    "# Saved form answers\n\n"
    "Your answers to questions that application forms asked and the program could not answer itself. "
    "They are filled in automatically in later forms that ask the same question. "
    "Edit or delete any of them here.\n"
)
_SECTION = re.compile(r"^## (.+?)\s*$", re.M)


def question_key(label: str) -> str:
    """The label as it is compared: lower case, no punctuation, no ``*`` or ``(optional)``."""
    return re.sub(r"[?!.:]+$", "", norm(label)).strip()


def _body(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if not (ln.strip().startswith("_") and ln.strip().endswith("_")
                                                              and len(ln.strip()) > 2)]
    return "\n".join(lines).strip()


def read_sections(path: Path) -> dict[str, tuple[str, str]]:
    """``{question_key: (label, answer)}`` for every ``## Question`` section of a Markdown answers file."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    marks = list(_SECTION.finditer(text))
    out: dict[str, tuple[str, str]] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        value = _body(text[m.end():end])
        if value and value != "not set":
            out[question_key(m.group(1))] = (m.group(1).strip(), value)
    return out


def write_section(path: Path, label: str, value: str, *, header: str = "") -> None:
    """Put ``## label`` + ``value`` into the file: replace the section of the same question, else append it."""
    text = path.read_text(encoding="utf-8") if path.exists() else header
    block = f"## {label.strip()}\n\n{value.strip()}\n"
    key = question_key(label)
    marks = list(_SECTION.finditer(text))
    for i, m in enumerate(marks):
        if question_key(m.group(1)) == key:
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            tail = text[end:]
            text = text[:m.start()] + block + ("\n" + tail if tail else "")
            break
    else:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


@dataclass
class Answered:
    """One open question and what was put in it."""

    field: FieldInfo
    label: str
    value: str
    assignment: Assignment
    reused: bool = False  # taken from the saved answers instead of asked


def is_long(f: FieldInfo) -> bool:
    """Free text is about one job; it is kept with that job only."""
    return f.type == "textarea" or f.tag == "textarea" or (f.maxlength or 0) >= 300


def assignment_for(f: FieldInfo, text: str) -> Assignment | None:
    """Turn what the candidate typed into a fill/select action for ``f``; ``None`` if it does not fit the field."""
    text = text.strip()
    if not text:
        return None
    if f.type in ("select", "radio"):
        usable = [(v, t) for v, t in f.options if v != ""]
        if text.isdigit() and 1 <= int(text) <= len(usable):
            option = usable[int(text) - 1][0]
        else:
            option = _pick_option(f.options, text)
        if option is None:
            return None
        return Assignment(f.field_id, "select" if f.type == "select" else "check", option)
    if f.type == "number":
        number = _numeric(text)
        return Assignment(f.field_id, "fill", number) if number else None
    if f.type == "date":
        iso = _iso_date(text)
        return Assignment(f.field_id, "fill", iso) if iso else None
    return Assignment(f.field_id, "fill", truncate_at_word(text, f.maxlength))


def field_label(f: FieldInfo) -> str:
    return (f.label or f.name or f.html_id or f.field_id).strip()


def _prompt(f: FieldInfo) -> str:
    lines = [f"Question: {field_label(f)}" + (" (required)" if f.required else "")]
    usable = [(v, t) for v, t in f.options if v != ""]
    lines += [f"  {i}) {t or v}" for i, (v, t) in enumerate(usable, 1)]
    if usable:
        hint = "the number or the text of an option"
    elif f.type == "date":
        hint = "a date, e.g. 01.12.2026"
    elif f.type == "number":
        hint = "a number"
    else:
        hint = "your answer" + (f", at most {f.maxlength} characters" if f.maxlength else "")
    lines.append(f"Your answer ({hint}; Enter leaves it for you in the browser): ")
    return "\n".join(lines)


def ask_open_fields(fields: list[FieldInfo], saved: dict[str, tuple[str, str]], ask: Callable[[str], str],
                    say: Callable[[str], None]) -> list[Answered]:
    """Answer each open field from the saved answers, otherwise ask the candidate. Skipped fields stay empty."""
    answered: list[Answered] = []
    for f in fields:
        label = field_label(f)
        hit = saved.get(question_key(label))
        assignment = assignment_for(f, hit[1]) if hit else None
        if assignment is not None:
            say(f"Used your saved answer for '{label}': {hit[1]}")
            answered.append(Answered(f, label, hit[1], assignment, reused=True))
            continue
        for attempt in range(2):
            try:
                text = (ask(_prompt(f)) or "").strip()
            except (EOFError, KeyboardInterrupt):
                return answered
            if not text:
                break
            assignment = assignment_for(f, text)
            if assignment is not None:
                if f.type in ("select", "radio"):  # keep the option's own words, not the number that was typed
                    text = next((t or v for v, t in f.options if v == assignment.value), text)
                answered.append(Answered(f, label, text, assignment))
                break
            say("That does not fit this field." + (" Try again." if attempt == 0 else " Left for you in the browser."))
    return answered
