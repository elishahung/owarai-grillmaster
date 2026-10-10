"""Glue several stages share: the program's rules, the yt-dlp request for the
source, the translation inputs, the frame tool, and definition helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from grillmaster.agents.adapters.base import Capability
from grillmaster.config.programs import resolve_program_rules
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import FramesTool
from grillmaster.project.layout import ProjectLayout
from grillmaster.sources.registry import source_platform
from grillmaster.sources.ytdlp import shared_options
from grillmaster.stages.base import require
from grillmaster.translate.chunker import Chunk, split_into_chunks
from grillmaster.translate.errors import TranslateError
from grillmaster.translate.inputs import SourceContext

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.model import AppConfig
    from grillmaster.config.programs import ProgramRules
    from grillmaster.core.model_spec import Role
    from grillmaster.core.srt import SrtBlock
    from grillmaster.project.state import SourceInfo
    from grillmaster.sources.base import SourcePlatform
    from grillmaster.stages.base import RunOptions, StageContext

type ParamValue = str | Callable[[AppConfig], str]


# --- definition helpers -----------------------------------------------------


def role_params(
    role: Role, **extra: ParamValue
) -> Callable[[AppConfig], dict[str, str]]:
    """`params` naming `role`'s model, plus `extra` values (fixed text or
    read from the config)."""

    def params(config: AppConfig) -> dict[str, str]:
        return {
            "model": str(config.agents.roles.spec(role)),
            **{
                key: value if isinstance(value, str) else value(config)
                for key, value in extra.items()
            },
        }

    return params


def tool_params(name: str) -> Callable[[AppConfig], dict[str, str]]:
    """`params` naming the external tool a stage drives."""

    def params(_config: AppConfig) -> dict[str, str]:
        return {"tool": name}

    return params


def flag_or_feature(
    name: Literal["cover", "date_research"],
) -> Callable[[RunOptions, AppConfig], bool]:
    """`SideTaskDef.enabled`: the run flag `name` or `[features] <name>`."""

    def enabled(options: RunOptions, config: AppConfig) -> bool:
        return bool(getattr(options, name)) or bool(getattr(config.features, name))

    return enabled


def chat_enabled(options: RunOptions) -> bool:
    """`StageDef.enabled` of the `--chat` stages."""
    return options.chat


# --- project context --------------------------------------------------------


def program_rules(config: AppConfig, source: SourceInfo) -> ProgramRules:
    """The merged `[programs.*]` rules of `source`'s series and channel."""
    return resolve_program_rules(
        config.programs, config.package, series=source.series, channel=source.channel
    )


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """How yt-dlp reaches this project's video: its platform, URL and the
    shared options (cookies per the platform's policy)."""

    platform: SourcePlatform
    url: str
    options: dict[str, Any]


def source_request(ctx: StageContext) -> SourceRequest:
    state = ctx.state
    platform = source_platform(state.platform)
    return SourceRequest(
        platform=platform,
        url=platform.url(state.id),
        options=shared_options(platform, ctx.config.paths.cookies),
    )


def frames_tool(
    ctx: StageContext,
    frames_dir: Path,
    window: tuple[float, float | None] = (0.0, None),
) -> FramesTool:
    """`get_frames` over `video.mp4` within `window` (to the end by default),
    saving into `frames_dir`."""
    return FramesTool(
        video=ctx.layout.video,
        frames_dir=frames_dir,
        window=window,
        max_side=ctx.config.translate.frame_max_side,
    )


# --- translation inputs -----------------------------------------------------


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
        talents=tuple(info.talents),
        program_instruction=program_rules(ctx.config, info).instruction_text(stage),
        official_subtitles=tuple(read_srt_file(official)) if official.exists() else (),
        parent_briefing=parent_briefing,
    )


def accepts_audio(agents: AgentRunner, role: Role) -> bool:
    """Whether the backend configured for `role` takes audio input."""
    return Capability.AUDIO_INPUT in agents.capabilities(role)
