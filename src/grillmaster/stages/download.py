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
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner
from grillmaster.pipeline.stage import StageDef
from grillmaster.sources.registry import source_platform
from grillmaster.sources.ytdlp import YtDlpLibrary, download_full_video, shared_options

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.pipeline.stage import StageContext
    from grillmaster.sources.ytdlp import YtDlp


def build(ytdlp: YtDlp, ffmpeg: FfmpegRunner) -> StageDef:
    """The stage downloading through `ytdlp` and probing through `ffmpeg`."""

    def run(ctx: StageContext) -> None:
        state = ctx.state
        platform = source_platform(state.platform)
        platform.before_download()
        download_full_video(
            ytdlp,
            ffmpeg,
            platform.url(state.id),
            parts_dir=ctx.layout.download_parts_dir,
            output=ctx.layout.full_video,
            poster=ctx.layout.poster,
            options=shared_options(platform, ctx.config.paths.cookies),
            captions=ctx.config.features.official_subtitles,
            events=ctx.events,
        )
        source = state.source
        if source.series or source.channel:
            register_program(
                ctx.config_file, series=source.series, channel=source.channel
            )

    def params(_config: AppConfig) -> dict[str, str]:
        return {"tool": "yt-dlp"}

    return StageDef(
        key=StageKey.DOWNLOAD,
        label="Download video",
        weight=4,
        run=run,
        outputs=lambda layout: (layout.poster,),
        params=params,
    )


STAGE = build(YtDlpLibrary(), SubprocessFfmpegRunner())
