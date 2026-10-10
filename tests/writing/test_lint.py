from __future__ import annotations

from importlib import resources

import pytest

from autoapp.writing.lint import count_words, fix_dashes, lint, parse_list

PHRASES = ["i am writing to express my interest", "hiermit bewerbe ich mich", "in today's fast-paced world"]
CONNECTORS = ["darüber hinaus", "zudem", "außerdem", "moreover", "furthermore"]


def run(text, **kw):
    kw.setdefault("language", "de")
    kw.setdefault("max_words", None)
    kw.setdefault("phrases", PHRASES)
    kw.setdefault("connectors", CONNECTORS)
    return lint(text, **kw)


def kinds(vs):
    return [v.kind for v in vs]


def test_dash_flagged_then_fixed():
    text = "Ich bringe Erfahrung mit — vor allem in Python."
    assert kinds(run(text)) == ["dash"]
    fixed = fix_dashes(text)
    assert fixed == "Ich bringe Erfahrung mit, vor allem in Python."
    assert run(fixed) == []


@pytest.mark.parametrize(
    "text", ["A – B", "A—B", "A - B", "Zeile\n- Punkt", "Ende -\nNeu", "x —\n\ny", "— Start"]
)
def test_fix_dashes_leaves_no_dash_violation(text):
    assert "dash" not in kinds(run(fix_dashes(text)))


@pytest.mark.parametrize(
    "text", ["Backend-Entwicklung", "Software-Entwickler und E-Mail", "Backend- und Frontend-Entwicklung"]
)
def test_in_word_hyphen_not_flagged_nor_touched(text):
    assert run(text) == []
    assert fix_dashes(text) == text


def test_numeric_range_becomes_plain_hyphen():
    assert fix_dashes("von 2019–2021 bei X") == "von 2019-2021 bei X"
    assert fix_dashes("10 – 20 Personen") == "10-20 Personen"
    assert fix_dashes("2019 - 2021") == "2019-2021"


def test_unspaced_dash_between_words():
    assert fix_dashes("Alpha—Beta") == "Alpha, Beta"


def test_fix_keeps_paragraphs_and_existing_commas():
    text = "Sehr geehrte Frau Muster,\n\nich arbeite — gern — mit Python.\n\nGrüße"
    assert fix_dashes(text) == "Sehr geehrte Frau Muster,\n\nich arbeite, gern, mit Python.\n\nGrüße"


def test_no_double_comma_artifacts():
    assert fix_dashes("Erstens, — zweitens") == "Erstens, zweitens"
    assert fix_dashes("Satz —. Weiter") == "Satz. Weiter"


def test_builtin_style_phrase_flagged_case_insensitive():
    vs = run("I am writing to express my interest in the role.", language="en")
    assert kinds(vs) == ["phrase"]
    assert kinds(run("I AM WRITING TO EXPRESS MY INTEREST")) == ["phrase"]


def test_phrase_checked_regardless_of_language():
    assert kinds(run("Hiermit bewerbe ich mich als Entwickler.", language="en")) == ["phrase"]


def test_user_added_phrase():
    text = "Das ist eine spannende Herausforderung für mich."
    assert run(text) == []
    vs = run(text, banned_extra=["spannende Herausforderung"])
    assert kinds(vs) == ["phrase"]
    assert "spannende herausforderung" in vs[0].detail


def test_allowed_removes_builtin_phrase():
    text = "Hiermit bewerbe ich mich."
    assert kinds(run(text)) == ["phrase"]
    assert run(text, allowed=["Hiermit bewerbe ich mich"]) == []


def test_phrase_needs_word_boundary():
    assert run("Zuerst I am writing to express my interestingly") == []


def test_exclamation_and_question():
    assert kinds(run("Toll! Wirklich?")) == ["exclamation", "question"]


def test_connectors_three_flagged_two_not():
    two = "Zudem arbeite ich viel. Außerdem lerne ich. Das ist gut."
    three = two + " Darüber hinaus bin ich flexibel."
    assert run(two) == []
    vs = run(three)
    assert kinds(vs) == ["connectors"]
    assert "3" in vs[0].detail


def test_connector_word_boundary_and_case():
    assert run("ZUDEM zudem Zudemhin") == []  # "Zudemhin" must not count as a third
    assert kinds(run("ZUDEM zudem Moreover")) == ["connectors"]


def test_word_count_limit():
    body = " ".join(["wort"] * 301)
    assert kinds(run(body, max_words=300)) == ["length"]
    assert run(" ".join(["wort"] * 300), max_words=300) == []
    assert run(body, max_words=None) == []


def test_count_words_tokens():
    assert count_words("Backend-Entwicklung ist don't schön, äöü 2019.") == 6


def test_parse_list_ignores_comments_and_blank():
    assert parse_list("# c\nFoo Bar  # x\n\nbaz\n") == ["foo bar", "baz"]


def _packaged(name: str) -> bool:
    return resources.files("autoapp.writing").joinpath(name).is_file()


@pytest.mark.skipif(
    not all(_packaged(n) for n in ("phrases_de.txt", "phrases_en.txt", "connectors.txt")),
    reason="packaged lists not present",
)
def test_packaged_lists_load():
    from autoapp.writing.lint import builtin_connectors, builtin_phrases

    assert builtin_phrases()
    assert builtin_connectors()
    assert kinds(lint("I am writing to express my interest.", language="en", max_words=None)) == ["phrase"]


def test_ordinary_german_wording_is_not_flagged():
    from autoapp.writing.lint import lint

    text = (
        "Ich habe vielfältige Projekte eigenverantwortlich realisiert und dabei Chancen genutzt. "
        "Außerdem arbeite ich gern im Team.\n\nMit freundlichen Grüßen\nMax Mustermann"
    )
    assert lint(text, language="de", max_words=300) == []
