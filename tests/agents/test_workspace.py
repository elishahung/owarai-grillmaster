from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.agents.workspace import BOUNDARY_NAME, seal_workspace_root

if TYPE_CHECKING:
    from pathlib import Path


def test_sealing_makes_the_root_a_repository_root(tmp_path: Path):
    root = tmp_path / "projects"
    seal_workspace_root(root)
    seal_workspace_root(root)  # idempotent

    boundary = root / BOUNDARY_NAME
    assert boundary.is_file()
    assert boundary.stat().st_size == 0
