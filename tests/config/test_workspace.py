import os

from autoapp.config.workspace import ENV_VAR, resolve_workspace


def test_cli_value_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from-env"))
    ws = resolve_workspace(tmp_path / "from-cli")
    assert ws.root == (tmp_path / "from-cli").resolve()


def test_env_var_used_without_cli(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from-env"))
    assert resolve_workspace(None).root == (tmp_path / "from-env").resolve()


def test_default_is_cwd_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve_workspace(None, cwd=tmp_path).root == (tmp_path / "workspace").resolve()


def test_layout_and_paths(tmp_path):
    ws = resolve_workspace(tmp_path / "ws")
    ws.ensure_layout()
    for d in (ws.documents_dir, ws.applications_dir, ws.logs_dir, ws.backups_dir):
        assert d.is_dir()
    assert ws.config_path.name == "config.yaml"
    assert ws.csv_path.parent == ws.root


def test_env_file_does_not_override_real_env(tmp_path, monkeypatch):
    ws = resolve_workspace(tmp_path)
    ws.env_path.write_text("AUTOAPP_TEST_A=from-file\nAUTOAPP_TEST_B=from-file\n")
    monkeypatch.setenv("AUTOAPP_TEST_A", "real")
    monkeypatch.delenv("AUTOAPP_TEST_B", raising=False)
    ws.load_env()
    assert os.environ["AUTOAPP_TEST_A"] == "real"
    assert os.environ["AUTOAPP_TEST_B"] == "from-file"
    monkeypatch.delenv("AUTOAPP_TEST_B")
