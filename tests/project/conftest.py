from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.project.state import ProjectState


@pytest.fixture
def layout(tmp_path: Path, state: ProjectState) -> ProjectLayout:
    """A real project directory under `tmp_path/projects` with `state` saved."""
    project_layout = ProjectLayout(tmp_path / "projects" / state.id)
    save_state(project_layout, state)
    return project_layout
