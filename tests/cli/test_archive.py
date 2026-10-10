from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from grillmaster.cli import app
from grillmaster.config.load import CONFIG_FILE_NAME
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import load_state, project_lock, save_state

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

    from grillmaster.project.state import ProjectState


@pytest.fixture
def archiving_home(home: Path) -> Path:
    """`home` with `[paths] archive` set to `home/nas`."""
    toml = home / CONFIG_FILE_NAME
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text + '\n[paths]\narchive = "nas"\n', encoding="utf-8")
    return home


@pytest.fixture
def finished(layout: ProjectLayout, state: ProjectState) -> ProjectLayout:
    """`layout` holding a dated, named project whose stages are done."""
    state.broadcast_date = date(2026, 5, 3)
    state.name = "demo_show"
    state.mark_done(StageKey.FINALIZE, elapsed_s=1.0)
    save_state(layout, state)
    layout.cht_srt.parent.mkdir(parents=True, exist_ok=True)
    layout.cht_srt.write_text("1\n", encoding="utf-8")
    return layout


def test_archives_a_finished_project_by_id(
    cli: CliRunner, archiving_home: Path, finished: ProjectLayout, state: ProjectState
):
    result = cli.invoke(app, ["archive", finished.root.name])

    assert result.exit_code == 0, result.output
    archived = ProjectLayout(archiving_home / "nas/26/05/260503_epabc123_demo_show")
    assert load_state(archived) == state
    assert archived.cht_srt.read_text(encoding="utf-8") == "1\n"
    assert not finished.root.exists()


def test_an_archived_project_stays_where_it_is(
    cli: CliRunner, archiving_home: Path, finished: ProjectLayout
):
    assert cli.invoke(app, ["archive", finished.root.name]).exit_code == 0
    archived = archiving_home / "nas/26/05/260503_epabc123_demo_show"

    result = cli.invoke(app, ["archive", str(archived)])

    assert result.exit_code == 0, result.output
    assert (archived / "project.json").is_file()


def test_refuses_without_an_archive_root(
    cli: CliRunner, home: Path, finished: ProjectLayout
):
    result = cli.invoke(app, ["archive", finished.root.name])

    assert result.exit_code == 1
    assert "[paths] archive" in result.output
    assert finished.project_json.is_file()


def test_refuses_an_unfinished_project(
    cli: CliRunner, archiving_home: Path, layout: ProjectLayout
):
    result = cli.invoke(app, ["archive", layout.root.name])

    assert result.exit_code == 1
    assert "not finished" in result.output
    assert layout.project_json.is_file()
    assert not (archiving_home / "nas").exists()


def test_unknown_project_fails(cli: CliRunner, archiving_home: Path):
    result = cli.invoke(app, ["archive", "epmissing"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_refuses_a_project_another_process_holds(
    cli: CliRunner, archiving_home: Path, finished: ProjectLayout, state: ProjectState
):
    with project_lock(finished.root.parent, state.id):
        result = cli.invoke(app, ["archive", finished.root.name])

    assert result.exit_code == 1
    assert "project epabc123 is in use by another grill process" in result.output
    assert finished.project_json.is_file()
