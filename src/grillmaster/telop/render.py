"""The telop layer: each translated card drawn beside its own card.

Packaging burns it as one more `subtitles` layer, on the source timeline like
the dialogue, so the package trim and tempo apply to it for free, and under
the dialogue, which always stays on top: this layer knows nothing of it. A
pale yellow keeps the cards apart from the white dialogue.

Every translation sits next to the card it translates, so the eye finds it
where the card is: right below it (one side for every item of a list),
right above only when below does not fit in the picture, and away from the
other translations and cards up with it. Above and below are measured on
the card lines it shares columns with, so a tab or corner label off to one
side does not push it away from the rest. It leaves with its card, or after
`MAX_SHOWN_S` when the card stays well past that. Positions follow `frame`, the
picture's box on the canvas (a side layout shrinks it).

The telop stage also writes the full-picture layer together with the
dialogue as one script beside the video, for playing it with external
subtitles.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.core.timecode import ass_centiseconds, format_ass_centiseconds
from grillmaster.subtitles.ass import (
    CANVAS_INFO,
    DIALOGUE_STYLE,
    FONT_NAME,
    FULL_PICTURE,
    PLAY_RES_X,
    PictureBox,
    bgr,
    escape_text,
    script_header,
    text_width,
)
from grillmaster.subtitles.finalize import dialogue_line
from grillmaster.telop.scan import set_vertically

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock
    from grillmaster.telop.scan import Box
    from grillmaster.telop.select import TelopSubtitle, TelopSubtitles

STYLE_NAME = "Telop"
# Smaller than the dialogue: it sits in the picture, often between cards.
FONT_SIZE = 42
TEXT_COLOR = "FFEC8B"
# Keeps a translation (and its wrapping) inside the picture. Every event
# carries its own `\an`/`\pos`; the style's alignment 8 is a placeholder.
MARGIN_H = 60
MARGIN_V = 28
STYLE = (
    f"Style: {STYLE_NAME},{FONT_NAME},{FONT_SIZE},&H00{bgr(TEXT_COLOR)},&H000000FF,"
    "&H00000000,&H7D000000,0,0,0,0,100,100,0,0,1,5,2,8,"
    f"{MARGIN_H},{MARGIN_H},{MARGIN_V},1"
)
HEADER = script_header(CANVAS_INFO, [STYLE])
WITH_DIALOGUE_HEADER = script_header(CANVAS_INFO, [DIALOGUE_STYLE, STYLE])
# Room between a translation and its card.
_GAP = 8
# What covering a share of a placed translation costs, against `_AWAY` for
# moving it a picture height from its card: another translation is
# unreadable, an original card only partly hidden, and a translation far
# from its card loses what it belongs to.
_COVER_TRANSLATION = 10.0
_COVER_CARD = 1.0
_AWAY = 3.0
# A spot nearer another card than its own reads as that card's translation.
_AMBIGUOUS = 1.0
# A rendered line's height, outline included (a vertical column's width).
_LINE = FONT_SIZE * 1.2
# A card up longer than `MAX_SHOWN_S + SHOWN_TOLERANCE_S` has its
# translation leave `MAX_SHOWN_S` after it appears: by then it is read
# (owner's call; most cards are up 4-8 s). A shorter card keeps it to the end.
MAX_SHOWN_S = 10.0
SHOWN_TOLERANCE_S = 5.0
# A vertical card's translation is set vertically too, the way libass sets
# vertical text: the font's vertical form (`@` before its name) turned a
# quarter, so CJK stands upright and a Latin run lies along the column.
# libass keeps the font's horizontal glyphs for the brackets and marks below
# beside CJK, so they take their vertical forms (measured 2026-10-28);
# commas, stops, colons and semicolons keep theirs. Each line is a column,
# right to left; a column too long for the room goes on in the next one,
# never inside a Latin run or before closing punctuation.
_VERTICAL_TAGS = f"\\fn@{FONT_NAME}\\an7\\frz270"
_VERTICAL_FORMS = str.maketrans(
    {
        "「": "\N{PRESENTATION FORM FOR VERTICAL LEFT CORNER BRACKET}",
        "」": "\N{PRESENTATION FORM FOR VERTICAL RIGHT CORNER BRACKET}",
        "『": "\N{PRESENTATION FORM FOR VERTICAL LEFT WHITE CORNER BRACKET}",
        "』": "\N{PRESENTATION FORM FOR VERTICAL RIGHT WHITE CORNER BRACKET}",
        "（": "\N{PRESENTATION FORM FOR VERTICAL LEFT PARENTHESIS}",
        "）": "\N{PRESENTATION FORM FOR VERTICAL RIGHT PARENTHESIS}",
        "(": "\N{PRESENTATION FORM FOR VERTICAL LEFT PARENTHESIS}",
        ")": "\N{PRESENTATION FORM FOR VERTICAL RIGHT PARENTHESIS}",
        "《": "\N{PRESENTATION FORM FOR VERTICAL LEFT DOUBLE ANGLE BRACKET}",
        "》": "\N{PRESENTATION FORM FOR VERTICAL RIGHT DOUBLE ANGLE BRACKET}",
        "〈": "\N{PRESENTATION FORM FOR VERTICAL LEFT ANGLE BRACKET}",
        "〉": "\N{PRESENTATION FORM FOR VERTICAL RIGHT ANGLE BRACKET}",
        "【": "\N{PRESENTATION FORM FOR VERTICAL LEFT BLACK LENTICULAR BRACKET}",
        "】": "\N{PRESENTATION FORM FOR VERTICAL RIGHT BLACK LENTICULAR BRACKET}",
        "〔": "\N{PRESENTATION FORM FOR VERTICAL LEFT TORTOISE SHELL BRACKET}",
        "〕": "\N{PRESENTATION FORM FOR VERTICAL RIGHT TORTOISE SHELL BRACKET}",
        "…": "\N{PRESENTATION FORM FOR VERTICAL HORIZONTAL ELLIPSIS}",
        "⋯": "\N{PRESENTATION FORM FOR VERTICAL HORIZONTAL ELLIPSIS}",
        "‥": "\N{PRESENTATION FORM FOR VERTICAL TWO DOT LEADER}",
        "—": "\N{PRESENTATION FORM FOR VERTICAL EM DASH}",
        "―": "\N{PRESENTATION FORM FOR VERTICAL EM DASH}",
        "ー": "\N{CJK UNIFIED IDEOGRAPH-4E28}",
        "～": "\N{PRESENTATION FORM FOR VERTICAL WAVY LOW LINE}",
        "〜": "\N{PRESENTATION FORM FOR VERTICAL WAVY LOW LINE}",
        "~": "\N{PRESENTATION FORM FOR VERTICAL WAVY LOW LINE}",
        "!": "！",
        "?": "？",
    }
)
_TOKEN = re.compile(r"[A-Za-z0-9.,'&%+/-]+(?: [A-Za-z0-9.,'&%+/-]+)*|.")
_CLOSING = frozenset("，。、：；！？」』）》〉】〕…‥".translate(_VERTICAL_FORMS))
# Setting a vertical translation in more, shorter columns to fit beside its
# card costs this much per extra column (against `_AWAY` per picture height
# it moves).
_MORE_COLUMNS = 0.1


@dataclass(frozen=True, slots=True)
class _Rect:
    """A box on the canvas, in pixels."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def overlap(self, other: _Rect) -> float:
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        return max(0.0, width) * max(0.0, height)

    def distance(self, other: _Rect) -> float:
        """The gap between the two boxes (0 when they touch or overlap)."""
        gap_x = max(other.x0 - self.x1, self.x0 - other.x1, 0.0)
        gap_y = max(other.y0 - self.y1, self.y0 - other.y1, 0.0)
        return math.hypot(gap_x, gap_y)


