from __future__ import annotations

from dataclasses import replace

import pytest
from tests.fakes import make_blocks

from grillmaster.core.srt import SrtBlock
from grillmaster.subtitles.structure import MAX_REPORTED_PROBLEMS, check_aligned

REFERENCE = make_blocks(3)


def test_rewritten_text_is_valid():
    candidate = [replace(block, text=f"改寫 {block.index}") for block in REFERENCE]
    assert check_aligned(REFERENCE, candidate) == []


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        pytest.param(
            REFERENCE[:2],
            "block count differs: reference=3 candidate=2",
            id="count",
        ),
        pytest.param(
            [REFERENCE[0], replace(REFERENCE[1], index=7), REFERENCE[2]],
            "position 2: index changed 2 -> 7",
            id="index",
        ),
        pytest.param(
            [
                REFERENCE[0],
                replace(REFERENCE[1], timecode="00:00:04,000 --> 00:00:05,600"),
                REFERENCE[2],
            ],
            (
                "block 2: timecode changed "
                "'00:00:04,000 --> 00:00:05,500' -> '00:00:04,000 --> 00:00:05,600'"
            ),
            id="timecode",
        ),
        pytest.param(
            [REFERENCE[0], REFERENCE[1], replace(REFERENCE[2], text="")],
            "block 3: text is empty",
            id="empty-text",
        ),
    ],
)
def test_single_problem_is_reported(candidate: list[SrtBlock], expected: str):
    assert check_aligned(REFERENCE, candidate) == [expected]


def test_problems_on_one_block_are_all_listed():
    broken = SrtBlock(9, "00:00:00,000 --> 00:00:00,500", "")
    assert check_aligned(REFERENCE, [broken, *REFERENCE[1:]]) == [
        "position 1: index changed 1 -> 9",
        (
            "block 1: timecode changed "
            "'00:00:02,000 --> 00:00:03,500' -> '00:00:00,000 --> 00:00:00,500'"
        ),
        "block 9: text is empty",
    ]


def test_dropped_block_report_is_capped():
    reference = make_blocks(1000)
    candidate = reference[:10] + reference[11:]  # block 11 dropped
    problems = check_aligned(reference, candidate)
    assert len(problems) == MAX_REPORTED_PROBLEMS + 1
    assert problems[0] == "block count differs: reference=1000 candidate=999"
    assert problems[1] == "position 11: index changed 11 -> 12"
    # 989 misaligned positions x (index + timecode) + the count line.
    assert (
        problems[-1] == f"... and {989 * 2 + 1 - MAX_REPORTED_PROBLEMS} more problems"
    )
