from __future__ import annotations

import re
from typing import TYPE_CHECKING

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.subtitles.ass import DIALOGUE_STYLE_NAME, PictureBox
from grillmaster.subtitles.finalize import dialogue_line
from grillmaster.telop.render import (
    FONT_SIZE,
    STYLE_NAME,
    WITH_DIALOGUE_HEADER,
    render_telop_ass,
    render_with_dialogue,
)
from grillmaster.telop.select import TelopSubtitle, TelopSubtitles

if TYPE_CHECKING:
    from grillmaster.telop.scan import Box

_POS = re.compile(r"\\an(\d)\\pos\((\d+),(\d+)\)")
CARD: Box = (0.4, 0.3, 0.6, 0.36)


def subtitle(
    zh: str,
    box: Box = CARD,
    start: float = 1.0,
    end: float = 2.5,
    id: int = 1,  # noqa: A002 - the model's field
) -> TelopSubtitle:
    return TelopSubtitle(id=id, start=start, end=end, ja="ja", zh=zh, box=box)


def events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def anchors(ass: str) -> list[tuple[int, int, int]]:
    """(alignment, x, y) of every event, in order."""
    return [
        (int(match[1]), int(match[2]), int(match[3]))
        for line in events(ass)
        if (match := _POS.search(line))
    ]


def test_a_card_is_translated_right_under_it_on_the_source_timeline():
    ass = render_telop_ass(TelopSubtitles(items=[subtitle("好像在燒垃圾")]))

    assert f"Dialogue: 0,0:00:01.00,0:00:02.50,{STYLE_NAME},,60,60,28,," in ass
    ((alignment, x, y),) = anchors(ass)
    # Top-anchored just under the card (y 0.36 of 1080), centred on it.
    assert alignment == 8
    assert x == 960
    assert 0.36 * 1080 < y <= 0.36 * 1080 + 10


def test_a_long_card_s_translation_leaves_after_ten_seconds_but_stays_an_obstacle():
    long = subtitle("長卡", box=(0.4, 0.3, 0.6, 0.36), start=1.0, end=40.0, id=1)
    # Right above the long card: its spot below would cover that card.
    later = subtitle("後來", box=(0.4, 0.22, 0.6, 0.28), start=20.0, end=22.0, id=2)

    ass = render_telop_ass(TelopSubtitles(items=[long, later]))

    assert events(ass)[0].startswith(f"Dialogue: 0,0:00:01.00,0:00:11.00,{STYLE_NAME}")
    # The long card is still up at 20 s, so the later translation goes above
    # its own card, though the long card's translation has left.
    (alignment, _, y) = anchors(ass)[1]
    assert (alignment, y) == (2, round(0.22 * 1080) - 8)


def test_a_card_up_a_little_past_ten_seconds_keeps_its_translation():
    ass = render_telop_ass(
        TelopSubtitles(items=[subtitle("稍長", start=1.0, end=15.0)])
    )

    assert events(ass)[0].startswith(f"Dialogue: 0,0:00:01.00,0:00:15.00,{STYLE_NAME}")


def test_positions_follow_a_shrunk_picture():
    ass = render_telop_ass(
        TelopSubtitles(items=[subtitle("卡")]), PictureBox(0, 108, 1536, 864)
    )

    assert f",{STYLE_NAME},,60,444,136,," in ass
    ((_, x, y),) = anchors(ass)
    assert x == 768
    assert 108 + 0.36 * 864 < y <= 108 + 0.36 * 864 + 10


def test_line_breaks_and_braces_are_safe_in_an_event():
    ass = render_telop_ass(TelopSubtitles(items=[subtitle("【有吉MEMO】\n{好}\\")]))

    assert ass.rstrip().endswith(r"【有吉MEMO】\N｛好｝＼")  # noqa: RUF001 - full-width stand-ins


def test_no_cards_render_a_valid_empty_layer():
    ass = render_telop_ass(TelopSubtitles())

    assert "[Events]" in ass
    assert "Dialogue:" not in ass


def test_the_dialogue_draws_over_the_telops_in_one_script():
    dialogue = [SrtBlock(1, format_timecode_line(1.0, 2.0), "第一句\n第二行")]

    ass = render_with_dialogue(dialogue, TelopSubtitles(items=[subtitle("卡")]))

    assert ass.startswith(WITH_DIALOGUE_HEADER)
    assert f"Style: {DIALOGUE_STYLE_NAME}," in ass
    assert f"Style: {STYLE_NAME}," in ass
    telop, line = events(ass)
    assert f",{STYLE_NAME},,60,60,28,," in telop
    assert line == dialogue_line(dialogue[0])


def test_translations_of_cards_side_by_side_stay_in_their_columns():
    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle("左邊\n名牌", box=(0.05, 0.3, 0.3, 0.4), id=1),
                subtitle("右邊\n名牌", box=(0.7, 0.3, 0.95, 0.4), id=2),
            ]
        )
    )

    (_, left_x, left_y), (_, right_x, right_y) = anchors(ass)
    assert left_x < 960 < right_x
    assert min(left_y, right_y) >= 0.4 * 1080


def test_translations_of_stacked_cards_do_not_overlap():
    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle("上面那張卡", box=(0.4, 0.3, 0.6, 0.34), id=1),
                subtitle("下面那張卡", box=(0.4, 0.4, 0.6, 0.44), id=2),
            ]
        )
    )

    tops = sorted(y for _, _, y in anchors(ass))
    assert tops[1] - tops[0] >= FONT_SIZE * 1.2


def test_a_translation_goes_above_only_when_below_does_not_fit():
    # Low in the picture but with room below: still below, like the rest of
    # a list. On the bottom edge: above, anchored on its bottom edge.
    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle("清單最後一項", box=(0.4, 0.80, 0.6, 0.84), id=1),
                subtitle("底部名牌", box=(0.05, 0.92, 0.3, 0.98), id=2),
            ]
        )
    )

    (low_alignment, _, low_y), (edge_alignment, _, edge_y) = anchors(ass)
    assert low_alignment == 8
    assert low_y >= 0.84 * 1080
    assert edge_alignment == 2
    assert 0.9 * 1080 <= edge_y <= 0.92 * 1080
