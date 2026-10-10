"""Fixtures for the translate tests: chunk inputs and tool sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import frames_tool, make_blocks, make_briefing

from grillmaster.core.tool_session import ToolSession
from grillmaster.glossary.fixed import FixedGlossary
from grillmaster.translate.assets import Frame, MediaAssets
from grillmaster.translate.chunker import Chunk
from grillmaster.translate.inputs import ChunkInputs, PrepassInputs, SourceContext

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def chunk() -> Chunk:
    """Blocks 1-3 (2.0 s to 7.5 s)."""
    return Chunk(tuple(make_blocks(3)))


@pytest.fixture
def tools(tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path,
        frames=frames_tool(tmp_path, window=(0.0, 10.0)),
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
            briefing=make_briefing((1, 3)),
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
