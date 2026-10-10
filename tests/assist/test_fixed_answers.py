"""Questions that are answered the same way for every application (LinkedIn, no referral, never worked there)."""

from __future__ import annotations

import pytest

from autoapp.assist.fields import FieldInfo
from autoapp.assist.mapper import FillValues, map_fields


def mk(label: str, type: str = "text", options=None, **kw) -> FieldInfo:
    return FieldInfo(field_id="f0-" + label[:8], frame_index=0, selector="[x]", tag="select" if type == "select" else "input",
                     type=type, label=label, options=options or [], **kw)


def run(field: FieldInfo, **values):
    return map_fields([field], FillValues(first_name="Max", linkedin_url="https://linkedin.com/in/max", **values))


@pytest.mark.parametrize("label", [
    "Where did you hear about this position?", "How did you hear about us?", "Wie haben Sie von uns erfahren?",
    "Wie sind Sie auf uns aufmerksam geworden?", "Source",
])
def test_how_found_text_is_linkedin_and_not_the_profile_url(label) -> None:
    res = run(mk(label))
    assert [a.value for a in res.assignments] == ["LinkedIn"]


def test_how_found_select_picks_linkedin_else_nearest_source() -> None:
    opts = [("", "Please choose"), ("1", "Company website"), ("2", "LinkedIn"), ("3", "Other")]
    assert run(mk("How did you hear about us?", "select", opts)).assignments[0].value == "2"
    opts = [("", ""), ("1", "Friend"), ("2", "Job board"), ("3", "Other")]
    assert run(mk("How did you hear about us?", "select", opts)).assignments[0].value == "2"
    opts = [("1", "Friend"), ("2", "Newspaper")]  # nothing fits: left to the model / the candidate
    res = run(mk("How did you hear about us?", "select", opts))
    assert not res.assignments and len(res.unmatched) == 1


@pytest.mark.parametrize(("label", "options", "expected"), [
    ("Were you referred by an employee?", [("y", "Yes"), ("n", "No")], "n"),
    ("Wurden Sie empfohlen?", [("j", "Ja"), ("n", "Nein")], "n"),
    ("Have you worked at Muster GmbH before?", [("y", "Yes"), ("n", "No, I have not")], "n"),
    ("Waren Sie bereits bei uns beschäftigt?", [("j", "Ja"), ("n", "Nein")], "n"),
    ("Are you a former employee?", [("y", "Yes"), ("n", "No")], "n"),
])
def test_referral_and_prior_employment_are_no(label, options, expected) -> None:
    res = run(mk(label, "select", options), company="Muster GmbH")
    assert res.assignments[0].value == expected


def test_company_name_in_the_question_counts() -> None:
    res = run(mk("Have you ever been employed by Muster GmbH?", "select", [("y", "Yes"), ("n", "No")]), company="Muster GmbH")
    assert res.assignments[0].value == "n"


def test_text_answer_follows_the_application_language() -> None:
    assert run(mk("Wurden Sie empfohlen? Von wem?"), language="de").assignments[0].value == "Keine"
    assert run(mk("Were you referred? By whom?"), language="en").assignments[0].value == "None"
    assert run(mk("Have you worked for us before?"), language="en").assignments[0].value == "No"


def test_unticked_checkbox_is_the_answer_and_needs_no_input() -> None:
    res = run(mk("I was referred by an employee", "checkbox"))
    assert not res.assignments and not res.unmatched and len(res.left_empty) == 1


def test_applied_before_is_not_employment() -> None:
    res = run(mk("Have you applied to us before?", "select", [("y", "Yes"), ("n", "No")]))
    assert not res.assignments


def test_unrelated_questions_are_untouched() -> None:
    assert not run(mk("Where have you worked so far?")).assignments
    assert not run(mk("Lieblingsfarbe")).assignments
