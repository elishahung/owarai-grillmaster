"""Strict structural validation for chunk SRT outputs.

Kept deliberately dependency-light — it imports only `services.srt`, never
`settings` or `genai` — so the standalone validator CLI
(`validate_chunk.py`) can load it in a subprocess without pulling the whole app
or reading `.env`.

`validate_chunk_structure` is the single source of truth for "does this chunk
output match the source skeleton", used both by the translation worker and by
the agent's self-check command.
"""

from __future__ import annotations

from services.srt import SrtBlock, parse_srt


def validate_chunk_structure(
    expected: list[SrtBlock], candidate_text: str
) -> list[SrtBlock]:
    """Validate output SRT against the source chunk by timecode.

    Raises ValueError (joined error list) when the candidate diverges from the
    source skeleton. On success returns blocks normalized onto the source
    skeleton: source index/timecode with output text matched by timecode.
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
    if missing:
        errors.append(
            "Missing source block(s): "
            + ", ".join(str(block.index) for block in missing)
        )

    count_delta = len(expected) - len(parsed)
    if count_delta:
        errors.append(
            f"Output block count delta {count_delta} "
            f"(output {len(parsed)} vs input {len(expected)})"
        )

    empty_blocks = [
        src.index
        for src in expected
        if (out := output_by_timecode.get(src.timecode)) is not None
        and not out.text.strip()
    ]
    if empty_blocks:
        errors.append(
            "Empty translated text for source block(s): "
            + ", ".join(str(index) for index in empty_blocks)
        )

    if errors:
        raise ValueError("; ".join(errors))

    normalized: list[SrtBlock] = []
    for src in expected:
        out = output_by_timecode[src.timecode]
        normalized.append(
            SrtBlock(index=src.index, timecode=src.timecode, text=out.text)
        )
    return normalized