def _span(rects: Sequence[_Rect]) -> _Rect:
    return _Rect(
        min(rect.x0 for rect in rects),
        min(rect.y0 for rect in rects),
        max(rect.x1 for rect in rects),
        max(rect.y1 for rect in rects),
    )


@dataclass(frozen=True, slots=True)
class _Layout:
    """One way to set a translation: its estimated size (libass's measured
    glyph advances) and, for a vertical card, its columns, right to left."""

    width: float
    height: float
    columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Card:
    """One translation to draw: its lines, the boxes on the canvas of the
    card lines it translates (`own`) and of the card's other lines
    (`beside`), and the ways to set it, fewest columns first (one for a
    horizontal card: a line wider than the picture wraps)."""

    item: TelopSubtitle
    lines: list[str]
    own: list[_Rect]
    beside: list[_Rect]
    layouts: list[_Layout]

    def facing(self, x0: float, x1: float) -> _Rect:
        """The box around the translated lines a translation spanning `x0` to
        `x1` sits above or below: those it shares columns with, so a card's
        line set off to the side (a corner label, a tab) does not push it
        away from the rest; all of them when it shares none."""
        shared = [rect for rect in self.own if rect.x0 < x1 and x0 < rect.x1]
        return _span(shared or self.own)

    def distance(self, rect: _Rect) -> float:
        return min(rect.distance(own) for own in self.own)

    @property
    def shown_until(self) -> float:
        """When the translation leaves: with its card, or `MAX_SHOWN_S` in
        when the card stays well past that."""
        if self.item.end - self.item.start > MAX_SHOWN_S + SHOWN_TOLERANCE_S:
            return self.item.start + MAX_SHOWN_S
        return self.item.end

    def card_up_with(self, other: _Card) -> bool:
        """Whether `other`'s card is on screen while this translation is."""
        return other.item.start < self.shown_until and self.item.start < other.item.end

    def shown_with(self, other: _Card) -> bool:
        return (
            self.item.start < other.shown_until and other.item.start < self.shown_until
        )


