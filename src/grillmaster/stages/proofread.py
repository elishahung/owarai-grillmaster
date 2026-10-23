"""Proofread stage (`--proofread` / `[features] proofread`): an agent's final
proofread of `.work/11_glossary/checked.srt` into
`.work/12_proofread/proofread.srt`, plus `briefing.json` when it corrected the
briefing (+ `answer.json`, `report.md`, and the dossier `aligned.tsv`,
`history.md`). With `[features] proofread_watch_clip` the agent also gets the
`watch_clip` tool (speech from Azure's MAI-Transcribe-2, biased toward the
briefing's cast names; captions and scene from Meta's Muse, `[watch_clip]`), its
spans kept under `watch_clip/`; the cost of the requests this run made (each
span's records, cached ones cost nothing) is added to the project's
`watch_clip_cost_usd`, even when the proofread fails, since it is paid.

Finalize reads `proofread.srt` once it exists (`layout.effective_subtitles`)
and every later reader the corrected briefing (`layout.effective_briefing`).
On a project whose finalize already ran, the proofread would be paid for and
never delivered (a later stage never re-runs by itself), so it is recorded
as skipped with a warning naming `grill reset <id> --from proofread`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agent_tools.mai import MODEL as MAI_MODEL
from grillmaster.agent_tools.mai import MaiError, read_credentials
from grillmaster.agent_tools.meta import MetaError, read_api_key
from grillmaster.agent_tools.watch_record import billed_records, cost_since
from grillmaster.config.errors import ConfigError
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model
from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import ToolSession, WatchClipTool
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
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.stages.base import StageContext


# The longest span one `watch_clip` call takes: a preview runs ~50 KB/s, so
# 120 s is ~8 MB once base64'd inline, well under Meta's 50 MB inline limit.
WATCH_CLIP_MAX_SPAN_S = 120.0
# One call's limit, its retry included (gemini's CLI gives an MCP call 10
# min): a 62 s span at `high` answers in ~30 s. A timeout reaches the
# proofread as a tool error.
WATCH_CLIP_TIMEOUT_S = 300.0


def _preflight(config: AppConfig, secrets: Secrets) -> None:
    """With `watch_clip`, the Meta key and the Azure Speech key and endpoint."""
    if config.features.proofread_watch_clip:
        secrets.require_watch_clip_keys()


def watch_clip_tool(ctx: StageContext, briefing: Path) -> WatchClipTool | None:
    """`watch_clip` over `video.mp4` into `.work/12_proofread/watch_clip/`
    when enabled, its speech biased toward the cast in `briefing`. The tool
    server reads the keys from `.env` alone, so a key only in the
    environment (which passes the preflight) fails here, before the
    proofread starts."""
    if not ctx.config.features.proofread_watch_clip:
        return None
    try:
        read_api_key(ctx.env_file)
        read_credentials(ctx.env_file)
    except (MetaError, MaiError) as error:
        raise ConfigError(
            f"{error}; the watch_clip tool server reads it from there alone"
        ) from error
    settings = ctx.config.watch_clip
    return WatchClipTool(
        video=require(ctx.layout.video, StageKey.COMBINE),
        clips_dir=ctx.layout.proofread_watch_clip_dir,
        model=settings.model,
        effort=settings.effort,
        speech_phrases=speech_phrases(read_model(briefing, Briefing)),
        env_file=ctx.env_file,
        timeout_s=WATCH_CLIP_TIMEOUT_S,
        max_span_s=WATCH_CLIP_MAX_SPAN_S,
    )


def speech_phrases(briefing: Briefing) -> tuple[str, ...]:
    """The cast's Japanese names, once each. Proper nouns stay out: their
    `source` is often the ASR's mishearing (ひろおみ for ひるおび), which
    would bias the second hearing toward the very error it should catch."""
    names = (character.name_jp.strip() for character in briefing.characters)
    return tuple(dict.fromkeys(name for name in names if name))


def _watch_clip_param(config: AppConfig) -> str:
    if not config.features.proofread_watch_clip:
        return "off"
    return f"{MAI_MODEL} + {config.watch_clip.model}/{config.watch_clip.effort}"


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
        watch_clip=watch_clip_tool(ctx, inputs.briefing),
    )
    clips_dir = layout.proofread_watch_clip_dir
    seen = billed_records(clips_dir)
    spent = 0.0
    try:
        answer = proofread_subtitles(
            inputs, ctx.agents, session_dir=ctx.session_dir(), tools=tools
        )
    finally:
        if tools.watch_clip is not None:
            spent = _record_watch_clip_cost(ctx, clips_dir, seen)
    logger.success(
        f"Proofread applied {len(answer.fixes)} fixes and "
        f"{len(answer.briefing_changes)} briefing changes: {layout.proofread_report}"
    )
    result = (
        f"{len(answer.fixes)} fixes, {len(answer.briefing_changes)} briefing changes"
    )
    if tools.watch_clip is None:
        return result
    return (
        f"{result} · watch_clip ${spent:.4f} · "
        f"project ${ctx.state.watch_clip_cost_usd:.4f}"
    )


def _record_watch_clip_cost(
    ctx: StageContext, clips_dir: Path, seen: frozenset[str]
) -> float:
    """Add the requests billed since `seen` to the project's spend."""
    requests, spent = cost_since(clips_dir, seen)
    if requests:
        ctx.update(lambda state: state.add_watch_clip_cost(spent))
        logger.info(
            f"Stage watch_clip cost: ${spent:.4f} for {requests} requests "
            f"(project total ${ctx.state.watch_clip_cost_usd:.4f})"
        )
    return spent


# No `clear_state`: `watch_clip_cost_usd` is the project's cumulative spend,
# and money paid before a reset stays paid.
STAGE = StageDef(
    key=StageKey.PROOFREAD,
    label="Proofread",
    weight=2,
    run=_run,
    enabled=flag_or_feature("proofread"),
    preflight=_preflight,
    params=role_params(Role.PROOFREAD, watch_clip=_watch_clip_param),
)
