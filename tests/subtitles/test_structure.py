from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_blocks

from grillmaster.core.srt import SrtBlock, serialize_srt, write_srt_file
from grillmaster.subtitles.structure import (
    MAX_REPORTED_PROBLEMS,
    check_aligned,
    check_aligned_file,
)

if TYPE_CHECKING:
    from pathlib import Path

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


def test_aligned_file_is_checked_against_the_reference(tmp_path: Path):
    path = tmp_path / "candidate.srt"
    write_srt_file(path, REFERENCE[:2])

    assert check_aligned_file(REFERENCE, path) == [
        "block count differs: reference=3 candidate=2"
    ]


def test_aligned_file_with_a_bom_is_valid(tmp_path: Path):
    path = tmp_path / "candidate.srt"
    path.write_text("﻿" + serialize_srt(REFERENCE), encoding="utf-8")

    assert check_aligned_file(REFERENCE, path) == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(None, "file not found: ", id="missing"),
        pytest.param("1\nnot a timecode\n字\n", "cannot parse ", id="malformed"),
        pytest.param(b"\xff\xfe\x00", "cannot parse ", id="not-utf8"),
    ],
)
def test_unreadable_file_is_a_problem(
    tmp_path: Path, content: str | bytes | None, expected: str
):
    path = tmp_path / "candidate.srt"
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    elif content is not None:
        path.write_bytes(content)

    (problem,) = check_aligned_file(REFERENCE, path)
    assert problem.startswith(expected)
    assert str(path) in problem


def test_a_path_that_cannot_be_read_is_a_problem(tmp_path: Path):
    # A directory stands in for a locked file: both fail with an `OSError`.
    path = tmp_path / "candidate.srt"
    path.mkdir()

    (problem,) = check_aligned_file(REFERENCE, path)
    assert problem.startswith(f"cannot read {path}: ")
