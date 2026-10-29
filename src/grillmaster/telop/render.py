"""The telop layer: each translated card drawn beside its own card.

Packaging burns it as one more `subtitles` layer, on the source timeline like
the dialogue, so the package trim and tempo apply to it for free, and under
the dialogue, which always stays on top. A pale yellow keeps the cards apart
from the white dialogue.

Every translation sits next to the card it translates, so the eye finds it
where the card is: right below it (one side for every item of a list),
right above only when below does not fit in the picture, and away from the
other translations and cards up with it, and from the dialogue (which draws
over it) where staying by its card allows. Above and below are measured on
the card lines it shares columns with, so a tab or corner label off to one
side does not push it away from the rest. Every card line counts by the
text's own extent, not the OCR box around it (`ocr.text_box`), which
reaches well past big text. It leaves with its card, or after
`MAX_SHOWN_S` when the card stays well past that. Positions follow `frame`, the
picture's box on the canvas (a side layout shrinks it).

The telop stage also writes the full-picture layer together with the
dialogue as one script beside the video, for playing it with external
subtitles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.core.timecode import (
    TimeRange,
    ass_centiseconds,
    format_ass_centiseconds,
)
from grillmaster.subtitles.ass import (
    CANVAS_INFO,
    DIALOGUE_MARGINS,
    DIALOGUE_STYLE,
    FONT_NAME,
    FULL_PICTURE,
    PLAY_RES_X,
    DialogueMargins,
    LineBreaks,
    PictureBox,
    bgr,
    dialogue_rows,
    escape_text,
    script_header,
    text_width,
)
from grillmaster.subtitles.finalize import dialogue_line
from grillmaster.telop.ocr import text_box
from grillmaster.telop.scan import set_vertically

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock
    from grillmaster.telop.scan import Box
    from grillmaster.telop.select import TelopSubtitle, TelopSubtitles

STYLE_NAME = "Telop"
# A vertical card's translation, set the way libass sets vertical text: the
# font's vertical form (`@` before its name) turned a quarter, so CJK stands
# upright and a Latin run lies along the column.
VERTICAL_STYLE_NAME = "TelopVertical"
# Smaller than the dialogue: it sits in the picture, often between cards.
FONT_SIZE = 42
TEXT_COLOR = "FFEC8B"
# Keeps a translation (and its wrapping) inside the picture. Every event
# carries its own `\pos` (and a horizontal one its `\an`); the alignment of
# `STYLE_NAME` is a placeholder.
MARGIN_H = 60
MARGIN_V = 28


def _style(name: str, font: str, angle: int, alignment: int) -> str:
    return (
        f"Style: {name},{font},{FONT_SIZE},&H00{bgr(TEXT_COLOR)},&H000000FF,"
        f"&H00000000,&H7D000000,0,0,0,0,100,100,0,{angle},1,5,2,{alignment},"
        f"{MARGIN_H},{MARGIN_H},{MARGIN_V},1"
    )


STYLES = [
    _style(STYLE_NAME, FONT_NAME, 0, 8),
    _style(VERTICAL_STYLE_NAME, f"@{FONT_NAME}", 270, 7),
]
HEADER = script_header(CANVAS_INFO, STYLES)
WITH_DIALOGUE_HEADER = script_header(CANVAS_INFO, [DIALOGUE_STYLE, *STYLES])
# Room between a translation and its card.
_GAP = 8
# What covering a share of a placed translation or a card line costs (of
# the smaller of the two), against `_AWAY` for moving it a picture height
# from its card: another translation is unreadable, a translated line
# (its own card's or another's) hidden leaves its translation nothing to be
# read against, a line no one translates is only partly hidden, and a
# translation far from its card loses what it belongs to.
_COVER_TRANSLATION = 10.0
_COVER_TRANSLATED = 10.0
_COVER_CARD = 1.0
_AWAY = 3.0
# Away is measured from the preferred spot or from the same spot on the
# card's other side, which costs this instead: measured from the preferred
# spot alone, crossing the card cost its height, so a big caption's
# translation stayed under the dialogue rather than go above it (owner:
# plainly wrong, 2026-10-29). About what crossing a name plate cost before.
# (Measured from the nearest card line instead, a board's translation
# jumped to its far lines.)
_OTHER_SIDE = 0.3
# The dialogue draws over a translation, so a line of it the dialogue covers
# is lost while that cue is up. Hidden for a moment, it is read the rest of
# the time and stays by its card (owner's call: a translation away from its
# card is worse than one under a cue), so the share of its lines and time
# hidden counts squared; at 10 a cue up a tenth of the time still pulled
# translations off their captions (2026-10-29, every selection).
_COVER_DIALOGUE = 5.0
# A line the dialogue only touches is read, but jammed against it (owner's
# call, 2026-10-29: a caption's translation touching the cue's outline); it
# counts this share of a hidden one, so a free spot right by its card on
# another side wins.
_CROWDED = 0.5
# A spot nearer another card than its own reads as that card's translation.
_AMBIGUOUS = 1.0
# A rendered line's height, outline included (a vertical column's width).
_LINE = FONT_SIZE * 1.2
# A card up longer than `MAX_SHOWN_S + SHOWN_TOLERANCE_S` has its
# translation leave `MAX_SHOWN_S` after it appears: by then it is read
# (owner's call; most cards are up 4-8 s). A shorter card keeps it to the end.
MAX_SHOWN_S = 10.0
SHOWN_TOLERANCE_S = 5.0
# libass keeps the font's horizontal glyphs for the brackets and marks below
# beside CJK in `VERTICAL_STYLE_NAME`, so they take their vertical forms
# (measured 2026-10-28); commas, stops, colons and semicolons keep theirs.
# Each line is a column, right to left; a column too long for the room goes
# on in the next one (`_BREAKS`).
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
# A broken line's part may be 20 % longer or shorter than an even one to end
# after a pause rather than inside a phrase (owner's call, 2026-10-29).
_BREAKS = LineBreaks(FONT_SIZE, uneven=0.2)
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

    @property
    def transposed(self) -> _Rect:
        return _Rect(self.y0, self.x0, self.y1, self.x1)

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
class _Cue:
    """A dialogue line's time and the box of each row it is drawn in."""

    span: TimeRange
    rows: tuple[_Rect, ...]


