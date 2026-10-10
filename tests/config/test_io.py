from __future__ import annotations

from pathlib import Path

from autoapp.config.io import load_yaml, save_yaml, update_section

SAMPLE = """\
# My config - hand edited
search_profiles:   # keep these
  - name: dev
    keywords: [python, "django"]   # main skills

# Limits for each run
limits:
  max_llm_calls_per_run: 60  # cost guard
  # evaluate cap
  max_evaluate_per_run: 40

# Mail section
mail:
    alerts:
        preset: gmail
        address: me@gmail.com

# last: writing
writing:
  max_words: 300
"""


def test_update_section_preserves_everything_else(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    update_section(p, "limits", {"max_llm_calls_per_run": 10, "max_evaluate_per_run": 40})
    before = SAMPLE.splitlines()
    after = p.read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    diff = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert diff == [
        ("  max_llm_calls_per_run: 60  # cost guard", "  max_llm_calls_per_run: 10  # cost guard")
    ]
    assert load_yaml(p)["limits"]["max_llm_calls_per_run"] == 10


def test_update_section_keeps_comment_before_next_section(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    update_section(p, "limits", {"max_llm_calls_per_run": 5})
    text = p.read_text(encoding="utf-8")
    assert "\n# Mail section\nmail:\n    alerts:" in text
    assert text.startswith("# My config - hand edited\nsearch_profiles:   # keep these\n")
    assert "max_evaluate_per_run" not in text


def test_update_last_and_nested_section(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    update_section(p, "writing", {"max_words": 250, "language": "de"})
    update_section(p, "mail", {"alerts": {"preset": "gmail", "address": "new@gmail.com"}})
    text = p.read_text(encoding="utf-8")
    assert "# last: writing\nwriting:\n  max_words: 250\n  language: de\n" in text
    assert "new@gmail.com" in text and "me@gmail.com" not in text
    assert text.startswith(SAMPLE.split("# Mail section")[0])


def test_update_section_adds_missing_key_and_file(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("a: 1  # c\n", encoding="utf-8")
    update_section(p, "b", {"x": [1, 2]})
    assert p.read_text(encoding="utf-8").startswith("a: 1  # c\nb:")
    assert load_yaml(p)["b"]["x"] == [1, 2]
    new = tmp_path / "new.yaml"
    update_section(new, "k", "v")
    assert load_yaml(new)["k"] == "v"


def test_load_missing_is_empty_and_save_is_atomic(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "x.yaml"
    assert len(load_yaml(p)) == 0
    save_yaml(p, {"a": 1, "b": [1, 2]})
    assert load_yaml(p)["b"] == [1, 2]
    assert [f.name for f in p.parent.iterdir()] == ["x.yaml"]
