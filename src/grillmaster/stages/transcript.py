"""Transcript stage: build the Japanese source SRT `.subs/ja.srt`, and its
voice-tagged twin `.subs/ja.voiced.srt` for the agents, from the kept
MAI-Transcribe-2 response. Platform captions play no part here;
translation reads them separately as ground truth."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.asr import mai
from grillmaster.asr.srt_builder import build_srt_blocks
from grillmaster.core.srt import write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    payload = mai.read_response(require(layout.asr_json, StageKey.ASR))
    source = build_srt_blocks(mai.srt_tokens(payload))
    write_srt_file(layout.ja_voiced_srt, source.voiced)
    write_srt_file(layout.ja_srt, source.blocks)
    logger.success(
        f"Wrote {len(source.blocks)} Japanese subtitle blocks: {layout.ja_srt}"
    )


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.ja_srt, layout.ja_voiced_srt)


STAGE = StageDef(
    key=StageKey.TRANSCRIPT,
    label="Build Japanese SRT",
    weight=1,
    run=_run,
    outputs=_outputs,
)
