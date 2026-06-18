"""Structural validation + the one local fast-path for chunk SRT outputs.

Kept deliberately dependency-light — it imports only `services.srt` (and
`loguru`), never `settings` or `genai` — so the standalone validator CLI
(`validate_chunk.py`) can load it in a subprocess without pulling the whole app
or reading `.env`. The caller passes `tolerance` explicitly for the same reason.

`validate_chunk_structure` is the single source of truth for "does this chunk
output match the source skeleton", used both by the translation worker and by
the agent's self-check command. `canonicalize_by_position` is the one cheap
in-process repair retained from the old fix layer: when block counts already
match, output text is paired to the source skeleton by physical order.
"""

from __future__ import annotations

import re

from loguru import logger

from services.srt import SrtBlock, parse_srt, serialize_srt

_BLOCK_SEPARATOR = re.compile(r"\r?\n\r?\n")
# Lenient timecode pattern. The strict parser requires zero-padded fields; this
# lenient form accepts malformed-but-timecode-shaped lines so the lenient parser
# can strip them as metadata instead of preserving them as text.
_TIMECODE_LINE = re.compile(
    r"^\d{1,2}:\d{1,2}:\d{1,2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{1,2}:\d{1,2}[,.]\d{1,3}$"
)


def validate_chunk_structure(
    expected: list[SrtBlock], candidate_text: str, tolerance: int
) -> list[SrtBlock]:
    """Validate output SRT against the source chunk by timecode.

    Raises ValueError (joined error list) when the candidate diverges beyond
    `tolerance`. On success returns blocks normalized onto the source skeleton:
    source index/timecode with output text matched by timecode, missing source
    blocks emitted as empty text.
    """
    parsed = parse_srt(candidate_text)
    errors: list[str] = []

    expected_by_timecode = {block.timecode: block for block in expected}
    output_by_timecode: dict[str, SrtBlock] = {}
    duplicate_timecodes: set[str] = set()

    for out in parsed:
        if out.timecode not in expected_by_timecode:
            errors.append(f"Unexpected output timecode {out.timecode!r}")
            continue
        if out.timecode in output_by_timecode:
            duplicate_timecodes.add(out.timecode)
            continue
        output_by_timecode[out.timecode] = out

    if duplicate_timecodes:
        dupes = ", ".join(repr(tc) for tc in sorted(duplicate_timecodes))
        errors.append(f"Duplicate output timecodes: {dupes}")

    missing = [
        src for src in expected if src.timecode not in output_by_timecode
    ]
    if missing and len(missing) <= tolerance:
        logger.warning(
            "Output missing {} source block(s) but within tolerance {}: {}",
            len(missing),
            tolerance,
            ", ".join(str(block.index) for block in missing),
        )
    if len(missing) > tolerance:
        errors.append(
            f"Missing {len(missing)} source block(s) exceeds tolerance {tolerance}"
        )

    count_delta = len(expected) - len(parsed)
    if abs(count_delta) > tolerance:
        errors.append(
            f"Output block count delta {count_delta} exceeds tolerance {tolerance} "
            f"(output {len(parsed)} vs input {len(expected)})"
        )

    if errors:
        raise ValueError("; ".join(errors))

    normalized: list[SrtBlock] = []
    for src in expected:
        out = output_by_timecode.get(src.timecode)
        if out is None:
            normalized.append(
                SrtBlock(index=src.index, timecode=src.timecode, text="")
            )
            continue
        normalized.append(
            SrtBlock(index=src.index, timecode=src.timecode, text=out.text)
        )
    return normalized


def _parse_output_blocks_lenient(output_srt: str) -> list[SrtBlock]:
    """Parse broken model output while preserving text as much as possible.

    The strict SRT parser raises on malformed index/timecode lines; this
    tolerant view replaces the printed index with physical order, accepts a
    valid timecode found in the first few lines, and preserves text lines.
    A dummy timecode is used only so text survives — final metadata always
    comes from source, never from this dummy.
    """
    blocks: list[SrtBlock] = []
    for ordinal, raw_block in enumerate(
        _BLOCK_SEPARATOR.split(output_srt.strip()), start=1
    ):
        lines = raw_block.strip().splitlines()
        if not lines:
            continue

        timecode_line_index: int | None = None
        for index, line in enumerate(lines[:3]):
            if _TIMECODE_LINE.match(line.strip()):
                timecode_line_index = index
                break

        if timecode_line_index is None:
            text = "\n".join(lines)
            timecode = "00:00:00,000 --> 00:00:00,000"
        else:
            text = "\n".join(lines[timecode_line_index + 1 :])
            timecode = lines[timecode_line_index].strip()

        blocks.append(SrtBlock(index=ordinal, timecode=timecode, text=text))
    return blocks


def canonicalize_by_position(source_srt: str, output_srt: str) -> str | None:
    """Use physical order when source/output block counts already match.

    This handles common "metadata only" mistakes: shifted printed indices,
    wrong timecodes, or malformed index lines. It is safe only when the block
    counts match, because every output text can be paired with exactly one
    source block by position.
    """
    source_blocks = parse_srt(source_srt)
    output_blocks = _parse_output_blocks_lenient(output_srt)
    if len(source_blocks) != len(output_blocks):
        return None

    fixed_blocks = [
        SrtBlock(index=src.index, timecode=src.timecode, text=out.text)
        for src, out in zip(source_blocks, output_blocks)
    ]
    return serialize_srt(fixed_blocks)