def _cues(dialogue: Sequence[SrtBlock], margins: DialogueMargins) -> list[_Cue]:
    return [
        _Cue(block.time_range, tuple(_Rect(*row) for row in rows))
        for block in dialogue
        if (rows := dialogue_rows(block.text, margins))
    ]


@dataclass(frozen=True, slots=True)
class _Layout:
    """One way to set a translation: its estimated size (libass's measured
    glyph advances), its lines as drawn (rows top to bottom, each centred;
    for a vertical card, columns right to left, from the top) and the
    length of each."""

    width: float
    height: float
    lines: tuple[str, ...]
    lengths: tuple[float, ...]
    vertical: bool


@dataclass(frozen=True, slots=True)
class _Card:
    """One translation to draw: the boxes on the canvas of the card lines it
    translates (`own`) and of the card's other lines (`beside`), and the
    ways to set it, fewest columns first (one for a horizontal card)."""

    item: TelopSubtitle
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

    def share_with(self, cue: _Cue) -> float:
        """The share of the time this translation is up that `cue` is too."""
        shown = TimeRange(self.item.start, self.shown_until)
        return shown.overlap(cue.span) / shown.duration if shown.duration else 0.0


def _layout(lines: tuple[str, ...], *, vertical: bool) -> _Layout:
    lengths = tuple(text_width(line, FONT_SIZE) for line in lines)
    size, longest = len(lines) * _LINE, max(lengths)
    width, height = (size, longest) if vertical else (longest, size)
    return _Layout(width, height, lines, lengths, vertical)


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
        columns = _BREAKS.broken(lines, room, picture=full)
        if columns not in layouts:
            layouts[columns] = _layout(columns, vertical=True)
    return list(layouts.values())


def _card(item: TelopSubtitle, frame: PictureBox) -> _Card | None:
    lines = [line.strip() for line in item.zh.splitlines() if line.strip()]
    if not lines:
        return None

    def rect(box: Box) -> _Rect:
        """The text's own extent on the canvas, without the OCR margin."""
        x0, y0, x1, y1 = text_box(box)
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
        return _Card(item, own, beside, _vertical_layouts(lines, frame))
    room = frame.width - 2 * MARGIN_H
    rows = _BREAKS.broken(lines, room)
    return _Card(item, own, beside, [_layout(rows, vertical=False)])


