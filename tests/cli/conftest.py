from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import pytest
from typer.testing import CliRunner

from grillmaster.config.load import CONFIG_FILE_NAME, PROJECTS_DIR_NAME
from grillmaster.pipeline.registry import Pipeline
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import Result

    from grillmaster.project.state import ProjectState


@pytest.fixture
def isolated_cwd(tmp_path: Path) -> Path:
    """The working directory the root conftest isolates every test in."""
    return tmp_path


@pytest.fixture
def home(isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch, roles_toml: str) -> Path:
    """A working root holding `grill.toml`, made the working directory so no
    other `grill.toml` (the developer's own) is found."""
    root = isolated_cwd / "home"
    root.mkdir()
    toml = roles_toml + '\n[package]\nremix_pool = "noise"\n'
    (root / CONFIG_FILE_NAME).write_text(toml, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def projects_root(home: Path) -> Path:
    return home / PROJECTS_DIR_NAME


@pytest.fixture
def layout(projects_root: Path, state: ProjectState) -> ProjectLayout:
    project = ProjectLayout.for_id(projects_root, state.id)
    save_state(project, state)
    return project


class IsolatedCli(CliRunner):
    """Runs commands against an empty pipeline (through the context object)
    so `grill run` never reaches the real stages, network or agents."""

    @override
    def invoke(self, *args: Any, **kwargs: Any) -> Result:
        kwargs.setdefault("obj", Pipeline(()))
        return super().invoke(*args, **kwargs)


@pytest.fixture
def cli() -> CliRunner:
    return IsolatedCli()
