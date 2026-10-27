from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.subtitles.ass import DIALOGUE_STYLE_NAME, PictureBox, text_width
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
    from collections.abc import Sequence

    from grillmaster.telop.scan import Box

_POS = re.compile(r"\\an(\d)\\pos\((\d+),(\d+)\)")
CARD: Box = (0.4, 0.3, 0.6, 0.36)


def subtitle(
    zh: str,
    *,
    boxes: Sequence[Box] = (CARD,),
    beside: Sequence[Box] = (),
    start: float = 1.0,
    end: float = 2.5,
    id: int = 1,  # noqa: A002 - the model's field
) -> TelopSubtitle:
    return TelopSubtitle(
        id=id,
        start=start,
        end=end,
        ja="ja",
        zh=zh,
        boxes=list(boxes),
        beside=list(beside),
    )


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
    long = subtitle("長卡", boxes=[(0.4, 0.3, 0.6, 0.36)], start=1.0, end=40.0, id=1)
    # Right above the long card: its spot below would cover that card.
    later = subtitle("後來", boxes=[(0.4, 0.22, 0.6, 0.28)], start=20.0, end=22.0, id=2)

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
                subtitle("左邊\n名牌", boxes=[(0.05, 0.3, 0.3, 0.4)], id=1),
                subtitle("右邊\n名牌", boxes=[(0.7, 0.3, 0.95, 0.4)], id=2),
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
                subtitle("上面那張卡", boxes=[(0.4, 0.3, 0.6, 0.34)], id=1),
                subtitle("下面那張卡", boxes=[(0.4, 0.4, 0.6, 0.44)], id=2),
            ]
        )
    )

    tops = sorted(y for _, _, y in anchors(ass))
    assert tops[1] - tops[0] >= FONT_SIZE * 1.2


@pytest.mark.parametrize(
    ("zh", "boxes", "facing_top"),
    [
        pytest.param(
            "前三名預測\n理由：忠於搞笑教科書",
            [
                (0.778, 0.685, 0.969, 0.764),  # トップ3予想, a tab on the right
                (0.080, 0.766, 0.943, 0.976),  # お笑いの教科書に忠実
                (0.033, 0.776, 0.085, 0.957),  # 理由
            ],
            0.766,
            id="tab over a full-width caption",
        ),
        pytest.param(
            "【熊元筆記】會先在意有沒有人抽菸嗎？",
            [
                (0.025, 0.768, 0.197, 0.834),  # 熊元MEMO, a corner label
                (0.107, 0.844, 0.893, 0.956),  # まず喫煙者がいるか気になるかな？
            ],
            0.844,
            id="memo under its corner label",
        ),
    ],
)
def test_a_translation_sits_on_the_lines_it_shares_columns_with(
    zh: str, boxes: list[Box], facing_top: float
):
    # On the bottom edge, so above: right on the caption, not on the box
    # the tab or label sticking up beside it would make.
    ass = render_telop_ass(TelopSubtitles(items=[subtitle(zh, boxes=boxes)]))

    ((alignment, _, y),) = anchors(ass)
    assert (alignment, y) == (2, round(facing_top * 1080) - 8)


def test_a_translation_avoids_the_lines_of_its_card_it_leaves_out():
    ranks = [
        (0.080, 0.712, 0.153, 0.796),
        (0.271, 0.710, 0.347, 0.797),
        (0.463, 0.712, 0.539, 0.797),
        (0.656, 0.713, 0.730, 0.798),
        (0.848, 0.709, 0.921, 0.799),
    ]
    title = (0.042, 0.794, 0.961, 0.999)

    ass = render_telop_ass(
        TelopSubtitles(
            items=[subtitle("Westland井口的預測", boxes=[title], beside=ranks)]
        )
    )

    # Right above the title is the row of rank labels: it goes over them.
    ((alignment, _, y),) = anchors(ass)
    assert alignment == 2
    assert y <= min(rank[1] for rank in ranks) * 1080


def test_a_translation_goes_above_only_when_below_does_not_fit():
    # Low in the picture but with room below: still below, like the rest of
    # a list. On the bottom edge: above, anchored on its bottom edge.
    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle("清單最後一項", boxes=[(0.4, 0.80, 0.6, 0.84)], id=1),
                subtitle("底部名牌", boxes=[(0.05, 0.92, 0.3, 0.98)], id=2),
            ]
        )
    )

    (low_alignment, _, low_y), (edge_alignment, _, edge_y) = anchors(ass)
    assert low_alignment == 8
    assert low_y >= 0.84 * 1080
    assert edge_alignment == 2
    assert 0.9 * 1080 <= edge_y <= 0.92 * 1080


