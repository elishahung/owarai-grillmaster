from __future__ import annotations

import pytest

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.telop.scan import (
    CardKind,
    TelopScan,
    TextRead,
    build_cards,
    track_lines,
)

# A memo card: a label above its text, bottom left (fractions of the frame).
LABEL = (0.02, 0.76, 0.20, 0.84)
MEMO = (0.25, 0.84, 0.75, 0.96)
CORNER = (0.01, 0.01, 0.17, 0.08)


def read(box: tuple[float, float, float, float], text: str, score: float = 0.99):
    return TextRead(box=box, text=text, score=score)


def scan_of(*frames: list[TextRead]) -> TelopScan:
    return TelopScan(fps=2.0, frames=list(frames))


def cue(index: int, start: float, end: float, text: str) -> SrtBlock:
    return SrtBlock(index, format_timecode_line(start, end), text)


def test_a_track_keeps_its_most_trusted_reading_across_a_fade_in():
    scan = scan_of(
        [read(MEMO, "るみたいだ", 0.7)],
        [read(MEMO, "ゴミ焼いてるみたいだ")],
        [read(MEMO, "ゴミ焼いてるみたいだ")],
        [],
    )

    (line,) = track_lines(scan)

    assert line.text == "ゴミ焼いてるみたいだ"
    assert (line.first, line.last) == (0, 2)


def test_a_track_bridges_a_short_gap_but_not_a_long_one():
    present = [read(MEMO, "ゴミ焼いてる")]
    bridged = scan_of(present, [], [], present)
    split = scan_of(present, [], [], [], present)

    assert len(track_lines(bridged)) == 1
    assert len(track_lines(split)) == 2


def test_the_next_card_in_the_same_place_starts_a_new_line():
    first = [read(MEMO, "唸り声かと思ったら痰絡んだだけか")]
    second = [read(MEMO, "2人とも気まずそうで申し訳ない")]

    lines = track_lines(scan_of(first, first, second, second))

    assert [(line.text, line.first, line.last) for line in lines] == [
        ("唸り声かと思ったら痰絡んだだけか", 0, 1),
        ("2人とも気まずそうで申し訳ない", 2, 3),
    ]


def test_a_memo_label_and_its_text_form_one_candidate_card():
    frames = [[read(LABEL, "有吉MEMO"), read(MEMO, "ゴミ焼いてるみたいだ")]] * 4

    (card,) = build_cards(scan_of(*frames), []).cards

    assert card.kind is CardKind.CANDIDATE
    assert card.text == "有吉MEMO / ゴミ焼いてるみたいだ"
    assert (card.start, card.end) == (0.0, 2.0)


def test_noise_is_dropped_before_any_card():
    frames = [
        [read(MEMO, "ALPHARD")],  # one frame only
        [read(LABEL, "李")],
        [read(LABEL, "李")],
        [read(LABEL, "李")],  # a single character
    ]
    assert build_cards(scan_of(*frames), []).cards == []


def test_a_card_without_japanese_is_kept_but_not_offered():
    frames = [
        [
            read(CORNER, "REPLAY"),
            read((0.80, 0.40, 0.95, 0.48), "0:00:10"),
            read((0.80, 0.60, 0.95, 0.68), "★・★"),
            read(MEMO, "ゴミ焼いてる"),
            read((0.02, 0.40, 0.20, 0.48), "ｻﾝｷｭｰ"),
        ]
    ] * 4

    cards = build_cards(scan_of(*frames), [])

    assert sorted(card.text for card in cards.cards) == [
        "0:00:10",
        "REPLAY",
        "★・★",
        "ゴミ焼いてる",
        "ｻﾝｷｭｰ",
    ]
    assert sorted(card.text for card in cards.offered) == ["ゴミ焼いてる", "ｻﾝｷｭｰ"]


def test_a_card_repeating_the_speech_is_speech():
    frames = [[read(MEMO, "次回からは恋愛続けます!!")]] * 4
    transcript = [cue(1, 0.5, 2.0, "次回からはまたちょっと恋愛続けますか？")]

    (card,) = build_cards(scan_of(*frames), transcript).cards

    assert card.kind is CardKind.SPEECH


