"""ASS canvas, typeface, margins, colours and script headers.

The dialogue subtitles (finalize), the live-chat panel and the telops are
burned in as ASS layers on the same 1920x1080 canvas in the same font, so they
take these values (and the picture box, the measured glyph widths) from
here. Values are the tuned originals; changing one changes
every packaged video.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

PLAY_RES_X = 1920
PLAY_RES_Y = 1080
FONT_NAME = "源泉圓體月 M"
# Default style margins (left/right and bottom).
MARGIN_H = 10
MARGIN_V = 40

STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding"
)
EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)

# The dialogue style: near-white text, black outline, translucent shadow,
# bottom-centred.
DIALOGUE_STYLE_NAME = "Default"
DIALOGUE_FONT_SIZE = 64
DIALOGUE_STYLE = (
    f"Style: {DIALOGUE_STYLE_NAME},{FONT_NAME},{DIALOGUE_FONT_SIZE},&H00FDFDFD,"
    "&H000000FF,&H00000000,&H7D000000,0,0,0,0,100,100,0,0,1,6,2,2,"
    f"{MARGIN_H},{MARGIN_H},{MARGIN_V},1"
)


@dataclass(frozen=True, slots=True)
class DialogueMargins:
    """Where a layout sets the bottom-centred dialogue on the canvas: its
    left, right and bottom margins (the style's own by default)."""

    left: int = MARGIN_H
    right: int = MARGIN_H
    bottom: int = MARGIN_V

    @property
    def force_style(self) -> str | None:
        """The `subtitles` filter's override of the style's margins, `None`
        where the style sets the dialogue."""
        if self == DIALOGUE_MARGINS:
            return None
        return f"MarginL={self.left},MarginR={self.right},MarginV={self.bottom}"


# The dialogue where its style sets it, on the full canvas.
DIALOGUE_MARGINS = DialogueMargins()
# The dialogue's rows as libass draws them (measured 2026-10-29): 60 px apart,
# the outline 8 px past the glyphs on every side.
DIALOGUE_ROW = 60
DIALOGUE_OUTLINE = 8


# Glyph advance per font-size unit libass lays out with FONT_NAME: CJK
# measures ~0.89; Latin is proportional (0.49-0.57), so take its wide end.
FULL_WIDTH_ADVANCE = 0.9
HALF_WIDTH_ADVANCE = 0.57
# Code points laid out at the half-width advance: everything below the CJK
# radicals block, and the half-width forms.
_FULL_WIDTH_START = 0x2E80
_HALF_WIDTH_FORMS = (0xFF61, 0xFFDC)


@dataclass(frozen=True, slots=True)
class PictureBox:
    """Where the graded picture is shrunk to on the package frame (pixels)."""

    x: int
    y: int
    width: int
    height: int


FULL_PICTURE = PictureBox(0, 0, PLAY_RES_X, PLAY_RES_Y)


def char_width(char: str, font_size: int) -> float:
    """Approximate advance in `font_size` units, measured with libass."""
    code = ord(char)
    low, high = _HALF_WIDTH_FORMS
    half = code < _FULL_WIDTH_START or low <= code <= high
    return font_size * (HALF_WIDTH_ADVANCE if half else FULL_WIDTH_ADVANCE)


def text_width(text: str, font_size: int) -> float:
    return sum(char_width(char, font_size) for char in text)


def wrapped_rows(widths: Sequence[float], room: float) -> tuple[float, ...]:
    """The width of each row lines of these `widths` take in `room`: a line
    too wide wraps into evenly filled rows (libass's smart wrapping). libass
    breaks only at a space, so a CJK line without one in fact runs past
    `room` (2026-10-29: 75 of 771k dialogue lines)."""
    rows: list[float] = []
    for width in widths:
        count = max(1, math.ceil(width / room))
        rows.extend([width / count] * count)
    return tuple(rows)


type CanvasBox = tuple[float, float, float, float]


def dialogue_rows(text: str, margins: DialogueMargins) -> tuple[CanvasBox, ...]:
    """The box (x0, y0, x1, y1) of each row a dialogue line is drawn in on
    the canvas, top to bottom, outline included: bottom-centred between the
    `margins`, each line of its text one row or wrapped into more."""
    left, right = margins.left, PLAY_RES_X - margins.right
    widths = [
        text_width(line, DIALOGUE_FONT_SIZE)
        for line in text.splitlines()
        if line.strip()
    ]
    rows = wrapped_rows(widths, right - left)
    centre = (left + right) / 2
    top = PLAY_RES_Y - margins.bottom - len(rows) * DIALOGUE_ROW
    boxes: list[CanvasBox] = []
    for n, width in enumerate(rows):
        # The outline also reaches past the first row's top and the last's
        # bottom.
        y0 = top + n * DIALOGUE_ROW - (DIALOGUE_OUTLINE if n == 0 else 0)
        y1 = (
            top
            + (n + 1) * DIALOGUE_ROW
            + (DIALOGUE_OUTLINE if n == len(rows) - 1 else 0)
        )
        half = width / 2 + DIALOGUE_OUTLINE
        boxes.append((centre - half, y0, centre + half, y1))
    return tuple(boxes)


# User or model text loses its ASS override syntax to the full-width
# lookalikes of the backslash and the braces.
_ESCAPES = str.maketrans({"\\": chr(0xFF3C), "{": chr(0xFF5B), "}": chr(0xFF5D)})


def escape_text(text: str) -> str:
    """Neutralize ASS override syntax inside text drawn as-is."""
    return text.translate(_ESCAPES)


def bgr(rgb: str) -> str:
    """ASS colour order for an `RRGGBB` hex colour: `BBGGRR`."""
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def script_header(info: Sequence[str], styles: Sequence[str]) -> str:
    """`[Script Info]` (after `ScriptType`), `[V4+ Styles]` and the
    `[Events]` format line; dialogue lines follow directly."""
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            *info,
            "",
            "[V4+ Styles]",
            STYLE_FORMAT,
            *styles,
            "",
            "[Events]",
            EVENT_FORMAT,
            "",
        ]
    )


# `[Script Info]` of every full-canvas layer burned over the picture.
CANVAS_INFO = (
    "WrapStyle: 0",
    "ScaledBorderAndShadow: yes",
    "YCbCr Matrix: TV.709",
    f"PlayResX: {PLAY_RES_X}",
    f"PlayResY: {PLAY_RES_Y}",
)

# The header of the finalized dialogue ASS.
DIALOGUE_HEADER = script_header(CANVAS_INFO, [DIALOGUE_STYLE])