def _columns(lines: Sequence[str], room: float) -> tuple[str, ...]:
    """Each line a column, or as few evenly filled columns as keep it within
    `room` pixels."""
    columns: list[str] = []
    for line in lines:
        length = text_width(line, FONT_SIZE)
        parts = max(1, math.ceil(length / room))
        # Whole characters per column, so the last one is not left short.
        even = math.ceil(length / parts / FONT_SIZE) * FONT_SIZE
        column = ""
        for token in _TOKEN.findall(line):
            if (
                column
                and token not in _CLOSING
                and text_width(column + token, FONT_SIZE) > even
            ):
                columns.append(column.rstrip())
                column = token.lstrip()
            else:
                column += token
        columns.append(column.rstrip())
    return tuple(columns)


def _vertical_layouts(lines: Sequence[str], frame: PictureBox) -> list[_Layout]:
    """Columns as long as the picture allows, then two or three times
    shorter ones, to fit beside a card with something above or below it."""
    full = frame.height - 2 * MARGIN_V
    lines = [line.translate(_VERTICAL_FORMS) for line in lines]
    longest = max(text_width(line, FONT_SIZE) for line in lines)
    layouts: dict[tuple[str, ...], _Layout] = {}
    for parts in (1, 2, 3):
        room = min(longest / parts, full)
        if parts > 1 and room < 2 * FONT_SIZE:
            break
        columns = _columns(lines, room)
        layouts.setdefault(
            columns,
            _Layout(
                len(columns) * _LINE,
                max(text_width(column, FONT_SIZE) for column in columns),
                columns,
            ),
        )
    return list(layouts.values())


def _card(item: TelopSubtitle, frame: PictureBox) -> _Card | None:
    lines = [line.strip() for line in item.zh.splitlines() if line.strip()]
    if not lines:
        return None

    def rect(box: Box) -> _Rect:
        x0, y0, x1, y1 = box
        return _Rect(
            frame.x + x0 * frame.width,
            frame.y + y0 * frame.height,
            frame.x + x1 * frame.width,
            frame.y + y1 * frame.height,
        )

    own = [rect(box) for box in item.boxes]
    beside = [rect(box) for box in item.beside]
    # The card's way, as the selector was told it, from all its lines.
    if set_vertically([*item.boxes, *item.beside]):
        return _Card(item, lines, own, beside, _vertical_layouts(lines, frame))
    widths = [text_width(line, FONT_SIZE) for line in lines]
    room = frame.width - 2 * MARGIN_H
    rows = sum(max(1, math.ceil(width / room)) for width in widths)
    layout = _Layout(min(max(widths), room), rows * _LINE)
    return _Card(item, lines, own, beside, [layout])