def test_recurring_text_does_not_swallow_a_label():
    # The corner logo stays for 70 s; a memo card with a label shows twice.
    frames: list[list[TextRead]] = []
    for second in range(140):
        reads = [read(CORNER, "有吉クイズ")]
        if second in range(10, 14) or second in range(30, 34):
            reads += [read(LABEL, "有吉MEMO"), read(MEMO, f"メモ{second // 10}")]
        frames.append(reads)

    cards = build_cards(scan_of(*frames), []).cards

    assert [card.text for card in cards if card.kind is CardKind.RECURRING] == [
        "有吉クイズ"
    ]
    memos = [card.text for card in cards if card.kind is CardKind.CANDIDATE]
    assert memos == ["有吉MEMO / メモ1", "有吉MEMO / メモ3"]


def test_a_short_recurring_phrase_does_not_make_a_longer_card_recurring():
    phrase = (0.3, 0.4, 0.5, 0.45)
    frames = [[read(phrase, "夏の思い出")] for _ in range(130)]
    frames += [[read(MEMO, "井口さん「夏の思い出」って言葉好きやなぁ")]] * 4

    cards = build_cards(scan_of(*frames), []).cards

    assert [(card.kind, card.text) for card in cards] == [
        (CardKind.RECURRING, "夏の思い出"),
        (CardKind.CANDIDATE, "井口さん「夏の思い出」って言葉好きやなぁ"),
    ]


def test_a_new_name_in_a_label_s_place_starts_a_new_line():
    first = [read(LABEL, "熊元MEMO")]
    second = [read(LABEL, "小橋MEMO")]

    lines = track_lines(scan_of(first, first, second, second))

    assert [line.text for line in lines] == ["熊元MEMO", "小橋MEMO"]


def test_memos_side_by_side_stay_apart_each_with_its_label():
    frames = [
        [
            read((0.02, 0.76, 0.20, 0.83), "左MEMO"),
            read((0.03, 0.84, 0.48, 0.96), "左のメモです"),
            read((0.51, 0.76, 0.76, 0.83), "右MEMO"),
            read((0.52, 0.84, 0.97, 0.96), "右のメモです"),
        ]
    ] * 4

    cards = build_cards(scan_of(*frames), []).cards

    assert sorted(card.text for card in cards) == [
        "右MEMO / 右のメモです",
        "左MEMO / 左のメモです",
    ]


def test_a_label_kept_up_across_two_memos_labels_each():
    # The label is a fixture (shown for over a minute in all); it stays up
    # while the memo under it changes.
    frames = [[read(LABEL, "熊元MEMO")] for _ in range(130)]
    frames += [[]] * 4
    frames += [[read(LABEL, "熊元MEMO"), read(MEMO, "山添さん嬉しい")]] * 6
    frames += [[read(LABEL, "熊元MEMO"), read(MEMO, "お腹空きすぎた")]] * 6

    cards = build_cards(scan_of(*frames), []).offered

    assert [(card.text, card.start, card.end) for card in cards] == [
        ("熊元MEMO / 山添さん嬉しい", 67.0, 70.0),
        ("熊元MEMO / お腹空きすぎた", 70.0, 73.0),
    ]


def test_a_fixture_does_not_chain_cards_together():
    logo = (0.2, 0.75, 0.8, 0.95)
    plates = [
        [read((0.1, 0.6, 0.3, 0.67), "せいや"), read((0.7, 0.6, 0.9, 0.67), "有吉弘行")]
    ]
    frames = [[read(logo, "有吉クイズ")] for _ in range(130)]
    frames += [[*plate, read(logo, "有吉クイズ")] for plate in plates * 4]

    cards = build_cards(scan_of(*frames), []).offered

    assert sorted(card.text for card in cards) == ["せいや", "有吉弘行"]


def test_lines_of_a_card_read_row_by_row_left_to_right():
    group = (0.16, 0.932, 0.21, 0.971)
    name = (0.205, 0.922, 0.29, 0.975)
    frames = [[read(name, "兼近(35)"), read(group, "EXIT")]] * 4

    (card,) = build_cards(scan_of(*frames), []).cards

    assert card.text == "EXIT / 兼近(35)"


def test_simultaneous_texts_apart_on_screen_stay_separate_cards():
    shirt = (0.6, 0.3, 0.8, 0.4)
    frames = [[read(MEMO, "相変わらず何も知らない"), read(shirt, "LAUGHING")]] * 4

    cards = build_cards(scan_of(*frames), []).cards

    assert sorted(card.text for card in cards) == ["LAUGHING", "相変わらず何も知らない"]


