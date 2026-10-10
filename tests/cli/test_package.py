from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.pipeline.fakes import Journal, fake_delivery

from grillmaster.cli import app
from grillmaster.cli.args import expand_bare_remix
from grillmaster.config.load import CONFIG_FILE_NAME
from grillmaster.live_chat.layout import ChatLayout
from grillmaster.pipeline.registry import Pipeline
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import RunOptions, StageContext


class Seen:
    """What the fake package step was run with."""

    def __init__(self) -> None:
        self.roots: list[Path] = []
        self.options: list[RunOptions] = []

    def record(self, ctx: StageContext) -> str:
        self.roots.append(ctx.layout.root)
        self.options.append(ctx.options)
        return "packaged"


@pytest.fixture
def seen() -> Seen:
    return Seen()


@pytest.fixture
def pipeline(seen: Seen) -> Pipeline:
    step = fake_delivery("package", Journal(), action=seen.record)
    return Pipeline((), delivery=(step,))


@pytest.fixture
def packaging_home(home: Path) -> Path:
    """`home` with `[paths] package` set."""
    toml = home / CONFIG_FILE_NAME
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text + '\n[paths]\npackage = "deliverables"\n', encoding="utf-8")
    return home


def test_packages_a_project_by_id(
    cli: CliRunner,
    packaging_home: Path,
    layout: ProjectLayout,
    pipeline: Pipeline,
    seen: Seen,
):
    result = cli.invoke(app, ["package", layout.root.name], obj=pipeline)

    assert result.exit_code == 0, result.output
    assert seen.roots == [layout.root]
    options = seen.options[0]
    assert options.remix is None
    assert options.chat_layout is ChatLayout.SIDE
    assert options.complete_run


def test_packages_an_archived_directory(
    cli: CliRunner,
    packaging_home: Path,
    state: ProjectState,
    pipeline: Pipeline,
    seen: Seen,
):
    archived = ProjectLayout(packaging_home.parent / "nas" / "25" / "10" / state.id)
    save_state(archived, state)

    result = cli.invoke(app, ["package", str(archived.root)], obj=pipeline)

    assert result.exit_code == 0, result.output
    assert seen.roots == [archived.root]


@pytest.mark.parametrize(
    ("args", "pool"),
    [
        pytest.param(["--remix"], "noise", id="bare-remix"),
        pytest.param(["--remix", "sleep"], "sleep", id="named-pool"),
    ],
)
def test_remix_names_the_pool(
    cli: CliRunner,
    *,
    packaging_home: Path,
    layout: ProjectLayout,
    pipeline: Pipeline,
    seen: Seen,
    args: list[str],
    pool: str,
):
    argv = expand_bare_remix(["package", layout.root.name, *args])

    result = cli.invoke(app, argv, obj=pipeline)

    assert result.exit_code == 0, result.output
    assert seen.options[0].remix == pool


def test_chat_layout_is_passed_on(
    cli: CliRunner,
    packaging_home: Path,
    layout: ProjectLayout,
    pipeline: Pipeline,
    seen: Seen,
):
    result = cli.invoke(
        app, ["package", layout.root.name, "--chat-layout", "none"], obj=pipeline
    )

    assert result.exit_code == 0, result.output
    assert seen.options[0].chat_layout is ChatLayout.NONE


def test_refuses_without_a_package_root(
    cli: CliRunner, home: Path, layout: ProjectLayout, pipeline: Pipeline, seen: Seen
):
    result = cli.invoke(app, ["package", layout.root.name], obj=pipeline)

    assert result.exit_code == 1
    assert "[paths] package" in result.output
    assert seen.roots == []


def test_unknown_project_fails(
    cli: CliRunner, packaging_home: Path, pipeline: Pipeline, seen: Seen
):
    result = cli.invoke(app, ["package", "epmissing"], obj=pipeline)

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert seen.roots == []


def test_a_failing_step_fails_the_command(
    cli: CliRunner, packaging_home: Path, layout: ProjectLayout
):
    def explode(ctx: StageContext) -> str:
        raise RuntimeError("nvenc out of sessions")

    pipeline = Pipeline(
        (), delivery=(fake_delivery("package", Journal(), action=explode),)
    )

    result = cli.invoke(app, ["package", layout.root.name], obj=pipeline)

    assert result.exit_code == 1
    assert "nvenc out of sessions" in result.output
