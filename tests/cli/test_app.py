from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.cli import app
from grillmaster.cli.args import expand_bare_options
from grillmaster.config.load import CONFIG_FILE_NAME
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import load_state, project_lock, save_state

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

    from grillmaster.project.state import ProjectState


def test_no_arguments_shows_help(cli: CliRunner):
    result = cli.invoke(app, [])
    assert "run" in result.output
    assert "reset" in result.output


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["epnew1"], id="shortcut"),
        pytest.param(["run", "epnew1"], id="explicit"),
        pytest.param(["--chat", "epnew1", "提示"], id="option-first"),
        pytest.param(expand_bare_options(["epnew1", "--remix"]), id="bare-remix"),
    ],
)
def test_run_dispatch_creates_the_project(
    cli: CliRunner, projects_root: Path, args: list[str]
):
    result = cli.invoke(app, args)
    assert result.exit_code == 0, result.output
    layout = ProjectLayout.for_id(projects_root, "epnew1")
    assert load_state(layout).id == "epnew1"
    assert list(layout.logs_dir.glob("run-*.log"))


def test_run_passes_the_hint(cli: CliRunner, projects_root: Path):
    result = cli.invoke(app, ["epnew1", "漫才の大会"])
    assert result.exit_code == 0, result.output
    state = load_state(ProjectLayout.for_id(projects_root, "epnew1"))
    assert state.translation_hint == "漫才の大会"


def test_run_refuses_a_project_directory(
    cli: CliRunner, home: Path, layout: ProjectLayout
):
    result = cli.invoke(app, [str(layout.root)])
    assert result.exit_code == 1
    assert "grill package" in result.output


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["epnew1", "--start", "soon"], id="bad-time"),
        pytest.param(["epnew1", "--start", "10:00", "--to", "5:00"], id="reversed"),
        pytest.param(["https://example.com/watch"], id="bad-url"),
    ],
)
def test_run_rejects_bad_input(cli: CliRunner, projects_root: Path, args: list[str]):
    result = cli.invoke(app, args)
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert not projects_root.exists()


def test_run_break_after_an_unregistered_stage_creates_nothing(
    cli: CliRunner, projects_root: Path
):
    result = cli.invoke(app, ["epnew1", "--break-after", "asr"])
    assert result.exit_code == 1
    assert "not registered" in result.output
    assert not (projects_root / "epnew1").exists()


def test_run_without_grill_toml_fails(cli: CliRunner, isolated_cwd: Path):
    result = cli.invoke(app, ["epnew1"])
    assert result.exit_code == 1
    assert "No grill.toml" in result.output