# Grouping cases from reviewed episodes, with their real boxes. A grouping
# rule must keep every one of them; a known gap is a strict xfail, so fixing
# it fails the run until the marker goes.


def test_a_caption_in_two_sizes_stays_one_card():
    # A small lead-in over a big full-width line: the same shape as a name
    # plate over a caption, so size and gap alone cannot split the latter.
    frames = [
        [
            read((0.139, 0.691, 0.565, 0.787), "テレビを愛するからこそ出た"),
            read((0.117, 0.744, 0.885, 0.999), "渾身の本音が炸裂!"),
        ]
    ] * 4

    (card,) = build_cards(scan_of(*frames), []).cards

    assert {line.text for line in card.lines} == {
        "テレビを愛するからこそ出た",
        "渾身の本音が炸裂!",
    }


def test_a_quote_keeps_its_small_attribution():
    frames = [
        [
            read(
                (0.131, 0.812, 0.969, 0.942), "コントは面白いが本人の顔の印象が薄い。"
            ),
            read((0.027, 0.931, 0.121, 0.977), "49歳·男性"),
        ]
    ] * 4

    (card,) = build_cards(scan_of(*frames), []).cards

    assert card.text == "コントは面白いが本人の顔の印象が薄い。 / 49歳·男性"


def test_a_caption_stays_one_card_when_the_speech_repeats_one_line():
    frames = [
        [
            read((0.431, 0.816, 0.759, 0.896), "前回出演のスパイ企画で"),
            read((0.433, 0.888, 0.970, 0.971), "奇天烈な行動を起こし現場を混乱させた"),
        ]
    ] * 4
    transcript = [cue(1, 0.0, 2.0, "前回出演のスパイ企画でね")]

    (card,) = build_cards(scan_of(*frames), transcript).cards

    assert card.text == "前回出演のスパイ企画で / 奇天烈な行動を起こし現場を混乱させた"


def test_a_name_plate_stays_one_card_when_its_name_is_spoken():
    frames = [
        [
            read((0.835, 0.449, 0.931, 0.524), "ラランド"),
            read((0.829, 0.493, 0.945, 0.596), "サーヤ"),
        ]
    ] * 4
    transcript = [cue(1, 0.0, 2.0, "サーヤ、どうですか")]

    (card,) = build_cards(scan_of(*frames), transcript).cards

    assert card.text == "ラランド / サーヤ"


def test_a_label_shown_again_on_a_later_memo_still_joins_it():
    frames: list[list[TextRead]] = []
    for frame in range(100):
        reads: list[TextRead] = []
        if frame in range(2, 6):
            reads = [read(LABEL, "あいなぷうMEMO"), read(MEMO, "知らない人だ")]
        elif frame in range(90, 94):
            reads = [read(LABEL, "あいなぷうMEMO"), read(MEMO, "井口さん嬉しい")]
        frames.append(reads)

    cards = build_cards(scan_of(*frames), []).offered

    assert [card.text for card in cards] == [
        "あいなぷうMEMO / 知らない人だ",
        "あいなぷうMEMO / 井口さん嬉しい",
    ]


# A name plate or photo label beside a big caption is its own card (owner's
# call, 2026-10-28): the label says who, the caption is what is said.
_PLATE_AND_CAPTION = {
    "plate over a full-width caption": [
        read((0.832, 0.577, 0.928, 0.652), "ラランド"),
        read((0.826, 0.622, 0.943, 0.724), "サーヤ"),
        read((0.003, 0.731, 0.996, 0.999), "最後の大根痺れました"),
    ],
    "recommender plate over a caption": [
        read((0.697, 0.503, 0.764, 0.558), "推薦人"),
        read((0.758, 0.557, 0.901, 0.627), "屋敷裕政"),
        read((0.770, 0.629, 0.890, 0.669), "ニューヨーク"),
        read((0.163, 0.686, 0.809, 0.856), "やってる事カリスマ性"),
    ],
    "photo label overlapping a caption": [
        read((0.766, 0.656, 0.960, 0.726), "中学時代津田"),
        read((0.110, 0.702, 0.895, 0.999), "そんな訳ない!!"),
    ],
}


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            "plate over a full-width caption",
            marks=pytest.mark.xfail(
                strict=True,
                reason="known gap: a plate as big as a lead-in joins the caption",
            ),
        ),
        "recommender plate over a caption",
        "photo label overlapping a caption",
    ],
)
def test_a_name_plate_and_the_caption_beside_it_are_two_cards(case: str):
    reads = _PLATE_AND_CAPTION[case]
    *plate, caption = reads

    cards = build_cards(scan_of(*[reads] * 4), []).cards

    assert sorted(card.text for card in cards) == sorted(
        [" / ".join(line.text for line in plate), caption.text]
    )


