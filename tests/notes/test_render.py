from __future__ import annotations

import re
from typing import TYPE_CHECKING

from grillmaster.notes.annotate import Note, Notes
from grillmaster.notes.render import (
    HEADER,
    MAX_ROWS,
    PITCH,
    STYLE,
    STYLE_NAME,
    TOP,
    YPAD,
    notes_events,
    rows,
    write_notes_ass,
)
from grillmaster.subtitles.ass import PictureBox

if TYPE_CHECKING:
    from pathlib import Path

_POS = re.compile(r"\{\\pos\((\d+),(\d+)\)")
LONG = "田中：爆笑問題的另一名成員田中裕二，是太田的搭檔，負責吐槽，兩人自1988年組成至今已近四十年"


def note(text: str = "三浦和義：說明") -> Note:
    return Note(block=1, start=1.0, end=5.0, term="三浦", text=text, evidence="url")


def test_a_note_sits_at_the_top_centre_on_libass_boxes():
    (event,) = notes_events(Notes(items=[note()]))

    assert event == (
        f"Dialogue: 0,0:00:01.00,0:00:05.00,{STYLE_NAME},,0,0,0,,"
        f"{{\\pos(960,{TOP})\\ybord{YPAD}}}※三浦和義：說明"
    )
    # The box is the style's own (BorderStyle 3), drawn per row.
    assert ",0,0,3,8,0,8," in STYLE


def test_each_row_is_an_event_of_its_own_a_pitch_apart():
    first, second = notes_events(Notes(items=[note(LONG)]))

    assert _POS.findall(first) == [("960", str(TOP))]
    assert _POS.findall(second) == [("960", str(TOP + PITCH))]
    assert len(rows(LONG)) == MAX_ROWS


def test_rows_break_at_a_pause_not_inside_a_word():
    assert rows(LONG) == (
        "※田中：爆笑問題的另一名成員田中裕二，是太田的搭檔，",
        "負責吐槽，兩人自1988年組成至今已近四十年",
    )


def test_every_look_but_the_box_height_lives_in_the_header():
    for event in notes_events(Notes(items=[note(LONG)])):
        overrides = "".join(re.findall(r"{([^}]*)}", event))
        assert re.sub(r"\\pos\(\d+,\d+\)", "", overrides) == f"\\ybord{YPAD}"


def test_the_note_follows_a_shrunk_picture():
    frame = PictureBox(x=0, y=108, width=1536, height=864)

    (event,) = notes_events(Notes(items=[note()]), frame)

    assert _POS.findall(event) == [("768", str(108 + TOP))]


def test_the_layer_is_a_script_of_its_own(tmp_path: Path):
    path = tmp_path / "notes.ass"

    write_notes_ass(Notes(items=[note()]), path, PictureBox(0, 0, 1920, 1080))

    ass = path.read_text(encoding="utf-8")
    assert ass.startswith(HEADER)
    assert ass.count("Dialogue:") == 1
