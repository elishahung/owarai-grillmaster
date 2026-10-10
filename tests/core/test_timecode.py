from __future__ import annotations

import pytest

from grillmaster.core.timecode import (
    TimeRange,
    ass_centiseconds,
    ass_time_from_ms,
    format_ass_centiseconds,
    format_clock,
    format_elapsed,
    format_srt_time,
    format_timecode_line,
    parse_timecode_line,
    parse_timecode_ms,
)


def test_parse_timecode_ms_reads_both_ends():
    assert parse_timecode_ms("01:02:03,456 --> 01:02:04,007") == (3_723_456, 3_724_007)


@pytest.mark.parametrize(
    "line",
    [
        "00:00:01.500 --> 00:00:02.000",
        "  00:00:01,500-->00:00:02,000  ",
    ],
)
def test_parse_timecode_tolerates_dots_and_spacing(line: str):
    assert parse_timecode_ms(line) == (1500, 2000)


@pytest.mark.parametrize(
    "line", ["0:00:01,500 --> 00:00:02,000", "00:00:01,500", "garbage", ""]
)
def test_parse_timecode_rejects_malformed_lines(line: str):
    with pytest.raises(ValueError, match="Invalid SRT timecode"):
        parse_timecode_ms(line)


def test_parse_timecode_line_returns_seconds():
    assert parse_timecode_line("00:01:00,250 --> 00:01:02,000") == TimeRange(
        60.25, 62.0
    )


def test_time_range_helpers():
    span = TimeRange(10.0, 12.5)
    assert span.duration == 2.5
    assert TimeRange(5.0, 3.0).duration == 0.0
    assert span.padded(1.0) == TimeRange(9.0, 13.5)
    assert span.overlaps(TimeRange(12.0, 20.0))
    # Touching ends do not overlap.
    assert not span.overlaps(TimeRange(12.5, 20.0))
    assert not span.overlaps(TimeRange(0.0, 10.0))


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00:00,000"),
        (3723.4567, "01:02:03,457"),
        (59.9996, "00:01:00,000"),
        (-1.0, "00:00:00,000"),
        (360_000.0, "100:00:00,000"),
    ],
)
def test_format_srt_time(seconds: float, expected: str):
    assert format_srt_time(seconds) == expected


def test_format_timecode_line_round_trips():
    line = format_timecode_line(1.5, 62.25)
    assert line == "00:00:01,500 --> 00:01:02,250"
    assert parse_timecode_line(line) == TimeRange(1.5, 62.25)


@pytest.mark.parametrize(
    ("centiseconds", "expected"),
    [(0, "0:00:00.00"), (366_101, "1:01:01.01"), (-5, "0:00:00.00")],
)
def test_format_ass_centiseconds(centiseconds: int, expected: str):
    assert format_ass_centiseconds(centiseconds) == expected


def test_ass_time_from_ms_truncates():
    assert ass_time_from_ms(1999) == "0:00:01.99"


def test_ass_centiseconds_rounds():
    assert ass_centiseconds(1.235) == 124
    assert ass_centiseconds(-2.0) == 0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00"),
        (65.9, "01:05"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (-3, "00:00"),
    ],
)
def test_format_clock(seconds: float, expected: str):
    assert format_clock(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (12.345, "12.35s"),
        (184.5, "3m 4.5s"),
        (3723.4, "1h 2m 3.4s"),
        (59.999, "1m 0.0s"),
        (3599.97, "1h 0m 0.0s"),
    ],
)
def test_format_elapsed(seconds: float, expected: str):
    assert format_elapsed(seconds) == expected
