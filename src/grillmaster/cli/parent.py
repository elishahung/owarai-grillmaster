"""The parent project of `grill run` / `grill serial`: the `--parent`
directory, or one picked on screen from the recently archived projects when
`--parent` is given bare (`args.expand_bare_options`)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from loguru import logger

from grillmaster.cli.args import PICK_PARENT_FLAG
from grillmaster.cli.common import fail
from grillmaster.cli.live import ABORT_EXIT_CODE, interactive_terminal

if TYPE_CHECKING:
    from grillmaster.config.load import LoadedConfig

# A bare `--parent` lists the archived projects completed this recently.
RECENT_PARENT_DAYS = 10

ParentOption = Annotated[
    Path | None,
    typer.Option(
        "--parent",
        help=(
            "Project directory (archived ones too) whose briefing seeds this "
            "new project's pre-pass for cross-episode consistency. Given bare "
            "on a terminal, pick from the archived projects completed in the last "
            f"{RECENT_PARENT_DAYS} days."
        ),
        exists=True,
        file_okay=False,
        show_default=False,
    ),
]
# What a bare `--parent` is rewritten to; not meant to be typed.
PickParentOption = Annotated[bool, typer.Option(PICK_PARENT_FLAG, hidden=True)]


def resolve_parent(
    parent: Path | None, *, pick: bool, loaded: LoadedConfig
) -> Path | None:
    """`parent`, or the archived project the user picks when `pick`."""
    if not pick:
        return parent
    if parent is not None:
        fail("--parent was given both bare and with a directory")
    return _pick_archived_parent(loaded)


def _pick_archived_parent(loaded: LoadedConfig) -> Path:
    from grillmaster.project.state import now
    from grillmaster.project.store import recent_archived
    from grillmaster.tui.picker import Choice, pick

    if not interactive_terminal():
        fail("A bare --parent picks on a terminal only; give the parent directory")
    archive_root = loaded.config.paths.archive
    if archive_root is None:
        fail("A bare --parent lists archived projects, but paths.archive is not set")
    try:
        projects = recent_archived(
            archive_root, since=now() - timedelta(days=RECENT_PARENT_DAYS)
        )
    except OSError as error:
        fail(f"Cannot list {archive_root}: {error}")
    if not projects:
        fail(
            f"No project under {archive_root} completed in the last "
            f"{RECENT_PARENT_DAYS} days; give the parent directory"
        )
    choice = pick(
        f"Parent project (archived, completed in the last {RECENT_PARENT_DAYS} days)",
        [
            Choice(f"{p.completed_at:%m-%d %H:%M}  {p.layout.root.name}", p.layout.root)
            for p in projects
        ],
    )
    if choice is None:
        logger.warning("No parent picked")
        raise typer.Exit(code=ABORT_EXIT_CODE)
    logger.info(f"Parent: {choice}")
    return choice