def test_status_shows_the_ledger(
    cli: CliRunner, layout: ProjectLayout, state: ProjectState
):
    state.mark_done(StageKey.METADATA, elapsed_s=2.5, params={"official_cc": "on"})
    state.add_asr_cost(0.31)
    save_state(layout, state)
    result = cli.invoke(app, ["status", "epabc123"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("epabc123 (tver)")
    assert "$0.3100" in result.output
    metadata = next(line for line in lines if " metadata " in line)
    assert "done" in metadata
    assert "(2.50s)" in metadata
    assert "official_cc=on" in metadata
    download = next(line for line in lines if " download " in line)
    assert download.rstrip().endswith("-")


def test_status_accepts_a_project_directory_without_grill_toml(
    cli: CliRunner, layout: ProjectLayout, home: Path
):
    (home / CONFIG_FILE_NAME).unlink()
    result = cli.invoke(app, ["status", str(layout.root)])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("epabc123")


def test_status_lists_projects(cli: CliRunner, layout: ProjectLayout):
    result = cli.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert result.output.split() == ["epabc123", f"0/{len(StageKey)}", "(unnamed)"]


def test_status_list_marks_a_broken_project_and_goes_on(
    cli: CliRunner, layout: ProjectLayout, projects_root: Path
):
    broken = ProjectLayout(projects_root / "aa_broken")
    broken.root.mkdir()
    broken.project_json.write_text("{", encoding="utf-8")
    (projects_root / "not_a_project").mkdir()
    result = cli.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("aa_broken")
    assert "unreadable project.json" in lines[0]
    assert lines[1].startswith("epabc123")


def test_status_of_a_broken_project_fails(cli: CliRunner, layout: ProjectLayout):
    layout.project_json.write_text("{}", encoding="utf-8")
    result = cli.invoke(app, ["status", "epabc123"])
    assert result.exit_code == 1
    assert "validation error" in result.output


def test_status_of_a_missing_project_fails(cli: CliRunner, home: Path):
    result = cli.invoke(app, ["status", "epmissing"])
    assert result.exit_code == 1
    assert "No project.json" in result.output


@pytest.fixture
def finished(layout: ProjectLayout, state: ProjectState) -> ProjectState:
    for key in StageKey:
        state.mark_done(key, elapsed_s=1.0)
    save_state(layout, state)
    layout.work_dir(StageKey.REFINE).mkdir(parents=True)
    return state


def test_reset_from(cli: CliRunner, layout: ProjectLayout, finished: ProjectState):
    result = cli.invoke(app, ["reset", "epabc123", "--from", "refine", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Reset refine, glossary, finalize, chat_translate of epabc123" in (
        result.output
    )
    assert f"removed {layout.work_dir(StageKey.REFINE)}" in result.output
    saved = load_state(layout)
    assert saved.is_done(StageKey.CHUNKS)
    assert not saved.is_done(StageKey.REFINE)


def test_reset_only_asks_first(
    cli: CliRunner, layout: ProjectLayout, finished: ProjectState
):
    declined = cli.invoke(app, ["reset", "epabc123", "--only", "chunks"], input="n\n")
    assert declined.exit_code == 1
    assert load_state(layout).is_done(StageKey.CHUNKS)
    accepted = cli.invoke(app, ["reset", "epabc123", "--only", "chunks"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    saved = load_state(layout)
    assert not saved.is_done(StageKey.CHUNKS)
    assert saved.is_done(StageKey.REFINE)


def test_reset_refuses_an_archived_project(
    cli: CliRunner, home: Path, state: ProjectState
):
    for key in StageKey:
        state.mark_done(key, elapsed_s=1.0)
    archived = ProjectLayout(home.parent / "archive" / "etc" / state.id)
    save_state(archived, state)

    result = cli.invoke(app, ["reset", str(archived.root), "--from", "refine", "-y"])

    assert result.exit_code == 1
    assert "stages cannot re-run there" in result.output
    assert load_state(archived).is_done(StageKey.REFINE)


def test_reset_by_id_finds_and_refuses_the_archived_project(
    cli: CliRunner, home: Path, state: ProjectState
):
    archive_root = home.parent / "archive"
    config = home / CONFIG_FILE_NAME
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"\n[paths]\narchive = '{archive_root.as_posix()}'\n",
        encoding="utf-8",
    )
    state.mark_done(StageKey.REFINE, elapsed_s=1.0)
    archived = ProjectLayout(archive_root / "etc" / state.id)
    save_state(archived, state)

    result = cli.invoke(app, ["reset", state.id, "--from", "refine", "-y"])

    assert result.exit_code == 1
    assert f'grill package "{archived.root}"' in result.output.replace("\n", "")
    assert load_state(archived).is_done(StageKey.REFINE)


def test_reset_accepts_a_local_project_directory(
    cli: CliRunner, layout: ProjectLayout, finished: ProjectState
):
    result = cli.invoke(app, ["reset", str(layout.root), "--only", "chunks", "-y"])
    assert result.exit_code == 0, result.output
    assert not load_state(layout).is_done(StageKey.CHUNKS)


@pytest.mark.parametrize(
    "flags",
    [[], ["--from", "asr", "--only", "asr"]],
    ids=["neither", "both"],
)
def test_reset_needs_exactly_one_flag(
    cli: CliRunner, layout: ProjectLayout, flags: list[str]
):
    result = cli.invoke(app, ["reset", "epabc123", *flags, "--yes"])
    assert result.exit_code == 1
    assert "exactly one" in result.output


def test_reset_refuses_a_project_another_process_holds(
    cli: CliRunner, projects_root: Path, layout: ProjectLayout, finished: ProjectState
):
    with project_lock(projects_root, finished.id):
        result = cli.invoke(app, ["reset", "epabc123", "--from", "refine"])
    assert result.exit_code == 1
    assert "project epabc123 is in use by another grill process" in result.output
    assert "Reset refine" not in result.output  # refused before asking
    assert load_state(layout).is_done(StageKey.REFINE)
