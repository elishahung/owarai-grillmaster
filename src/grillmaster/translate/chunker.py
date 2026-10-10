"""Char-balanced, deterministic chunk splitting for SRT translation.

The pre-pass and the chunk stage both call `split_into_chunks` on the same
SRT with the same limit, so they always agree on the boundaries: the
briefing's segment summaries and the chunk caches are keyed by those exact
index ranges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.timecode import TimeRange

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.core.srt import SrtBlock


@dataclass(frozen=True, slots=True)
class Chunk:
    """A contiguous, non-empty run of source blocks one translator handles."""

    blocks: tuple[SrtBlock, ...]

    def __post_init__(self) -> None:
        if not self.blocks:
            raise ValueError("A chunk needs at least one block")

    @property
    def from_index(self) -> int:
        return self.blocks[0].index

    @property
    def to_index(self) -> int:
        return self.blocks[-1].index

    @property
    def index_range(self) -> tuple[int, int]:
        """`(from_index, to_index)`, inclusive."""
        return self.from_index, self.to_index

    @property
    def time_range(self) -> TimeRange:
        """From the first block's start to the last block's end."""
        return TimeRange(
            self.blocks[0].time_range.start, self.blocks[-1].time_range.end
        )

    @property
    def char_count(self) -> int:
        return sum(block.char_count for block in self.blocks)


def split_into_chunks(
    blocks: Sequence[SrtBlock], target_char_limit: int
) -> list[Chunk]:
    """Split blocks into chunks of roughly equal character count.

    N = ceil(total / limit); blocks are added greedily until a chunk reaches
    the average target, and the last chunk takes whatever remains. Blocks are
    never split. With N at least the block count, each block is its own chunk.
    `target_char_limit` is positive (config validation).
    """
    if not blocks:
        return []

    total_chars = sum(block.char_count for block in blocks)
    num_chunks = max(1, math.ceil(total_chars / target_char_limit))
    if num_chunks >= len(blocks):
        return [Chunk((block,)) for block in blocks]

    target_per_chunk = total_chars / num_chunks
    chunks: list[Chunk] = []
    current: list[SrtBlock] = []
    current_chars = 0
    for block in blocks:
        current.append(block)
        current_chars += block.char_count
        # The last chunk absorbs the rest instead of closing at the target.
        if current_chars >= target_per_chunk and len(chunks) < num_chunks - 1:
            chunks.append(Chunk(tuple(current)))
            current = []
            current_chars = 0
    if current:
        chunks.append(Chunk(tuple(current)))
    return chunks
