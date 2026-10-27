"""From per-frame OCR reads to telop cards, by rules alone.

A scan samples the video at `SAMPLE_FPS` and keeps every text box OCR read
(`TelopScan`). `track_lines` follows each box through consecutive frames
into a line with a start, an end and its most trusted reading; a new
reading in the same place (the next memo in the same frame) starts a new
line. `build_cards` then drops OCR noise, joins lines that share their
timing and touch on screen into cards (stacked, or close on one row: a card
beside another stays apart), copies a recurring label that stays up across
several cards into each card it sits on, and labels each card:

- `recurring`: every line's text is on screen for over `RECURRING_S`
  across the episode (corner logos and titles, shirt prints, bottle
  labels); near-identical reads and longer texts a line is a fragment of
  count towards it, never a fragment towards a longer text;
- `speech`: every other line repeats the ASR transcript around it;
- `candidate`: anything else.

The selector sees every card with Japanese text but the recurring ones
(`TelopCards.offered`), each line that repeats the speech marked: a card
can echo the speech and still add a label or a point. `cards.json` keeps
every label, so tuning sees the rules' view.
Boxes are fractions of the frame, so every threshold here is scale-free.
The thresholds were tuned on two episodes (a memo show and a commentary-
card location show); they are not settings.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum, StrEnum
from typing import TYPE_CHECKING

from grillmaster.core.models import StrictModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.core.srt import SrtBlock

SAMPLE_FPS = 2.0
# A track survives this many sampled frames without a matching box.
MAX_GAP_FRAMES = 2
# Lines shorter than this, of one character, or read with less confidence
# are OCR noise (single-frame shirt prints, set decoration).
MIN_LINE_S = 1.0
MIN_CONFIDENCE = 0.7
# Episode-wide seconds after which a text is a fixture, not a card.
RECURRING_S = 60.0
# Share of a line's characters found in the transcript near it.
SPEECH_OVERLAP = 0.6
SPEECH_WINDOW_S = 3.0
# Lines of one card appear and vanish within this of each other.
CARD_TIMING_S = 1.0
# A box continues a track at (box overlap, reading match) of at least one
# of these pairs. The match counts a fading-in fragment as a full match,
# and a box in the same place with a different text starts a new line.
_CONTINUES = ((0.5, 0.75), (0.3, 0.9))
_MIN_OVERLAP = min(overlap for overlap, _ in _CONTINUES)
# Share of a text that must match another for the other to count as it.
_SAME_TEXT = 0.8

# A kana or kanji letter (half-width katakana too; not the middle dot or the
# long-vowel mark alone).
_JAPANESE = re.compile(r"[ぁ-ゖァ-ヺｦ-ﾝ㐀-鿿々]")

type Box = tuple[float, float, float, float]


class TextRead(StrictModel):
    """One OCR box: `box` is (x0, y0, x1, y1) as fractions of the frame."""

    box: Box
    text: str
    score: float


class TelopScan(StrictModel):
    """Every sampled frame's OCR reads (the scan cache); frame `i` is at
    `i / fps` seconds."""

    fps: float
    frames: list[list[TextRead]]


class CardKind(StrEnum):
    CANDIDATE = "candidate"
    RECURRING = "recurring"
    SPEECH = "speech"


class CardLine(StrictModel):
    """`echoes_speech`: the line repeats the ASR transcript around it."""

    text: str
    box: Box
    echoes_speech: bool = False


class TelopCard(StrictModel):
    id: int
    start: float
    end: float
    kind: CardKind
    lines: list[CardLine]

    @property
    def text(self) -> str:
        """The lines top to bottom, joined with ` / `."""
        return " / ".join(line.text for line in self.lines)

    @property
    def vertical(self) -> bool:
        """Whether the card is set vertically (see `set_vertically`)."""
        return set_vertically([line.box for line in self.lines])

    @property
    def box(self) -> Box:
        """The box around every line."""
        return (
            min(line.box[0] for line in self.lines),
            min(line.box[1] for line in self.lines),
            max(line.box[2] for line in self.lines),
            max(line.box[3] for line in self.lines),
        )


class TelopCards(StrictModel):
    """The labelled cards of one episode (`cards.json`)."""

    cards: list[TelopCard]

    @property
    def offered(self) -> list[TelopCard]:
        """The cards the selector and the reference tables see: those with
        Japanese text (the others are timer digits, prints, badges, English
        marks and OCR garbage), but not the recurring ones."""
        return [
            card
            for card in self.cards
            if card.kind is not CardKind.RECURRING and _JAPANESE.search(card.text)
        ]


@dataclass(slots=True)
class TrackedLine:
    """One text box followed through consecutive frames."""

    first: int
    last: int
    box: Box
    reading: str
    # Summed OCR confidence per raw reading.
    reads: dict[str, float] = field(default_factory=dict[str, float])
    hits: int = 0

    @property
    def text(self) -> str:
        """The reading with the highest summed confidence."""
        return max(self.reads, key=self.reads.__getitem__)

    @property
    def confidence(self) -> float:
        return sum(self.reads.values()) / self.hits


def normalize(text: str) -> str:
    """NFKC without spaces and the punctuation OCR drops or doubles."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s。、.,・…!?！？~〜ー\-]+", "", text)


