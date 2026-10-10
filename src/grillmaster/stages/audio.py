"""Audio stage: encode `video.mp4`'s audio track for ASR (`work/05_audio/`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.stage_key import StageKey
from grillmaster.media.audio import extract_audio
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner
from grillmaster.pipeline.stage import StageDef, require

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.pipeline.stage import StageContext


def build(ffmpeg: FfmpegRunner) -> StageDef:
    """The stage running ffmpeg through `ffmpeg` (tests pass a fake)."""

    def run(ctx: StageContext) -> None:
        video = require(ctx.layout.video, StageKey.COMBINE)
        extract_audio(ffmpeg, video, ctx.layout.audio)

    def params(_config: AppConfig) -> dict[str, str]:
        return {"tool": "ffmpeg"}

    return StageDef(
        key=StageKey.AUDIO,
        label="Extract audio",
        weight=1,
        run=run,
        outputs=lambda _layout: (),
        params=params,
    )


STAGE = build(SubprocessFfmpegRunner())
