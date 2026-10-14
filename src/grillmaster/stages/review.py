"""Review stage (`--review` / `[features] review`): an agent's final review
of `work/11_glossary/checked.srt` into `work/12_review/reviewed.srt` (+
`findings.json`, `report.md`, and the dossier `aligned.tsv`, `history.md`).

Finalize reads `reviewed.srt` once it exists (`layout.effective_subtitles`).
On a project whose finalize already ran, the review would be paid for and
never delivered (a later stage never re-runs by itself), so it is recorded
as skipped with a warning naming `grill reset <id> --from review`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import ToolSession
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    load_fixed_glossary,
)
from grillmaster.postprocess.review import ReviewInputs, review_subtitles
from grillmaster.stages._common import (
    flag_or_feature,
    frames_tool,
    official_srt,
    program_rules,
    role_params,
    split_source,
)
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> str:
    if ctx.state.is_done(StageKey.FINALIZE):
        logger.warning(
            "Review skipped: finalize already ran, so its deliverables would "
            f"ignore it; run `grill reset {ctx.state.id} --from review` to review"
        )
        return "skipped: finalize already ran"
    layout = ctx.layout
    _, chunks = split_source(ctx)
    frames_dirs = (
        layout.prepass_frames_dir,
        *(layout.chunk_frames_dir(*chunk.index_range) for chunk in chunks),
        layout.refine_frames_dir,
        layout.glossary_frames_dir,
    )
    inputs = ReviewInputs(
        checked_srt=require(layout.glossary_checked_srt, StageKey.GLOSSARY),
        refined_srt=require(layout.refined_srt, StageKey.REFINE),
        merged_srt=require(layout.merged_srt, StageKey.CHUNKS),
        ja_srt=layout.ja_srt,
        official_srt=official_srt(layout),
        briefing=require(layout.effective_briefing(), StageKey.PREPASS),
        refine_report=layout.refine_report,
        glossary_report=layout.glossary_report,
        frames_dirs=tuple(path for path in frames_dirs if path.is_dir()),
        seams=tuple(chunk.to_index for chunk in chunks[:-1]),
        fixed_glossary=load_fixed_glossary(),
        fixed_glossary_path=FIXED_GLOSSARY_PATH,
        fixed_glossary_guide_path=FIXED_GLOSSARY_GUIDE_PATH,
        workdir=ctx.workdir,
        aligned=layout.review_aligned,
        history=layout.review_history,
        findings=layout.review_findings,
        output_srt=layout.reviewed_srt,
        report=layout.review_report,
        program_instruction=program_rules(
            ctx.config, ctx.state.source
        ).instruction_text(StageKey.REVIEW),
    )
    tools = ToolSession(
        project_root=layout.root,
        frames=frames_tool(ctx, layout.review_frames_dir),
        check_srt=None,
    )
    answer = review_subtitles(
        inputs, ctx.agents, session_dir=ctx.session_dir(), tools=tools
    )
    logger.success(
        f"Review applied {len(answer.findings)} findings "
        f"({len(answer.briefing_notes)} briefing notes reported): "
        f"{layout.review_report}"
    )
    return f"{len(answer.findings)} findings"


STAGE = StageDef(
    key=StageKey.REVIEW,
    label="Final review",
    weight=2,
    run=_run,
    enabled=flag_or_feature("review"),
    params=role_params(Role.REVIEW),
)