def _iou(a: Box, b: Box) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _matched(a: str, b: str) -> int:
    """Characters `a` and `b` share in order."""
    blocks = SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks)


def _same_track(line: TrackedLine, box: Box, reading: str) -> float:
    """How well a box continues `line` (0 = not at all)."""
    overlap = _iou(line.box, box)
    if overlap < _MIN_OVERLAP:
        return 0.0
    match = _matched(line.reading, reading) / min(len(line.reading), len(reading))
    if any(overlap >= least and match >= most for least, most in _CONTINUES):
        return overlap + match
    return 0.0


def track_lines(scan: TelopScan) -> list[TrackedLine]:
    """Every box followed from frame to frame into lines, by start frame."""
    open_lines: list[TrackedLine] = []
    closed: list[TrackedLine] = []
    for index, reads in enumerate(scan.frames):
        taken: set[int] = set()
        for read in reads:
            reading = normalize(read.text)
            if not reading:
                continue
            best, best_score = None, 0.0
            for line in open_lines:
                if id(line) not in taken:
                    score = _same_track(line, read.box, reading)
                    if score > best_score:
                        best, best_score = line, score
            if best is None:
                best = TrackedLine(first=index, last=index, box=read.box, reading="")
                open_lines.append(best)
            best.last = index
            best.box = read.box
            best.reading = reading
            best.reads[read.text] = best.reads.get(read.text, 0.0) + read.score
            best.hits += 1
            taken.add(id(best))
        closed.extend(line for line in open_lines if index - line.last > MAX_GAP_FRAMES)
        open_lines = [
            line for line in open_lines if index - line.last <= MAX_GAP_FRAMES
        ]
    closed.extend(open_lines)
    return sorted(closed, key=lambda line: (line.first, line.box[1]))


@dataclass(frozen=True, slots=True)
class _Line:
    start: float
    end: float
    text: str
    normalized: str
    box: Box


def _recurring(lines: Sequence[_Line]) -> list[bool]:
    """Whether each line's text is on screen for over `RECURRING_S` in all,
    counting every distinct text that holds most of it."""
    seconds: dict[str, float] = {}
    for line in lines:
        seconds[line.normalized] = (
            seconds.get(line.normalized, 0.0) + line.end - line.start
        )
    texts = list(seconds)
    chars = [Counter(text) for text in texts]
    total = dict(seconds)
    for i, a in enumerate(texts):
        for j in range(i + 1, len(texts)):
            b = texts[j]
            # Shared characters bound the in-order match from above.
            if (chars[i] & chars[j]).total() < _SAME_TEXT * min(len(a), len(b)):
                continue
            matched = _matched(a, b)
            if matched >= _SAME_TEXT * len(a):
                total[a] += seconds[b]
            if matched >= _SAME_TEXT * len(b):
                total[b] += seconds[a]
    return [total[line.normalized] > RECURRING_S for line in lines]


def _repeats_speech(line: _Line, cues: Sequence[tuple[float, float, str]]) -> bool:
    near = "".join(
        cue
        for start, end, cue in cues
        if end >= line.start - SPEECH_WINDOW_S and start <= line.end + SPEECH_WINDOW_S
    )
    return bool(near) and (
        _matched(line.normalized, near) >= SPEECH_OVERLAP * len(line.normalized)
    )


