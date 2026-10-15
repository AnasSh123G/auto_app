"""Deleting an application folder is confined to the workspace's applications directory."""

from __future__ import annotations

from autoapp.package.folders import remove_application_folder
from autoapp.steps.context import RunContext
from tests.helpers import make_workspace


def _ctx(tmp_path):
    return RunContext.open(make_workspace(tmp_path))


def test_deletes_a_folder_inside_the_applications_directory(tmp_path):
    ctx = _ctx(tmp_path)
    folder = ctx.ws.applications_dir / "application_muster-gmbh_backend-developer_abc123"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("x", encoding="utf-8")
    assert remove_application_folder(ctx, folder) is True
    assert not folder.exists()
    ctx.close()


def test_the_applications_directory_itself_is_never_deleted(tmp_path):
    ctx = _ctx(tmp_path)
    assert remove_application_folder(ctx, ctx.ws.applications_dir) is False
    assert ctx.ws.applications_dir.is_dir()
    ctx.close()


def test_a_folder_outside_the_applications_directory_is_kept(tmp_path):
    ctx = _ctx(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert remove_application_folder(ctx, elsewhere) is False
    assert elsewhere.exists()
    ctx.close()


def test_a_missing_path_returns_false(tmp_path):
    ctx = _ctx(tmp_path)
    assert remove_application_folder(ctx, ctx.ws.applications_dir / "application_missing_x_abc123") is False
    ctx.close()
