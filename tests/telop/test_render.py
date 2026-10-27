from __future__ import annotations

from grillmaster.telop.render import STYLE_NAME, render_telop_ass
from grillmaster.telop.select import TelopSubtitle, TelopSubtitles


def subtitle(zh: str, start: float = 1.0, end: float = 2.5) -> TelopSubtitle:
    return TelopSubtitle(
        id=1, start=start, end=end, ja="ja", zh=zh, box=(0.0, 0.0, 1.0, 1.0)
    )


def test_cards_are_top_centred_events_on_the_source_timeline():
    ass = render_telop_ass(TelopSubtitles(items=[subtitle("好像在燒垃圾")]))

    style = next(line for line in ass.splitlines() if line.startswith("Style:"))
    # Alignment 8: top centre.
    assert style.split(",")[18] == "8"
    assert ass.endswith(
        f"Dialogue: 0,0:00:01.00,0:00:02.50,{STYLE_NAME},,0,0,0,,好像在燒垃圾\n"
    )


def test_line_breaks_and_braces_are_safe_in_an_event():
    ass = render_telop_ass(TelopSubtitles(items=[subtitle("【有吉MEMO】\n{好}\\")]))

    assert ass.rstrip().endswith(r",,【有吉MEMO】\N｛好｝＼")  # noqa: RUF001 - full-width stand-ins


def test_no_cards_render_a_valid_empty_layer():
    ass = render_telop_ass(TelopSubtitles())

    assert "[Events]" in ass
    assert "Dialogue:" not in ass