# Boxes are fractions of a 16:9 frame: a height fraction spans 16/9 as many
# pixels as the same width fraction.
_ASPECT = 9 / 16
# Lines of one card sit at most `_STACK_GAP` of a line height apart above
# each other (straight or diagonally, at most a line height sideways), or on
# one row at most `_ROW_GAP` of it apart (a space inside a line; name
# plates or memos side by side sit further apart), and a wider gap above
# is a separate label (map annotations). Lines above each other whose
# characters differ more than `_SIZE_RATIO` times are two cards (a name
# plate over a caption, a footnote under a title, a logo by a caption).
_STACK_GAP = 0.5
_ROW_GAP = 0.25
_SIZE_RATIO = 3.5
# Share of the shorter line two boxes must share vertically to be on one row
# (horizontally to be in one column of a vertical card).
_ROW_OVERLAP = 0.5
# A line `_VERTICAL` times taller than wide, in pixels, is set vertically. A
# vertical and a horizontal line share a card only when one labels the
# other: at most `_LABEL_CHARS` characters, each at most `_LABEL_SIZE` of the
# other line's (このあと or 私の好きは beside a caption); otherwise they are
# two cards (a speech bubble beside a vertical caption, a corner title over
# one).
_VERTICAL = 1.2
_LABEL_CHARS = 6
_LABEL_SIZE = 0.75


def _is_vertical(box: Box) -> bool:
    return box[3] - box[1] > _VERTICAL * (box[2] - box[0]) / _ASPECT


def _char_size(box: Box) -> float:
    """A line's character size, in frame heights: a vertical line's width,
    any other line's height."""
    return (box[2] - box[0]) / _ASPECT if _is_vertical(box) else box[3] - box[1]


def set_vertically(boxes: Sequence[Box]) -> bool:
    """Whether lines read as a vertical card: their biggest characters are
    set vertically (a small horizontal label does not count)."""
    return _is_vertical(max(boxes, key=_char_size))


def _may_join(a: _Line, b: _Line) -> bool:
    """Lines set in one direction, or a label and its text."""
    if _is_vertical(a.box) == _is_vertical(b.box):
        return True
    small, big = sorted((a, b), key=lambda line: _char_size(line.box))
    return len(small.normalized) <= _LABEL_CHARS and _char_size(
        small.box
    ) <= _LABEL_SIZE * _char_size(big.box)


class _Touch(Enum):
    ROW = "row"
    STACKED = "stacked"
    DIAGONAL = "diagonal"


def _in_line(a: Box, b: Box) -> bool:
    """Whether two boxes share a row."""
    smaller = min(a[3] - a[1], b[3] - b[1])
    shared = min(a[3], b[3]) - max(a[1], b[1])
    return shared >= _ROW_OVERLAP * smaller


def _transposed(box: Box) -> Box:
    """A vertical line's box with its columns, right to left, turned into
    rows, top to bottom, so it is measured and read like a horizontal one
    (its character width as its height)."""
    return (box[1] * _ASPECT, -box[2] / _ASPECT, box[3] * _ASPECT, -box[0] / _ASPECT)


def _touch(a: Box, b: Box) -> _Touch | None:
    """How two boxes touch, if they do. Gaps count in the smaller line's
    height, so a big line does not reach far (map labels, a logo near a
    card); only a corner label may sit as far sideways as the bigger line
    is high. Two vertical lines are measured as columns, in their character
    width."""
    if _is_vertical(a) and _is_vertical(b):
        a, b = _transposed(a), _transposed(b)
    height = min(a[3] - a[1], b[3] - b[1])
    gap_x = max(a[0], b[0]) - min(a[2], b[2])
    if _in_line(a, b):
        return _Touch.ROW if gap_x <= _ROW_GAP * height * _ASPECT else None
    if max(a[1], b[1]) - min(a[3], b[3]) > _STACK_GAP * height:
        return None
    small, big = sorted((_char_size(a), _char_size(b)))
    if big > _SIZE_RATIO * small:
        return None
    if gap_x < 0:
        return _Touch.STACKED
    bigger = max(a[3] - a[1], b[3] - b[1])
    return _Touch.DIAGONAL if gap_x <= bigger * _ASPECT else None


_ABOVE_OR_BELOW = (_Touch.STACKED, _Touch.DIAGONAL)


