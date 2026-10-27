"""The telop layer: translated cards as top-centred ASS events.

Packaging burns it as one more `subtitles` layer, on the source timeline like
the dialogue, so the package trim and tempo apply to it for free. Cards on
screen together stack downwards (libass moves colliding events of one
alignment apart). A pale yellow keeps them apart from the white dialogue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.core.timecode import ass_centiseconds, format_ass_centiseconds
from grillmaster.subtitles.ass import (
    CANVAS_INFO,
    FONT_NAME,
    PLAY_RES_X,
    bgr,
    escape_text,
    script_header,
)

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.telop.select import TelopSubtitles

STYLE_NAME = "Telop"
FONT_SIZE = 50
TEXT_COLOR = "FFEC8B"
MARGIN_H = 60
MARGIN_V = 28
STYLE = (
    f"Style: {STYLE_NAME},{FONT_NAME},{FONT_SIZE},&H00{bgr(TEXT_COLOR)},&H000000FF,"
    "&H00000000,&H7D000000,0,0,0,0,100,100,0,0,1,5,2,8,"
    f"{MARGIN_H},{MARGIN_H},{MARGIN_V},1"
)
HEADER = script_header(CANVAS_INFO, [STYLE])


def render_telop_ass(subtitles: TelopSubtitles) -> str:
    events = [
        f"Dialogue: 0,{format_ass_centiseconds(ass_centiseconds(item.start))},"
        f"{format_ass_centiseconds(ass_centiseconds(item.end))},{STYLE_NAME},,0,0,0,,"
        + "\\N".join(
            escape_text(line.strip()) for line in item.zh.splitlines() if line.strip()
        )
        for item in subtitles.items
    ]
    return HEADER + "".join(f"{event}\n" for event in events)


def write_telop_ass(subtitles: TelopSubtitles, path: Path) -> None:
    # Atomic and BOM-less UTF-8, like the dialogue ASS.
    atomic_write_text(path, render_telop_ass(subtitles))


def telop_force_style(x: int, y: int, width: int) -> str:
    """`force_style` margins that keep the cards over a picture shrunk into
    the box at (`x`, `y`) of `width` on the canvas."""
    right = PLAY_RES_X - x - width
    return f"MarginL={x + MARGIN_H},MarginR={right + MARGIN_H},MarginV={y + MARGIN_V}"
