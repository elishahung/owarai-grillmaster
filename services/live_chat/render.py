"""Render translated chat as a scrolling panel in ASS.

The panel is plain ASS so packaging burns it with the same `subtitles`
filter as the main subtitles: no extra input, and the package tempo/trim
apply to it for free because it stays on the source timeline.

ASS cannot animate one event through several positions, so the scroll is
expressed per *state*: between two message arrivals every visible message
gets its own events at a fixed slot, and each new state slides the old
messages up (``\\move``) while the newcomer fades in at the bottom. Event
count therefore grows with messages × visible rows (~200k events for a
6k-message stream); keep each visible message to as few, as short, events
as the look allows.
"""

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import TextIO

from loguru import logger

from project import CHAT_ASS_FILE_NAME, CHAT_TRANSLATED_FILE_NAME
from services.finalize.finalize import (
    ASS_FONT_NAME,
    ASS_MARGIN_H,
    ASS_PLAY_RES_X,
    ASS_PLAY_RES_Y,
)
from services.media import BurnPlan, Box, MediaProcessor, SubtitleLayer

from .schema import TranslatedChatLog, TranslatedChatMessage


class ChatLayout(StrEnum):
    """How a translated chat is placed in the packaged frame."""

    # 16:9 picture letterboxed on the left, chat in its own right column,
    # dialogue in the bottom letterbox bar.
    SIDE = "side"
    # Full-frame picture with a translucent panel over its right side.
    OVERLAY = "overlay"
    # No chat: dialogue alone on the full frame.
    NONE = "none"


DEFAULT_CHAT_LAYOUT = ChatLayout.SIDE

PANEL_PADDING = 12
PANEL_RADIUS = 18

# Side layout: the picture keeps 16:9 in what the chat column leaves; a
# multiple of 32 keeps its height exact and even.
SIDE_VIDEO_WIDTH = 1536
SIDE_VIDEO_HEIGHT = SIDE_VIDEO_WIDTH * 9 // 16
SIDE_COLUMN_WIDTH = ASS_PLAY_RES_X - SIDE_VIDEO_WIDTH
# Dialogue sits in the bottom bar, centred over the picture.
SIDE_DIALOGUE_MARGIN_V = 24

ITEM_PADDING_X = 14
ITEM_GAP = 12
AVATAR_SIZE = 30
AVATAR_TEXT_GAP = 10
INITIAL_FONT_SIZE = 17
NAME_FONT_SIZE = 21
NAME_COLOR = "B8B8B8"
# Line pitch libass actually lays out for these sizes with ASS_FONT_NAME.
NAME_LINE_HEIGHT = 26
BODY_FONT_SIZE = 27
BODY_LINE_HEIGHT = 30
# Glyph advance per font-size unit libass lays out with ASS_FONT_NAME: CJK
# measures ~0.89; Latin is proportional (0.49–0.57), so take its wide end.
FULL_WIDTH_ADVANCE = 0.9
HALF_WIDTH_ADVANCE = 0.57
MAX_BODY_LINES = 4
PAID_COLOR = "E8A317"
PAID_ALPHA = "40"
# The amount stands out by colour and weight only; a larger size would
# stretch the name line.
PAID_AMOUNT_COLOR = "FF3B30"
PAID_RADIUS = 10
# The body's last line box already carries leading below the glyphs, so the
# highlight reaches less far below the block than above it to look even.
PAID_INSET_X = 6
PAID_INSET_TOP = 6
PAID_INSET_BOTTOM = 1

SCROLL_MS = 150
FADE_IN_MS = 150

AVATAR_COLORS = (
    "4C8BF5",
    "E8453C",
    "F9AB00",
    "34A853",
    "AB47BC",
    "00ACC1",
    "FF7043",
    "8D6E63",
)

_SCROLL_CS = math.ceil(SCROLL_MS / 10)


@dataclass(frozen=True)
class _Panel:
    """Panel box on the 1920×1080 canvas and the positions derived from it."""

    left: int
    top: int
    bottom: int
    width: int
    # Rounded background (RRGGBB, ASS alpha); None draws none — messages sit
    # straight on the canvas.
    color: str | None = None
    alpha: str = "00"

    @property
    def inner_bottom(self) -> int:
        return self.bottom - PANEL_PADDING

    @property
    def item_left(self) -> int:
        return self.left + ITEM_PADDING_X

    @property
    def text_left(self) -> int:
        return self.item_left + AVATAR_SIZE + AVATAR_TEXT_GAP

    @property
    def text_width(self) -> int:
        return self.left + self.width - ITEM_PADDING_X - self.text_left

    @cached_property
    def clip(self) -> str:
        # Messages scroll out at the panel's own edge, not at the padded
        # content box: the padding only sets where the newest message
        # rests. Only rows that reach above the edge carry it.
        return f"\\clip({self.left},{self.top},{self.left + self.width},{self.bottom})"