def _group(lines: Sequence[_Line]) -> list[list[_Line]]:
    """Lines (sorted by start) that share their timing and touch on screen.

    A diagonal neighbour joins unless both lines already have a line
    straight above or below: a memo's corner label belongs to the text
    under it, but the label over the right of two memos side by side does
    not join the left one's text. A vertical and a horizontal line join
    only as a label and its text.
    """
    touching: list[tuple[int, int, _Touch]] = []
    for i, a in enumerate(lines):
        for j in range(i + 1, len(lines)):
            b = lines[j]
            if b.start - a.start > CARD_TIMING_S:
                break
            if (
                abs(a.end - b.end) <= CARD_TIMING_S
                and (touch := _touch(a.box, b.box))
                and _may_join(a, b)
            ):
                touching.append((i, j, touch))
    stacked = {k for i, j, touch in touching if touch is _Touch.STACKED for k in (i, j)}
    parent = list(range(len(lines)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j, touch in touching:
        if touch is not _Touch.DIAGONAL or not {i, j} <= stacked:
            parent[root(i)] = root(j)
    groups: dict[int, list[_Line]] = {}
    for i, line in enumerate(lines):
        groups.setdefault(root(i), []).append(line)
    return sorted(groups.values(), key=lambda group: group[0].start)


def _labels_of(
    group: Sequence[_Line], start: float, end: float, labels: Sequence[_Line]
) -> list[_Line]:
    """Recurring `labels` that stay on screen across `group` (on from `start`
    to `end`), start or end with it, sit above or below it and may join its
    biggest line (set the same way, or labelling it): a label kept up while
    the cards it names change (whose memo, which corner), copied into each
    of them."""
    return [
        label
        for label in labels
        if label.start <= start + CARD_TIMING_S
        and label.end >= end - CARD_TIMING_S
        and (
            abs(label.start - start) <= CARD_TIMING_S
            or abs(label.end - end) <= CARD_TIMING_S
        )
        and any(_touch(label.box, line.box) in _ABOVE_OR_BELOW for line in group)
        and _may_join(label, max(group, key=lambda line: _char_size(line.box)))
    ]


def _reading_order(lines: Sequence[_Line]) -> list[_Line]:
    """Rows top to bottom, each left to right; a line shares a row with the
    first of the row when they overlap vertically (a smaller group name
    before a name stays first). A vertical card reads in columns instead,
    right to left, each top to bottom."""
    vertical = set_vertically([line.box for line in lines])
    boxes = {
        id(line): _transposed(line.box) if vertical else line.box for line in lines
    }
    rows: list[list[_Line]] = []
    for line in sorted(lines, key=lambda line: boxes[id(line)][1]):
        if rows and _in_line(boxes[id(rows[-1][0])], boxes[id(line)]):
            rows[-1].append(line)
        else:
            rows.append([line])
    return [
        line
        for row in rows
        for line in sorted(row, key=lambda line: boxes[id(line)][0])
    ]


def build_cards(scan: TelopScan, transcript: Sequence[SrtBlock]) -> TelopCards:
    """The episode's labelled cards (see the module doc)."""
    lines: list[_Line] = []
    for tracked in track_lines(scan):
        start, end = tracked.first / scan.fps, (tracked.last + 1) / scan.fps
        text = tracked.text
        normalized = normalize(text)
        if (
            end - start >= MIN_LINE_S
            and len(normalized) > 1
            and tracked.confidence >= MIN_CONFIDENCE
        ):
            lines.append(_Line(start, end, text, normalized, tracked.box))
    lines.sort(key=lambda line: line.start)
    recurring = dict(zip(map(id, lines), _recurring(lines), strict=True))
    cues = [
        (block.time_range.start, block.time_range.end, normalize(block.text))
        for block in transcript
    ]
    # Fixtures never join fresh lines into one card (a logo under a row of
    # name plates would chain them together); one only labels a card.
    labels = [line for line in lines if recurring[id(line)]]
    fresh = [line for line in lines if not recurring[id(line)]]
    groups = sorted(
        [
            *((group, False) for group in _group(fresh)),
            *((group, True) for group in _group(labels)),
        ],
        key=lambda tagged: tagged[0][0].start,
    )
    cards: list[TelopCard] = []
    for group, is_fixture in groups:
        start = min(line.start for line in group)
        end = max(line.end for line in group)
        echoes = set[int]()
        if is_fixture:
            kind = CardKind.RECURRING
        else:
            echoes = {id(line) for line in group if _repeats_speech(line, cues)}
            kind = CardKind.SPEECH if len(echoes) == len(group) else CardKind.CANDIDATE
            group.extend(_labels_of(group, start, end, labels))
        ordered = _reading_order(group)
        cards.append(
            TelopCard(
                id=len(cards) + 1,
                start=round(start, 2),
                end=round(end, 2),
                kind=kind,
                lines=[
                    CardLine(
                        text=line.text, box=line.box, echoes_speech=id(line) in echoes
                    )
                    for line in ordered
                ],
            )
        )
    return TelopCards(cards=cards)
