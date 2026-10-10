"""Glossary stage: the agent terminology check of `work/10_refine/refined.srt`
into `work/11_glossary/checked.srt` (+ `report.md` on changes, `briefing.json`
on an accepted briefing correction, which then becomes the effective
briefing).

The agent's files are declared outputs, deleted before each fresh attempt;
its correction is a candidate promoted only after acceptance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import SrtCheckTool, ToolSession
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    load_fixed_glossary,
)
from grillmaster.postprocess.glossary_check import GlossaryInputs, check_glossary
from grillmaster.stages._common import frames_tool, program_rules, role_params
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    official = layout.ja_official_srt
    refined = require(layout.refined_srt, StageKey.REFINE)
    inputs = GlossaryInputs(
        refined_srt=refined,
        ja_srt=layout.ja_srt,
        briefing=require(layout.prepass_briefing, StageKey.PREPASS),
        official_srt=official if official.exists() else None,
        fixed_glossary=load_fixed_glossary(),
        fixed_glossary_path=FIXED_GLOSSARY_PATH,
        fixed_glossary_guide_path=FIXED_GLOSSARY_GUIDE_PATH,
        workdir=ctx.workdir,
        output_srt=layout.glossary_checked_srt,
        report=layout.glossary_report,
        briefing_candidate=layout.glossary_briefing_candidate,
        corrected_briefing=layout.glossary_briefing,
        program_instruction=program_rules(ctx).instruction_text(StageKey.GLOSSARY),
    )
    tools = ToolSession(
        project_root=layout.root,
        frames=frames_tool(ctx, layout.glossary_frames_dir),
        check_srt=SrtCheckTool(reference_srt=refined),
    )
    outcome = check_glossary(
        inputs, ctx.agents, session_dir=ctx.session_dir(), tools=tools
    )
    logger.success(
        "Glossary check validated: "
        f"subtitles {'changed' if outcome.srt_changed else 'unchanged'}, "
        f"briefing {'corrected' if outcome.briefing_corrected else 'unchanged'}"
    )
    if not outcome.report_written:
        logger.info(
            f"Glossary check report absent (expected at {layout.glossary_report} "
            "only when changes occur)"
        )


STAGE = StageDef(
    key=StageKey.GLOSSARY,
    label="Glossary check",
    weight=2,
    run=_run,
    params=role_params(Role.POSTPROCESS),
)