@dataclass(frozen=True)
class _LayoutSpec:
    """Everything one layout decides: the panel, the picture, the dialogue."""

    panel: _Panel
    picture: Box | None = None
    dialogue_style: str | None = None


_LAYOUTS = {
    ChatLayout.SIDE: _LayoutSpec(
        # With no background the panel is the whole column: messages scroll
        # out at the frame's top edge and only the item insets pad them.
        panel=_Panel(
            left=SIDE_VIDEO_WIDTH,
            top=0,
            bottom=ASS_PLAY_RES_Y,
            width=SIDE_COLUMN_WIDTH,
        ),
        picture=Box(
            x=0,
            y=(ASS_PLAY_RES_Y - SIDE_VIDEO_HEIGHT) // 2,
            width=SIDE_VIDEO_WIDTH,
            height=SIDE_VIDEO_HEIGHT,
        ),
        # Same size in the bottom bar, centred over the picture rather than
        # the whole canvas; video.cht.ass itself is never rewritten.
        dialogue_style=(
            f"MarginR={SIDE_COLUMN_WIDTH + ASS_MARGIN_H},"
            f"MarginV={SIDE_DIALOGUE_MARGIN_V}"
        ),
    ),
    # Ends above the bottom-centred dialogue lines.
    ChatLayout.OVERLAY: _LayoutSpec(
        panel=_Panel(
            left=1530, top=60, bottom=870, width=360, color="101010", alpha="60"
        ),
    ),
}


def _num(value: float) -> str:
    """Drawing coordinate at 0.1 px; finer is invisible and only bloats."""
    return f"{round(value, 1):g}"


def _rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float) -> str:
    k = r * 0.4477  # bezier handle offset that approximates a quarter circle
    n = _num
    return (
        f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} "
        f"b {n(x1 - k)} {n(y0)} {n(x1)} {n(y0 + k)} {n(x1)} {n(y0 + r)} "
        f"l {n(x1)} {n(y1 - r)} "
        f"b {n(x1)} {n(y1 - k)} {n(x1 - k)} {n(y1)} {n(x1 - r)} {n(y1)} "
        f"l {n(x0 + r)} {n(y1)} "
        f"b {n(x0 + k)} {n(y1)} {n(x0)} {n(y1 - k)} {n(x0)} {n(y1 - r)} "
        f"l {n(x0)} {n(y0 + r)} "
        f"b {n(x0)} {n(y0 + k)} {n(x0 + k)} {n(y0)} {n(x0 + r)} {n(y0)}"
    )


def _circle(size: int) -> str:
    """A circle from four quarter arcs, without zero-length edges."""
    r = size / 2
    k = r * 0.5523  # handle length for a quarter circle
    n = _num
    return (
        f"m {n(r)} 0 b {n(r + k)} 0 {size} {n(r - k)} {size} {n(r)} "
        f"b {size} {n(r + k)} {n(r + k)} {size} {n(r)} {size} "
        f"b {n(r - k)} {size} 0 {n(r + k)} 0 {n(r)} "
        f"b 0 {n(r - k)} {n(r - k)} 0 {n(r)} 0"
    )


_AVATAR_SHAPE = _circle(AVATAR_SIZE)


