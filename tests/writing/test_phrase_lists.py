"""Test phrase lists for validity and consistency."""

from pathlib import Path


def _load_phrases(file_path: Path) -> set[str]:
    """Load phrase list from file, stripping blanks and comments."""
    phrases = set()
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            phrases.add(line)
    return phrases


def test_english_phrases_load() -> None:
    """Test that English phrases file loads and is not empty."""
    phrases_en = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_en.txt"
    assert phrases_en.exists(), f"phrases_en.txt not found at {phrases_en}"
    phrases = _load_phrases(phrases_en)
    assert len(phrases) >= 25, f"Expected at least 25 phrases, got {len(phrases)}"
    assert len(phrases) <= 50, f"Expected at most 50 phrases, got {len(phrases)}"


def test_german_phrases_load() -> None:
    """Test that German phrases file loads and is not empty."""
    phrases_de = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_de.txt"
    assert phrases_de.exists(), f"phrases_de.txt not found at {phrases_de}"
    phrases = _load_phrases(phrases_de)
    assert len(phrases) >= 25, f"Expected at least 25 phrases, got {len(phrases)}"
    assert len(phrases) <= 50, f"Expected at most 50 phrases, got {len(phrases)}"


def test_connectors_load() -> None:
    """Test that connectors file loads."""
    connectors = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "connectors.txt"
    assert connectors.exists(), f"connectors.txt not found at {connectors}"
    conns = _load_phrases(connectors)
    assert len(conns) > 0, "Connectors list is empty"


def test_english_phrases_lowercase() -> None:
    """Test that all English phrases are lowercase."""
    phrases_en = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_en.txt"
    phrases = _load_phrases(phrases_en)
    for phrase in phrases:
        assert phrase == phrase.lower(), f"Phrase '{phrase}' is not lowercase"


def test_german_phrases_lowercase() -> None:
    """Test that all German phrases are lowercase."""
    phrases_de = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_de.txt"
    phrases = _load_phrases(phrases_de)
    for phrase in phrases:
        assert phrase == phrase.lower(), f"Phrase '{phrase}' is not lowercase"


def test_english_phrases_unique() -> None:
    """Test that all English phrases are unique."""
    phrases_en = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_en.txt"
    with open(phrases_en) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    assert len(lines) == len(set(lines)), "English phrases contain duplicates"


def test_german_phrases_unique() -> None:
    """Test that all German phrases are unique."""
    phrases_de = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_de.txt"
    with open(phrases_de) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    assert len(lines) == len(set(lines)), "German phrases contain duplicates"


def test_english_no_standard_closings() -> None:
    """Test that English phrases do not include standard letter closings or neutral words."""
    phrases_en = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_en.txt"
    phrases = _load_phrases(phrases_en)

    forbidden = {
        "kind regards",
        "best regards",
        "sincerely",
        "thank you",
        "thanks",
        "regards",
        "yours truly",
        "yours sincerely",
    }

    for phrase in phrases:
        assert phrase not in forbidden, f"Forbidden closing/neutral word found: '{phrase}'"


def test_german_no_standard_closings() -> None:
    """Test that German phrases do not include standard letter closings or neutral words."""
    phrases_de = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_de.txt"
    phrases = _load_phrases(phrases_de)

    forbidden = {
        "mit freundlichen grüßen",
        "freundliche grüße",
        "herzliche grüße",
        "liebe grüße",
        "vielen dank",
        "danke",
        "team",
        "erfahrung",
    }

    for phrase in phrases:
        assert phrase not in forbidden, f"Forbidden closing/neutral word found: '{phrase}'"


def test_connectors_not_in_banned_lists() -> None:
    """Test that connectors are not in the banned phrase lists."""
    phrases_en = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_en.txt"
    phrases_de = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "phrases_de.txt"
    connectors = Path(__file__).parent.parent.parent / "src" / "autoapp" / "writing" / "connectors.txt"

    banned_en = _load_phrases(phrases_en)
    banned_de = _load_phrases(phrases_de)
    conns = _load_phrases(connectors)

    for conn in conns:
        assert conn not in banned_en, f"Connector '{conn}' found in English banned phrases"
        assert conn not in banned_de, f"Connector '{conn}' found in German banned phrases"
