"""Builders for the translate tests: briefings, chunk inputs, tool sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_blocks

from grillmaster.core.briefing import Briefing, SegmentSummary
from grillmaster.core.tool_session import FramesTool, ToolSession
from grillmaster.glossary.fixed import FixedGlossary
from grillmaster.translate.assets import Frame, MediaAssets
from grillmaster.translate.chunker import Chunk
from grillmaster.translate.inputs import ChunkInputs, PrepassInputs, SourceContext

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    type MakeBriefing = Callable[..., Briefing]


def briefing(*ranges: tuple[int, int], summary: str = "summary") -> Briefing:
    """A minimal briefing with one segment summary `seg a-b` per range."""
    return Briefing(
        summary=summary,
        characters=[],
        proper_nouns=[],
        glossary=[],
        catchphrases=[],
        tone_notes="tone",
        segment_summaries=[
            SegmentSummary(from_index=start, to_index=end, summary=f"seg {start}-{end}")
            for start, end in ranges
        ],
    )


@pytest.fixture
def chunk() -> Chunk:
    """Blocks 1-3 (2.0 s to 7.5 s)."""
    return Chunk(tuple(make_blocks(3)))


@pytest.fixture
def tools(tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path,
        frames=FramesTool(
            video=tmp_path / "video.mp4",
            frames_dir=tmp_path / "frames",
            window=(0.0, 10.0),
            max_side=768,
        ),
        check_srt=None,
    )


@pytest.fixture
def chunk_inputs(tmp_path: Path, chunk: Chunk) -> Callable[..., ChunkInputs]:
    def make(*, audio: bool = True, source: SourceContext | None = None) -> ChunkInputs:
        return ChunkInputs(
            source=source or SourceContext(),
            chunk=chunk,
            position=0,
            total=2,
            briefing=briefing((1, 3)),
            assets=MediaAssets(
                frames=(
                    Frame(2.0, tmp_path / "f1.jpg"),
                    Frame(4.2, tmp_path / "f2.jpg"),
                ),
                audio=tmp_path / "audio.ogg" if audio else None,
            ),
        )

    return make


@pytest.fixture
def prepass_inputs(tmp_path: Path) -> Callable[..., PrepassInputs]:
    def make(
        *, audio: bool = True, source: SourceContext | None = None
    ) -> PrepassInputs:
        blocks = tuple(make_blocks(6))
        return PrepassInputs(
            source=source or SourceContext(),
            blocks=blocks,
            chunks=(Chunk(blocks[:3]), Chunk(blocks[3:])),
            fixed_glossary=FixedGlossary(),
            assets=MediaAssets(
                frames=(Frame(2.2, tmp_path / "f1.jpg"),),
                audio=tmp_path / "audio.ogg" if audio else None,
            ),
        )

    return make
