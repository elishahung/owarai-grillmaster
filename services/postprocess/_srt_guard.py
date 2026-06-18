"""Shared SRT structural guard for Codex post-processing steps.

`refine` and `glossary_check` both require that a Codex-rewritten SRT keeps
the source skeleton (block count, indexes, timecodes) and never empties a
block. The check is identical for both, so it lives here once. Behavior is
byte-identical to the original `refine.py` helpers, including the literal
"refined" wording in the error strings.
"""

from __future__ import annotations

from pathlib import Path

from services.srt import SrtBlock, parse_srt


def parse_srt_file(path: Path) -> list[SrtBlock]:
    # utf-8-sig tolerates a UTF-8 BOM that Codex sometimes writes.
    raw = path.read_text(encoding="utf-8-sig").strip()
    return parse_srt(raw) if raw else []


def validate_srt_against_source(source: Path, candidate: Path) -> list[str]:
    src_blocks = parse_srt_file(source)
    cand_blocks = parse_srt_file(candidate)
    errors: list[str] = []

    if len(src_blocks) != len(cand_blocks):
        errors.append(
            f"block count differs: source={len(src_blocks)} refined={len(cand_blocks)}"
        )

    for position, (left, right) in enumerate(
        zip(src_blocks, cand_blocks), start=1
    ):
        if left.index != right.index:
            errors.append(
                f"position {position}: index changed {left.index} -> {right.index}"
            )
        if left.timecode != right.timecode:
            errors.append(
                f"block {left.index}: timecode changed "
                f"{left.timecode!r} -> {right.timecode!r}"
            )
        if not right.text:
            errors.append(f"block {right.index}: refined text is empty")

    return errors
