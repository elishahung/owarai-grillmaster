"""`grill telop <dir|id>`: translate the telops of an existing project."""

from __future__ import annotations

from typing import Annotated

import typer
from loguru import logger

from grillmaster.cli.common import load_or_exit, load_project_or_exit
from grillmaster.cli.live import run_or_exit


def telop_command(
    project: Annotated[
        str,
        typer.Argument(
            help=(
                "Project ID, URL or directory; a directory also reaches archived "
                "projects."
            ),
            show_default=False,
        ),
    ],
) -> None:
    """OCR and translate the on-screen text cards of a finished project into
    .subs/telop.cht.json; `grill package` then burns them in."""
    from grillmaster.pipeline.registry import TELOP_ONLY
    from grillmaster.pipeline.runner import deliver_project
    from grillmaster.stages.base import RunOptions

    loaded = load_or_exit()
    layout, state = load_project_or_exit(project)
    options = RunOptions(source=state.source_id)
    run_or_exit(
        lambda sinks: deliver_project(
            loaded, layout, options, sinks=sinks, pipeline=TELOP_ONLY
        ),
        failure=f"Failed to translate the telops of {layout.root}",
    )
    logger.success(f"Telops of {state.id} ready; run `grill package` to burn them in")
