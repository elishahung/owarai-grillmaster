"""Refine stage: agent polish of `work/09_chunks/merged.srt` into
`work/10_refine/refined.srt` (+ `report.md`).

Every file the agent may write is a declared output, which the agent runner
deletes before each fresh attempt; `grill reset` re-runs a finished refine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.model_spec import Role
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import SrtCheckTool, ToolSession
from grillmaster.postprocess.refine import RefineInputs, build_refine_task
from grillmaster.stages._common import frames_tool, program_rules, role_params
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    merged = require(layout.merged_srt, StageKey.CHUNKS)
    inputs = RefineInputs(
        translated_srt=merged,
        ja_srt=layout.ja_srt,
        briefing=layout.effective_briefing(),
        prepass_frames_dir=layout.prepass_frames_dir,
        chunks_dir=layout.work_dir(StageKey.CHUNKS),
        output_srt=layout.refined_srt,
        report=layout.refine_report,
        program_instruction=program_rules(ctx).instruction_text(StageKey.REFINE),
    )
    tools = ToolSession(
        project_root=layout.root,
        frames=frames_tool(ctx, layout.refine_frames_dir),
        check_srt=SrtCheckTool(reference_srt=merged),
    )
    ctx.agents.run(
        build_refine_task(inputs, session_dir=ctx.session_dir(), tools=tools)
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
    params=role_params(Role.POSTPROCESS),
)