def test_a_footnote_under_a_big_title_is_its_own_card():
    frames = [
        [
            read(
                (0.127, 0.020, 0.975, 0.148), "芸人1000人が選ぶ今年面白かった芸人TOP10"
            ),
            read((0.719, 0.153, 0.974, 0.183), "2025年12月20日に放送されたものです"),
        ]
    ] * 4

    cards = build_cards(scan_of(*frames), []).cards

    assert sorted(card.text for card in cards) == [
        "2025年12月20日に放送されたものです",
        "芸人1000人が選ぶ今年面白かった芸人TOP10",
    ]


# Vertical and horizontal lines (owner's calls, 2026-10-28): they share a card
# only as a short, smaller label and its text, whichever way each is set.
_LABEL_AND_TEXT = {
    "vertical label before a caption": [
        read((0.020, 0.801, 0.056, 0.974), "このあと"),
        read((0.057, 0.799, 0.199, 0.881), "2025年"),
        read((0.046, 0.855, 0.473, 0.990), "一番面白かった芸人を発表"),
    ],
    "vertical label inside a caption panel": [
        read((0.158, 0.740, 0.181, 0.952), "私の好きは"),
        read((0.141, 0.596, 0.900, 0.999), "ロバート"),
    ],
    "vertical tag beside a reason": [
        read((0.029, 0.692, 0.315, 0.756), "ケツナダル(ワースト2)"),
        read((0.033, 0.777, 0.086, 0.956), "理由"),
        read((0.078, 0.770, 0.958, 0.970), "誕生日を理由に仕事を断る。"),
    ],
    "vertical name beside an answer": [
        read((0.353, 0.664, 0.395, 0.796), "大吉"),
        read((0.395, 0.669, 0.649, 0.793), "うさぎとかめ"),
    ],
}


@pytest.mark.parametrize("case", list(_LABEL_AND_TEXT))
def test_a_label_set_the_other_way_stays_with_its_text(case: str):
    reads = _LABEL_AND_TEXT[case]

    (card,) = build_cards(scan_of(*[reads] * 4), []).cards

    assert {line.text for line in card.lines} == {read.text for read in reads}


_SET_APART = {
    "speech bubble beside a vertical caption": (
        [
            read((0.839, 0.000, 0.999, 0.829), "食べたくて"),
            read((0.721, 0.019, 0.903, 0.999), "座る前に注文"),
        ],
        [read((0.268, 0.450, 0.752, 0.755), "親子丼!!")],
    ),
    "vertical poster name beside a caption": (
        [read((0.655, 0.000, 0.760, 0.853), "三遊亭鳳月")],
        [read((0.040, 0.852, 0.497, 0.964), "落語家さんは歯が命!!")],
    ),
    "long small caption under a vertical place sign": (
        [read((0.424, 0.167, 0.450, 0.280), "公園")],
        [read((0.455, 0.276, 0.517, 0.298), "空気が普通です")],
    ),
}


@pytest.mark.parametrize("case", list(_SET_APART))
def test_vertical_and_horizontal_text_are_two_cards(case: str):
    vertical, horizontal = _SET_APART[case]

    cards = build_cards(scan_of(*[[*vertical, *horizontal]] * 4), []).cards

    assert sorted(card.text for card in cards) == sorted(
        [" / ".join(line.text for line in vertical), horizontal[0].text]
    )
    assert [card.vertical for card in cards if card.text == horizontal[0].text] == [
        False
    ]


def test_a_vertical_card_reads_its_columns_right_to_left():
    frames = [
        [
            read((0.721, 0.019, 0.903, 0.999), "座る前に注文"),
            read((0.839, 0.000, 0.999, 0.829), "食べたくて"),
        ]
    ] * 4

    (card,) = build_cards(scan_of(*frames), []).cards

    assert card.vertical
    assert card.text == "食べたくて / 座る前に注文"


