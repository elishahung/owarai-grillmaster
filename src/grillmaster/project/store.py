"""Load, save, create and archive project directories.

`project.json` is always written atomically, and read strictly: a missing or
invalid file raises (there are no old formats to tolerate; the migration
script converts those).
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.fs import staged_dir
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.project.errors import (
    ProjectError,
    ProjectExistsError,
    ProjectNotFoundError,
)
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.naming import archive_destination
from grillmaster.project.state import ProjectState, ProjectSummary

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pydantic import BaseModel

    from grillmaster.core.source_id import SourceId


def load_state(layout: ProjectLayout) -> ProjectState:
    return _read(layout, ProjectState)


def load_summary(layout: ProjectLayout) -> ProjectSummary:
    """The listing fields of `project.json` (see `ProjectSummary`)."""
    return _read(layout, ProjectSummary)


def _read[M: BaseModel](layout: ProjectLayout, model: type[M]) -> M:
    try:
        return read_model(layout.project_json, model)
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
    undated), trimmed to the MAX_PATH budget. The project is copied into a
    staging directory, the copy is verified (same files, same sizes), and
    only then swapped in (`core.fs.staged_dir`): an existing destination leaf
    is replaced, the shared `YY/MM` parents never are. The local project is
    deleted last. A failure before the swap leaves it untouched; a failure
    deleting it (a file held open) is only logged, since the archive is
    already complete. Returns the moved layout.
    """
    if not layout.root.is_dir():
        raise ProjectNotFoundError(f"Project directory not found: {layout.root}")
    destination = archive_destination(state, archived_root)
    if destination.resolve() == layout.root.resolve():
        return layout
    logger.info(f"Archiving project {state.id} to {destination}")
    with staged_dir(destination, create=False) as staging:
        shutil.copytree(layout.root, staging)
        _verify_copy(layout.root, staging)
    try:
        shutil.rmtree(layout.root)
    except OSError as error:
        logger.warning(
            f"Archived to {destination}, but the local project could not be "
            f"removed ({error}); delete {layout.root} by hand"
        )
    return ProjectLayout(destination)


def _verify_copy(source: Path, copy: Path) -> None:
    """Raise unless `copy` holds the same files with the same sizes."""
    expected = dict(_file_sizes(source))
    actual = dict(_file_sizes(copy))
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        differing = sorted(
            name
            for name in expected.keys() & actual.keys()
            if expected[name] != actual[name]
        )
        raise ProjectError(
            f"Archive copy {copy} does not match {source}: "
            f"missing {missing[:5]}, size differs {differing[:5]}"
        )


def _file_sizes(root: Path) -> Iterator[tuple[str, int]]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path.relative_to(root).as_posix(), path.stat().st_size
