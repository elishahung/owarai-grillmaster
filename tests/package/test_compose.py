from __future__ import annotations

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.notes import render as notes_render
from grillmaster.notes.annotate import Note, Notes
from grillmaster.package.compose import render_composed
from grillmaster.subtitles.ass import CANVAS_INFO, DIALOGUE_STYLE, script_header
from grillmaster.subtitles.finalize import dialogue_line
from grillmaster.telop import render as telop_render
from grillmaster.telop.select import TelopSubtitle, TelopSubtitles

DIALOGUE = [SrtBlock(1, format_timecode_line(1.0, 2.0), "第一句\n第二行")]
TELOPS = TelopSubtitles(
    items=[
        TelopSubtitle(
            id=1,
            start=1.0,
            end=4.0,
            ja="ja",
            zh="字卡",
            boxes=[(0.35, 0.01, 0.65, 0.04)],
            beside=[],
        )
    ]
)
NOTES = Notes(
    items=[
        Note(
            block=1, start=1.0, end=4.0, term="第一", text="第一：說明", evidence="url"
        )
    ]
)


def events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_every_layer_in_one_script_the_dialogue_drawn_last():
    ass = render_composed(DIALOGUE, telops=TELOPS, notes=NOTES)

    assert ass.startswith(
        script_header(
            CANVAS_INFO, [DIALOGUE_STYLE, *telop_render.STYLES, notes_render.STYLE]
        )
    )
    telop, note, line = events(ass)
    assert f",{telop_render.STYLE_NAME}," in telop
    assert note == notes_render.notes_events(NOTES)[0]
    assert line == dialogue_line(DIALOGUE[0])


def test_the_telops_are_placed_beside_the_dialogue_they_share_the_script_with():
    telop, *_ = events(render_composed(DIALOGUE, telops=TELOPS, notes=None))

    assert [telop] == telop_render.telop_events(TELOPS, dialogue=DIALOGUE)


def test_a_layer_left_out_takes_its_styles_with_it():
    ass = render_composed(DIALOGUE, telops=None, notes=None)

    assert ass == script_header(CANVAS_INFO, [DIALOGUE_STYLE]) + (
        dialogue_line(DIALOGUE[0]) + "\n"
    )
