"""Parse and serialize SRT block streams."""

import re

from .timecode import TIMECODE_LINE_REGEX
from .types import SrtBlock


_BLOCK_SEPARATOR = re.compile(r"\r?\n\r?\n")


def parse_srt(srt_text: str) -> list[SrtBlock]:
    """Parse raw SRT text into a list of SrtBlock.

    Tolerates trailing whitespace and CRLF line endings. Empty blocks (no text
    body) are preserved with an empty text field.
    """
    blocks: list[SrtBlock] = []
    for raw_block in _BLOCK_SEPARATOR.split(srt_text.strip()):
        lines = raw_block.strip().splitlines()
        if len(lines) < 2:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            raise ValueError(f"Invalid SRT index line: {lines[0]!r}")
        timecode = lines[1].strip()
        if not TIMECODE_LINE_REGEX.match(timecode):
            raise ValueError(f"Invalid SRT timecode line: {timecode!r}")
        text = "\n".join(lines[2:])
        blocks.append(SrtBlock(index=index, timecode=timecode, text=text))
    return blocks


def serialize_srt(blocks: list[SrtBlock]) -> str:
    """Serialize a list of SrtBlock back to SRT text with blank-line separators."""
    return "\n\n".join(b.raw for b in blocks) + "\n"
