"""12.5: personal data in the workspace, samples and .env never shows up in git."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_workspace_samples_and_env_are_ignored(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(ROOT / ".gitignore", repo / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for rel in ("workspace/config.yaml", "workspace/profile.yaml", "workspace/.env", "workspace/autoapp.db",
                "workspace/documents/Lebenslauf_de.pdf", "workspace/applications/application_x/README.md",
                "workspace/browser-profile/Default/Cookies", "workspace/logs/run-1.log", "samples/alert.eml",
                ".env"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("personal")
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "?? .gitignore"
