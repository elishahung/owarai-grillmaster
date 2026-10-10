"""Download stage: the whole video, platform captions and the poster.

yt-dlp writes parts and raw captions to `work/02_download/parts/`; checked
for audio gaps (before ASR spends money on drifting timestamps), the parts
are joined into `work/02_download/full.mp4`, the thumbnail goes to the root
`poster.jpg`. New series/channel names are then registered in `grill.toml`
as empty program entries, the one write outside the project directory any
stage makes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.config.programs import register_program
from grillmaster.core.stage_key import StageKey
from grillmaster.sources.ytdlp import download_full_video
from grillmaster.stages._common import source_request, tool_params
from grillmaster.stages.base import StageDef

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
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
    source = ctx.state.source
    if source.series or source.channel:
        register_program(ctx.config_file, series=source.series, channel=source.channel)


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.poster,)


STAGE = StageDef(
    key=StageKey.DOWNLOAD,
    label="Download video",
    weight=4,
    run=_run,
    outputs=_outputs,
    params=tool_params("yt-dlp"),
)
