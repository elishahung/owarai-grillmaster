from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.subtitles.ass import (
    DIALOGUE_MARGINS,
    DIALOGUE_STYLE_NAME,
    DialogueMargins,
    PictureBox,
    text_width,
)
from grillmaster.subtitles.finalize import dialogue_line
from grillmaster.telop.ocr import text_box
from grillmaster.telop.render import (
    FONT_SIZE,
    STYLE_NAME,
    VERTICAL_STYLE_NAME,
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


def test_a_translation_wider_than_the_picture_is_broken_after_a_comma():
    # A memo card filling the bottom of the picture, its translation about
    # 2200 px in one line: libass would not wrap it (no space), so it ran
    # off both edges. Even rows would break inside 捧場; the comma two
    # characters on is near enough.
    memo: list[Box] = [
        (0.0266, 0.7148, 0.1979, 0.7769),
        (0.0484, 0.7898, 0.9438, 0.8787),
        (0.0536, 0.8759, 0.9510, 0.9602),
    ]
    zh = (
        "【熊元筆記】蓮見君雖然比我小，卻會主動帶著我，也會捧場笑，"
        "連唯一一張拍貼都理所當然地讓給我，說不定是極品。"
    )

    ass = render_telop_ass(TelopSubtitles(items=[subtitle(zh, boxes=memo)]))

    ((event,),) = [events(ass)]
    rows = event.split("}", 1)[1].split(r"\N")
    assert rows == [
        "【熊元筆記】蓮見君雖然比我小，卻會主動帶著我，也會捧場笑，",
        "連唯一一張拍貼都理所當然地讓給我，說不定是極品。",
    ]
    widths = [text_width(row, FONT_SIZE) for row in rows]
    # Right above the memo's text, beside its label, inside the picture.
    ((alignment, x, y),) = anchors(ass)
    assert (alignment, y) == (2, round(text_box(memo[1])[1] * 1080 - 8))
    assert x - max(widths) / 2 >= 60
    assert x + max(widths) / 2 <= 1920 - 60


def test_a_latin_run_wider_than_the_picture_is_broken_at_a_space():
    # About 2750 px: libass would wrap it at a space too, so the layout
    # must count the two rows it draws.
    run = " ".join(["champions"] * 12)

    ass = render_telop_ass(TelopSubtitles(items=[subtitle(run)]))

    ((event,),) = [events(ass)]
    rows = event.split("}", 1)[1].split(r"\N")
    assert " ".join(rows) == run
    assert [row.count("champions") for row in rows] == [6, 6]


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


# A group name over a name plate, low in the picture, and the dialogue up
# while it is (real boxes and times).
PLATE: list[Box] = [(0.401, 0.7213, 0.5995, 0.7639), (0.4198, 0.7583, 0.5823, 0.8583)]
PLATE_ZH = "Korokoro Chikichiki Peppers\nNadal"
PLATE_DIALOGUE = [
    SrtBlock(1, format_timecode_line(221.46, 222.44), "你給我回去啦"),
    SrtBlock(2, format_timecode_line(222.52, 223.37), "幹嘛要我回去"),
    SrtBlock(3, format_timecode_line(223.70, 224.20), "我剛剛都有在配合吧"),
]


def plate_anchor(
    dialogue: Sequence[SrtBlock], margins: DialogueMargins = DIALOGUE_MARGINS
) -> tuple[int, int, int]:
    plate = subtitle(PLATE_ZH, boxes=PLATE, start=221.65, end=223.85)
    ass = render_telop_ass(
        TelopSubtitles(items=[plate]), dialogue=dialogue, margins=margins
    )
    ((alignment, x, y),) = anchors(ass)
    return alignment, x, y


def test_a_translation_the_dialogue_would_cover_goes_above_its_card():
    # Below the plate its second line sat under the dialogue drawn on top.
    assert plate_anchor(PLATE_DIALOGUE) == (2, 960, round(0.7213 * 1080) - 8)


def test_a_translation_stays_below_while_the_dialogue_is_mostly_not_up():
    # Only the last cue, up for the last tenth of the card.
    alignment, _, _ = plate_anchor(PLATE_DIALOGUE[2:])

    assert alignment == 8
    assert plate_anchor(PLATE_DIALOGUE[2:]) == plate_anchor([])


def test_a_translation_the_dialogue_only_touches_never_slides_off_its_card():
    # Above a low caption, two rows of dialogue reach a few pixels into it:
    # below does not fit, and slid along the caption's top edge it would no
    # longer line up with the caption, so it stays.
    caption = subtitle("請務必加油", boxes=[(0.2, 0.86, 0.8, 0.95)], start=1.0, end=4.0)
    dialogue = [SrtBlock(1, format_timecode_line(1.0, 4.0), "-第一位\n-第二位講話")]

    with_dialogue = render_telop_ass(TelopSubtitles(items=[caption]), dialogue=dialogue)

    assert anchors(with_dialogue) == anchors(
        render_telop_ass(TelopSubtitles(items=[caption]))
    )


def test_a_big_caption_s_translation_crosses_it_as_readily_as_a_plate_s():
    # A caption 133 px tall, its translation under it covered by a cue for
    # 30 % of its time (大喜利 2026-10-26 24:29): above it is as near to
    # the caption as below; measured from the preferred spot, crossing the
    # caption cost its height and the translation stayed covered.
    caption = subtitle(
        "元斌、啤酒瓶",
        boxes=[(0.3047, 0.7676, 0.8396, 0.9213)],
        start=1469.75,
        end=1473.75,
    )
    dialogue = [
        SrtBlock(
            1, format_timecode_line(1470.306, 1471.466), "這是「啤酒瓶」那時候的節奏吧"
        ),
        SrtBlock(2, format_timecode_line(1471.466, 1475.186), "-木村\n-是"),
    ]

    ass = render_telop_ass(TelopSubtitles(items=[caption]), dialogue=dialogue)

    ((alignment, _, y),) = anchors(ass)
    assert (alignment, y) == (2, round(text_box(caption.boxes[0])[1] * 1080 - 8))


def test_a_covered_translation_under_its_card_takes_the_other_side_first():
    # A big caption low in the picture, its translation under it covered by
    # a cue most of its time (渋谷ハロウィン 2026-10-28 0:28): sliding along
    # the caption's bottom edge beside the cue cost less, but above the
    # caption is free and better than staying, so it goes there.
    caption = subtitle(
        "好可愛", boxes=[(0.3068, 0.6333, 0.7911, 0.9583)], start=28.25, end=29.25
    )
    dialogue = [
        SrtBlock(1, format_timecode_line(28.06, 28.54), "那個"),
        SrtBlock(2, format_timecode_line(28.62, 29.47), "嚇我一跳"),
    ]

    ass = render_telop_ass(TelopSubtitles(items=[caption]), dialogue=dialogue)

    ((alignment, _, y),) = anchors(ass)
    assert (alignment, y) == (2, round(text_box(caption.boxes[0])[1] * 1080 - 8))


def test_a_translation_the_dialogue_only_touches_takes_its_card_s_other_side():
    # A place-name caption low on a map, its translation below it touching
    # the top of a two-row cue's outline (有吉 2026-01-18 0:17): above the
    # caption is free.
    caption = subtitle(
        "朝日電視台", boxes=[(0.4609, 0.7241, 0.6026, 0.8)], start=17.15, end=22.85
    )
    dialogue = [
        SrtBlock(
            1,
            format_timecode_line(18.1, 22.44),
            "一邊將在意的事記在筆記上\n一邊開車前往烤肉場地的兜風之旅",
        )
    ]

    ass = render_telop_ass(TelopSubtitles(items=[caption]), dialogue=dialogue)

    ((alone_alignment, alone_x, _),) = anchors(
        render_telop_ass(TelopSubtitles(items=[caption]))
    )
    assert alone_alignment == 8
    ((alignment, x, y),) = anchors(ass)
    assert (alignment, x) == (2, alone_x)
    assert y == round(text_box(caption.boxes[0])[1] * 1080 - 8)


def cues(*spans: tuple[float, float, str]) -> list[SrtBlock]:
    return [
        SrtBlock(n, format_timecode_line(start, end), text)
        for n, (start, end, text) in enumerate(spans, 1)
    ]


def test_the_dialogue_never_pulls_a_translation_off_its_caption():
    # A low caption up 15 s; the last cue has two rows reaching its
    # translation's spot above it (real boxes and times).
    caption = subtitle(
        "太田＆Nadal：節奏滿分，內容零分",  # noqa: RUF001 - the selection's text
        boxes=[(0.28125, 0.912, 0.728125, 0.975)],
        start=783.15,
        end=797.85,
    )
    dialogue = cues(
        (783.908, 785.208, "冷場的狀態不是嗎！"),
        (785.348, 787.208, "不要在我吐槽的時候特寫我啦！"),
        (787.928, 790.168, "拜託別在我吐槽的時候特寫我啦！"),
        (790.668, 792.328, "節奏倒是一百分呢"),
        (794.008, 798.508, "因為以前跟土田一起合作過一個叫\n《檢索醬》的節目啦"),
    )
    subtitles = TelopSubtitles(items=[caption])

    with_dialogue = render_telop_ass(subtitles, dialogue=dialogue)

    assert anchors(with_dialogue) == anchors(render_telop_ass(subtitles))


def test_a_translation_under_the_dialogue_stays_by_its_card():
    # A small plate under the dialogue, a big caption above it; away from
    # the plate, the translation read as the caption's (real boxes).
    caption = subtitle(
        "憑依型角色製造機\nSchool Zone",
        boxes=[(0.2266, 0.5519, 0.775, 0.6806), (0.1625, 0.662, 0.7495, 0.9398)],
        start=440.25,
        end=443.25,
        id=279,
    )
    plate = subtitle(
        "參賽編號1",
        boxes=[(0.3995, 0.9037, 0.6, 0.9713)],
        start=440.25,
        end=443.25,
        id=280,
    )
    dialogue = cues((440.708, 443.208, "第二輪首先是 School Zone"))
    subtitles = TelopSubtitles(items=[caption, plate])

    with_dialogue = render_telop_ass(subtitles, dialogue=dialogue)

    assert anchors(with_dialogue) == anchors(render_telop_ass(subtitles))


def test_the_dialogue_beside_a_translation_does_not_move_it():
    # A plate low on the left; a short cue up the whole time, centred, clear
    # of the translation's spot but offering its edges as spots.
    plate = subtitle(
        "渡邊（48歲）", boxes=[(0.05, 0.84, 0.25, 0.9)], start=1.0, end=4.0
    )
    dialogue = cues((1.0, 4.0, "好"))
    subtitles = TelopSubtitles(items=[plate])

    with_dialogue = render_telop_ass(subtitles, dialogue=dialogue)

    assert anchors(with_dialogue) == anchors(render_telop_ass(subtitles))


def test_the_dialogue_is_avoided_where_its_layout_sets_it():
    # Set off to the left, the dialogue is clear of the spot below the plate.
    alignment, _, _ = plate_anchor(PLATE_DIALOGUE, DialogueMargins(right=1100))

    assert alignment == 8


def test_the_translation_in_one_script_with_the_dialogue_avoids_it():
    plate = subtitle(PLATE_ZH, boxes=PLATE, start=221.65, end=223.85)

    ass = render_with_dialogue(PLATE_DIALOGUE, TelopSubtitles(items=[plate]))

    assert anchors(ass) == [plate_anchor(PLATE_DIALOGUE)]


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
    ("zh", "boxes", "facing"),
    [
        pytest.param(
            "前三名預測\n理由：忠於搞笑教科書",
            [
                (0.778, 0.685, 0.969, 0.764),  # トップ3予想, a tab on the right
                (0.080, 0.766, 0.943, 0.976),  # お笑いの教科書に忠実
                (0.033, 0.776, 0.085, 0.957),  # 理由
            ],
            1,
            id="tab over a full-width caption",
        ),
        pytest.param(
            "【熊元筆記】會先在意有沒有人抽菸嗎？",
            [
                (0.025, 0.768, 0.197, 0.834),  # 熊元MEMO, a corner label
                (0.107, 0.844, 0.893, 0.956),  # まず喫煙者がいるか気になるかな？
            ],
            1,
            id="memo under its corner label",
        ),
    ],
)
def test_a_translation_sits_on_the_lines_it_shares_columns_with(
    zh: str, boxes: list[Box], facing: int
):
    # On the bottom edge, so above: right on the caption, not on the box
    # the tab or label sticking up beside it would make.
    ass = render_telop_ass(TelopSubtitles(items=[subtitle(zh, boxes=boxes)]))

    ((alignment, _, y),) = anchors(ass)
    assert (alignment, y) == (2, round(text_box(boxes[facing])[1] * 1080) - 8)


