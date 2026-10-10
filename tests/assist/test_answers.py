"""Personal questions: asked once, saved in form_answers.md, reused in later forms."""

from __future__ import annotations

from pathlib import Path

from autoapp.assist.answers import (
    ask_open_fields,
    assignment_for,
    is_long,
    question_key,
    read_sections,
    write_section,
)
from autoapp.assist.fields import FieldInfo


def mk(label: str, type: str = "text", options=None, **kw) -> FieldInfo:
    return FieldInfo(field_id="f0-" + label[:6], frame_index=0, selector="[x]", tag="textarea" if type == "textarea" else "input",
                     type=type, label=label, options=options or [], **kw)


def test_question_key_ignores_case_marks_and_asterisks() -> None:
    assert question_key("Do you have a driving licence? *") == question_key("do you have a driving licence")


def test_write_replaces_the_same_question_and_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "form_answers.md"
    path.write_text("# Form answers\n\n## Motivation\n\nText\n\n_hint_\n\n## Email\n\nmax@example.com\n", encoding="utf-8")
    write_section(path, "Führerschein?", "Klasse B")
    write_section(path, "Führerschein", "Klasse B und A")
    sections = read_sections(path)
    assert sections["fuehrerschein"] == ("Führerschein", "Klasse B und A")
    assert sections["motivation"][1] == "Text"  # hint line dropped, text kept
    assert sections["email"][1] == "max@example.com"
    assert path.read_text(encoding="utf-8").count("Führerschein") == 1


def test_read_ignores_not_set_and_missing_file(tmp_path: Path) -> None:
    assert read_sections(tmp_path / "none.md") == {}
    p = tmp_path / "a.md"
    p.write_text("## Phone\n\nnot set\n", encoding="utf-8")
    assert read_sections(p) == {}


def test_select_answer_by_number_or_text() -> None:
    f = mk("Licence", "select", [("", "-"), ("a", "Class A"), ("b", "Class B")])
    assert assignment_for(f, "2").value == "b"
    assert assignment_for(f, "class a").value == "a"
    assert assignment_for(f, "9") is None


def test_number_and_date_fields_are_validated() -> None:
    assert assignment_for(mk("Kids", "number"), "two") is None
    assert assignment_for(mk("Kids", "number"), "2").value == "2"
    assert assignment_for(mk("Born", "date"), "01.12.1990").value == "1990-12-01"
    assert assignment_for(mk("Born", "date"), "soon") is None


def test_long_text_is_not_shared() -> None:
    assert is_long(mk("Describe a project", "textarea"))
    assert not is_long(mk("Licence"))


def test_ask_uses_saved_answers_and_only_asks_the_rest() -> None:
    saved = {"licence": ("Licence", "Class B")}
    asked, said = [], []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "Blue"

    fields = [mk("Licence"), mk("Favourite colour")]
    out = ask_open_fields(fields, saved, ask, said.append)
    assert [(a.label, a.value, a.reused) for a in out] == [("Licence", "Class B", True), ("Favourite colour", "Blue", False)]
    assert len(asked) == 1 and "Favourite colour" in asked[0]


def test_enter_skips_and_eof_stops() -> None:
    answers = iter(["", "x"])
    out = ask_open_fields([mk("One"), mk("Two")], {}, lambda p: next(answers), lambda m: None)
    assert [a.label for a in out] == ["Two"]

    def eof(prompt: str) -> str:
        raise EOFError

    assert ask_open_fields([mk("One")], {}, eof, lambda m: None) == []


def test_select_stores_the_option_text_not_the_typed_number() -> None:
    f = mk("Licence", "select", [("a", "Class A"), ("b", "Class B")])
    out = ask_open_fields([f], {}, lambda p: "2", lambda m: None)
    assert out[0].value == "Class B" and out[0].assignment.value == "b"


def test_a_wrong_choice_is_asked_again_once() -> None:
    f = mk("Licence", "select", [("a", "Class A"), ("b", "Class B")])
    answers = iter(["7", "1"])
    out = ask_open_fields([f], {}, lambda p: next(answers), lambda m: None)
    assert out[0].assignment.value == "a"
