"""Skeleton check for agent-rewritten SRT files.

Refine, glossary check and the `check_srt` agent tool all require that a
rewritten SRT keeps the reference skeleton (block count, indexes, timecodes)
and never empties a block. Blocks are compared position by position, so one
dropped block misaligns everything after it; the report is capped so such a
file still yields a readable message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.srt import read_srt_file

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock

MAX_REPORTED_PROBLEMS = 20


def check_aligned(reference: list[SrtBlock], candidate: list[SrtBlock]) -> list[str]:
    """Problems that make `candidate` diverge from `reference`; empty = valid.

    At most `MAX_REPORTED_PROBLEMS` problems are listed, followed by one
    line counting the rest.
    """
    problems: list[str] = []
    if len(reference) != len(candidate):
        problems.append(
            f"block count differs: reference={len(reference)} "
            f"candidate={len(candidate)}"
        )
    for position, (ref, cand) in enumerate(
        zip(reference, candidate, strict=False), start=1
    ):
        if ref.index != cand.index:
            problems.append(
                f"position {position}: index changed {ref.index} -> {cand.index}"
            )
        if ref.timecode != cand.timecode:
            problems.append(
                f"block {ref.index}: timecode changed "
                f"{ref.timecode!r} -> {cand.timecode!r}"
            )
        if not cand.text:
            problems.append(f"block {cand.index}: text is empty")
    if len(problems) > MAX_REPORTED_PROBLEMS:
        omitted = len(problems) - MAX_REPORTED_PROBLEMS
        problems = [
            *problems[:MAX_REPORTED_PROBLEMS],
            f"... and {omitted} more problems",
        ]
    return problems


def check_aligned_file(reference: list[SrtBlock], path: Path) -> list[str]:
    """`check_aligned` against the SRT file at `path`; a missing,
    unreadable (locked, a directory) or unparsable file is reported as a
    problem, not raised."""
    try:
        candidate = read_srt_file(path)
    except OSError as error:
        return [unreadable_problem(path, error)]
    except ValueError as error:  # UnicodeDecodeError included
        return [f"cannot parse {path}: {error}"]
    return check_aligned(reference, candidate)


def unreadable_problem(path: Path, error: OSError) -> str:
    """The problem line for an SRT file that could not be read at all."""
    if isinstance(error, FileNotFoundError):
        return f"file not found: {path}"
    return f"cannot read {path}: {error}"