def _place(
    card: _Card,
    frame: PictureBox,
    *,
    others: Sequence[_Card],
    translations: Sequence[_Rect],
) -> tuple[_Rect, _Layout]:
    """Where and how to draw `card`, among the `others` translated cards and
    the `translations` already placed that are up with it: the cheapest spot
    of any of its layouts (see `_spot`), a layout costing `_MORE_COLUMNS`
    per column."""
    their_lines = [rect for other in others for rect in (*other.own, *other.beside)]
    obstacles = [
        *((rect, _COVER_CARD) for rect in (*card.own, *card.beside, *their_lines)),
        *((rect, _COVER_TRANSLATION) for rect in translations),
    ]
    choices = [
        (cost + _MORE_COLUMNS * len(layout.columns), rect, layout)
        for layout in card.layouts
        for cost, rect in [_spot(card, layout, frame, obstacles, their_lines)]
    ]
    _, rect, layout = min(choices, key=lambda choice: choice[0])
    return rect, layout


def _preferred(
    card: _Card,
    layout: _Layout,
    *,
    left: float,
    right: float,
    lowest: float,
    highest: float,
) -> tuple[float, float, set[float], set[float]]:
    """The preferred spot's left and top, and the lefts and tops to try:
    right below the lines it faces and centred on the card, or right above
    them when below does not fit; for a vertical card, right of it and
    centred on it top to bottom, or left of it when right does not fit."""
    span = _span(card.own)
    if layout.columns:
        beside_right = span.x1 + _GAP
        beside_left = span.x0 - _GAP - layout.width
        top = min(max((span.y0 + span.y1 - layout.height) / 2, lowest), highest)
        x0 = beside_right if beside_right <= right else beside_left
        return x0, top, {beside_right, beside_left}, {top}
    centred = min(max((span.x0 + span.x1 - layout.width) / 2, left), right)
    facing = card.facing(centred, centred + layout.width)
    above = facing.y0 - _GAP - layout.height
    below = facing.y1 + _GAP
    return centred, below if below <= highest else above, {centred}, {above, below}


def _spot(
    card: _Card,
    layout: _Layout,
    frame: PictureBox,
    obstacles: Sequence[tuple[_Rect, float]],
    their_lines: Sequence[_Rect],
) -> tuple[float, _Rect]:
    """The cheapest spot for `card` set in `layout`, and its cost: the share
    of it each obstacle (every line of the cards up, translated or not, and
    the translations) covers, weighted (another translation most), its
    distance from the preferred spot, and being nearer another card's line
    than its own. Spots tried: the preferred ones or against a side edge of
    any obstacle, times the preferred ones or against a top or bottom edge
    of any obstacle, kept inside the picture."""
    width, height = layout.width, layout.height
    left = frame.x + MARGIN_H
    right = frame.x + frame.width - MARGIN_H - width
    lowest = frame.y + MARGIN_V
    # A translation taller than the picture starts at its top.
    highest = max(lowest, frame.y + frame.height - MARGIN_V - height)
    preferred_x, preferred_y, lefts, tops = _preferred(
        card, layout, left=left, right=right, lowest=lowest, highest=highest
    )
    for rect, _ in obstacles:
        lefts |= {rect.x0 - _GAP - width, rect.x1 + _GAP}
        tops |= {rect.y0 - _GAP - height, rect.y1 + _GAP}
    # Kept inside the picture before pairing, so the edges past it collapse
    # into one spot instead of repeating it.
    lefts = {min(max(x0, left), right) for x0 in lefts}
    tops = {min(max(top, lowest), highest) for top in tops}

    def cost(rect: _Rect) -> float:
        covered = sum(weight * rect.overlap(o) for o, weight in obstacles)
        away = (abs(rect.y0 - preferred_y) + abs(rect.x0 - preferred_x)) / frame.height
        nearest = card.distance(rect)
        ambiguous = any(rect.distance(line) < nearest for line in their_lines)
        return covered / rect.area + _AWAY * away + _AMBIGUOUS * ambiguous

    best = min(
        (
            _Rect(x0, top, x0 + width, top + height)
            for x0 in sorted(lefts)
            for top in sorted(tops)
        ),
        key=cost,
    )
    return cost(best), best


