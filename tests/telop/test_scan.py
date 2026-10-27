from __future__ import annotations

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


def test_simultaneous_texts_apart_on_screen_stay_separate_cards():
    shirt = (0.6, 0.3, 0.8, 0.4)
    frames = [[read(MEMO, "相変わらず何も知らない"), read(shirt, "LAUGHING")]] * 4

    cards = build_cards(scan_of(*frames), []).cards

    assert sorted(card.text for card in cards) == ["LAUGHING", "相変わらず何も知らない"]