def _place(
    card: _Card,
    frame: PictureBox,
    *,
    others: Sequence[_Card],
    translations: Sequence[_Rect],
    cues: Sequence[_Cue],
) -> tuple[_Rect, _Layout]:
    """Where and how to draw `card`, among the `others` translated cards,
    the `translations` already placed and the dialogue `cues` that are up
    with it: the cheapest spot of any of its layouts (see `_spot`), a layout
    costing `_MORE_COLUMNS` per line (only a vertical card has more than
    one layout)."""
    their_lines = [rect for other in others for rect in (*other.own, *other.beside)]
    cards = (card, *others)
    obstacles = [
        *((rect, _COVER_TRANSLATED) for each in cards for rect in each.own),
        *((rect, _COVER_CARD) for each in cards for rect in each.beside),
        *((rect, _COVER_TRANSLATION) for rect in translations),
    ]
    dialogue = [
        (cue.rows, share) for cue in cues if (share := card.share_with(cue)) > 0
    ]
    choices: list[tuple[float, _Rect, _Layout]] = []
    for layout in card.layouts:
        cost, rect = _spot(
            card,
            layout,
            frame,
            obstacles=obstacles,
            dialogue=dialogue,
            their_lines=their_lines,
        )
        choices.append((cost + _MORE_COLUMNS * len(layout.lines), rect, layout))
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
) -> tuple[tuple[float, float], tuple[float, float]]:
    """The preferred spot's left and top, and the same on the card's other
    side: right below the lines it faces and centred on the card, or right
    above them when below does not fit; for a vertical card, right of it and
    centred on it top to bottom, or left of it when right does not fit."""
    span = _span(card.own)
    if layout.vertical:
        beside_right = span.x1 + _GAP
        beside_left = span.x0 - _GAP - layout.width
        top = min(max((span.y0 + span.y1 - layout.height) / 2, lowest), highest)
        if beside_right <= right:
            return (beside_right, top), (beside_left, top)
        return (beside_left, top), (beside_right, top)
    centred = min(max((span.x0 + span.x1 - layout.width) / 2, left), right)
    facing = card.facing(centred, centred + layout.width)
    above = facing.y0 - _GAP - layout.height
    below = facing.y1 + _GAP
    if below <= highest:
        return (centred, below), (centred, above)
    return (centred, above), (centred, below)


def _hidden(
    rect: _Rect, layout: _Layout, cue: Sequence[_Rect], *, touched: float
) -> float:
    """The share of a translation set in `layout` at `rect` that the rows of
    a `cue` hide: each of its lines counts for the part of its length they
    cover at least half across (a row's height, a column's width), and
    `touched` of that where they only touch it or come within `_GAP`."""
    if layout.vertical:
        # A column is a row turned a quarter, the first one rightmost.
        rect, cue = rect.transposed, [row.transposed for row in cue]
        lines = [
            _Rect(
                rect.x0,
                rect.y1 - (n + 1) * _LINE,
                rect.x0 + length,
                rect.y1 - n * _LINE,
            )
            for n, length in enumerate(layout.lengths)
        ]
    else:
        centre = (rect.x0 + rect.x1) / 2
        lines = [
            _Rect(
                centre - length / 2,
                rect.y0 + n * _LINE,
                centre + length / 2,
                rect.y0 + (n + 1) * _LINE,
            )
            for n, length in enumerate(layout.lengths)
        ]
    hidden = 0.0
    for line in lines:

        def along(rows: Sequence[_Rect], line: _Rect = line) -> float:
            """The share of `line`'s length the `rows` reach along it."""
            reach = max(min(line.x1, row.x1) - max(line.x0, row.x0) for row in rows)
            return max(0.0, reach) / max(1.0, line.x1 - line.x0)

        over = [row for row in cue if line.overlap(row) > 0]
        across = sum(min(line.y1, row.y1) - max(line.y0, row.y0) for row in over)
        if across >= _LINE / 2:
            hidden += along(over)
        elif near := [row for row in cue if line.distance(row) <= _GAP]:
            hidden += touched * along(near)
    return hidden / len(lines)