def _anchor(rect: _Rect, card: _Card) -> str:
    """Override tags drawing a translation in `rect`, anchored on the edge
    facing its card's lines, so the estimated height never opens a gap
    there."""
    x = (rect.x0 + rect.x1) / 2
    own = card.facing(rect.x0, rect.x1)
    if (rect.y0 + rect.y1) < (own.y0 + own.y1):
        return f"{{\\an2\\pos({x:.0f},{rect.y1:.0f})}}"
    return f"{{\\an8\\pos({x:.0f},{rect.y0:.0f})}}"


def _events(subtitles: TelopSubtitles, frame: PictureBox) -> list[str]:
    # Event margins keep the wrapping width inside the picture.
    margins = (
        f"{frame.x + MARGIN_H},{PLAY_RES_X - frame.x - frame.width + MARGIN_H},"
        f"{frame.y + MARGIN_V}"
    )
    cards = [
        card
        for item in sorted(subtitles.items, key=lambda item: (item.start, item.id))
        if (card := _card(item, frame)) is not None
    ]
    placed: list[tuple[_Card, _Rect]] = []
    events: list[str] = []
    for card in cards:
        rect, layout = _place(
            card,
            frame,
            others=[
                other
                for other in cards
                if other is not card and card.card_up_with(other)
            ],
            translations=[rect for other, rect in placed if other.shown_with(card)],
        )
        placed.append((card, rect))
        start = format_ass_centiseconds(ass_centiseconds(card.item.start))
        end = format_ass_centiseconds(ass_centiseconds(card.shown_until))
        prefix = f"Dialogue: 0,{start},{end},{STYLE_NAME},,{margins},,"
        if not layout.columns:
            events.append(
                prefix
                + _anchor(rect, card)
                + "\\N".join(escape_text(line) for line in card.lines)
            )
            continue
        # One event per column, right to left, anchored on its top right
        # corner (the turned line's top left) and centred in its width.
        for n, column in enumerate(layout.columns):
            x = rect.x1 - n * _LINE - (_LINE - FONT_SIZE) / 2
            events.append(
                f"{prefix}{{{_VERTICAL_TAGS}\\pos({x:.0f},{rect.y0:.0f})}}"
                + escape_text(column)
            )
    return events


def render_telop_ass(
    subtitles: TelopSubtitles, frame: PictureBox = FULL_PICTURE
) -> str:
    return HEADER + "".join(f"{event}\n" for event in _events(subtitles, frame))


def render_with_dialogue(
    dialogue: Sequence[SrtBlock], subtitles: TelopSubtitles
) -> str:
    """The finalized dialogue and the telops in one script, for players that
    show one subtitle track: the telops come first, so the dialogue draws on
    top of them as it does when burned in."""
    events = [
        *_events(subtitles, FULL_PICTURE),
        *(dialogue_line(block) for block in dialogue),
    ]
    return WITH_DIALOGUE_HEADER + "".join(f"{event}\n" for event in events)


def write_telop_ass(
    subtitles: TelopSubtitles, path: Path, frame: PictureBox = FULL_PICTURE
) -> None:
    # Atomic and BOM-less UTF-8, like the dialogue ASS.
    atomic_write_text(path, render_telop_ass(subtitles, frame))


def write_with_dialogue(
    dialogue: Sequence[SrtBlock], subtitles: TelopSubtitles, path: Path
) -> None:
    atomic_write_text(path, render_with_dialogue(dialogue, subtitles))
