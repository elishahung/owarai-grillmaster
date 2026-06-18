"""Gemini-specific chunk splitting for SRT translation."""

import math

from services.srt import SrtBlock


def split_into_chunks(
    blocks: list[SrtBlock], target_char_limit: int
) -> list[list[SrtBlock]]:
    """Split blocks into chunks of roughly equal character count.

    Strategy: compute total chars, derive N = ceil(total / limit), then greedily
    add blocks to each chunk until it reaches the average target. The final
    chunk receives whatever remains and may be shorter.

    SRT block boundaries are always preserved (blocks are never split).
    """
    if not blocks:
        return []
    if target_char_limit <= 0:
        raise ValueError("target_char_limit must be positive")

    total_chars = sum(b.char_count for b in blocks)
    num_chunks = max(1, math.ceil(total_chars / target_char_limit))
    if num_chunks >= len(blocks):
        # One block per chunk maximum; otherwise each chunk gets one block.
        return [[b] for b in blocks]

    target_per_chunk = total_chars / num_chunks
    chunks: list[list[SrtBlock]] = []
    current: list[SrtBlock] = []
    current_chars = 0

    for block in blocks:
        current.append(block)
        current_chars += block.char_count
        # Close the chunk when it reaches target, unless this is the last chunk
        # (in which case we absorb the rest).
        if (
            current_chars >= target_per_chunk
            and len(chunks) < num_chunks - 1
        ):
            chunks.append(current)
            current = []
            current_chars = 0

    if current:
        chunks.append(current)
    return chunks