def _spot(
    card: _Card,
    layout: _Layout,
    frame: PictureBox,
    *,
    obstacles: Sequence[tuple[_Rect, float]],
    dialogue: Sequence[tuple[Sequence[_Rect], float]],
    their_lines: Sequence[_Rect],
) -> tuple[float, _Rect]:
    """The cheapest spot for `card` set in `layout`, and its cost: the share
    of the smaller of it and each obstacle (every line of the cards up,
    translated or not, and the translations) the two overlap, weighted
    (another translation most), so half a short card line hidden counts as
    half however wide the translation; its distance from the preferred
    spot; and being nearer another card's line than its own. Spots tried:
    the preferred ones or against a side edge of any obstacle, times the
    preferred ones or against a top or bottom edge of any obstacle, kept
    inside the picture. The `dialogue` (each cue's rows and share of the
    time) then adds the share of its lines and time it hides, squared, to
    that spot and to those right by the card (against a cue's edges too),
    and the cheapest of them wins."""
    width, height = layout.width, layout.height
    left = frame.x + MARGIN_H
    right = frame.x + frame.width - MARGIN_H - width
    lowest = frame.y + MARGIN_V
    # A translation taller than the picture starts at its top.
    highest = max(lowest, frame.y + frame.height - MARGIN_V - height)
    preferred, other_side = _preferred(
        card, layout, left=left, right=right, lowest=lowest, highest=highest
    )

    def spots(edges: Sequence[_Rect]) -> list[_Rect]:
        xs, ys = (set(both) for both in zip(preferred, other_side, strict=True))
        for rect in edges:
            xs |= {rect.x0 - _GAP - width, rect.x1 + _GAP}
            ys |= {rect.y0 - _GAP - height, rect.y1 + _GAP}
        # Kept inside the picture before pairing, so the edges past it
        # collapse into one spot instead of repeating it.
        xs = {min(max(x0, left), right) for x0 in xs}
        ys = {min(max(top, lowest), highest) for top in ys}
        return [
            _Rect(x0, top, x0 + width, top + height)
            for x0 in sorted(xs)
            for top in sorted(ys)
        ]

    # Every spot has the same area, so each obstacle's weight per pixel
    # covered is fixed (a hand-edited box may have none: count it a pixel).
    per_pixel = [
        (o, weight / max(1.0, min(width * height, o.area))) for o, weight in obstacles
    ]

    def away(rect: _Rect) -> float:
        """Moving from the preferred spot, or from the same spot on the
        card's other side plus `_OTHER_SIDE`, whichever is less."""

        def moved(x0: float, top: float) -> float:
            return _AWAY * (abs(rect.x0 - x0) + abs(rect.y0 - top)) / frame.height

        return min(moved(*preferred), moved(*other_side) + _OTHER_SIDE)

    def placed(rect: _Rect) -> float:
        covered = sum(weight * rect.overlap(o) for o, weight in per_pixel)
        nearest = card.distance(rect)
        ambiguous = any(rect.distance(line) < nearest for line in their_lines)
        return covered + away(rect) + _AMBIGUOUS * ambiguous

    def hidden(rect: _Rect, touched: float) -> float:
        return sum(
            share * _hidden(rect, layout, cue, touched=touched)
            for cue, share in dialogue
        )

    def cost(rect: _Rect, touched: float) -> float:
        return placed(rect) + _COVER_DIALOGUE * hidden(rect, touched) ** 2

    # The dialogue only moves a translation it hides, and only to another
    # spot right by the card (another side, along an edge), never away from
    # it (owner's call: a translation away from its card is worse than one
    # under a cue). One it only crowds only weighs the card's other side,
    # no further off the card's middle (slid along an edge, it left its
    # card). One it covers under its card takes the other side when that is
    # better, else slides along an edge; one above its card or beside it
    # weighs both at once (below it is nearer the dialogue and the edge).
    # Owner's calls, 2026-10-29.
    edges = [rect for rect, _ in obstacles]
    own = min(spots(edges), key=placed)
    touched = 0.0 if hidden(own, 0.0) else _CROWDED
    if not (lost := hidden(own, touched)):
        return placed(own), own
    cue_edges = [row for rows, _ in dialogue for row in rows]
    by_card = [
        rect
        for rect in spots([*edges, *cue_edges])
        if card.distance(rect) <= _GAP + 1 and hidden(rect, touched) < lost
    ]
    sides = [
        rect for rect in by_card if not _slid(rect, own, card, vertical=layout.vertical)
    ]
    under = not layout.vertical and not _above(own, card)

    def key(rect: _Rect) -> float:
        return cost(rect, touched)

    best = min([own, *(sides if touched or under else by_card)], key=key)
    if best is own and under and not touched:
        best = min([own, *by_card], key=key)
    return cost(best, touched), best


