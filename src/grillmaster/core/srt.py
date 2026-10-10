"""SubRip blocks: the subtitle data model every stage shares."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.core.timecode import TimeRange, parse_timecode_line, parse_timecode_ms

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

# One or more blank (or whitespace-only) lines.
_BLOCK_SEPARATOR = re.compile(r"\r?\n(?:[ \t]*\r?\n)+")
# Index and timecode lines; the text body may be empty.
_MIN_BLOCK_LINES = 2


@dataclass(frozen=True, slots=True)
class SrtBlock:
    """One SRT entry: index, timecode line, and text body."""

    index: int
    timecode: str
    text: str

    @property
    def raw(self) -> str:
        """The block in SRT form, without the trailing blank line."""
        return f"{self.index}\n{self.timecode}\n{self.text}"

    @property
    def char_count(self) -> int:
        """Length of the raw block; chunk sizing is measured in this unit."""
        return len(self.raw)

    @property
    def time_range(self) -> TimeRange:
        return parse_timecode_line(self.timecode)


def parse_srt(text: str) -> list[SrtBlock]:
    """Parse SRT text. CRLF and trailing whitespace are tolerated.

    A block without a text body keeps an empty `text`; a block missing its
    index or timecode line, or with a malformed one, raises `ValueError`.
    """
    blocks: list[SrtBlock] = []
    stripped = text.strip()
    if not stripped:
        return blocks
    for raw_block in _BLOCK_SEPARATOR.split(stripped):
        lines = raw_block.strip().splitlines()
        if len(lines) < _MIN_BLOCK_LINES:
            raise ValueError(f"Incomplete SRT block: {raw_block.strip()!r}")
        try:
            index = int(lines[0].strip())
        except ValueError:
            raise ValueError(f"Invalid SRT index line: {lines[0]!r}") from None
        timecode = lines[1].strip()
        parse_timecode_ms(timecode)  # raises `ValueError` on a malformed line
        blocks.append(SrtBlock(index, timecode, "\n".join(lines[2:])))
    return blocks


def serialize_srt(blocks: Iterable[SrtBlock]) -> str:
    """SRT text with blank-line separators and a trailing newline."""
    return "\n\n".join(block.raw for block in blocks) + "\n"


def reindex(blocks: Iterable[SrtBlock]) -> list[SrtBlock]:
    """Renumber blocks contiguously from 1."""
    return [replace(block, index=i) for i, block in enumerate(blocks, start=1)]


def read_srt_file(path: Path) -> list[SrtBlock]:
    """Read an SRT file, accepting the UTF-8 BOM agents sometimes write."""
    return parse_srt(path.read_text(encoding="utf-8-sig"))


def write_srt_file(path: Path, blocks: Iterable[SrtBlock]) -> None:
    """Write atomically with LF line endings on every platform."""
    atomic_write_text(path, serialize_srt(blocks))
