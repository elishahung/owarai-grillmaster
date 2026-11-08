from __future__ import annotations

from typing import Any

from grillmaster.asr import scribe
from grillmaster.asr.timing import (
    COLLAPSED_STACK_WORDS,
    STACK_WINDOW_S,
)
from grillmaster.asr.timing import collapsed_stacks as stacks_of_words


def collapsed_stacks(payload: dict[str, Any]) -> list[str]:
    """The check as the ASR stage runs it on a Scribe response."""
    return stacks_of_words(scribe.timed_words(payload))


def word(text: str, start: float) -> dict[str, object]:
    return {"text": text, "start": start, "end": start, "type": "word"}


def spaced(count: int, start: float = 0.0) -> list[dict[str, object]]:
    """`count` words 0.1 s apart."""
    return [word("あ", start + index * 0.1) for index in range(count)]


def stacked(count: int, start: float) -> list[dict[str, object]]:
    return [word("い", start) for _ in range(count)]


def test_ordinary_timings_have_no_stacks():
    assert collapsed_stacks({"words": spaced(500)}) == []


def test_a_stack_below_the_limit_is_not_collapsed():
    payload = {"words": [*spaced(5), *stacked(COLLAPSED_STACK_WORDS - 1, 1.0)]}

    assert collapsed_stacks(payload) == []


def test_each_collapsed_stack_is_described_with_its_place():
    payload = {
        "words": [
            *spaced(5),
            *stacked(COLLAPSED_STACK_WORDS, 908.98),
            *spaced(5, 920.0),
            *stacked(COLLAPSED_STACK_WORDS + 20, 3700.0),
        ]
    }

    assert collapsed_stacks(payload) == [
        f"15:08: 100 words ({'い' * 30}…)",
        f"1:01:40: 120 words ({'い' * 30}…)",
    ]


def test_a_stack_tolerates_jitter_inside_the_window():
    jitter = STACK_WINDOW_S / 2
    words = [
        word("い", 1.0 + jitter * (index % 2)) for index in range(COLLAPSED_STACK_WORDS)
    ]

    assert len(collapsed_stacks({"words": words})) == 1


def test_backward_jumps_do_not_count_as_a_stack():
    words = [word("あ", 10.0 - index * 0.1) for index in range(COLLAPSED_STACK_WORDS)]

    assert collapsed_stacks({"words": words}) == []


def test_spacing_and_untimed_entries_do_not_count():
    words: list[object] = []
    for _ in range(COLLAPSED_STACK_WORDS // 2):
        words += [
            word("い", 1.0),
            {"text": " ", "start": 1.0, "end": 1.0, "type": "spacing"},
            {"text": "う", "type": "word"},
            "not a word",
        ]

    assert collapsed_stacks({"words": words}) == []


def test_multichannel_transcripts_are_checked():
    payload = {"transcripts": [{"words": stacked(COLLAPSED_STACK_WORDS, 5.0)}]}

    assert len(collapsed_stacks(payload)) == 1
