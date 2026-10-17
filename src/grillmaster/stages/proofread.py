"""Proofread stage (`--proofread` / `[features] proofread`): an agent's final
proofread of `work/11_glossary/checked.srt` into
`work/12_proofread/proofread.srt`, plus `briefing.json` when it corrected the
briefing (+ `answer.json`, `report.md`, and the dossier `aligned.tsv`,
`history.md`). With `[features] proofread_transcribe` the agent also gets the
`transcribe` tool (role `transcribe`), its spans kept under `transcribe/`.

Finalize reads `proofread.srt` once it exists (`layout.effective_subtitles`)
and every later reader the corrected briefing (`layout.effective_briefing`).
On a project whose finalize already ran, the proofread would be paid for and
never delivered (a later stage never re-runs by itself), so it is recorded
as skipped with a warning naming `grill reset <id> --from proofread`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import ToolSession, TranscribeTool
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    load_fixed_glossary,
)
from grillmaster.postprocess.proofread import ProofreadInputs, proofread_subtitles
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
    from grillmaster.config.model import AppConfig
    from grillmaster.core.model_spec import ModelSpec
    from grillmaster.stages.base import StageContext


# The longest span one `transcribe` call takes: a preview runs ~50 KB/s, so
# 120 s is ~8 MB once base64'd inline, well under gemini's 100 MB request.
TRANSCRIBE_MAX_SPAN_S = 120.0


def transcribe_spec(config: AppConfig) -> ModelSpec | None:
    """The `transcribe` role's model with `[features] proofread_transcribe`, else `None`."""
    if not config.features.proofread_transcribe:
        return None
    return config.agents.roles.spec(Role.TRANSCRIBE)


def transcribe_tool(ctx: StageContext) -> TranscribeTool | None:
    """`transcribe` over `video.mp4` into `work/12_proofread/transcribe/` when
    enabled; the runner refuses a transcribe model that cannot watch video
    before the proofread takes a slot."""
    spec = transcribe_spec(ctx.config)
    if spec is None:
        return None
    return TranscribeTool(
        video=require(ctx.layout.video, StageKey.COMBINE),
        clips_dir=ctx.layout.proofread_transcribe_dir,
        model=spec,
        timeout_s=ctx.config.agents.timeout_s,
        max_span_s=TRANSCRIBE_MAX_SPAN_S,
    )


def _run(ctx: StageContext) -> str:
    if ctx.state.is_done(StageKey.FINALIZE):
        logger.warning(
            "Proofread skipped: finalize already ran, so its deliverables would "
            f"ignore it; run `grill reset {ctx.state.id} --from proofread` to "
            "proofread"
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
    inputs = ProofreadInputs(
        checked_srt=require(layout.glossary_checked_srt, StageKey.GLOSSARY),
        refined_srt=require(layout.refined_srt, StageKey.REFINE),
        merged_srt=require(layout.merged_srt, StageKey.CHUNKS),
        ja_srt=layout.ja_srt,
        official_srt=official_srt(layout),
        briefing=require(layout.briefing_before(StageKey.PROOFREAD), StageKey.PREPASS),
        refine_report=layout.refine_report,
        glossary_report=layout.glossary_report,
        frames_dirs=tuple(path for path in frames_dirs if path.is_dir()),
        seams=tuple(chunk.to_index for chunk in chunks[:-1]),
        fixed_glossary=load_fixed_glossary(),
        fixed_glossary_path=FIXED_GLOSSARY_PATH,
        fixed_glossary_guide_path=FIXED_GLOSSARY_GUIDE_PATH,
        workdir=ctx.workdir,
        aligned=layout.proofread_aligned,
        history=layout.proofread_history,
        output_answer=layout.proofread_answer,
        output_srt=layout.proofread_srt,
        output_briefing=layout.proofread_briefing,
        report=layout.proofread_report,
        program_instruction=program_rules(
            ctx.config, ctx.state.source
        ).instruction_text(StageKey.PROOFREAD),
    )
    tools = ToolSession(
        project_root=layout.root,
        frames=frames_tool(ctx, layout.proofread_frames_dir),
        check_srt=None,
        transcribe=transcribe_tool(ctx),
    )
    answer = proofread_subtitles(
        inputs, ctx.agents, session_dir=ctx.session_dir(), tools=tools
    )
    logger.success(
        f"Proofread applied {len(answer.fixes)} fixes and "
        f"{len(answer.briefing_changes)} briefing changes: {layout.proofread_report}"
    )
    return f"{len(answer.fixes)} fixes, {len(answer.briefing_changes)} briefing changes"


STAGE = StageDef(
    key=StageKey.PROOFREAD,
    label="Proofread",
    weight=2,
    run=_run,
    enabled=flag_or_feature("proofread"),
    params=role_params(
        Role.PROOFREAD,
        transcribe=lambda config: str(transcribe_spec(config) or "off"),
    ),
)
