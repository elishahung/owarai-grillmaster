"""`grill package <dir|id>`: package an existing project again."""

from __future__ import annotations

from typing import Annotated

import typer
from loguru import logger

from grillmaster.cli.args import resolve_remix
from grillmaster.cli.common import (
    fail,
    load_or_exit,
    load_project_or_exit,
    pipeline_from,
)
from grillmaster.cli.live import run_or_exit
from grillmaster.live_chat.layout import DEFAULT_CHAT_LAYOUT, ChatLayout


def package_command(
    ctx: typer.Context,
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
    remix: Annotated[
        str | None,
        typer.Option(
            "--remix",
            help=(
                "Package as remix with this noise pool; a bare --remix uses "
                "package.remix_pool."
            ),
            show_default=False,
        ),
    ] = None,
    chat_layout: Annotated[
        ChatLayout,
        typer.Option(
            "--chat-layout",
            help=(
                "Where a translated chat goes: 'side', 'overlay', or 'none' to "
                "leave it out."
            ),
            case_sensitive=False,
        ),
    ] = DEFAULT_CHAT_LAYOUT,
) -> None:
    """Build the deliverable of a finished project without running any stage."""
    from grillmaster.pipeline.runner import deliver_project
    from grillmaster.stages.base import RunOptions

    loaded = load_or_exit()
    if loaded.config.paths.package is None:
        fail("[paths] package is not set in grill.toml; nowhere to package to")
    layout, state = load_project_or_exit(project)
    options = RunOptions(
        source=state.source_id,
        chat_layout=chat_layout,
        remix=resolve_remix(remix, loaded.config.package.remix_pool),
    )
    pipeline = pipeline_from(ctx)
    run_or_exit(
        lambda sinks: deliver_project(
            loaded, layout, options, sinks=sinks, pipeline=pipeline
        ),
        failure=f"Failed to package {layout.root}",
    )
    logger.success(f"Packaged {state.id}")
