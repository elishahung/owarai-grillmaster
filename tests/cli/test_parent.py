"""A bare `--parent`: pick a recently completed archived project."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.cli import app
from grillmaster.cli.args import expand_bare_options
from grillmaster.cli.live import ABORT_EXIT_CODE
from grillmaster.config.load import CONFIG_FILE_NAME
from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import ProjectState
from grillmaster.project.store import load_state, locate_project, save_state
from grillmaster.tui import picker

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from typer.testing import CliRunner

    from grillmaster.tui.picker import Choice


@pytest.fixture
def archived(home: Path) -> ProjectLayout:
    """`home` archiving to `home/nas`, which holds one project completed now."""
    toml = home / CONFIG_FILE_NAME
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text + '\n[paths]\narchive = "nas"\n', encoding="utf-8")
    state = ProjectState.create(SourceId(Platform.TVER, "epold1"))
    state.mark_done(StageKey.FINALIZE, elapsed_s=1.0)
    layout = ProjectLayout(home / "nas/26/10/261001_epold1_show")
    save_state(layout, state)
    return layout


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list[Sequence[Choice[Path]]]:
    """On an interactive terminal, the choice lists the picker was shown; it
    picks the first choice, or cancels when given none."""
    monkeypatch.setattr("grillmaster.cli.parent.interactive_terminal", lambda: True)
    calls: list[Sequence[Choice[Path]]] = []

    def pick(title: str, choices: Sequence[Choice[Path]]) -> Path | None:
        calls.append(choices)
        return choices[0].value if choices else None

    monkeypatch.setattr(picker, "pick", pick)
    return calls


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["epnew1", "--parent"], id="run"),
        pytest.param(["--parent", "epnew1"], id="run-flag-first"),
        pytest.param(["serial", "epnew1", "--parent"], id="serial"),
    ],
)
def test_a_bare_parent_seeds_from_the_picked_project(
    cli: CliRunner,
    projects_root: Path,
    archived: ProjectLayout,
    shown: list[Sequence[Choice[Path]]],
    args: list[str],
):
    result = cli.invoke(app, expand_bare_options(args))

    assert result.exit_code == 0, result.output
    [choices] = shown
    assert [choice.label.split("  ")[1] for choice in choices] == [archived.root.name]
    # The empty pipeline finishes, so the new project is archived too.
    location = locate_project(
        projects_root, "epnew1", archive_root=archived.root.parents[2]
    )
    assert location is not None
    assert load_state(location.layout).parent == archived.root.resolve()


def test_cancelling_the_picker_creates_nothing(
    cli: CliRunner,
    projects_root: Path,
    archived: ProjectLayout,
    shown: list[Sequence[Choice[Path]]],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(picker, "pick", lambda title, choices: None)

    result = cli.invoke(app, expand_bare_options(["epnew1", "--parent"]))

    assert result.exit_code == ABORT_EXIT_CODE
    assert not projects_root.exists()
    assert not shown
    assert archived.project_json.is_file()


@pytest.mark.usefixtures("archived")
def test_a_bare_parent_needs_a_terminal(cli: CliRunner, projects_root: Path):
    result = cli.invoke(app, expand_bare_options(["epnew1", "--parent"]))

    assert result.exit_code == 1
    assert "terminal" in result.output
    assert not projects_root.exists()


@pytest.mark.usefixtures("shown")
def test_a_bare_parent_needs_an_archive(cli: CliRunner, projects_root: Path):
    result = cli.invoke(app, expand_bare_options(["epnew1", "--parent"]))

    assert result.exit_code == 1
    assert "paths.archive" in result.output
    assert not projects_root.exists()


def test_a_bare_parent_with_nothing_recent_fails(
    cli: CliRunner,
    projects_root: Path,
    archived: ProjectLayout,
    shown: list[Sequence[Choice[Path]]],
):
    state = load_state(archived)
    state.stages.clear()
    save_state(archived, state)

    result = cli.invoke(app, expand_bare_options(["epnew1", "--parent"]))

    assert result.exit_code == 1
    assert "last 10 days" in result.output
    assert not shown
    assert not projects_root.exists()