def test_a_translation_sits_on_a_big_caption_s_text_not_its_ocr_box():
    # 深夜のダイアン 1:06: the caption's box starts at y 720, its letters at
    # about 800 (measured); a speech bubble stands right of it.
    caption: Box = (0.0, 0.6667, 0.9995, 0.9991)
    bubble: Box = (0.6667, 0.3176, 0.7792, 0.7324)

    ass = render_telop_ass(
        TelopSubtitles(
            items=[subtitle("不是妝太厚，是太燙？", boxes=[caption], beside=[bubble])]
        )
    )

    ((alignment, _, y),) = anchors(ass)
    assert alignment == 2
    assert 760 <= y <= 800 - 8


def test_the_ocr_margin_grows_with_the_text():
    small: Box = (0.4, 0.5, 0.6, 0.56)  # 65 px letters: a tight box
    big: Box = (0.1, 0.6, 0.9, 0.9)  # 324 px letters: about 50 px around

    assert text_box(small) == small
    x0, y0, x1, y1 = text_box(big)
    top = (y0 - big[1]) * 1080
    assert 45 < top < 55
    # The same on every side.
    assert (big[3] - y1) * 1080 == pytest.approx(top)
    assert (x0 - big[0]) * 1920 == pytest.approx(top)
    assert (big[2] - x1) * 1920 == pytest.approx(top)