_COLUMN = re.compile(r"\\fn@[^\\]+\\an7\\frz270\\pos\((\d+),(\d+)\)\}(.*)$")
# Real boxes: the vertical caption of 相席食堂 2:48 and a profile card set in
# three columns against the left edge (華大さんと千鳥くん 4:15).
CAPTION: Box = (0.4687, 0.2152, 0.5352, 0.7694)
PROFILE: list[Box] = [
    (0.108, 0.322, 0.150, 0.974),
    (0.062, 0.337, 0.107, 0.923),
    (0.027, 0.586, 0.068, 0.955),
]


def columns(ass: str) -> list[tuple[int, int, str]]:
    """(x, y, text) of every vertical column, in order."""
    return [
        (int(match[1]), int(match[2]), match[3])
        for line in events(ass)
        if (match := _COLUMN.search(line))
    ]


def test_a_vertical_card_s_translation_is_set_vertically_right_of_it():
    ass = render_telop_ass(
        TelopSubtitles(items=[subtitle("漫遊惠那峽", boxes=[CAPTION])])
    )

    ((x, y, text),) = columns(ass)
    assert text == "漫遊惠那峽"
    # The column's right edge, just right of the card.
    assert CAPTION[2] * 1920 + FONT_SIZE < x < CAPTION[2] * 1920 + 2 * FONT_SIZE
    # Centred on the card top to bottom.
    middle = y + text_width(text, FONT_SIZE) / 2
    assert abs(middle - (CAPTION[1] + CAPTION[3]) / 2 * 1080) < FONT_SIZE / 2


def test_a_vertical_label_translated_alone_follows_its_horizontal_card():
    # The selector is told the card's way, from all its lines (real boxes:
    # a vertical 理由 tag beside a horizontal reason).
    tag: Box = (0.033, 0.777, 0.086, 0.956)
    others: list[Box] = [(0.029, 0.692, 0.315, 0.756), (0.078, 0.770, 0.958, 0.970)]

    ass = render_telop_ass(
        TelopSubtitles(items=[subtitle("理由", boxes=[tag], beside=others)])
    )

    assert not columns(ass)
    assert len(events(ass)) == 1


def test_a_vertical_card_at_the_right_edge_has_its_translation_left_of_it():
    edge: Box = (0.95, 0.1, 0.99, 0.9)

    ass = render_telop_ass(TelopSubtitles(items=[subtitle("想吃", boxes=[edge])]))

    ((x, _, _),) = columns(ass)
    assert x < edge[0] * 1920


def test_vertical_lines_are_columns_right_to_left_with_vertical_marks():
    ass = render_telop_ass(
        TelopSubtitles(
            items=[subtitle("龜姬說「我不要」……\nPanther（尾形）", boxes=[CAPTION])]
        )
    )

    (first_x, _, first), (second_x, _, second) = columns(ass)
    assert first == "龜姬說﹁我不要﹂︙︙"
    assert second == "Panther︵尾形︶"
    assert second_x < first_x


def test_a_column_ends_without_the_space_after_a_latin_run():
    # Two columns between a card above and one below: the break falls after
    # the space that follows the name.
    above: Box = (0.40, 0.05, 0.60, 0.30)
    below: Box = (0.40, 0.62, 0.60, 0.95)
    caption: Box = (0.45, 0.32, 0.48, 0.60)

    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle("Panther 尾形貴弘", boxes=[caption], id=1),
                subtitle("上面", boxes=[above], id=2),
                subtitle("下面", boxes=[below], id=3),
            ]
        )
    )

    texts = [text for _, _, text in columns(ass)]
    assert texts == ["Panther", "尾形貴弘"]


def test_a_vertical_translation_takes_shorter_columns_to_stay_beside_its_card():
    # Another card comes up right of the profile, low down: in one long
    # column the translation would cover it or rise above the profile.
    beside: Box = (0.164, 0.626, 0.412, 0.684)

    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle(
                    "KEY TO LIT 豬狩蒼彌\n12月即將出道，狀態絕佳",
                    boxes=PROFILE,
                    start=255.0,
                    end=281.0,
                    id=1,
                ),
                subtitle(
                    "2026年8月11日播出", boxes=[beside], start=257.5, end=268.0, id=2
                ),
            ]
        )
    )

    placed = columns(ass)
    assert [text for _, _, text in placed] == [
        "KEY TO LIT",
        "豬狩蒼彌",
        "12月即將出道，",
        "狀態絕佳",
    ]
    tops = {y for _, y, _ in placed}
    assert len(tops) == 1
    assert min(box[1] for box in PROFILE) * 1080 <= tops.pop()