def test_a_long_running_title_does_not_label_a_vertical_caption():
    # The segment title stays up for minutes; it sits flush on a vertical
    # caption, which it would otherwise label (unrounded boxes: they sit just
    # within a touch of each other).
    title = read(
        (0.53542, 0.02315, 0.97552, 0.0713),
        "数多の賞レースを制した極み人COWCOW多田健二",
    )
    caption = read((0.45573, 0.09537, 0.54896, 0.81389), "へぼを見せてらう")
    frames = [[title] for _ in range(130)] + [[title, caption]] * 4

    cards = build_cards(scan_of(*frames), []).offered

    assert [card.text for card in cards] == ["へぼを見せてらう"]


def test_a_long_running_title_does_not_label_a_vertical_caption_through_a_tag():
    # A short horizontal tag beside the caption joins it as a label; the
    # title, set the tag's way, still does not label the caption.
    title = read(
        (0.53542, 0.02315, 0.97552, 0.0713),
        "数多の賞レースを制した極み人COWCOW多田健二",
    )
    caption = read((0.45573, 0.09537, 0.54896, 0.81389), "へぼを見せてらう")
    tag = read((0.550, 0.400, 0.600, 0.440), "えっ")
    frames = [[title] for _ in range(130)] + [[title, caption, tag]] * 4

    (card,) = build_cards(scan_of(*frames), []).offered

    assert {line.text for line in card.lines} == {"へぼを見せてらう", "えっ"}


def test_a_long_running_group_name_labels_the_vertical_name_plate_it_heads():
    # Columns side by side are measured like stacked rows, so the group name
    # (on screen all episode) is the plate's label, read first.
    group = read((0.871, 0.056, 0.918, 0.380), "かまいたち")
    name = read((0.821, 0.053, 0.884, 0.393), "濱家隆")
    frames = [[group] for _ in range(130)] + [[group, name]] * 4

    (card,) = build_cards(scan_of(*frames), []).offered

    assert card.vertical
    assert card.text == "かまいたち / 濱家隆"


def test_vertical_captions_a_column_apart_are_two_cards():
    # No reviewed episode had one yet: the caption's real box, and a second
    # one a column's width to its left (a column-length share let them join).
    right = read((0.45573, 0.09537, 0.54896, 0.81389), "へぼを見せてらう")
    left = read((0.27, 0.09537, 0.36323, 0.81389), "まだまだ")
    frames = [[right, left]] * 4

    cards = build_cards(scan_of(*frames), []).cards

    assert sorted(card.text for card in cards) == ["へぼを見せてらう", "まだまだ"]


def test_a_plate_split_by_a_long_running_column_is_one_card():
    # Its middle column's text is also the corner title, so it is recurring
    # and the plate's other columns sit a column apart (real boxes).
    name = read((0.931, 0.326, 0.972, 0.971), "パンサーオガタタカヒロ")
    event = read((0.887, 0.331, 0.928, 0.910), "足つぼルームランナー")
    title = read((0.849, 0.548, 0.892, 0.977), "世界記録保持者")
    frames = [[event] for _ in range(130)] + [[event, name, title]] * 4

    (card,) = build_cards(scan_of(*frames), []).offered

    assert card.text == "パンサーオガタタカヒロ / 足つぼルームランナー / 世界記録保持者"


def test_a_long_running_score_between_two_plates_does_not_join_them():
    # Only Japanese text bridges: a score readout between two contestants'
    # plates (real boxes) leaves them two cards.
    score = read((0.066, 0.081, 0.183, 0.243), "1 pt")
    upper = read((0.089, 0.029, 0.155, 0.102), "津田")
    lower = read((0.089, 0.270, 0.156, 0.344), "石井")
    frames = [[score] for _ in range(130)] + [[score, upper, lower]] * 4

    cards = build_cards(scan_of(*frames), []).offered

    assert sorted(card.text for card in cards) == ["1 pt / 石井", "津田 / 1 pt"]


def test_a_long_running_vertical_tag_still_labels_the_text_beside_it():
    tag = read((0.033, 0.777, 0.086, 0.956), "理由")
    who = read((0.029, 0.692, 0.315, 0.756), "ケツナダル(ワースト2)")
    reason = read((0.078, 0.770, 0.958, 0.970), "誕生日を理由に仕事を断る。")
    frames = [[tag] for _ in range(130)] + [[tag, who, reason]] * 4

    (card,) = build_cards(scan_of(*frames), []).offered

    assert card.text == "ケツナダル(ワースト2) / 理由 / 誕生日を理由に仕事を断る。"
