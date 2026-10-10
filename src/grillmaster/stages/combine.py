"""Combine stage: `video.mp4` from the downloaded `work/02_download/full.mp4`,
cut to the section.

`--start` / `--to` apply only while this stage has not run: the section is
recorded on `ProjectState.section` (the chat fetch reads it there). A cut
writes `video.mp4` and keeps `full.mp4`, so `grill reset --from combine`
can cut again; without a section `full.mp4` becomes `video.mp4`. Platform
captions become `subs/ja.official.srt`, shifted onto the section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.stage_key import StageKey
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner
from grillmaster.media.video import cut
from grillmaster.pipeline.stage import StageDef, require
from grillmaster.project.state import Section
from grillmaster.sources.official_subs import (
    normalize_official_subtitles,
    raw_caption_files,
)

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.pipeline.stage import StageContext
    from grillmaster.project.state import ProjectState


def build(ffmpeg: FfmpegRunner) -> StageDef:
    """The stage running ffmpeg through `ffmpeg` (tests pass a fake)."""

    def run(ctx: StageContext) -> None:
        layout = ctx.layout
        section = ctx.options.section
        full = layout.full_video
        if not ctx.options.has_section and not full.exists() and layout.video.exists():
            # The move below is this stage's last step: an earlier attempt
            # got that far before it was recorded.
            logger.info(f"Reusing the moved video {layout.video}")
        else:
            require(full, StageKey.DOWNLOAD)

        def record_section(state: ProjectState) -> None:
            state.section = section.model_copy()

        ctx.update(record_section)

        if ctx.config.features.official_subtitles:
            normalize_official_subtitles(
                raw_caption_files(layout.download_parts_dir),
                layout.ja_official_srt,
                section_start=section.start,
                section_end=section.end,
            )

        if ctx.options.has_section:
            cut(ffmpeg, full, layout.video, start=section.start, end=section.end)
        elif full.exists():
            logger.info(f"Moving {full} to {layout.video}")
            full.replace(layout.video)

    def on_skip(ctx: StageContext) -> None:
        if ctx.options.has_section:
            logger.warning("Video already combined; --start/--to are ignored on resume")

    def params(_config: AppConfig) -> dict[str, str]:
        return {"tool": "ffmpeg"}

    return StageDef(
        key=StageKey.COMBINE,
        label="Combine video",
        weight=2,
        run=run,
        outputs=lambda layout: (layout.video, layout.ja_official_srt),
        on_skip=on_skip,
        params=params,
        clear_state=_clear_state,
    )


def _clear_state(state: ProjectState) -> None:
    state.section = Section()


STAGE = build(SubprocessFfmpegRunner())
