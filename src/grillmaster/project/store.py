"""Load, save, create and archive project directories.

`project.json` is always written atomically, and read strictly: a missing or
invalid file raises (there are no old formats to tolerate).
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.fs import exclusive_lock, staged_dir
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.project.errors import (
    DuplicateProjectError,
    ProjectBusyError,
    ProjectError,
    ProjectExistsError,
    ProjectNotFoundError,
)
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.naming import archive_destination, archived_candidates
from grillmaster.project.state import ProjectState, ProjectSummary

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pydantic import BaseModel

    from grillmaster.core.source_id import SourceId

# Under the local projects root: one `<id>.lock` per project (`project_lock`).
LOCKS_DIR_NAME = ".locks"


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
    undated), trimmed to the MAX_PATH budget. An archived copy of the same
    ID anywhere under `archived_root` (`find_archived`: a changed broadcast
    date moves the destination) or a destination that already holds a
    `project.json` is refused: replacing or duplicating an archived project
    could lose work (remove the stale one by hand). The project is copied into a staging directory, the
    copy is verified (same files, same sizes), and only then swapped in
    (`core.fs.staged_dir`): any other existing destination leaf is replaced,
    the shared `YY/MM` parents never are. The local project is deleted last,
    its `project.json` first, so a failed deletion (a file held open) leaves
    only a non-project leftover that no later run mistakes for the project;
    it is logged, since the archive is already complete. A failure before
    the swap leaves the local project untouched. Returns the moved layout.
    """
    if not layout.root.is_dir():
        raise ProjectNotFoundError(f"Project directory not found: {layout.root}")
    destination = archive_destination(state, archived_root)
    if destination.resolve() == layout.root.resolve():
        return layout
    archived = ProjectLayout(destination)
    existing = find_archived(archived_root, state.id)
    if existing is not None and existing.root.resolve() == layout.root.resolve():
        existing = None  # this archived project moves within the archive
    for taken in (existing, archived if archived.project_json.exists() else None):
        if taken is not None:
            raise ProjectExistsError(
                f"Cannot archive {layout.root}: {taken.root} already holds an "
                "archived project; compare the two and remove the stale one "
                "manually"
            )
    logger.info(f"Archiving project {state.id} to {destination}")
    with staged_dir(destination, create=False) as staging:
        shutil.copytree(layout.root, staging)
        _verify_copy(layout.root, staging)
    try:
        layout.project_json.unlink()
        shutil.rmtree(layout.root)
    except OSError as error:
        logger.warning(
            f"Archived to {destination}, but the local copy could not be "
            f"removed ({error}); {layout.root} is a leftover without "
            "project.json: delete it by hand"
        )
    return archived


@dataclass(frozen=True, slots=True)
class ProjectLocation:
    """Where a project lives: under the local projects root (`local`), or
    archived."""

    layout: ProjectLayout
    local: bool


def locate_project(
    projects_root: Path, video_id: str, *, archive_root: Path | None
) -> ProjectLocation | None:
    """The one answer to "where is this ID": its local project (a
    `project.json` under `projects_root`), else its archived one under
    `archive_root` (`find_archived`), else `None`. A local directory without
    `project.json` (an archive's failed cleanup) is no project.

    Both at once raises `DuplicateProjectError` (the archive move would
    refuse it later anyway). An archive that cannot be listed (an
    unreachable NAS) is logged and counts as holding no copy; the archive
    move's own check still fails loudly on it.
    """
    local = ProjectLayout.for_id(projects_root, video_id)
    archived = None
    if archive_root is not None:
        try:
            archived = find_archived(archive_root, video_id)
        except OSError as error:
            logger.warning(
                f"cannot check {archive_root} for an archived copy of "
                f"{video_id} ({error}); continuing as if there is none"
            )
    if local.project_json.is_file():
        if archived is not None:
            raise DuplicateProjectError(video_id, local.root, archived.root)
        return ProjectLocation(local, local=True)
    if archived is not None:
        return ProjectLocation(archived, local=False)
    return None


@contextmanager
def project_lock(projects_root: Path, video_id: str) -> Iterator[None]:
    """Hold project `video_id` for one mutating command (run, package,
    archive, reset); a second holder fails at once with `ProjectBusyError`.

    The lock file is `<projects_root>/.locks/<id>.lock`, outside every
    project directory, so it never travels into the archive, never blocks
    the archive move, and also covers the archived copy (`grill package`
    locks by ID from the same root). It is an OS-level lock
    (`core.fs.exclusive_lock`): a crashed holder leaves nothing to clean up.
    """
    held = False
    try:
        with exclusive_lock(
            projects_root / LOCKS_DIR_NAME / f"{video_id}.lock", timeout=0
        ):
            held = True
            yield
    except TimeoutError as error:
        if held:
            raise  # the body's own timeout, not a held lock
        raise ProjectBusyError(video_id) from error


def find_archived(archived_root: Path, video_id: str) -> ProjectLayout | None:
    """The archived project of `video_id` under `archived_root`, if any.

    Candidates come from the archive naming (`naming.archived_candidates`);
    each must hold a `project.json` whose `id` is `video_id` (an ID that is
    a prefix of another's matches its name pattern too).
    """
    for candidate in archived_candidates(archived_root, video_id):
        layout = ProjectLayout(candidate)
        if layout.project_json.is_file() and load_summary(layout).id == video_id:
            return layout
    return None


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