class _Item:
    """One message's layout and every per-message piece of its events.

    Built once per message; each state only adds timing and position. The
    ``avatar``/``initial``/``text``/``highlight`` strings close the event's
    override block and carry its content.
    """

    def __init__(self, message: TranslatedChatMessage, panel: _Panel) -> None:
        self.seconds = message.seconds
        self.paid = message.kind == "paid"
        amount = message.amount or ""
        # The amount follows the name after two spaces.
        amount_width = _text_width(f"  {amount}", NAME_FONT_SIZE) if amount else 0
        # Model output may carry line breaks; a raw newline would split the
        # Dialogue line, so every user/model string is laid out as one line.
        name = _truncate(
            _single_line(message.author),
            NAME_FONT_SIZE,
            panel.text_width - amount_width,
        )
        body_lines = _wrap(
            _single_line(message.translation), BODY_FONT_SIZE, panel.text_width
        )
        self.height = NAME_LINE_HEIGHT + BODY_LINE_HEIGHT * len(body_lines)

        digest = hashlib.md5(message.author.encode("utf-8")).hexdigest()
        color = AVATAR_COLORS[int(digest, 16) % len(AVATAR_COLORS)]
        self.avatar = f"\\p1\\c&H{_bgr(color)}&}}{_AVATAR_SHAPE}"
        self.initial = "}" + _escape((message.author.lstrip("@")[:1] or "?").upper())
        text = "}" + _escape(name)
        if amount:
            text += f"  {{\\b1\\c&H{_bgr(PAID_AMOUNT_COLOR)}&}}{_escape(amount)}"
        if body_lines:
            # The Chat style is the body text style.
            text += "\\N{\\rChat}" + "\\N".join(_escape(line) for line in body_lines)
        self.text = text
        self.highlight = (
            f"\\p1\\c&H{_bgr(PAID_COLOR)}&\\1a&H{PAID_ALPHA}&}}"
            + _rounded_rect(
                0,
                0,
                panel.width - 2 * PAID_INSET_X,
                self.height + PAID_INSET_TOP + PAID_INSET_BOTTOM,
                PAID_RADIUS,
            )
            if self.paid
            else ""
        )


def chat_burn_plan(
    project_dir: Path,
    video_file: Path,
    dialogue: Path,
    layout: ChatLayout,
) -> BurnPlan:
    """Burn plan for packaging ``project_dir``: chat panel plus dialogue.

    This is the one place that decides the plan. With a translated chat
    and a layout other than ``NONE`` it renders ``video.chat.ass`` and
    places picture, panel, and dialogue per the layout; otherwise (or if
    rendering fails, with a warning) the dialogue burns alone on the full
    frame. Rendering happens at package time rather than in a stage so
    layout changes only need a re-package.
    """
    chat_in = project_dir / CHAT_TRANSLATED_FILE_NAME
    if layout is ChatLayout.NONE or not chat_in.exists():
        return BurnPlan.dialogue(dialogue)
    spec = _LAYOUTS[layout]
    chat_ass = project_dir / CHAT_ASS_FILE_NAME
    try:
        write_chat_ass(
            TranslatedChatLog.read(chat_in),
            MediaProcessor.get_media_duration(video_file),
            chat_ass,
            layout,
        )
    except Exception as error:
        logger.warning(f"Chat panel skipped ({chat_in}): {error}")
        return BurnPlan.dialogue(dialogue)
    logger.info(f"Rendered {layout} chat panel: {chat_ass}")
    return BurnPlan(
        picture=spec.picture,
        layers=(
            SubtitleLayer(path=chat_ass),
            SubtitleLayer(path=dialogue, force_style=spec.dialogue_style),
        ),
    )


def write_chat_ass(
    log: TranslatedChatLog,
    duration_seconds: float,
    output_path: Path,
    layout: ChatLayout,
) -> None:
    # utf-8-sig like the main ASS: libass and players sniff the BOM.
    with output_path.open("w", encoding="utf-8-sig") as out:
        _write_document(log, duration_seconds, out, _LAYOUTS[layout].panel)


def _write_document(
    log: TranslatedChatLog, duration_seconds: float, out: TextIO, panel: _Panel
) -> None:
    items = [_Item(m, panel) for m in log.messages]
    duration = _centiseconds(duration_seconds)
    out.write(_HEADER)
    out.write(_panel_event(duration, panel))
    # Index of the newest message in the last state actually written.
    shown = -1
    for index, newest in enumerate(items):
        start = _centiseconds(newest.seconds)
        end = (
            _centiseconds(items[index + 1].seconds)
            if index + 1 < len(items)
            else duration
        )
        if end <= start:
            # Same-centisecond arrivals: only the last state is visible; its
            # slide and fade-in cover every message that arrived with it.
            continue
        shift = sum(
            items[position].height + ITEM_GAP
            for position in range(shown + 1, index + 1)
        )
        bottom = panel.inner_bottom + ITEM_GAP
        for position in range(index, -1, -1):
            item = items[position]
            top = bottom - item.height - ITEM_GAP
            # Keep a message until it has fully slid past the top edge.
            if top + shift + item.height < panel.top:
                break
            # A row that ends its slide above the edge lives only for the
            # slide, not the whole state.
            row_end = (
                min(end, start + _SCROLL_CS) if top + item.height <= panel.top else end
            )
            _write_item(
                out,
                item,
                panel,
                top=top,
                shift=shift,
                entering=position > shown,
                timing=f"{_timestamp(start)},{_timestamp(row_end)}",
            )
            bottom = top
        shown = index


