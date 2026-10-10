"""Audio stage: encode `video.mp4`'s audio track for ASR (`work/05_audio/`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.stage_key import StageKey
from grillmaster.media.audio import extract_audio
from grillmaster.stages._common import tool_params
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    video = require(ctx.layout.video, StageKey.COMBINE)
    extract_audio(ctx.ffmpeg, video, ctx.layout.audio)


STAGE = StageDef(
    key=StageKey.AUDIO,
    label="Extract audio",
    weight=1,
    run=_run,
    params=tool_params("ffmpeg"),
)
