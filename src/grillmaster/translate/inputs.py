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
    from grillmaster.core.talent import Talent
    from grillmaster.core.timecode import TimeRange
    from grillmaster.glossary.fixed import FixedGlossary
    from grillmaster.translate.assets import MediaAssets
    from grillmaster.translate.chunker import Chunk


@dataclass(frozen=True, slots=True)
class SourceSegment:
    """One video of a compilation: its title and its span on the timeline."""

    title: str
    span: TimeRange


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Program context: platform metadata, the user's hint, program rules,
    the platform's closed captions, the parent episode's briefing and, for
    a compilation, the videos it joins.

    `program_instruction` is the configured text for the stage being run
    (empty when none); prompts wrap it in its header. `official_subtitles`
    is empty when the platform had no captions, `segments` for anything but
    a compilation.
    """

    title: str | None = None
    description: str | None = None
    hint: str | None = None
    talents: tuple[Talent, ...] = ()
    program_instruction: str = ""
    official_subtitles: tuple[SrtBlock, ...] = ()
    parent_briefing: Briefing | None = None
    segments: tuple[SourceSegment, ...] = ()


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
    """One chunk translation call; `position` is 0-based among `total`.

    `voiced` is the chunk's blocks with voice tags (what the agent reads in
    place of `chunk.blocks`); `preceding` the voiced source blocks just
    before the chunk, shown read-only so the first lines keep their context.
    """

    source: SourceContext
    chunk: Chunk
    position: int
    total: int
    briefing: Briefing
    assets: MediaAssets
    voiced: tuple[SrtBlock, ...]
    preceding: tuple[SrtBlock, ...]


@dataclass(frozen=True, slots=True)
class ChunkBatchInputs:
    """Every chunk translation of one run (`chunk.translate_chunks`).

    `audio` is the full track for an audio-capable backend (each chunk gets
    its slice), `None` otherwise. `project_root` scopes the `get_frames`
    tool session; frames come from `video`. `voiced` is the whole voiced
    source SRT, block for block the source the chunks were cut from.
    """

    source: SourceContext
    chunks: tuple[Chunk, ...]
    voiced: tuple[SrtBlock, ...]
    briefing: Briefing
    video: Path
    audio: Path | None
    project_root: Path
    frame_interval_s: int
    frame_max_side: int
    attempts: int
