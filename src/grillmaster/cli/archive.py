"""`grill archive <id>`: move a finished local project to `[paths] archive`."""

from __future__ import annotations

from typing import Annotated

import typer
from loguru import logger

from grillmaster.cli.common import (
    fail,
    hold_project_or_exit,
    load_or_exit,
    load_project_or_exit,
)


def archive_command(
    project: Annotated[
        str,
        typer.Argument(help="Project ID, URL or directory.", show_default=False),
    ],
) -> None:
    """Archive a finished project without running any stage or delivery step.

    For a run whose archive move failed (a NAS behind a dropped VPN): the
    move is retried without re-running any stage; `grill package <archived
    dir>` then builds the deliverable, which the failed run never reached.
    """
    from grillmaster.core.stage_key import StageKey
    from grillmaster.project.errors import ProjectError
    from grillmaster.project.store import archive_project

    loaded = load_or_exit()
    archive_root = loaded.config.paths.archive
    if archive_root is None:
        fail("[paths] archive is not set in grill.toml; nowhere to archive to")
    layout, found = load_project_or_exit(project)
    with hold_project_or_exit(loaded, layout, found.id) as state:
        if not state.is_done(StageKey.FINALIZE):
            fail(f"{state.id} is not finished ({StageKey.FINALIZE} is not done)")
        try:
            archived = archive_project(layout, state, archive_root)
        except (ProjectError, OSError) as error:
            fail(f"Failed to archive {layout.root}: {error}")
    if archived.root == layout.root:
        logger.info(f"{state.id} is already archived at {archived.root}")
        return
    logger.success(f"Archived {state.id} to {archived.root}")
