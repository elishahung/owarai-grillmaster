"""Glue the refine and glossary stages share: the program's instruction text,
the whole-video frame tool, and the role snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.config.programs import resolve_program_rules
from grillmaster.core.model_spec import Role
from grillmaster.core.tool_session import FramesTool

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.core.stage_key import StageKey
    from grillmaster.pipeline.stage import StageContext


def program_instruction(ctx: StageContext, stage: StageKey) -> str:
    """The configured instruction text of this project's program for `stage`."""
    info = ctx.state.source
    rules = resolve_program_rules(
        ctx.config.programs,
        ctx.config.package,
        series=info.series,
        channel=info.channel,
    )
    return rules.instruction_text(stage)


def whole_video_frames(ctx: StageContext, frames_dir: Path) -> FramesTool:
    """`get_frames` over the entire video, saving into `frames_dir`."""
    return FramesTool(
        video=ctx.layout.video,
        frames_dir=frames_dir,
        window=(0.0, None),
        max_side=ctx.config.translate.frame_max_side,
    )


def postprocess_params(config: AppConfig) -> dict[str, str]:
    return {"model": str(config.agents.roles.spec(Role.POSTPROCESS))}