def _write_item(
    out: TextIO,
    item: _Item,
    panel: _Panel,
    *,
    top: int,
    shift: int,
    entering: bool,
    timing: str,
) -> None:
    clip = panel.clip if top - PAID_INSET_TOP < panel.top else ""

    def event(
        layer: int, style: str, x: int, y: int, content: str, align: int = 7
    ) -> None:
        if entering:
            place = f"\\an{align}\\pos({x},{y})\\fad({FADE_IN_MS},0)"
        else:
            place = f"\\an{align}\\move({x},{y + shift},{x},{y},0,{SCROLL_MS})"
        out.write(
            f"Dialogue: {layer},{timing},{style},,0,0,0,,{{{place}{clip}{content}\n"
        )

    if item.paid:
        event(
            1,
            "ChatShape",
            panel.left + PAID_INSET_X,
            top - PAID_INSET_TOP,
            item.highlight,
        )
    event(2, "ChatShape", panel.item_left, top, item.avatar)
    half = AVATAR_SIZE // 2
    event(3, "ChatInitial", panel.item_left + half, top + half, item.initial, 5)
    event(3, "ChatName", panel.text_left, top, item.text)


def _panel_event(duration: int, panel: _Panel) -> str:
    """The panel's rounded background for the whole video, if it has one."""
    if panel.color is None:
        return ""
    return (
        f"Dialogue: 0,0:00:00.00,{_timestamp(duration)},ChatShape,,0,0,0,,"
        f"{{\\an7\\pos(0,0)\\p1"
        f"\\c&H{_bgr(panel.color)}&\\1a&H{panel.alpha}&}}"
        + _rounded_rect(
            panel.left,
            panel.top,
            panel.left + panel.width,
            panel.bottom,
            PANEL_RADIUS,
        )
        + "\n"
    )


def _char_width(char: str, font_size: int) -> float:
    """Approximate advance in ``font_size`` units, measured with libass."""
    code = ord(char)
    half = code < 0x2E80 or 0xFF61 <= code <= 0xFFDC
    return font_size * (HALF_WIDTH_ADVANCE if half else FULL_WIDTH_ADVANCE)


def _text_width(text: str, font_size: int) -> float:
    return sum(_char_width(char, font_size) for char in text)


def _wrap(text: str, font_size: int, width: int) -> list[str]:
    if not text:
        return []
    lines = [""]
    used = 0.0
    for char in text:
        advance = _char_width(char, font_size)
        if lines[-1] and used + advance > width:
            lines.append("")
            used = 0.0
        lines[-1] += char
        used += advance
    if len(lines) > MAX_BODY_LINES:
        lines = lines[:MAX_BODY_LINES]
        lines[-1] = _truncate(lines[-1] + "…", font_size, width)
    return lines


def _truncate(text: str, font_size: int, width: float) -> str:
    if _text_width(text, font_size) <= width:
        return text
    ellipsis = "…"
    budget = width - _char_width(ellipsis, font_size)
    kept = ""
    used = 0.0
    for char in text:
        advance = _char_width(char, font_size)
        if used + advance > budget:
            break
        kept += char
        used += advance
    return kept.rstrip() + ellipsis


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _escape(text: str) -> str:
    """Neutralize ASS override syntax inside user text."""
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def _bgr(rgb: str) -> str:
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def _centiseconds(seconds: float) -> int:
    return max(0, round(seconds * 100))


def _timestamp(centiseconds: int) -> str:
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    seconds, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


# Chat: body text (the \rChat target). ChatName: the name line. ChatShape:
# borderless drawings. ChatInitial: the bold letter on the avatar.
_HEADER = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 2
PlayResX: {ASS_PLAY_RES_X}
PlayResY: {ASS_PLAY_RES_Y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Chat,{ASS_FONT_NAME},{BODY_FONT_SIZE},&H00FFFFFF,&H000000FF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,7,0,0,0,1
Style: ChatName,{ASS_FONT_NAME},{NAME_FONT_SIZE},&H00{_bgr(NAME_COLOR)},&H000000FF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,7,0,0,0,1
Style: ChatShape,{ASS_FONT_NAME},{BODY_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: ChatInitial,{ASS_FONT_NAME},{INITIAL_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
