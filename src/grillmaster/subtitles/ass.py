"""ASS canvas, typeface, margins, colours and script headers.

The dialogue subtitles (finalize), the live-chat panel and the telops are
burned in as ASS layers on the same 1920x1080 canvas in the same font, so they
take these values (and the picture box, the measured glyph widths, the
line breaking) from here. Values are the tuned originals; changing one changes
every packaged video.
"""

from __future__ import annotations

import math
import re
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
    def room(self) -> int:
        """The width a row has between the margins."""
        return PLAY_RES_X - self.left - self.right

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


# A Latin run (words, numbers, their spaces) is one token, anything else
# one character.
_TOKEN = re.compile(r"[A-Za-z0-9.,'&%+/-]+(?: [A-Za-z0-9.,'&%+/-]+)*|.")
# Marks a part never starts with or ends on, with the vertical presentation
# forms a vertical telop column sets them in.
CLOSING = frozenset("，。、：；！？」』）》〉】〕…‥)︙︰︶︺︼︾﹀﹂﹄")  # noqa: RUF001 - vertical forms on purpose
OPENING = frozenset("「『（《〈【〔(︵︹︻︽︿﹁﹃")
_SLASHES = frozenset({"/", "\N{FULLWIDTH SOLIDUS}"})
# What a part may end after: not a colon, which leads into what follows.
_PAUSES = (CLOSING - {"："}) | _SLASHES | {" ", "\N{IDEOGRAPHIC SPACE}"}


@dataclass(frozen=True, slots=True)
class LineBreaks:
    """How a line too wide for its room is broken into rows (or vertical
    columns). libass breaks a line only at a space, so CJK wider than its
    room runs off both edges (2026-10-29: 75 of 771k dialogue lines); these
    break it themselves, into as few evenly filled parts as fit, each part
    ending after the punctuation, slash or space nearest an even share
    within `uneven` of it, never before closing punctuation or a slash (a
    separator ends its row), after opening punctuation or inside a Latin run
    (owner's calls, 2026-10-29)."""

    font_size: int
    uneven: float

    def broken(
        self, lines: Sequence[str], room: float, *, picture: float | None = None
    ) -> tuple[str, ...]:
        """Each line as it is, or broken into as few evenly filled parts as
        keep it within `room` pixels, no part past the `picture` (`room` by
        default; a vertical telop's room is only the length it aims at). A
        Latin run stays whole unless it alone is longer than the picture
        allows."""
        picture = room if picture is None else picture
        broken: list[str] = []
        for line in lines:
            tokens = [
                word
                for token in _TOKEN.findall(line)
                for word in (
                    re.split(r"( )", token)
                    if text_width(token, self.font_size) > picture
                    else [token]
                )
            ]
            if not tokens:
                broken.append("")
            while tokens:
                end = self._part_end(tokens, room, picture)
                broken.append("".join(tokens[:end]).strip())
                tokens = tokens[end:]
        return tuple(broken)

    def _part_end(self, tokens: Sequence[str], room: float, picture: float) -> int:
        """How many of `tokens` the first of as few parts as fit them in
        `room` takes: all when one does; else up to the pause nearest an
        even share, within `uneven` of it and leaving the rest room in the
        other parts, or as many as fill an even share."""
        size = self.font_size
        length = text_width("".join(tokens), size)
        parts = math.ceil(length / room)
        if parts <= 1:
            return len(tokens)
        # Whole characters per part, so the last one is not left short, but
        # never past the picture.
        even = min(math.ceil(length / parts / size) * size, picture)
        slack = self.uneven * even
        width = 0.0
        filled: int | None = None
        candidates: list[tuple[float, int]] = []
        for end, token in enumerate(tokens[:-1], 1):
            width += text_width(token, size)
            if width > min(even + slack, room) and filled is not None:
                break
            following = tokens[end]
            if following in CLOSING or following in _SLASHES or token in OPENING:
                continue
            if filled is None and width + text_width(following, size) > even:
                filled = end
            if (
                token in _PAUSES
                and abs(width - even) <= slack
                and width <= room
                and length - width <= (parts - 1) * room
            ):
                candidates.append((abs(width - even), end))
        if candidates:
            return min(candidates)[1]
        return filled if filled is not None else len(tokens)


type CanvasBox = tuple[float, float, float, float]


def _drawn_rows(line: str, room: float) -> list[float]:
    """The width of each row libass draws a dialogue line in within `room`:
    one, or with a space to break at, evenly filled rows (its smart
    wrapping); a line without one runs past `room` instead."""
    width = text_width(line, DIALOGUE_FONT_SIZE)
    count = max(1, math.ceil(width / room)) if " " in line.strip() else 1
    return [width / count] * count


def dialogue_rows(text: str, margins: DialogueMargins) -> tuple[CanvasBox, ...]:
    """The box (x0, y0, x1, y1) of each row a dialogue line is drawn in on
    the canvas, top to bottom, outline included: bottom-centred between the
    `margins`, each line of its text one row (finalize breaks one too wide
    for the full canvas) or wrapped at its spaces into more."""
    rows = [
        row
        for line in text.splitlines()
        if line.strip()
        for row in _drawn_rows(line, margins.room)
    ]
    centre = margins.left + margins.room / 2
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
