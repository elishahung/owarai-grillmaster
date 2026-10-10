from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import typer

from grillmaster.cli.common import hold_project_or_exit
from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.config.secrets import Secrets
from grillmaster.project.errors import ProjectNotFoundError
from grillmaster.project.store import project_lock

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


@pytest.fixture
def loaded(home: Path, roles: dict[str, str]) -> LoadedConfig:
    config = validate_config({"agents": {"roles": roles}}, root=home)
    return LoadedConfig(root=home, config=config, secrets=Secrets())


def test_holding_a_busy_project_exits(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    with (
        project_lock(loaded.projects_root, state.id),
        pytest.raises(typer.Exit),
        hold_project_or_exit(loaded, layout, state.id),
    ):
        pass


def test_the_block_s_own_project_error_propagates_and_releases_the_hold(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    def use_a_vanished_project() -> None:
        with hold_project_or_exit(loaded, layout, state.id) as held:
            assert held.id == state.id
            raise ProjectNotFoundError("gone")

    with pytest.raises(ProjectNotFoundError, match="gone"):
        use_a_vanished_project()
    with hold_project_or_exit(loaded, layout, state.id):  # released
        pass
