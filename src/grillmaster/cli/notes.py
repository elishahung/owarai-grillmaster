"""`grill notes <dir|id>`: write the translator's notes of an existing
project, or with `--probe` ask what the agent would note."""

from __future__ import annotations

from typing import Annotated

import typer
from loguru import logger

from grillmaster.cli.common import load_or_exit, load_project_or_exit
from grillmaster.cli.live import run_or_exit


def notes_command(
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
    *,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe",
            help=(
                "Only ask the agent, without any research, what it would note "
                "and why, into .work/16_notes/probe.md; nothing is delivered."
            ),
        ),
    ] = False,
) -> None:
    """Write the translator's notes of a finished project into
    .subs/notes.cht.json, replacing any earlier ones, and compose
    video.cht.ass again; `grill package` then burns them in."""
    from grillmaster.pipeline.registry import NOTES_ONLY, NOTES_PROBE
    from grillmaster.pipeline.runner import deliver_project
    from grillmaster.stages.base import RunOptions

    loaded = load_or_exit()
    layout, state = load_project_or_exit(project)
    options = RunOptions(source=state.source_id)
    pipeline = NOTES_PROBE if probe else NOTES_ONLY
    run_or_exit(
        lambda sinks: deliver_project(
            loaded, layout, options, sinks=sinks, pipeline=pipeline
        ),
        failure=f"Failed to write the notes of {layout.root}",
    )
    if probe:
        logger.success(f"Notes probe of {state.id}: {layout.notes_probe_report}")
    else:
        logger.success(
            f"Notes of {state.id} ready; run `grill package` to burn them in"
        )
