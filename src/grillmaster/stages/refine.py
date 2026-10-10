"""Refine stage: agent polish of `work/09_chunks/merged.srt` into
`work/10_refine/refined.srt` (+ `report.md`).

Every file the agent may write is a declared output, which the agent runner
deletes before each fresh attempt; `grill reset` re-runs a finished refine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.stage import StageDef, require
from grillmaster.postprocess.refine import RefineInputs, build_refine_task
from grillmaster.project.layout import session_dir
from grillmaster.stages._postprocess import (
    postprocess_params,
    program_instruction,
    whole_video_frames,
)

if TYPE_CHECKING:
    from grillmaster.pipeline.stage import StageContext


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    inputs = RefineInputs(
        translated_srt=require(layout.merged_srt, StageKey.CHUNKS),
        ja_srt=layout.ja_srt,
        briefing=layout.effective_briefing(),
        prepass_frames_dir=layout.prepass_frames_dir,
        chunks_dir=layout.work_dir(StageKey.CHUNKS),
        output_srt=layout.refined_srt,
        report=layout.refine_report,
        program_instruction=program_instruction(ctx, StageKey.REFINE),
    )
    ctx.agents.run(
        build_refine_task(
            inputs,
            session_dir=session_dir(ctx.workdir),
            project_root=layout.root,
            frames=whole_video_frames(ctx, layout.refine_frames_dir),
        )
    )
    logger.success(
        f"Refined SRT validated: {len(read_srt_file(layout.refined_srt))} blocks"
    )
    if not layout.refine_report.exists():
        logger.warning(
            f"Refinement report missing (expected at {layout.refine_report})"
        )


STAGE = StageDef(
    key=StageKey.REFINE,
    label="Refine subtitles",
    weight=3,
    run=_run,
    outputs=lambda _layout: (),
    params=postprocess_params,
)
