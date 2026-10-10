"""What the pre-pass and a chunk translator are given, as explicit values.

`stages/` builds these from the project layout, state and config; nothing in
`translate` reads a project or config itself. `SourceContext` is the
program-level context both calls share; each call renders only the parts its
prompt has always used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock
    from grillmaster.glossary.fixed import FixedGlossary
    from grillmaster.translate.assets import MediaAssets
    from grillmaster.translate.chunker import Chunk


@dataclass(frozen=True, slots=True)
class Talent:
    """A cast member the source platform credits."""

    name: str
    name_kana: str | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Program context: platform metadata, the user's hint, program rules,
    the platform's closed captions and the parent episode's briefing.

    `program_instruction` is the configured text for the stage being run
    (empty when none); prompts wrap it in its header. `official_subtitles`
    is empty when the platform had no captions.
    """

    title: str | None = None
    description: str | None = None
    hint: str | None = None
    talents: tuple[Talent, ...] = ()
    program_instruction: str = ""
    official_subtitles: tuple[SrtBlock, ...] = ()
    parent_briefing: Briefing | None = None


@dataclass(frozen=True, slots=True)
class PrepassInputs:
    """One whole-film analysis call.

    `chunks` are the boundaries the chunk stage will use; the briefing must
    summarize each one. `assets.audio` is set only for an audio-capable
    backend, and decides which prompt variant is rendered.
    """

    source: SourceContext
    blocks: tuple[SrtBlock, ...]
    chunks: tuple[Chunk, ...]
    fixed_glossary: FixedGlossary
    assets: MediaAssets


@dataclass(frozen=True, slots=True)
class ChunkInputs:
    """One chunk translation call; `position` is 0-based among `total`."""

    source: SourceContext
    chunk: Chunk
    position: int
    total: int
    briefing: Briefing
    assets: MediaAssets


@dataclass(frozen=True, slots=True)
class ChunkBatchInputs:
    """Every chunk translation of one run (`chunk.translate_chunks`).

    `audio` is the full track for an audio-capable backend (each chunk gets
    its slice), `None` otherwise. `project_root` scopes the `get_frames`
    tool session; frames come from `video`.
    """

    source: SourceContext
    chunks: tuple[Chunk, ...]
    briefing: Briefing
    video: Path
    audio: Path | None
    project_root: Path
    frame_interval_s: int
    frame_max_side: int
    attempts: int