def _slid(rect: _Rect, own: _Rect, card: _Card, *, vertical: bool) -> bool:
    """Whether `rect` is further off the middle of its card than `own`
    along the card (sideways for a row above or below it, up or down for a
    column beside it)."""
    span = _span(card.own)
    if vertical:
        rect, own, span = rect.transposed, own.transposed, span.transposed
    middle = span.x0 + span.x1

    def off(each: _Rect) -> float:
        return abs(each.x0 + each.x1 - middle)

    return off(rect) > off(own) + 1


def _above(rect: _Rect, card: _Card) -> bool:
    """Whether a translation in `rect` is above the card lines it faces."""
    facing = card.facing(rect.x0, rect.x1)
    return rect.y0 + rect.y1 < facing.y0 + facing.y1


def _anchor(rect: _Rect, card: _Card) -> str:
    """Override tags drawing a translation in `rect`, anchored on the edge
    facing its card's lines, so the estimated height never opens a gap
    there."""
    x = (rect.x0 + rect.x1) / 2
    if _above(rect, card):
        return f"{{\\an2\\pos({x:.0f},{rect.y1:.0f})}}"
    return f"{{\\an8\\pos({x:.0f},{rect.y0:.0f})}}"


def _events(
    subtitles: TelopSubtitles, frame: PictureBox, cues: Sequence[_Cue]
) -> list[str]:
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
            cues=cues,
        )
        placed.append((card, rect))
        start = format_ass_centiseconds(ass_centiseconds(card.item.start))
        end = format_ass_centiseconds(ass_centiseconds(card.shown_until))
        style = VERTICAL_STYLE_NAME if layout.vertical else STYLE_NAME
        prefix = f"Dialogue: 0,{start},{end},{style},,{margins},,"
        if not layout.vertical:
            events.append(
                prefix
                + _anchor(rect, card)
                + "\\N".join(escape_text(row) for row in layout.lines)
            )
            continue
        # One event per column, right to left, anchored on its top right
        # corner (the turned line's top left) and centred in its width.
        for n, column in enumerate(layout.lines):
            x = rect.x1 - n * _LINE - (_LINE - FONT_SIZE) / 2
            events.append(
                f"{prefix}{{\\pos({x:.0f},{rect.y0:.0f})}}" + escape_text(column)
            )
    return events


def render_telop_ass(
    subtitles: TelopSubtitles,
    frame: PictureBox = FULL_PICTURE,
    *,
    dialogue: Sequence[SrtBlock] = (),
    margins: DialogueMargins = DIALOGUE_MARGINS,
) -> str:
    """The telop layer inside `frame`, kept off the `dialogue` set within
    `margins` where it can be."""
    events = _events(subtitles, frame, _cues(dialogue, margins))
    return HEADER + "".join(f"{event}\n" for event in events)


def render_with_dialogue(
    dialogue: Sequence[SrtBlock], subtitles: TelopSubtitles
) -> str:
    """The finalized dialogue and the telops in one script, for players that
    show one subtitle track: the telops come first, so the dialogue draws on
    top of them as it does when burned in."""
    events = [
        *_events(subtitles, FULL_PICTURE, _cues(dialogue, DIALOGUE_MARGINS)),
        *(dialogue_line(block) for block in dialogue),
    ]
    return WITH_DIALOGUE_HEADER + "".join(f"{event}\n" for event in events)


def write_telop_ass(
    subtitles: TelopSubtitles,
    path: Path,
    frame: PictureBox,
    *,
    dialogue: Sequence[SrtBlock],
    margins: DialogueMargins,
) -> None:
    # Atomic and BOM-less UTF-8, like the dialogue ASS.
    atomic_write_text(
        path, render_telop_ass(subtitles, frame, dialogue=dialogue, margins=margins)
    )


def write_with_dialogue(
    dialogue: Sequence[SrtBlock], subtitles: TelopSubtitles, path: Path
) -> None:
    atomic_write_text(path, render_with_dialogue(dialogue, subtitles))
