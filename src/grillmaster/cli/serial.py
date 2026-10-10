"""`grill serial <src>...`: several videos back to back, each seeding the next."""

from __future__ import annotations

from typing import Annotated

import typer
from loguru import logger

from grillmaster.cli.args import reject_directory_source, resolve_remix
from grillmaster.cli.common import fail, load_or_exit, pipeline_from
from grillmaster.cli.live import run_or_exit
from grillmaster.cli.parent import ParentOption, PickParentOption, resolve_parent
from grillmaster.cli.run import (
    ChatLayoutOption,
    ChatOption,
    CoverOption,
    DateResearchOption,
    RemixOption,
)
from grillmaster.live_chat.layout import DEFAULT_CHAT_LAYOUT


def serial_command(
    ctx: typer.Context,
    sources: Annotated[
        list[str],
        typer.Argument(
            help=(
                "Video IDs or URLs, processed in order. Each project's final "
                "directory (the archived one when paths.archive is set) becomes "
                "the next one's --parent, so names and terms stay consistent "
                "across episodes. The chain stops at the first failure and logs "
                "the command that resumes it."
            ),
            show_default=False,
        ),
    ],
    *,
    parent: ParentOption = None,
    pick_parent: PickParentOption = False,
    cover: CoverOption = False,
    date_research: DateResearchOption = False,
    chat: ChatOption = False,
    chat_layout: ChatLayoutOption = DEFAULT_CHAT_LAYOUT,
    remix: RemixOption = None,
) -> None:
    """Process several videos in order, seeding each from the previous one."""
    from grillmaster.core.source_id import parse_source
    from grillmaster.pipeline.serial import SerialRun
    from grillmaster.stages.base import RunOptions

    loaded = load_or_exit()
    parent = resolve_parent(parent, pick=pick_parent, loaded=loaded)
    try:
        for source in sources:
            reject_directory_source(source)
        ids = tuple(parse_source(source) for source in sources)
        serial = SerialRun(
            ids,
            RunOptions(
                source=ids[0],
                parent=parent,
                cover=cover,
                date_research=date_research,
                chat=chat,
                chat_layout=chat_layout,
                remix=resolve_remix(remix, loaded.config.package.remix_pool),
            ),
        )
    except ValueError as error:
        fail(str(error))
    pipeline = pipeline_from(ctx)
    final = run_or_exit(
        lambda sinks: serial.run(loaded, sinks=sinks, pipeline=pipeline),
        failure=f"Failed to process {len(ids)} serial sources",
    )
    logger.success(
        f"Finished {len(ids)} serial sources: {final.root if final else '-'}"
    )
