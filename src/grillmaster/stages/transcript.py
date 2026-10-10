"""Transcript stage: build the Japanese source SRT `subs/ja.srt` from the
ElevenLabs response. Platform captions play no part here; translation reads
them separately as ground truth."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.asr.payload import read_payload
from grillmaster.asr.srt_builder import build_srt_blocks
from grillmaster.asr.srt_compensation import srt_options_for_model
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
    payload = read_payload(require(layout.asr_json, StageKey.ASR))
    blocks = build_srt_blocks(payload, srt_options_for_model(ctx.config.asr.model))
    write_srt_file(layout.ja_srt, blocks)
    logger.success(f"Wrote {len(blocks)} Japanese subtitle blocks: {layout.ja_srt}")


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.ja_srt,)


STAGE = StageDef(
    key=StageKey.TRANSCRIPT,
    label="Build Japanese SRT",
    weight=1,
    run=_run,
    outputs=_outputs,
)
