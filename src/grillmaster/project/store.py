"""Load, save, create and archive project directories.

`project.json` is always written atomically, and read strictly: a missing or
invalid file raises (there are no old formats to tolerate; the migration
script converts those).
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.project.errors import ProjectExistsError, ProjectNotFoundError
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.naming import archive_destination
from grillmaster.project.state import ProjectState

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.source_id import SourceId


def load_state(layout: ProjectLayout) -> ProjectState:
    try:
        return read_model(layout.project_json, ProjectState)
    except FileNotFoundError as error:
        raise ProjectNotFoundError(f"No project.json in {layout.root}") from error


def save_state(layout: ProjectLayout, state: ProjectState) -> None:
    write_model(layout.project_json, state)


def create_project(
    projects_root: Path,
    source: SourceId,
    *,
    translation_hint: str | None = None,
    parent: Path | None = None,
) -> tuple[ProjectLayout, ProjectState]:
    """Start a new project for `source` and persist its initial state.

    `parent` must be an existing project directory (archived ones included);
    it is stored absolute because commands may run from any directory.
    """
    layout = ProjectLayout.for_id(projects_root, source.video_id)
    if layout.project_json.exists():
        raise ProjectExistsError(f"Project already exists: {layout.root}")
    if parent is not None:
        parent = parent.resolve()
        try:
            load_state(ProjectLayout(parent))
        except ProjectNotFoundError as error:
            raise ProjectNotFoundError(
                f"Parent is not a project directory: {parent}"
            ) from error
    state = ProjectState.create(
        source, translation_hint=translation_hint, parent=parent
    )
    save_state(layout, state)
    logger.info(f"Created project {state.id} at {layout.root}")
    return layout, state


def archive_project(
    layout: ProjectLayout, state: ProjectState, archived_root: Path
) -> ProjectLayout:
    """Move the whole project directory under `archived_root`.

    The destination is `<root>/YY/MM/<deliverable name>` (`etc/` when
    undated), trimmed to the MAX_PATH budget. An existing destination leaf is
    replaced; the shared `YY/MM` parents never are. Returns the moved layout.
    """
    if not layout.root.is_dir():
        raise ProjectNotFoundError(f"Project directory not found: {layout.root}")
    destination = archive_destination(state, archived_root)
    if destination.resolve() == layout.root.resolve():
        return layout
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The move to the NAS is a copy and can fail midway, so the previous
    # archive is only renamed aside (same volume, atomic) until it succeeds.
    previous = destination.with_name(f"{destination.name}.old")
    if destination.exists():
        logger.warning(f"Archived project already exists, replacing: {destination}")
        if previous.exists():
            shutil.rmtree(previous)
        destination.rename(previous)
    logger.info(f"Archiving project {state.id} to {destination}")
    try:
        shutil.move(layout.root, destination)
    except BaseException:
        if previous.exists():
            shutil.rmtree(destination, ignore_errors=True)
            previous.rename(destination)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    return ProjectLayout(destination)