def test_an_edge_the_frame_cut_keeps_its_place():
    caption: Box = (0.0, 0.6667, 0.9995, 0.9991)

    x0, y0, x1, y1 = text_box(caption)

    assert (x0, x1, y1) == (caption[0], caption[2], caption[3])
    assert y0 > caption[1]


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


def test_a_translation_does_not_cover_the_lines_it_translates():
    # 背徳フライデー 0:29: right under the plate is a translated caption's
    # line; squeezing up onto the plate's own group name hid it, so the
    # translation goes above the plate instead.
    plate = [
        (0.086, 0.482, 0.304, 0.570),  # 体重約130kg
        (0.091, 0.549, 0.303, 0.666),  # ぐんぴい
        (0.111, 0.649, 0.285, 0.713),  # 春とヒコーキ
    ]
    caption: Box = (0.084, 0.746, 0.889, 0.999)

    ass = render_telop_ass(
        TelopSubtitles(
            items=[
                subtitle("體重約130公斤\nGunpii\n春與飛機", boxes=plate, id=1),
                subtitle("背德秘密結社HSS", boxes=[caption], id=2),
            ]
        )
    )

    (alignment, _, y), _ = anchors(ass)
    assert alignment == 2
    assert y <= round(text_box(plate[0])[1] * 1080) - 8


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


_COLUMN = re.compile(rf",{VERTICAL_STYLE_NAME},.*\{{\\pos\((\d+),(\d+)\)\}}(.*)$")
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
    # The style sets it vertically: the font's vertical form, turned a
    # quarter, anchored on its top left.
    (style,) = (
        line.split(",")
        for line in ass.splitlines()
        if line.startswith(f"Style: {VERTICAL_STYLE_NAME},")
    )
    assert style[1].startswith("@")
    assert (style[14], style[18]) == ("270", "7")
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
    above: Box = (0.40, 0.25, 0.60, 0.30)
    below: Box = (0.40, 0.62, 0.60, 0.67)
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
