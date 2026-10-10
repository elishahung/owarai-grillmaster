from __future__ import annotations

import pytest
from tests.fakes import make_blocks

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import TimeRange
from grillmaster.translate.chunker import Chunk, split_into_chunks


def _uniform_blocks(count: int) -> list[SrtBlock]:
    """Blocks of identical `char_count` (single-digit indexes, same text)."""
    assert count <= 9
    return make_blocks(count)


def _sizes(chunks: list[Chunk]) -> list[int]:
    return [len(chunk.blocks) for chunk in chunks]


def test_empty_input_has_no_chunks() -> None:
    assert split_into_chunks([], 6000) == []


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        split_into_chunks(make_blocks(3), 0)


def test_everything_fits_in_one_chunk() -> None:
    blocks = make_blocks(5)
    [chunk] = split_into_chunks(blocks, 100_000)
    assert list(chunk.blocks) == blocks


@pytest.mark.parametrize(
    ("limit_in_blocks", "sizes"),
    [
        # N = ceil(9 / 4) = 3, target 3 blocks each.
        (4, [3, 3, 3]),
        # N = 5, target 1.8 blocks: each chunk closes at 2, the last takes 1.
        (2, [2, 2, 2, 2, 1]),
        # N = 2, target 4.5: closes at 5, the remainder is shorter.
        (5, [5, 4]),
    ],
)
def test_chunks_balance_characters(limit_in_blocks: int, sizes: list[int]) -> None:
    blocks = _uniform_blocks(9)
    unit = blocks[0].char_count
    chunks = split_into_chunks(blocks, limit_in_blocks * unit)
    assert _sizes(chunks) == sizes
    assert [block for chunk in chunks for block in chunk.blocks] == blocks


def test_more_chunks_than_blocks_gives_one_block_each() -> None:
    blocks = make_blocks(4)
    assert _sizes(split_into_chunks(blocks, 1)) == [1, 1, 1, 1]


def test_last_chunk_absorbs_the_rest() -> None:
    # One huge block first: the first chunk closes on it, the rest stay together.
    blocks = [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "x" * 300),
        *(SrtBlock(i, f"00:00:0{i},000 --> 00:00:0{i},500", "y") for i in range(2, 8)),
    ]
    chunks = split_into_chunks(blocks, 200)
    assert _sizes(chunks) == [1, 6]


def test_split_is_deterministic() -> None:
    blocks = make_blocks(40)
    first = split_into_chunks(blocks, 300)
    second = split_into_chunks(list(blocks), 300)
    assert [chunk.index_range for chunk in first] == [
        chunk.index_range for chunk in second
    ]


def test_chunk_ranges() -> None:
    chunk = Chunk(tuple(make_blocks(3)))
    assert chunk.index_range == (1, 3)
    # make_blocks: block i runs 2i .. 2i + 1.5 seconds.
    assert chunk.time_range == TimeRange(2.0, 7.5)
    assert chunk.char_count == sum(block.char_count for block in chunk.blocks)


def test_chunk_needs_blocks() -> None:
    with pytest.raises(ValueError, match="at least one block"):
        Chunk(())
