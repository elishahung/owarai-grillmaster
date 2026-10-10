"""Glue the pre-pass and chunk stages share: the source SRT split into the
same chunks, the program context, and whether a role's backend can hear."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.agents.adapters.base import Capability
from grillmaster.config.programs import resolve_program_rules
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.stage import require
from grillmaster.project.layout import ProjectLayout
from grillmaster.translate.chunker import Chunk, split_into_chunks
from grillmaster.translate.errors import TranslateError
from grillmaster.translate.inputs import SourceContext, Talent

if TYPE_CHECKING:
    from grillmaster.agents.runner import AgentRunner
    from grillmaster.core.model_spec import Role
    from grillmaster.core.srt import SrtBlock
    from grillmaster.pipeline.stage import StageContext


def split_source(ctx: StageContext) -> tuple[tuple[SrtBlock, ...], tuple[Chunk, ...]]:
    """`subs/ja.srt` and its chunks under `[translate] chunk_char_limit`."""
    blocks = tuple(read_srt_file(require(ctx.layout.ja_srt, StageKey.TRANSCRIPT)))
    if not blocks:
        raise TranslateError(f"No subtitle blocks to translate in {ctx.layout.ja_srt}")
    chunks = tuple(split_into_chunks(blocks, ctx.config.translate.chunk_char_limit))
    return blocks, chunks


def source_context(
    ctx: StageContext, stage: StageKey, *, with_parent: bool
) -> SourceContext:
    """Program context for `stage`; the parent briefing only `with_parent`.

    A configured parent whose briefing is missing fails here, before any
    media or agent work.
    """
    state = ctx.state
    info = state.source
    rules = resolve_program_rules(
        ctx.config.programs,
        ctx.config.package,
        series=info.series,
        channel=info.channel,
    )
    official = ctx.layout.ja_official_srt
    parent_briefing = None
    if with_parent and state.parent is not None:
        parent_briefing = read_model(
            ProjectLayout(state.parent).effective_briefing(), Briefing
        )
    return SourceContext(
        title=info.title,
        description=info.description,
        hint=state.translation_hint,
        talents=tuple(
            Talent(talent.name, talent.name_kana, tuple(talent.roles))
            for talent in info.talents
        ),
        program_instruction=rules.instruction_text(stage),
        official_subtitles=tuple(read_srt_file(official)) if official.exists() else (),
        parent_briefing=parent_briefing,
    )


def accepts_audio(agents: AgentRunner, role: Role) -> bool:
    """Whether the backend configured for `role` takes audio input."""
    return Capability.AUDIO_INPUT in agents.capabilities(role)
