"""The notes layer: each note fixed at the top centre of the picture, ※
first, each row on its own translucent box.

Packaging burns it as one more `subtitles` layer under the dialogue; the
compose delivery step puts it into `video.cht.ass`. Like the dialogue, a
note always sits in one place, whatever on-screen text is there (owner's
call: one place for the eye); its box keeps it readable over cards. A note
breaks into at most `MAX_ROWS` rows of `ROOM` (`subtitles.ass.LineBreaks`,
preferring a pause), which the agent's answer is checked against.

The box is libass's own (`BorderStyle` 3), drawn from the real glyphs of
each row, so it fits every row exactly; each row is an event of its own,
`PITCH` apart, so the boxes of two rows meet without overlapping (measured).
Every look lives in the header's style; an event carries only its position.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.core.timecode import ass_centiseconds, format_ass_centiseconds
from grillmaster.subtitles.ass import (
    CANVAS_INFO,
    FONT_NAME,
    FULL_PICTURE,
    LineBreaks,
    PictureBox,
    bgr,
    escape_text,
    script,
    script_header,
)

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.notes.annotate import Notes

STYLE_NAME = "Note"
MARK = "※"
# Smaller than the telops: it sits over the picture, read beside them.
FONT_SIZE = 40
TEXT_COLOR = "F5F5F5"
BOX_COLOR = "101010"
# ASS alpha, 00 opaque to FF clear.
BOX_ALPHA = "50"
# The box beyond each row's glyphs (the style's outline, as box padding).
PAD = 8
# Row to row: the boxes of two rows meet (measured with libass).
PITCH = 46
# The first row's top, below the picture's top edge.
TOP = 30
# The widest row: about 32 characters, so a note of up to 60 fits in two.
ROOM = 1150
MAX_ROWS = 2
_BREAKS = LineBreaks(FONT_SIZE, uneven=0.5)

STYLE = (
    f"Style: {STYLE_NAME},{FONT_NAME},{FONT_SIZE},&H00{bgr(TEXT_COLOR)},"
    f"&H000000FF,&H{BOX_ALPHA}{bgr(BOX_COLOR)},&H00000000,"
    f"0,0,0,0,100,100,0,0,3,{PAD},0,8,0,0,0,1"
)
HEADER = script_header(CANVAS_INFO, [STYLE])


def rows(text: str) -> tuple[str, ...]:
    """The rows a note with `text` is drawn in, ※ first."""
    return _BREAKS.broken([f"{MARK}{text}"], ROOM)


def notes_events(notes: Notes, frame: PictureBox = FULL_PICTURE) -> list[str]:
    """The notes layer's `Dialogue` events at the top centre of `frame`, one
    per row; they take `STYLE`."""
    x = frame.x + frame.width / 2
    events: list[str] = []
    for note in sorted(notes.items, key=lambda note: note.start):
        start = format_ass_centiseconds(ass_centiseconds(note.start))
        end = format_ass_centiseconds(ass_centiseconds(note.end))
        events += [
            f"Dialogue: 0,{start},{end},{STYLE_NAME},,0,0,0,,"
            f"{{\\pos({x:.0f},{frame.y + TOP + n * PITCH:.0f})}}{escape_text(row)}"
            for n, row in enumerate(rows(note.text))
        ]
    return events


def write_notes_ass(notes: Notes, path: Path, frame: PictureBox) -> None:
    """`notes_events` as a script of its own, atomic and BOM-less UTF-8."""
    atomic_write_text(path, script(HEADER, notes_events(notes, frame)))
