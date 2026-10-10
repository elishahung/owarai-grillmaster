from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.core.srt import (
    SrtBlock,
    parse_srt,
    read_srt_file,
    reindex,
    serialize_srt,
    write_srt_file,
)
from grillmaster.core.timecode import TimeRange

if TYPE_CHECKING:
    from pathlib import Path

SAMPLE = (
    "1\n00:00:01,000 --> 00:00:02,500\nこんにちは\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n一行目\n二行目\n"
)


def test_parse_reads_index_timecode_and_multiline_text():
    blocks = parse_srt(SAMPLE)
    assert blocks == [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,500", "こんにちは"),
        SrtBlock(2, "00:00:03,000 --> 00:00:04,000", "一行目\n二行目"),
    ]


def test_parse_tolerates_crlf_and_extra_blank_lines():
    text = (
        "\r\n\r\n" + SAMPLE.replace("\n\n", "\n\n\n\n").replace("\n", "\r\n") + "  \r\n"
    )
    assert parse_srt(text) == parse_srt(SAMPLE)


def test_parse_keeps_blocks_without_text():
    blocks = parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nb\n"
    )
    assert [block.text for block in blocks] == ["", "b"]


@pytest.mark.parametrize("text", ["", "   \n\n  "])
def test_parse_empty_input(text: str):
    assert parse_srt(text) == []


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("x\n00:00:01,000 --> 00:00:02,000\ntext\n", "index"),
        ("1\n00:00:01 --> 00:00:02\ntext\n", "timecode"),
    ],
    ids=["bad-index", "bad-timecode"],
)
def test_parse_rejects_malformed_blocks(text: str, match: str):
    with pytest.raises(ValueError, match=match):
        parse_srt(text)


def test_serialize_round_trips_including_empty_text():
    blocks = [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,000", ""),
        SrtBlock(2, "00:00:03,000 --> 00:00:04,000", "a\nb"),
    ]
    text = serialize_srt(blocks)
    assert text.endswith("\n")
    assert parse_srt(text) == blocks


def test_block_properties():
    block = SrtBlock(7, "00:00:01,000 --> 00:00:02,500", "台詞")
    assert block.raw == "7\n00:00:01,000 --> 00:00:02,500\n台詞"
    assert block.char_count == len(block.raw)
    assert block.time_range == TimeRange(1.0, 2.5)


def test_reindex_renumbers_from_one():
    timecode = "00:00:01,000 --> 00:00:02,000"
    blocks = [SrtBlock(5, timecode, "a")] * 3
    assert reindex(blocks) == [SrtBlock(i, timecode, "a") for i in (1, 2, 3)]


def test_read_srt_file_strips_utf8_bom(tmp_path: Path):
    path = tmp_path / "agent.srt"
    path.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))
    assert read_srt_file(path) == parse_srt(SAMPLE)


def test_write_srt_file_creates_parents_and_round_trips(tmp_path: Path):
    path = tmp_path / "nested" / "out.srt"
    blocks = parse_srt(SAMPLE)
    write_srt_file(path, blocks)
    assert read_srt_file(path) == blocks
    assert b"\r\n" not in path.read_bytes()


def test_parse_rejects_incomplete_block():
    with pytest.raises(ValueError, match="Incomplete SRT block"):
        parse_srt("1\n00:00:01,000 --> 00:00:02,000\nHi\n\nDone.\n")


def test_parse_splits_on_whitespace_only_separator():
    text = "1\n00:00:01,000 --> 00:00:02,000\nA\n \t\n2\n00:00:03,000 --> 00:00:04,000\nB\n"
    assert [block.text for block in parse_srt(text)] == ["A", "B"]
