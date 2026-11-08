from __future__ import annotations

from grillmaster.asr.model import WordToken
from grillmaster.asr.timing import (
    COLLAPSED_STACK_WORDS,
    STACK_WINDOW_S,
    collapsed_stacks,
)


def word(text: str, start: float) -> WordToken:
    return WordToken(text, start, start, None)


def spaced(count: int, start: float = 0.0) -> list[WordToken]:
    """`count` words 0.1 s apart."""
    return [word("あ", start + index * 0.1) for index in range(count)]


def stacked(count: int, start: float) -> list[WordToken]:
    return [word("い", start) for _ in range(count)]


def test_ordinary_timings_have_no_stacks():
    assert collapsed_stacks(spaced(500)) == []


def test_a_stack_below_the_limit_is_not_collapsed():
    words = [*spaced(5), *stacked(COLLAPSED_STACK_WORDS - 1, 1.0)]

    assert collapsed_stacks(words) == []


def test_each_collapsed_stack_is_described_with_its_place():
    words = [
        *spaced(5),
        *stacked(COLLAPSED_STACK_WORDS, 908.98),
        *spaced(5, 920.0),
        *stacked(COLLAPSED_STACK_WORDS + 20, 3700.0),
    ]

    assert collapsed_stacks(words) == [
        f"15:08: 100 words ({'い' * 30}…)",
        f"1:01:40: 120 words ({'い' * 30}…)",
    ]


def test_a_stack_tolerates_jitter_inside_the_window():
    jitter = STACK_WINDOW_S / 2
    words = [
        word("い", 1.0 + jitter * (index % 2)) for index in range(COLLAPSED_STACK_WORDS)
    ]

    assert len(collapsed_stacks(words)) == 1


def test_backward_jumps_do_not_count_as_a_stack():
    words = [word("あ", 10.0 - index * 0.1) for index in range(COLLAPSED_STACK_WORDS)]

    assert collapsed_stacks(words) == []
