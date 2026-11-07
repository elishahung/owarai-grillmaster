"""Download stage: the whole video, platform captions and the poster.

yt-dlp writes parts and raw captions to `.work/02_download/parts/`; checked
for audio gaps (before ASR spends money on drifting timestamps), the parts
are joined into `.work/02_download/full.mp4`, the thumbnail goes to the root
`poster.jpg`. New series/channel names are then registered in `grill.toml`
as empty program entries, the one write outside the project directory any
stage makes.

A compilation downloads each entry into `parts/<NN>/` (no captions; the
first entry's thumbnail is the poster), records where each will lie on
`full.mp4` (`state.segments`, from the parts' lengths and the titles the
metadata stage kept) and only then joins them, re-encoding when their
formats differ. An existing `full.mp4` is the finished download, so the
segments it was joined from are already recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.config.programs import register_program
from grillmaster.core.json_artifact import read_model
from grillmaster.core.stage_key import StageKey
from grillmaster.media.probe import duration
from grillmaster.media.video import join_parts
from grillmaster.package.render import render_progress
from grillmaster.project.state import Segment
from grillmaster.sources.ytdlp import (
    VideoInfo,
    download_entries,
    download_full_video,
)
from grillmaster.stages._common import source_request, tool_params
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext

_JOIN_SCOPE = "download:join"


def _run(ctx: StageContext) -> None:
    if ctx.state.compilation is not None:
        _download_compilation(ctx)
    else:
        _download_video(ctx)
    source = ctx.state.source
    if source.series or source.channel:
        register_program(ctx.config_file, series=source.series, channel=source.channel)


def _download_video(ctx: StageContext) -> None:
    request = source_request(ctx)
    request.platform.before_download()
    download_full_video(
        ctx.ytdlp,
        ctx.ffmpeg,
        request.url,
        parts_dir=ctx.layout.download_parts_dir,
        output=ctx.layout.full_video,
        poster=ctx.layout.poster,
        options=request.options,
        captions=ctx.config.features.official_subtitles,
        events=ctx.events,
    )


def _download_compilation(ctx: StageContext) -> None:
    layout = ctx.layout
    if layout.full_video.exists():
        logger.info(f"Reusing the downloaded video {layout.full_video}")
        return
    request = source_request(ctx)
    request.platform.before_download()
    sources = ctx.state.entry_sources
    numbers = range(1, len(sources) + 1)
    entries = download_entries(
        ctx.ytdlp,
        ctx.ffmpeg,
        [request.platform.url(source.video_id) for source in sources],
        entry_dirs=[layout.download_entry_dir(number) for number in numbers],
        poster=layout.poster,
        options=request.options,
        events=ctx.events,
    )
    segments: list[Segment] = []
    start = 0.0
    for number, parts in zip(numbers, entries, strict=True):
        info_path = require(layout.metadata_entry_info(number), StageKey.METADATA)
        info = read_model(info_path, VideoInfo)
        end = start + sum(duration(ctx.ffmpeg, part) for part in parts)
        segments.append(Segment(title=info.title, start=start, end=end))
        start = end

    def record(state: ProjectState) -> None:
        state.segments = segments

    ctx.update(record)
    with render_progress(ctx.events, _JOIN_SCOPE, "Joining videos", start) as bar:
        progress = bar.track(start)
        join_parts(
            ctx.ffmpeg,
            [part for parts in entries for part in parts],
            layout.full_video,
            on_progress=progress,
        )
        progress.complete()


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.poster,)


def _clear_state(state: ProjectState) -> None:
    state.segments = []


STAGE = StageDef(
    key=StageKey.DOWNLOAD,
    label="Download video",
    weight=4,
    run=_run,
    outputs=_outputs,
    params=tool_params("yt-dlp"),
    clear_state=_clear_state,
)
