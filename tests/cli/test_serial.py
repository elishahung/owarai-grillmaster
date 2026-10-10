from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.cli import app
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import load_state

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


def test_serial_chains_the_projects(cli: CliRunner, projects_root: Path):
    result = cli.invoke(app, ["serial", "epone111", "eptwo222", "--chat"])
    assert result.exit_code == 0, result.output
    first = ProjectLayout.for_id(projects_root, "epone111")
    second = load_state(ProjectLayout.for_id(projects_root, "eptwo222"))
    assert load_state(first).parent is None
    assert second.parent == first.root


def test_serial_refuses_a_duplicate_before_running(cli: CliRunner, projects_root: Path):
    result = cli.invoke(app, ["serial", "epone111", "eptwo222", "epone111"])
    assert result.exit_code == 1
    assert "Duplicate sources: epone111" in result.output
    assert not projects_root.exists()


def test_serial_refuses_a_project_directory(
    cli: CliRunner, home: Path, layout: ProjectLayout
):
    result = cli.invoke(app, ["serial", "epone111", str(layout.root)])
    assert result.exit_code == 1
    assert "grill package" in result.output
