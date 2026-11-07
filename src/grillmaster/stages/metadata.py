"""Metadata stage: yt-dlp info (`.work/01_metadata/info.json`), platform
extras and the broadcast date, recorded on `ProjectState`.

Extras and the date are best-effort (a missing date only leaves the
deliverable undated); the yt-dlp extraction itself must succeed. A re-run
never erases what an earlier run found: the date, the talents and the
on-air label are only overwritten by non-empty values (`grill reset`
clears them). A compilation fetches every entry's info instead
(`entries/<NN>.json`) and takes its name, title and overrides from the
manifest (`_run_compilation`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.stage_key import StageKey
from grillmaster.project.state import SourceInfo
from grillmaster.sources.ytdlp import fetch_info, safe_filename
from grillmaster.stages._common import source_request
from grillmaster.stages.base import StageDef

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.core.talent import Talent
    from grillmaster.project.state import Compilation, ProjectState
    from grillmaster.sources.base import SourceExtras
    from grillmaster.sources.ytdlp import VideoInfo
    from grillmaster.stages._common import SourceRequest
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    if ctx.state.compilation is not None:
        _run_compilation(ctx, ctx.state.compilation)
        return
    request = source_request(ctx)
    platform = request.platform
    info, extras, broadcast_date = _fetch(
        ctx, request, ctx.state.origin_source.video_id, ctx.layout.metadata_info
    )
    if broadcast_date is None:
        logger.warning(
            "Broadcast date could not be resolved; deliverables will use "
            "the undated name"
        )
    else:
        logger.info(f"Resolved broadcast date: {broadcast_date:%Y-%m-%d}")
    description = info.description if platform.describes_program else None

    def record(project: ProjectState) -> None:
        known = project.source
        project.name = info.filename
        if broadcast_date is not None:
            project.broadcast_date = broadcast_date
        project.source = SourceInfo(
            title=info.title or None,
            description=description or None,
            series=info.series,
            channel=info.channel,
            broadcast_label=extras.broadcast_label or known.broadcast_label,
            talents=list(extras.talents) or known.talents,
        )

    ctx.update(record)


def _run_compilation(ctx: StageContext, compilation: Compilation) -> None:
    """Each entry's info (`entries/<NN>.json`, the titles the download puts
    on the segments) and extras; the project is named and labelled by the
    manifest. The date is the manifest's, else the latest entry's (the
    compilation cannot predate its newest video); series and channel the
    manifest's, else the value every entry shares; talents all entries'."""
    request = source_request(ctx)
    dates: list[date] = []
    series: set[str | None] = set()
    channels: set[str | None] = set()
    talents: dict[str, Talent] = {}
    for number, source in enumerate(ctx.state.entry_sources, 1):
        info, extras, aired = _fetch(
            ctx, request, source.video_id, ctx.layout.metadata_entry_info(number)
        )
        if aired is not None:
            dates.append(aired)
        series.add(info.series)
        channels.add(info.channel)
        for talent in extras.talents:
            talents.setdefault(talent.id, talent)
    broadcast_date = compilation.broadcast_date or max(dates, default=None)
    if broadcast_date is None:
        logger.warning(
            "No entry has a broadcast date; deliverables will use the undated name"
        )

    def record(project: ProjectState) -> None:
        project.name = safe_filename(compilation.title)
        project.broadcast_date = broadcast_date
        project.source = SourceInfo(
            title=compilation.title,
            series=compilation.series or _shared(series),
            channel=compilation.channel or _shared(channels),
            talents=list(talents.values()),
        )

    ctx.update(record)


def _fetch(
    ctx: StageContext, request: SourceRequest, video_id: str, info_path: Path
) -> tuple[VideoInfo, SourceExtras, date | None]:
    """One video's yt-dlp info (kept at `info_path`), extras and date."""
    platform = request.platform
    info = fetch_info(
        ctx.ytdlp, platform.url(video_id), info_path, options=request.options
    )
    extras = platform.fetch_extras(video_id, ctx.http)
    return info, extras, platform.resolve_broadcast_date(info, extras)


def _shared(values: set[str | None]) -> str | None:
    """The one value every entry has, else `None`."""
    return next(iter(values)) if len(values) == 1 else None


def _params(config: AppConfig) -> dict[str, str]:
    return {"official_cc": "on" if config.features.official_subtitles else "off"}


def _clear_state(state: ProjectState) -> None:
    state.name = None
    state.broadcast_date = None
    state.source = SourceInfo()


STAGE = StageDef(
    key=StageKey.METADATA,
    label="Fetch metadata",
    weight=1,
    run=_run,
    params=_params,
    clear_state=_clear_state,
)
