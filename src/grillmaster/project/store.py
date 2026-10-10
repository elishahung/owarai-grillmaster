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


# The archive copy is built as `<leaf>.partial`; a replaced archive waits as
# `<leaf>.old` until the new copy is in place.
ARCHIVE_STAGING_SUFFIX = ".partial"
_ARCHIVE_BACKUP_SUFFIX = ".old"


def archive_project(
    layout: ProjectLayout, state: ProjectState, archived_root: Path
) -> ProjectLayout:
    """Move the whole project directory under `archived_root`.

    The destination is `<root>/YY/MM/<deliverable name>` (`etc/` when
    undated), trimmed to the MAX_PATH budget. The project is copied to
    `<destination>.partial`, the copy is verified (same files, same sizes),
    and only then swapped in: an existing destination leaf is renamed aside
    and restored if the swap fails; the shared `YY/MM` parents are never
    replaced. The local project is deleted last. A failure before the swap
    leaves it untouched; a failure deleting it (a file held open) is only
    logged, since the archive is already complete. Returns the moved layout.
    """
    if not layout.root.is_dir():
        raise ProjectNotFoundError(f"Project directory not found: {layout.root}")
    destination = archive_destination(state, archived_root)
    if destination.resolve() == layout.root.resolve():
        return layout
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f"{destination.name}{ARCHIVE_STAGING_SUFFIX}")
    if staging.exists():
        logger.warning(f"Removing a stale archive staging folder: {staging}")
        shutil.rmtree(staging)
    logger.info(f"Archiving project {state.id} to {destination}")
    try:
        shutil.copytree(layout.root, staging)
        _verify_copy(layout.root, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _swap_in(staging, destination)
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


def _swap_in(staging: Path, destination: Path) -> None:
    """Replace `destination` with `staging`; the old one is restored when the
    final rename fails, and removed (best effort) after it succeeds."""
    previous = destination.with_name(f"{destination.name}{_ARCHIVE_BACKUP_SUFFIX}")
    if destination.exists():
        logger.warning(f"Archived project already exists, replacing: {destination}")
        if previous.exists():
            shutil.rmtree(previous)
        destination.rename(previous)
    try:
        staging.replace(destination)
    except BaseException:
        if previous.exists():
            previous.rename(destination)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if previous.exists():
        try:
            shutil.rmtree(previous)
        except OSError as error:
            logger.warning(f"Could not remove the replaced archive {previous}: {error}")
