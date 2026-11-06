"""`grill run <src> [HINT]` (also plain `grill <src> [HINT]`), and the run
options `grill serial` shares."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from loguru import logger

from grillmaster.cli.args import (
    DEFAULT_POOL,
    parse_section_time,
    reject_directory_source,
)
from grillmaster.cli.common import fail, load_or_exit, pipeline_from
from grillmaster.cli.live import run_or_exit
from grillmaster.cli.parent import ParentOption, PickParentOption, resolve_parent
from grillmaster.core.stage_key import StageKey
from grillmaster.live_chat.layout import DEFAULT_CHAT_LAYOUT, ChatLayout

if TYPE_CHECKING:
    from grillmaster.config.load import LoadedConfig
    from grillmaster.stages.base import RunOptions

CoverOption = Annotated[
    bool, typer.Option("--cover", help="Generate a cover image this run.")
]
DateResearchOption = Annotated[
    bool,
    typer.Option(
        "--date-research",
        help="Research the broadcast date with an agent when metadata has none.",
    ),
]
ProofreadOption = Annotated[
    bool,
    typer.Option(
        "--proofread",
        help=(
            "Run the final proofread stage after the glossary check (role "
            "editor, else postprocess)."
        ),
    ),
]
TelopOption = Annotated[
    bool | None,
    typer.Option(
        "--telop/--no-telop",
        help=(
            "OCR the on-screen text cards on the GPU and burn the worthwhile "
            "ones in, translated, beside each card (needs `uv sync --extra "
            "telop`); --no-telop skips them and leaves existing ones out of "
            "the burn-in. Either overrides [features] telop and the program's "
            "telop."
        ),
        show_default=False,
    ),
]
NotesOption = Annotated[
    bool | None,
    typer.Option(
        "--notes/--no-notes",
        help=(
            "Add translator's notes at the top of the picture, explaining the "
            "references a Taiwanese viewer would miss (role editor, else "
            "postprocess); --no-notes skips them and leaves existing ones out "
            "of the burn-in. Either overrides [features] notes and the "
            "program's notes."
        ),
        show_default=False,
    ),
]
ChatOption = Annotated[
    bool,
    typer.Option(
        "--chat",
        help=(
            "Also fetch and translate the YouTube live-chat replay and burn it "
            "in as a scrolling panel when packaging (see --chat-layout)."
        ),
    ),
]
ChatLayoutOption = Annotated[
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
]
RemixOption = Annotated[
    str | None,
    typer.Option(
        "--remix",
        help=(
            "Package as remix with this noise pool; a bare --remix uses "
            "package.remix_pool. Overrides the program's remix."
        ),
        show_default=False,
    ),
]
NoRemixOption = Annotated[
    bool,
    typer.Option(
        "--no-remix",
        help="Package as a plain burn-in, whatever the program's remix says.",
    ),
]


def resolve_remix(value: str | None, *, no_remix: bool) -> str | bool | None:
    """What `--remix` / `--no-remix` ask for: the pool named, `True` for a bare
    `--remix`, `False` for `--no-remix`, `None` with neither."""
    if no_remix:
        if value is not None:
            fail("--remix and --no-remix cannot be combined")
        return False
    return True if value == DEFAULT_POOL else value


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
                "Stop after this stage. Side tasks (cover, date research, telop "
                "scan) and delivery are skipped entirely."
            ),
            show_default=False,
        ),
    ] = None,
    parent: ParentOption = None,
    pick_parent: PickParentOption = False,
    cover: CoverOption = False,
    date_research: DateResearchOption = False,
    proofread: ProofreadOption = False,
    telop: TelopOption = None,
    notes: NotesOption = None,
    chat: ChatOption = False,
    chat_layout: ChatLayoutOption = DEFAULT_CHAT_LAYOUT,
    remix: RemixOption = None,
    no_remix: NoRemixOption = False,
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
    parts: Annotated[
        int | None,
        typer.Option(
            "--parts",
            help=(
                "Split a long video into this many equal parts after the download "
                "and translate them as a serial chain of projects <id>p1, <id>p2, "
                "...; the source project is removed once they exist. Re-run the "
                "same command to resume."
            ),
            show_default=False,
        ),
    ] = None,
) -> None:
    """Download, transcribe and translate one video, resuming where it stopped."""
    from grillmaster.core.source_id import parse_source
    from grillmaster.pipeline.runner import ProjectRun
    from grillmaster.project.state import Section
    from grillmaster.stages.base import RunOptions

    loaded = load_or_exit()
    parent = resolve_parent(parent, pick=pick_parent, loaded=loaded)
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
        proofread=proofread,
        telop=telop,
        notes=notes,
        chat=chat,
        chat_layout=chat_layout,
        remix=resolve_remix(remix, no_remix=no_remix),
        section=section,
    )
    if parts is not None:
        _run_split(ctx, options, parts, loaded)
        return
    # The dashboard's retry after a failure past the archive move only
    # re-delivers the archived project (see `ProjectRun`).
    run = ProjectRun(loaded, options, pipeline_from(ctx))
    final = run_or_exit(run.run, failure=f"Failed to process {source}")
    logger.success(f"Finished {source}: {final.root}")


def _run_split(
    ctx: typer.Context, options: RunOptions, parts: int, loaded: LoadedConfig
) -> None:
    from grillmaster.pipeline.split import SplitRun

    try:
        split = SplitRun(options, parts)
    except ValueError as error:
        fail(str(error))
    pipeline = pipeline_from(ctx)
    final = run_or_exit(
        lambda sinks: split.run(loaded, sinks=sinks, pipeline=pipeline),
        failure=f"Failed to process {options.source} in {parts} parts",
    )
    logger.success(
        f"Finished {options.source} in {parts} parts: {final.root if final else '-'}"
    )
