"""Errors raised by the project store."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class ProjectError(Exception):
    """Base class for project-store failures."""


class ProjectExistsError(ProjectError):
    """A project directory already holds a `project.json`."""


class ProjectNotFoundError(ProjectError):
    """No project directory (or no `project.json`) where one was expected."""


class ProjectBusyError(ProjectError):
    """Another grill process holds the project's run lock
    (`store.project_lock`)."""

    def __init__(self, video_id: str) -> None:
        super().__init__(f"project {video_id} is in use by another grill process")


class ArchivedProjectError(ProjectExistsError):
    """The project lives in the archive (or anywhere outside the local
    projects root), where stages never re-run; the message names the
    `grill package` command that rebuilds its deliverable."""

    def __init__(self, video_id: str, root: Path) -> None:
        super().__init__(
            f"{video_id} is already archived at {root}; stages cannot re-run "
            f'there. To rebuild its deliverable: grill package "{root}"'
        )
        self.root = root


class DuplicateProjectError(ProjectExistsError):
    """The ID has both a local project and an archived copy: one is stale,
    and only the owner can tell which (compare, then remove one by hand)."""

    def __init__(self, video_id: str, local: Path, archived: Path) -> None:
        super().__init__(
            f"{video_id} exists both locally at {local} and archived at "
            f"{archived}; compare the two and remove the stale one by hand"
        )
        self.local = local
        self.archived = archived
