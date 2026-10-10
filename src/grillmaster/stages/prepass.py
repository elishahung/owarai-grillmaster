"""Pre-pass stage: whole-film analysis into `work/08_prepass/briefing.json`.

An existing briefing is reused as-is (fixed-name cache); `grill reset`
re-runs the analysis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import ToolSession
from grillmaster.glossary.fixed import load_fixed_glossary
from grillmaster.stages._common import (
    accepts_audio,
    frames_tool,
    role_params,
    source_context,
    split_source,
)
from grillmaster.stages.base import StageDef, require
from grillmaster.translate.assets import prepare_prepass_assets
from grillmaster.translate.inputs import PrepassInputs
from grillmaster.translate.prepass import build_prepass_task, ensure_briefing

if TYPE_CHECKING:
    from grillmaster.agents.task import AgentTask
    from grillmaster.core.briefing import Briefing
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    ensure_briefing(ctx.layout.prepass_briefing, ctx.agents, lambda: _prepass_task(ctx))


def _prepass_task(ctx: StageContext) -> AgentTask[Briefing]:
    layout = ctx.layout
    options = ctx.config.translate
    blocks, chunks = split_source(ctx)
    source = source_context(ctx, StageKey.PREPASS, with_parent=True)
    has_audio = accepts_audio(ctx.agents, Role.PREPASS)
    assets = prepare_prepass_assets(
        ctx.ffmpeg,
        video=require(layout.video, StageKey.COMBINE),
        blocks=blocks,
        frames_dir=layout.prepass_frames_dir,
        interval_s=options.prepass_frame_interval_s,
        max_side=options.frame_max_side,
        audio=require(layout.audio, StageKey.AUDIO) if has_audio else None,
    )
    tools = ToolSession(
        project_root=layout.root,
        frames=frames_tool(
            ctx, layout.prepass_frames_dir, (0.0, blocks[-1].time_range.end)
        ),
        check_srt=None,
    )
    inputs = PrepassInputs(
        source=source,
        blocks=blocks,
        chunks=chunks,
        fixed_glossary=load_fixed_glossary(),
        assets=assets,
    )
    logger.info(
        f"Pre-pass over {len(blocks)} blocks / {len(chunks)} chunks "
        f"({len(assets.frames)} frames, audio {'on' if has_audio else 'off'})"
    )
    return build_prepass_task(
        inputs,
        session_dir=ctx.session_dir(),
        workdir=ctx.workdir,
        tools=tools,
        add_dirs=(layout.audio.parent,) if has_audio else (),
    )


STAGE = StageDef(
    key=StageKey.PREPASS,
    label="Pre-pass analysis",
    weight=4,
    run=_run,
    params=role_params(Role.PREPASS),
)
