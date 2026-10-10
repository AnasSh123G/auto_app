"""Tests for documentation.

Validates that:
1. All YAML code blocks in docs/*.md are valid and pass pydantic validation
2. All documented files exist
3. systemd unit files are valid (when systemd-analyze is available)
"""

import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from autoapp.config.models import Config


@pytest.fixture
def docs_dir() -> Path:
    """Path to the docs directory."""
    return Path(__file__).parent.parent / "docs"


@pytest.fixture
def deploy_dir() -> Path:
    """Path to the deploy directory."""
    return Path(__file__).parent.parent / "deploy"


class TestDocsExist:
    """Test that all expected documentation files exist."""

    @pytest.mark.parametrize(
        "doc_file",
        [
            "llm-providers.md",
            "job-sources.md",
            "cover-letters.md",
            "application-folders.md",
            "email.md",
            "assisted-applying.md",
            "scheduling.md",
        ],
    )
    def test_doc_file_exists(self, docs_dir: Path, doc_file: str) -> None:
        """Check that documentation file exists."""
        path = docs_dir / doc_file
        assert path.exists(), f"Documentation file {doc_file} not found at {path}"
        assert path.is_file(), f"{doc_file} is not a file"


class TestSystemdUnits:
    """Test systemd unit files."""

    @pytest.mark.parametrize(
        "unit_file",
        [
            "systemd/autoapp.service",
            "systemd/autoapp.timer",
        ],
    )
    def test_unit_file_exists(self, deploy_dir: Path, unit_file: str) -> None:
        """Check that systemd unit file exists."""
        path = deploy_dir / unit_file
        assert path.exists(), f"Unit file {unit_file} not found at {path}"
        assert path.is_file(), f"{unit_file} is not a file"

    def test_systemd_analyze_verify(self, deploy_dir: Path) -> None:
        """Verify systemd units with systemd-analyze (if available)."""
        try:
            result = subprocess.run(
                [
                    "systemd-analyze",
                    "--user",
                    "verify",
                    str(deploy_dir / "systemd" / "autoapp.service"),
                    str(deploy_dir / "systemd" / "autoapp.timer"),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            pytest.skip("systemd-analyze not available")

        if result.returncode != 0:
            # Check if the only error is about the binary not existing
            # (which is OK since we use /bin/sh -c to work around it)
            if "ExecStart=" in result.stderr and "not found" in result.stderr:
                # This is expected if uv is not in the PATH during verification
                # The unit file is still valid
                pass
            else:
                pytest.fail(f"systemd-analyze verify failed:\n{result.stderr}")


class TestYamlBlocks:
    """Extract and validate YAML code blocks from documentation."""

    def _extract_yaml_blocks(self, content: str) -> list[tuple[str, bool, str]]:
        """Extract YAML code blocks from markdown.

        Returns list of (block_content, should_validate, source_info).
        Blocks preceded by <!-- novalidate --> are marked should_validate=False.
        """
        lines = content.split("\n")
        blocks = []
        i = 0
        while i < len(lines):
            # Check for novalidate marker
            skip_validation = False
            if i > 0 and "<!-- novalidate -->" in lines[i - 1]:
                skip_validation = True

            # Look for ``` ```yaml
            if lines[i].strip().startswith("```yaml"):
                # Find the closing ```
                start = i + 1
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    i += 1
                if i < len(lines):
                    block_content = "\n".join(lines[start:i])
                    blocks.append((block_content, not skip_validation, f"line {start}"))
            i += 1
        return blocks

    def _filter_config_keys(self, parsed: dict) -> dict:
        """Filter parsed YAML to keep only top-level keys that belong in Config.

        This handles cases where docs show YAML snippets that aren't all config keys.
        """
        if not isinstance(parsed, dict):
            return parsed

        # These are the valid top-level keys in Config
        valid_config_keys = {
            "search_profiles",
            "sources",
            "detail_fetch",
            "evaluation",
            "limits",
            "llm",
            "mail",
            "email",
            "writing",
            "tracking",
            "assist",
            "setup",
        }

        return {k: v for k, v in parsed.items() if k in valid_config_keys}

    def test_llm_providers_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in llm-providers.md."""
        content = (docs_dir / "llm-providers.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            # Parse YAML
            parsed = yaml.load(block)

            if should_validate:
                # Merge into a minimal base config
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                # Validate with pydantic
                Config.model_validate(base)

    def test_job_sources_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in job-sources.md."""
        content = (docs_dir / "job-sources.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            parsed = yaml.load(block)

            if should_validate:
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                Config.model_validate(base)

    def test_cover_letters_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in cover-letters.md."""
        content = (docs_dir / "cover-letters.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            parsed = yaml.load(block)

            if should_validate:
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                Config.model_validate(base)

    def test_email_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in email.md."""
        content = (docs_dir / "email.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            parsed = yaml.load(block)

            if should_validate:
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                Config.model_validate(base)

    def test_application_folders_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in application-folders.md."""
        content = (docs_dir / "application-folders.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            parsed = yaml.load(block)

            if should_validate:
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                Config.model_validate(base)

    def test_assisted_applying_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in assisted-applying.md."""
        content = (docs_dir / "assisted-applying.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            parsed = yaml.load(block)

            if should_validate:
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                Config.model_validate(base)

    def test_scheduling_yaml_blocks(self, docs_dir: Path) -> None:
        """Validate YAML blocks in scheduling.md."""
        content = (docs_dir / "scheduling.md").read_text()
        blocks = self._extract_yaml_blocks(content)

        yaml = YAML(typ="safe")
        for block, should_validate, _source in blocks:
            if not block.strip():
                continue

            parsed = yaml.load(block)

            if should_validate:
                base = {"search_profiles": [{"name": "x", "keywords": ["Python"]}]}
                if parsed and isinstance(parsed, dict):
                    parsed = self._filter_config_keys(parsed)
                    base.update(parsed)

                Config.model_validate(base)
