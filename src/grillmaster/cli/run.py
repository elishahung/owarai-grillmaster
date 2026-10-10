"""`grill run <src> [HINT]` (also plain `grill <src> [HINT]`)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from loguru import logger

from grillmaster.cli.args import (
    parse_section_time,
    reject_directory_source,
    resolve_remix,
)
from grillmaster.cli.common import fail, load_or_exit
from grillmaster.core.stage_key import StageKey
from grillmaster.live_chat.layout import DEFAULT_CHAT_LAYOUT, ChatLayout


def run_command(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Argument(
            help=(
                "Video ID or URL (e.g. 'BV1ZArvBaEqL', 'https://youtu.be/dQw4w9WgXcQ', "
                "'v=dQw4w9WgXcQ'). A local directory is not a source; use "
                "'grill package <dir>'."
            ),
            show_default=False,
        ),
    ],
    hint: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Translation hint given to the pre-pass alongside the source "
                "title and description. On an existing project it applies only "
                "while the pre-pass has not run."
            ),
            show_default=False,
        ),
    ] = None,
    *,
    break_after: Annotated[
        StageKey | None,
        typer.Option(
            "--break-after",
            help=(
                "Stop after this stage. Side tasks (cover, date research) and "
                "delivery are skipped entirely."
            ),
            show_default=False,
        ),
    ] = None,
    parent: Annotated[
        Path | None,
        typer.Option(
            "--parent",
            help=(
                "Project directory (archived ones too) whose briefing seeds this "
                "new project's pre-pass for cross-episode consistency."
            ),
            exists=True,
            file_okay=False,
            show_default=False,
        ),
    ] = None,
    cover: Annotated[
        bool, typer.Option("--cover", help="Generate a cover image this run.")
    ] = False,
    date_research: Annotated[
        bool,
        typer.Option(
            "--date-research",
            help="Research the broadcast date with an agent when metadata has none.",
        ),
    ] = False,
    chat: Annotated[
        bool,
        typer.Option(
            "--chat",
            help=(
                "Also fetch and translate the YouTube live-chat replay and burn it "
                "in as a scrolling panel when packaging (see --chat-layout)."
            ),
        ),
    ] = False,
    chat_layout: Annotated[
        ChatLayout,
        typer.Option(
            "--chat-layout",
            help=(
                "'side': 16:9 picture on the left, chat in a right column, "
                "dialogue in the bottom bar; 'overlay': translucent panel over "
                "the full frame; 'none': leave the chat out."
            ),
            case_sensitive=False,
        ),
    ] = DEFAULT_CHAT_LAYOUT,
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
    start: Annotated[
        str | None,
        typer.Option(
            "--start",
            help=(
                "Process only from this time on ('90', '1:30', '0:01:30'). The "
                "full video is still downloaded and kept; video.mp4 is cut locally."
            ),
            show_default=False,
        ),
    ] = None,
    to: Annotated[
        str | None,
        typer.Option(
            "--to",
            help="Process only up to this time ('600', '10:00'); see --start.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Download, transcribe and translate one video, resuming where it stopped."""
    from grillmaster.core.source_id import parse_source
    from grillmaster.events.sinks import ConsoleSink
    from grillmaster.pipeline.registry import PIPELINE, Pipeline
    from grillmaster.pipeline.runner import run_project
    from grillmaster.project.state import Section
    from grillmaster.stages.base import RunOptions

    loaded = load_or_exit()
    try:
        reject_directory_source(source)
        source_id = parse_source(source)
        section = Section(
            start=parse_section_time(start) if start else None,
            end=parse_section_time(to) if to else None,
        )
    except ValueError as error:
        fail(str(error))
    options = RunOptions(
        source=source_id,
        hint=hint,
        parent=parent,
        break_after=break_after,
        cover=cover,
        date_research=date_research,
        chat=chat,
        chat_layout=chat_layout,
        remix=resolve_remix(remix, loaded.config.package.remix_pool),
        section=section,
    )
    try:
        # Tests hand in their own pipeline as the context object.
        pipeline = ctx.obj if isinstance(ctx.obj, Pipeline) else PIPELINE
        final = run_project(loaded, options, sinks=[ConsoleSink()], pipeline=pipeline)
    except Exception as error:  # noqa: BLE001 - reported as the command's failure
        fail(f"Failed to process {source}: {error}")
    logger.success(f"Finished {source}: {final.root}")
