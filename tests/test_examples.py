"""Test that example files pass validation."""

from pathlib import Path

from autoapp.config.models import load_config, load_profile
from autoapp.config.workspace import Workspace


def test_example_config_valid(tmp_path: Path) -> None:
    """Test that config.example.yaml is valid once placed in a workspace."""
    example_config = Path(__file__).parent.parent / "examples" / "config.example.yaml"
    assert example_config.exists(), f"Example config not found at {example_config}"

    # Copy example to workspace
    ws = Workspace(tmp_path)
    ws.config_path.parent.mkdir(parents=True, exist_ok=True)
    ws.config_path.write_text(example_config.read_text())

    # Should load without validation errors
    config = load_config(ws)
    assert config is not None
    assert len(config.search_profiles) >= 1


def test_example_profile_valid(tmp_path: Path) -> None:
    """Test that profile.example.yaml is valid once placed in a workspace."""
    example_profile = Path(__file__).parent.parent / "examples" / "profile.example.yaml"
    assert example_profile.exists(), f"Example profile not found at {example_profile}"

    # Copy example to workspace
    ws = Workspace(tmp_path)
    ws.profile_path.parent.mkdir(parents=True, exist_ok=True)
    ws.profile_path.write_text(example_profile.read_text())

    # Should load without validation errors
    profile = load_profile(ws)
    assert profile is not None
    assert profile.personal.first_name == "Max"
    assert profile.personal.last_name == "Mustermann"


def test_env_example_contains_all_keys() -> None:
    """Test that .env.example lists all required environment variables."""
    env_example = Path(__file__).parent.parent / "examples" / ".env.example"
    assert env_example.exists(), f"Example .env not found at {env_example}"

    content = env_example.read_text()

    # Check for all expected keys
    required_keys = {
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "AUTOAPP_ALERTS_PASSWORD",
        "AUTOAPP_SMTP_PASSWORD",
    }

    for key in required_keys:
        assert key in content, f"Key {key} not found in .env.example"


def test_writing_instructions_example_exists() -> None:
    """Test that writing_instructions.example.md exists."""
    example_instructions = Path(__file__).parent.parent / "examples" / "writing_instructions.example.md"
    assert example_instructions.exists(), f"Example writing instructions not found at {example_instructions}"
    content = example_instructions.read_text()
    assert len(content) > 0
    assert "Voice" in content or "Tone" in content or "Emphasize" in content
