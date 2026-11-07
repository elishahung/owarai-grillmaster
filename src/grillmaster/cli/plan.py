"""`grill plan <playlist>`: cut a playlist into compilation manifests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from loguru import logger

from grillmaster.cli.common import fail, load_or_exit

if TYPE_CHECKING:
    from grillmaster.pipeline.compilation import PlannedEpisode

_TITLE_WIDTH = 60


def plan_command(
    playlist: Annotated[str, typer.Argument(help="Playlist URL.", show_default=False)],
    *,
    id_prefix: Annotated[
        str,
        typer.Option(
            "--id",
            help=(
                "Project ID prefix (letters and digits); episode k becomes "
                "<prefix>01, <prefix>02, ..."
            ),
            show_default=False,
        ),
    ],
    episodes: Annotated[
        int | None,
        typer.Option(
            "--episodes",
            help="How many compilations to cut the playlist into.",
            show_default=False,
        ),
    ] = None,
    minutes: Annotated[
        float | None,
        typer.Option(
            "--minutes",
            help="Target length of each compilation; sets the number of episodes.",
            show_default=False,
        ),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option(
            "--title",
            help="Episode title stem ('<title> #k'); defaults to the playlist's title.",
            show_default=False,
        ),
    ] = None,
    series: Annotated[
        str | None,
        typer.Option(
            "--series",
            help="Series for the program rules; defaults to what every video shares.",
            show_default=False,
        ),
    ] = None,
    channel: Annotated[
        str | None,
        typer.Option(
            "--channel",
            help="Channel for the program rules; defaults to what every video shares.",
            show_default=False,
        ),
    ] = None,
    items: Annotated[
        str | None,
        typer.Option(
            "--items",
            help="Only these playlist items (yt-dlp syntax, e.g. '1-40').",
            show_default=False,
        ),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory the manifests are written to."),
    ] = Path(),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the plan without writing manifests."),
    ] = False,
) -> None:
    """Cut a playlist, in order, into compilations of balanced length and
    write one manifest per compilation (`<id>.json`), each run with
    `grill <manifest.json>`. Existing manifests are never overwritten."""
    from grillmaster.pipeline.compilation import (
        PlanRequest,
        plan_compilation,
        write_plan,
    )
    from grillmaster.sources.compilation import CompilationError
    from grillmaster.sources.ytdlp import YtDlpLibrary

    loaded = load_or_exit()
    request = PlanRequest(
        url=playlist,
        id_prefix=id_prefix,
        out_dir=out.resolve(),
        episodes=episodes,
        minutes=minutes,
        title=title,
        series=series,
        channel=channel,
        items=items,
    )
    try:
        planned = plan_compilation(loaded, request, ytdlp=YtDlpLibrary())
        _print_plan(planned)
        if dry_run:
            return
        write_plan(planned)
    except CompilationError as error:
        fail(str(error))
    logger.success(f"Wrote {len(planned)} manifests to {out.resolve()}")


def _print_plan(planned: tuple[PlannedEpisode, ...]) -> None:
    from grillmaster.core.timecode import format_clock

    for episode in planned:
        manifest = episode.manifest
        typer.echo(
            f"{manifest.id:<12} {len(manifest.entries):>3} videos  "
            f"{format_clock(episode.duration):>8}  {manifest.title}"
        )
        for entry in episode.entries:
            typer.echo(f"    {entry.title[:_TITLE_WIDTH]}")
    total = sum(episode.duration for episode in planned)
    videos = sum(len(episode.manifest.entries) for episode in planned)
    typer.echo(f"{'total':<12} {videos:>3} videos  {format_clock(total):>8}")
