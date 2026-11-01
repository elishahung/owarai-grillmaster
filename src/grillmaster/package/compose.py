"""`video.cht.ass`: the finalized dialogue, the translated telops and the
notes as one script, for players that show one subtitle track.

The layers are drawn as packaging burns them on the full picture: the telops
and the notes first, and the dialogue last, so it draws on top. Every style
sits once in the header.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.notes import render as notes_render
from grillmaster.subtitles.ass import (
    CANVAS_INFO,
    DIALOGUE_STYLE,
    script,
    script_header,
)
from grillmaster.subtitles.finalize import dialogue_line
from grillmaster.telop import render as telop_render

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock
    from grillmaster.notes.annotate import Notes
    from grillmaster.telop.select import TelopSubtitles


def render_composed(
    dialogue: Sequence[SrtBlock],
    *,
    telops: TelopSubtitles | None,
    notes: Notes | None,
) -> str:
    """The script; `None` leaves a layer and its styles out."""
    styles = [DIALOGUE_STYLE]
    events: list[str] = []
    if telops is not None:
        styles += telop_render.STYLES
        events += telop_render.telop_events(telops, dialogue=dialogue)
    if notes is not None:
        styles.append(notes_render.STYLE)
        events += notes_render.notes_events(notes)
    events += [dialogue_line(block) for block in dialogue]
    return script(script_header(CANVAS_INFO, styles), events)


def write_composed(
    path: Path,
    dialogue: Sequence[SrtBlock],
    *,
    telops: TelopSubtitles | None,
    notes: Notes | None,
) -> None:
    """`render_composed`, atomic and BOM-less UTF-8."""
    atomic_write_text(path, render_composed(dialogue, telops=telops, notes=notes))
